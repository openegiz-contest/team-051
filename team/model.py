"""Deterministic, accelerated Stepnoe model. All masses are dry tonnes.

Inventory transfers happen once at state boundaries; clocks use virtual seconds.
No accounting value is ever used as a physical sensor input.
"""
import math
from dataclasses import asdict, dataclass, field

NS = "org.stepnoe"
FRAUD_TOPIC = "telemetry/stockpile/inject_fraud"
OK = "BALANCE OK"
CRITICAL = "CRITICAL ANOMALY: Mass Balance Mismatch"
PIT = (52.10, 63.10)
PLANT = (52.12, 63.13)
RAIL_END = (52.12 + 65 / 111.32, 63.132)


def feature(value, unit=None):
    return {"properties": {"value": value, **({"unit": unit} if unit else {})}}


def coordinates(a, b, fraction):
    return [a[i] + (b[i] - a[i]) * fraction for i in range(2)]


def in_corridor(lat, lon, start, end, width_m=150):
    """Distance to a line segment in a local equirectangular projection."""
    scale = math.cos(math.radians(start[0]))
    px, py = (lon - start[1]) * scale * 111320, (lat - start[0]) * 111320
    dx, dy = (end[1] - start[1]) * scale * 111320, (end[0] - start[0]) * 111320
    t = max(0, min(1, (px * dx + py * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - t * dx, py - t * dy) <= width_m


@dataclass
class Truck:
    name: str
    state: str = "loading"
    elapsed: float = 0
    weight: float = 0
    grade: float = .44
    trips: int = 0
    passes: int = 0


@dataclass
class Model:
    time: float = 0
    initial_stock: float = 3000
    stock: float = 3000
    buffer: float = 0
    buffer_fe: float = 0
    mined: float = 0
    mined_fe: float = 0
    feed: float = 0
    feed_fe: float = 0
    concentrate: float = 0
    tails: float = 0
    tails_fe: float = 0
    rail_loaded: float = 0
    shipped: float = 0
    shipped_fe: float = 0
    wagon_weights: list = field(default_factory=lambda: [0.] * 5)
    rail_state: str = "loading"
    rail_elapsed: float = 0
    departures: int = 0
    fraud: bool = False
    fraud_mode: str = "fixed"
    revision: int = 0
    trucks: list = field(default_factory=lambda: [Truck("Truck_01"), Truck("Truck_02", "queued", elapsed=60)])

    def control(self, payload):
        if payload not in (b"0", b"1"):
            raise ValueError("command must be exactly 0 or 1")
        self.fraud = payload == b"1"
        self.revision += 1

    def step(self, seconds):
        if not math.isfinite(seconds) or seconds <= 0 or seconds > 3600:
            raise ValueError("step must be in (0, 3600] virtual seconds")
        # One-second substeps prevent skipped bucket/unload/departure events.
        remaining = seconds
        while remaining > 1e-9:
            dt = min(1., remaining)
            self._advance(dt)
            remaining -= dt
        self.revision += 1

    def _advance(self, dt):
        self.time += dt
        for truck in self.trucks:
            truck.elapsed += dt
            if truck.state == "queued":
                if truck.elapsed >= 120:
                    truck.state, truck.elapsed = "loading", 0
            elif truck.state == "loading":
                # A single excavator: second truck waits if the first is loading.
                if any(t is not truck and t.state == "loading" and t.name < truck.name for t in self.trucks):
                    truck.elapsed = 0
                    continue
                if truck.elapsed >= 30:
                    truck.elapsed -= 30
                    truck.weight += 22.5
                    truck.passes += 1
                    self.mined += 22.5
                    self.mined_fe += 22.5 * truck.grade
                    if truck.weight >= 90:
                        truck.state, truck.elapsed = "hauling", 0
            elif truck.state == "hauling" and truck.elapsed >= 360:
                truck.state, truck.elapsed = "unloading", 0
            elif truck.state == "unloading" and truck.elapsed >= 40:
                self.buffer += truck.weight
                self.buffer_fe += truck.weight * truck.grade
                truck.weight = 0
                truck.trips += 1
                truck.state, truck.elapsed = "returning", 0
            elif truck.state == "returning" and truck.elapsed >= 360:
                truck.state, truck.elapsed = "loading", 0
                truck.grade = [.43, .44, .45][truck.trips % 3]

        raw = min(self.buffer, 600 / 3600 * dt)
        grade = self.buffer_fe / self.buffer if self.buffer > 1e-9 else .44
        recovery = .835
        product = raw * grade * recovery / .55
        self.buffer -= raw
        self.buffer_fe -= raw * grade
        self.feed += raw
        self.feed_fe += raw * grade
        self.concentrate += product
        self.stock += product
        self.tails += raw - product
        self.tails_fe += raw * grade - product * .55

        self.rail_elapsed += dt
        if self.rail_state == "loading":
            for i, weight in enumerate(self.wagon_weights):
                if weight < 70 - 1e-8:
                    amount = min(70 - weight, self.stock, 200 / 3600 * dt)
                    self.wagon_weights[i] += amount
                    self.stock -= amount
                    self.rail_loaded += amount
                    break
            if sum(self.wagon_weights) >= 350 - 1e-7:
                # Certified rail weighbridge: outbound product crosses boundary.
                self.shipped += sum(self.wagon_weights)
                self.shipped_fe += sum(self.wagon_weights) * .55
                self.departures += 1
                self.rail_state, self.rail_elapsed = "outbound", 0
        elif self.rail_state == "outbound" and self.rail_elapsed >= 5850:
            self.wagon_weights = [0.] * 5
            self.rail_state, self.rail_elapsed = "returning", 0
        elif self.rail_state == "returning" and self.rail_elapsed >= 5850:
            self.rail_state, self.rail_elapsed = "loading", 0

    def audit(self):
        trucks = sum(t.weight for t in self.trucks)
        rail = sum(self.wagon_weights) if self.rail_state == "loading" else 0
        inventory = self.stock + self.buffer + trucks + rail
        offset = (300 if self.fraud_mode == "fixed" else self.stock * .10) if self.fraud else 0
        # An elliptic cone is the synthetic scan; volume = pi*a*b*h/3.
        height = (self.stock / 2.7) * 3 / (math.pi * 25 * 15)
        scan_volume = math.pi * 25 * 15 * height / 3
        physical = scan_volume * 2.7
        stock_residual = self.initial_stock + self.concentrate - self.rail_loaded - physical
        physical_residual = self.initial_stock + self.mined - self.tails - self.shipped - inventory
        fe_inventory = self.stock * .55 + self.buffer_fe + sum(t.weight * t.grade for t in self.trucks) + rail * .55
        fe_residual = self.initial_stock * .55 + self.mined_fe - self.tails_fe - self.shipped_fe - fe_inventory
        book = physical + offset
        book_residual = self.initial_stock + self.concentrate - self.rail_loaded - book
        critical = max(abs(book_residual), abs(physical_residual), abs(fe_residual)) > 1
        return dict(physical_stock=physical, book_stock=book, scan_volume=scan_volume, height=height,
                    fraud_offset=offset, discrepancy_pct=100 * offset / physical if physical else 0,
                    stock_residual=stock_residual, physical_residual=physical_residual,
                    book_residual=book_residual, fe_residual=fe_residual, inventory=inventory,
                    anomaly=int(critical), status=CRITICAL if critical else OK)

    def telemetry(self):
        a = self.audit()
        f = feature
        common = {"simulation_time": f(self.time, "s"), "revision": f(self.revision)}
        result = {
            "Stepnoe": {"mined": f(self.mined, "t"), "shipped": f(self.shipped, "t"), "state": f(a["status"])},
            "Excavator_01": {"bucket_weight": f(22.5 if any(t.state == "loading" for t in self.trucks) else 0, "t"),
                             "passes": f(sum(t.passes for t in self.trucks)), "mined": f(self.mined, "t"),
                             "state": f("loading" if any(t.state == "loading" for t in self.trucks) else "idle")},
            "Processing_Plant": {"raw_inflow": f(self.feed, "t"), "concentrate": f(self.concentrate, "t"),
                                 "tails": f(self.tails, "t"), "buffer": f(self.buffer, "t"),
                                 "feed_grade": f(100 * self.feed_fe / self.feed if self.feed else 44, "% Fe"),
                                 "concentrate_grade": f(55, "% Fe"), "recovery": f(83.5, "%"),
                                 "tails_grade": f(100 * self.tails_fe / self.tails if self.tails else 0, "% Fe")},
            "Stockpile_01": {"volume": f(a["scan_volume"], "m3"), "weight": f(a["physical_stock"], "t"),
                             "book_weight": f(a["book_stock"], "t"), "density": f(2.7, "t/m3"),
                             "moisture": f(0, "%"), "height": f(a["height"], "m"), "fraud": f(int(self.fraud))},
            "Mass_Balance": {key: f(value, "t" if key.endswith("residual") or key in ("physical_stock", "book_stock", "fraud_offset", "inventory") else None)
                             for key, value in a.items()},
            "Rail_Siding": {"loaded": f(self.rail_loaded, "t"), "shipped": f(self.shipped, "t"), "departures": f(self.departures), "state": f(self.rail_state)},
            "Pit": {"mined": f(self.mined, "t")},
        }
        for t in self.trucks:
            p = min(1, t.elapsed / 360)
            position = coordinates(PIT, PLANT, p if t.state == "hauling" else 1 - p if t.state == "returning" else 1 if t.state == "unloading" else 0)
            result[t.name] = {"lat": f(position[0]), "lon": f(position[1]), "weight": f(t.weight, "t"),
                              "speed": f(30 if t.state in ("hauling", "returning") else 0, "km/h"),
                              "state": f(t.state), "geofence": f("INSIDE" if in_corridor(*position, PIT, PLANT) else "OUTSIDE"),
                              "trips": f(t.trips)}
        p = min(1, self.rail_elapsed / 5850)
        position = coordinates(PLANT, RAIL_END, p if self.rail_state == "outbound" else 1 - p if self.rail_state == "returning" else 0)
        for i, weight in enumerate(self.wagon_weights):
            result[f"Wagon_{i + 1:02}"] = {"lat": f(position[0]), "lon": f(position[1] + i * .00015),
                "weight": f(weight, "t"), "weighbridge": f(weight if self.rail_state == "outbound" else 0, "t"),
                "state": f(self.rail_state), "speed": f(40 if self.rail_state != "loading" else 0, "km/h"),
                "geofence": f("INSIDE" if in_corridor(position[0], position[1] + i * .00015, PLANT, RAIL_END) else "OUTSIDE")}
        return {name: {**values, **common} for name, values in result.items()}

    def dump(self):
        return asdict(self)

    @classmethod
    def restore(cls, data):
        data = dict(data)
        data["trucks"] = [Truck(**t) for t in data["trucks"]]
        return cls(**data)
