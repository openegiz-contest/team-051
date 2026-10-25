"""Deterministic, accelerated Stepnoe model. Legacy mass fields are dry tonnes.

Inventory transfers happen once at state boundaries; clocks use virtual seconds.
No accounting value is ever used as a physical sensor input.
"""
import math
from dataclasses import asdict, dataclass, field
from sensors import advance_sensors, new_sensors, reconcile

NS = "org.stepnoe"
FRAUD_TOPIC = "telemetry/stockpile/inject_fraud"
MAINTENANCE_TOPIC = "telemetry/control/maintenance/"
MAINTAINABLE = ("Processing_Plant", "Truck_01", "Truck_02")
SCENARIO_TOPIC = 'telemetry/control/scenario/'
SCENARIOS = ('plant_erp', 'sensor_bias', 'rain')
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
    maintenance: bool = False
    blocked: bool = False


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
    fraud_direction: int = 1
    fraud_mode: str = "fixed"
    drift_rate_tph: float = 5.
    drift_tonnes: float = 0.
    drift_compensation: float = 0.
    moisture_pct: float = 3.
    lidar_noise_pct: float = .5
    lidar_rng_state: int = 2026
    lidar_noise_fraction: float = 0.
    plant_maintenance: bool = False
    raw_inflow_rate: float = 0.
    revision: int = 0
    sensors: dict | None = None
    erp_history: list = field(default_factory=list)
    false_alarm_probability: float = 1e-6
    max_stock_capacity: float = 6000.
    max_buffer_capacity: float = 360.
    max_truck_queue: int = 1
    scenario_plant_erp: bool = False
    scenario_sensor_bias: bool = False
    scenario_rain: bool = False
    wet_feed: float | None = None
    erp_phantom_stock: float = 0.
    trucks: list = field(default_factory=lambda: [Truck("Truck_01"), Truck("Truck_02", "queued", elapsed=60)])

    def __post_init__(self):
        if self.fraud_mode not in ("fixed", "percent", "drift"):
            raise ValueError("invalid fraud_mode")
        for name, maximum in (("moisture_pct", 100), ("lidar_noise_pct", .5), ("drift_rate_tph", 1e6)):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError(f"invalid {name}")
        if not math.isfinite(self.false_alarm_probability) or not 0 <= self.false_alarm_probability <= 1:
            raise ValueError("invalid false_alarm_probability")
        if self.sensors is None:
            self.sensors = new_sensors(self.stock, self.time)
        self.sensors.setdefault('scale_moisture', self.moisture_pct)
        self.sensors.setdefault('wet_scale_reading', self.sensors['scale_reading'] * (1 + self.sensors['scale_moisture'] / 100))
        if self.wet_feed is None:
            self.wet_feed = self.feed * (1 + self.moisture_pct / 100)
        if not self.erp_history:
            self.erp_history = [[self.time, self.initial_stock + self.concentrate - self.rail_loaded]]
        if (not math.isfinite(self.max_stock_capacity) or self.max_stock_capacity <= 0 or self.max_stock_capacity < self.stock or
                not math.isfinite(self.max_buffer_capacity) or self.max_buffer_capacity < max(90, self.buffer) or
                type(self.max_truck_queue) is not int or self.max_truck_queue < 0):
            raise ValueError("capacities must contain existing inventory; buffer must fit one truck")

    @property
    def ground_truth_mass(self):
        return self.stock

    @property
    def truck_queue(self):
        return [t.name for t in self.trucks if t.blocked and not t.maintenance]

    @property
    def excavator_blocked(self):
        return len(self.truck_queue) > self.max_truck_queue

    @property
    def plant_stopped(self):
        return self.plant_maintenance or self.scenario_plant_erp

    def set_scenario(self, name, enabled):
        if name not in SCENARIOS or type(enabled) is not bool:
            raise ValueError('invalid scenario')
        setattr(self, 'scenario_' + name, enabled)
        if name == 'plant_erp' and not enabled:
            self.erp_phantom_stock = 0.
        if self.plant_stopped:
            self.raw_inflow_rate = 0.
        self.revision += 1

    @property
    def wet_stock(self):
        # Moisture is water/dry-solid mass, matching dry = wet / (1 + w/100).
        return self.stock * (1 + self.actual_moisture_pct / 100)

    @property
    def actual_moisture_pct(self):
        return 7. if self.scenario_rain else self.moisture_pct

    @property
    def dry_stock(self):
        return self.wet_stock / (1 + self.actual_moisture_pct / 100)

    def set_maintenance(self, thing, enabled):
        if thing not in MAINTAINABLE or type(enabled) is not bool:
            raise ValueError("maintenance requires a supported thing and a boolean")
        if thing == "Processing_Plant":
            self.plant_maintenance = enabled
            if enabled:
                self.raw_inflow_rate = 0.
        else:
            next(t for t in self.trucks if t.name == thing).maintenance = enabled
        self.revision += 1

    def control(self, payload):
        if payload not in (b"-1", b"0", b"1"):
            raise ValueError("command must be exactly -1, 0 or 1")
        self.fraud = payload != b"0"
        self.fraud_direction = -1 if payload == b"-1" else 1
        if not self.fraud:
            self.drift_tonnes = self.drift_compensation = 0.
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
        if self.scenario_plant_erp:
            # ERP keeps booking nominal dry concentrate while the mill is down.
            self.erp_phantom_stock += 600 * .44 * .835 / .55 / 3600 * dt
        if self.fraud and self.fraud_mode == "drift":
            # Compensated integration avoids a threshold crossing caused by rounding.
            increment = dt * self.drift_rate_tph / 3600 - self.drift_compensation
            total = self.drift_tonnes + increment
            self.drift_compensation = (total - self.drift_tonnes) - increment
            self.drift_tonnes = total
        for truck in self.trucks:
            if truck.maintenance or (truck.state == 'loading' and self.excavator_blocked):
                continue  # Keep its load, position, grade and in-flight state for resume.
            truck.elapsed += dt
            if truck.state == "queued":
                if truck.elapsed >= 120:
                    truck.state, truck.elapsed = "loading", 0
            elif truck.state == "loading":
                # A single excavator: second truck waits if the first is loading.
                if any(t is not truck and not t.maintenance and t.state == "loading" and t.name < truck.name for t in self.trucks):
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
                # A gated hopper may accept part of a load. The rest stays in
                # the tipped truck; arbitrary capacity values never lose mass
                # or deadlock below 100% because a whole truck cannot fit.
                amount = min(truck.weight, max(0., self.max_buffer_capacity - self.buffer))
                self.buffer += amount
                self.buffer_fe += amount * truck.grade
                truck.weight -= amount
                if truck.weight > 0:
                    truck.blocked = True
                    truck.elapsed = 40  # Remain at the receiving hopper with the remaining load.
                    continue
                truck.blocked = False
                truck.weight = 0
                truck.trips += 1
                truck.state, truck.elapsed = "returning", 0
            elif truck.state == "returning" and truck.elapsed >= 360:
                truck.state, truck.elapsed = "loading", 0
                truck.grade = [.43, .44, .45][truck.trips % 3]

        grade = self.buffer_fe / self.buffer if self.buffer > 1e-9 else .44
        recovery = .835
        yield_fraction = grade * recovery / .55
        raw = 0. if self.plant_stopped else min(self.buffer, 600 / 3600 * dt,
                  max(0., self.max_stock_capacity - self.stock) / yield_fraction)
        self.raw_inflow_rate = raw / dt * 3600
        product = raw * grade * recovery / .55
        self.buffer -= raw
        self.buffer_fe -= raw * grade
        self.feed += raw
        self.wet_feed += raw * (1 + self.actual_moisture_pct / 100)
        self.feed_fe += raw * grade
        self.concentrate += product
        self.stock += product
        self.tails += raw - product
        self.tails_fe += raw * grade - product * .55
        measured_transfer = product

        self.rail_elapsed += dt
        if self.rail_state == "loading":
            for i, weight in enumerate(self.wagon_weights):
                if weight < 70 - 1e-8:
                    amount = min(70 - weight, self.stock, 200 / 3600 * dt)
                    self.wagon_weights[i] += amount
                    self.stock -= amount
                    self.rail_loaded += amount
                    measured_transfer -= amount
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
        self.erp_history.append([self.time, self.initial_stock + self.concentrate - self.rail_loaded, self.erp_phantom_stock])
        self.erp_history = [row for row in self.erp_history if row[0] >= self.time - 60]
        height = self.stock / 2.7 * 3 / (math.pi * 25 * 15)
        advance_sensors(self.sensors, self.time, height, measured_transfer, self.lidar_noise_pct,
                        scale_bias_pct=2. if self.scenario_sensor_bias else 0.,
                        false_alarm_probability=self.false_alarm_probability, moisture_pct=self.actual_moisture_pct)
        self.lidar_noise_fraction = self.sensors['noise_fraction']

    def audit(self):
        trucks = sum(t.weight for t in self.trucks)
        rail = sum(self.wagon_weights) if self.rail_state == "loading" else 0
        inventory = self.stock + self.buffer + trucks + rail
        magnitude = {"fixed": 300, "percent": (self.initial_stock + self.concentrate - self.rail_loaded) * .10, "drift": self.drift_tonnes}[self.fraud_mode]
        offset = self.fraud_direction * magnitude if self.fraud else 0
        # An elliptic cone is the synthetic scan; volume = pi*a*b*h/3.
        height = (self.stock / 2.7) * 3 / (math.pi * 25 * 15)
        reference_volume = math.pi * 25 * 15 * height / 3
        scan_volume = self.sensors['lidar_volume']
        # Truth is retained for conservation diagnostics only; reconcile() below
        # cannot access these values, Model, inventories or physical residuals.
        physical = self.stock
        stock_residual = self.initial_stock + self.concentrate - self.rail_loaded - physical
        physical_residual = self.initial_stock + self.mined - self.tails - self.shipped - inventory
        fe_inventory = self.stock * .55 + self.buffer_fe + sum(t.weight * t.grade for t in self.trucks) + rail * .55
        fe_residual = self.initial_stock * .55 + self.mined_fe - self.tails_fe - self.shipped_fe - fe_inventory
        scale_moisture = self.sensors.get('scale_moisture', self.moisture_pct)
        wet_reading = self.sensors.get('wet_scale_reading', self.sensors['scale_reading'] * (1 + scale_moisture / 100))
        # ERP converts the wet weighbridge reading with its unchanged registered
        # moisture. No ground-truth inventory is used to calculate this error.
        moisture_offset = wet_reading / (1 + self.moisture_pct / 100) - wet_reading / (1 + scale_moisture / 100)
        book = self.initial_stock + self.concentrate - self.rail_loaded + offset + self.erp_phantom_stock + moisture_offset
        book_residual = self.initial_stock + self.concentrate - self.rail_loaded - book
        def aligned_book(timestamp):
            rows = [r for r in self.erp_history if r[0] <= timestamp]
            row = rows[-1] if rows else self.erp_history[0]
            return row[1] + (row[2] if len(row) > 2 else 0.) + offset + moisture_offset
        decision = reconcile({'value': self.sensors['scale_reading'], 'time': self.sensors['scale_time'],
                              'wet_value': wet_reading, 'moisture_pct': scale_moisture},
                             {'value': scan_volume, 'time': self.sensors['lidar_time']},
                             aligned_book(self.sensors['scale_time']), aligned_book(self.sensors['lidar_time']),
                             self.lidar_noise_pct, self.plant_stopped and self.erp_phantom_stock > 0, self.moisture_pct)
        return dict(physical_stock=physical, ground_truth_mass=physical, book_stock=book, scan_volume=scan_volume, height=height,
                    wet_stock=self.wet_stock, dry_stock=self.dry_stock,
                    moisture_pct=self.actual_moisture_pct, erp_moisture_pct=self.moisture_pct, reference_volume=reference_volume,
                    wet_scale_reading=wet_reading, scale_moisture_pct=scale_moisture, moisture_erp_offset=moisture_offset,
                    lidar_noise_bound_pct=self.lidar_noise_pct,
                    lidar_noise_pct=self.lidar_noise_fraction * 100,
                    fraud_active=int(self.fraud), fraud_direction=self.fraud_direction,
                    fraud_mode=self.fraud_mode, drift_rate_tph=self.drift_rate_tph,
                    fraud_offset=offset, discrepancy_pct=100 * offset / physical if physical else 0,
                    stock_residual=stock_residual, physical_residual=physical_residual,
                    book_residual=book_residual, fe_residual=fe_residual, inventory=inventory,
                    false_alarm_probability=self.false_alarm_probability,
                    stock_capacity=self.max_stock_capacity, buffer_capacity=self.max_buffer_capacity,
                    buffer_fill_pct=self.buffer / self.max_buffer_capacity * 100,
                    stock_fill_pct=self.stock / self.max_stock_capacity * 100,
                    truck_queue_length=len(self.truck_queue), excavator_interlock=int(self.excavator_blocked),
                    scenario_plant_erp=int(self.scenario_plant_erp), scenario_sensor_bias=int(self.scenario_sensor_bias),
                    scenario_rain=int(self.scenario_rain),
                    erp_offset=self.erp_phantom_stock, scale_bias_pct=2. if self.scenario_sensor_bias else 0.,
                    status=CRITICAL if decision['anomaly'] else OK, **decision)

    def telemetry(self):
        a = self.audit()
        f = feature
        common = {"simulation_time": f(self.time, "s"), "revision": f(self.revision)}
        result = {
            "Stepnoe": {"mined": f(self.mined, "t"), "shipped": f(self.shipped, "t"), "state": f(a["status"])},
            "Excavator_01": {"bucket_weight": f(22.5 if any(t.state == "loading" and not t.maintenance for t in self.trucks) else 0, "t"),
                             "passes": f(sum(t.passes for t in self.trucks)), "mined": f(self.mined, "t"),
                             "state": f("IDLE" if self.excavator_blocked else "loading" if any(t.state == "loading" and not t.maintenance for t in self.trucks) else "idle"),
                             "interlock": f(int(self.excavator_blocked))},
            "Processing_Plant": {"raw_inflow": f(self.feed, "t"), "concentrate": f(self.concentrate, "t"),
                                 "tails": f(self.tails, "t"), "buffer": f(self.buffer, "t"),
                                 "raw_inflow_rate": f(self.raw_inflow_rate, "t/h"),
                                 "wet_inflow": f(self.wet_feed, "t"),
                                 "moisture": f(self.actual_moisture_pct, "%"),
                                 "maintenance": f(int(self.plant_stopped)),
                                 "manual_maintenance": f(int(self.plant_maintenance)),
                                 "buffer_capacity": f(self.max_buffer_capacity, 't'),
                                 "buffer_fill_pct": f(self.buffer / self.max_buffer_capacity * 100, '%'),
                                 "truck_queue": f(self.truck_queue),
                                 "state": f("maintenance" if self.plant_stopped else "blocked" if self.stock >= self.max_stock_capacity - 1e-8 else "running"),
                                 "feed_grade": f(100 * self.feed_fe / self.feed if self.feed else 44, "% Fe"),
                                 "concentrate_grade": f(55, "% Fe"), "recovery": f(83.5, "%"),
                                 "tails_grade": f(100 * self.tails_fe / self.tails if self.tails else 0, "% Fe")},
            "Stockpile_01": {"volume": f(a["scan_volume"], "m3"), "weight": f(a["physical_stock"], "t"),
                             "book_weight": f(a["book_stock"], "t"), "density": f(2.7, "t/m3"),
                             "moisture": f(self.actual_moisture_pct, "%"), "height": f(a["height"], "m"), "fraud": f(int(self.fraud)),
                             "wet_stock": f(a["wet_stock"], "t"), "dry_stock": f(a["dry_stock"], "t"),
                             "wet_density": f(2.7 * (1 + self.actual_moisture_pct / 100), "t/m3"),
                             "reference_volume": f(a["reference_volume"], "m3"),
                             "lidar_dry_stock": f(a["lidar_dry_stock"], "t"),
                             "lidar_uncertainty_t": f(a["lidar_uncertainty_t"], "t"),
                             "weight_scale_reading": f(a['weight_scale_reading'], 't'),
                             "lidar_volume_reading": f(a['lidar_volume_reading'], 'm3'),
                             "scale_timestamp": f(a['scale_timestamp'], 's'), "lidar_timestamp": f(a['lidar_timestamp'], 's'),
                             "max_stock_capacity": f(self.max_stock_capacity, 't')},
            "Mass_Balance": {key: f(value, "t" if key.endswith("residual") or key in ("physical_stock", "ground_truth_mass", "measured_stock", "weight_scale_reading", "wet_scale_reading", "moisture_erp_offset", "sensor_disagreement", "stock_capacity", "buffer_capacity", "book_stock", "fraud_offset", "erp_offset", "inventory", "wet_stock", "dry_stock", "lidar_dry_stock", "lidar_uncertainty_t") else "m3" if key in ("scan_volume", "reference_volume", "lidar_volume_reading") else "s" if key.endswith('_timestamp') else "%" if key.endswith('_pct') else "t/h" if key == "drift_rate_tph" else None)
                             for key, value in a.items()},
            "Rail_Siding": {"loaded": f(self.rail_loaded, "t"), "shipped": f(self.shipped, "t"), "departures": f(self.departures), "state": f(self.rail_state)},
            "Pit": {"mined": f(self.mined, "t")},
            "Weighbridge_01": {
                "weight_scale_reading": f(a['weight_scale_reading'], 't'),
                "wet_reading": f(a['wet_scale_reading'], 't'), "moisture_at_sample": f(a['scale_moisture_pct'], '%'),
                "sample_timestamp": f(a['scale_timestamp'], 's'),
                "delay": f(5, 's'), "uncertainty": f(.06, 't'),
                "calibration_factor": f(1.02 if self.scenario_sensor_bias else 1.),
                "status": f('FAULT' if self.scenario_sensor_bias else 'OK')},
            "LiDAR_01": {
                "volume": f(a['lidar_volume_reading'], 'm3'), "dry_mass": f(a['lidar_dry_stock'], 't'),
                "sample_timestamp": f(a['lidar_timestamp'], 's'), "delay": f(10, 's'),
                "scan_period": f(10, 's'), "uncertainty_pct": f(self.lidar_noise_pct, '%'),
                "density_calibration": f(2.7, 't/m3'), "calibration_factor": f(1.), "status": f('OK')},
            "ERP_Register": {
                "book_stock": f(a['book_stock'], 't'), "registered_moisture_pct": f(self.moisture_pct, '%'),
                "nominal_output_rate": f(400.8 if self.scenario_plant_erp else 0., 't/h'),
                "sample_timestamp": f(self.time, 's'), "delay": f(0, 's'),
                "calibration_factor": f(1.), "status": f('STALE_MOISTURE' if abs(a['scale_moisture_pct']-self.moisture_pct)>.1 else 'UNCONFIRMED_OUTPUT' if self.scenario_plant_erp else 'OK')},
            "Moisture_Sensor_01": {
                "moisture_pct": f(self.actual_moisture_pct, '%'), "sample_timestamp": f(self.time, 's'),
                "moisture_at_scale_sample": f(a['scale_moisture_pct'], '%'), "scale_sample_timestamp": f(a['scale_timestamp'], 's'),
                "delay": f(0, 's'), "calibration_factor": f(1.), "status": f('OK')},
        }
        for t in self.trucks:
            p = min(1, t.elapsed / 360)
            position = coordinates(PIT, PLANT, p if t.state == "hauling" else 1 - p if t.state == "returning" else 1 if t.state == "unloading" else 0)
            result[t.name] = {"lat": f(position[0]), "lon": f(position[1]), "weight": f(t.weight, "t"),
                              "speed": f(30 if not t.maintenance and t.state in ("hauling", "returning") else 0, "km/h"),
                              "state": f("MAINTENANCE" if t.maintenance else t.state),
                              "maintenance": f(int(t.maintenance)), "operating_state": f(t.state),
                              "blocked": f(int(t.blocked)),
                              "elapsed": f(t.elapsed, "s"),
                              "geofence": f("INSIDE" if in_corridor(*position, PIT, PLANT) else "OUTSIDE"),
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
        # Commission legacy unlimited checkpoints without deleting a single tonne.
        data.setdefault('max_stock_capacity', max(6000., data.get('stock', 3000.)))
        data.setdefault('max_buffer_capacity', max(360., data.get('buffer', 0.)))
        data["trucks"] = [Truck(**t) for t in data["trucks"]]
        return cls(**data)
