"""Rebuild reviewed Ditto JSON and Grafana dashboards, using only stdlib."""
import json
from pathlib import Path
from model import Model, NS

ROOT = Path(__file__).resolve().parent
DS = {"type": "influxdb", "uid": "opentwins-influxdb"}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def twins():
    parents = {"Pit": "Stepnoe", "Excavator_01": "Pit", "Truck_01": "Pit", "Truck_02": "Pit",
               "Processing_Plant": "Stepnoe", "Stockpile_01": "Processing_Plant",
               "Rail_Siding": "Stepnoe", "Mass_Balance": "Stepnoe", **{f"Wagon_{i:02}": "Rail_Siding" for i in range(1, 6)}}
    kinds = {"Stepnoe": "mine", "Pit": "pit", "Processing_Plant": "plant", "Stockpile_01": "stockpile",
             "Rail_Siding": "rail-siding", "Mass_Balance": "audit", "Excavator_01": "excavator"}
    data = {}
    for name, features in Model().telemetry().items():
        attributes = {"name": name, "_isType": False, "kind": kinds.get(name, "truck" if name.startswith("Truck") else "wagon"),
                      "data_source": "synthetic deterministic simulation", "fictional": True}
        if name in parents:
            # OpenEgiz 1.0.4 consumes _parents as a string, not parents[]!
            attributes.update({"_parents": f"{NS}:{parents[name]}", "parents": [f"{NS}:{parents[name]}"]})
        if name == "Stockpile_01":
            attributes.update({"density_t_m3": 2.7, "geometry": "elliptic cone", "radius_x_m": 25, "radius_z_m": 15, "sensor": "synthetic LiDAR"})
        if name.startswith("Truck"):
            attributes["capacity_t"] = 90
        if name.startswith("Wagon"):
            attributes["capacity_t"] = 70
        data[f"{NS}:{name}"] = {"policyId": "default:basic_policy", "attributes": attributes, "features": features}
    # Create parents before children regardless of telemetry iteration order.
    order = ["Stepnoe", "Pit", "Excavator_01", "Truck_01", "Truck_02", "Processing_Plant", "Stockpile_01", "Mass_Balance", "Rail_Siding"] + [f"Wagon_{i:02}" for i in range(1, 6)]
    write(ROOT / "twins.json", {f"{NS}:{name}": data[f"{NS}:{name}"] for name in order})


def query(thing, field, last=False):
    return (f'from(bucket: "default")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
            f'  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.thingId == "{NS}:{thing}" and r._field == "value_{field}_properties_value")\n'
            '  |> group(columns: ["thingId", "_field"])\n' + ('  |> last()\n' if last else '') +
            f'  |> keep(columns: ["_time", "_value"])\n  |> yield(name: "{field}")')


def panel(title, thing, fields, x, y, w=12, h=7, kind="timeseries", unit="masst"):
    p = {"title": title, "type": kind, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "fieldConfig": {"defaults": {"unit": unit, "decimals": 2}, "overrides": []},
         "targets": [{"refId": chr(65 + i), "datasource": DS, "query": query(thing, f, kind == "stat")} for i, f in enumerate(fields)]}
    p["options"] = ({"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "background", "graphMode": "none"}
                    if kind == "stat" else {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}})
    return p


def iframe(title, view, y, h):
    # Relative URL is served through Grafana's own authenticated /stepnoe/ proxy.
    return {"title": title, "type": "text", "gridPos": {"x": 0, "y": y, "w": 24, "h": h},
            "options": {"mode": "html", "content": f'<iframe title="{title}" src="/stepnoe/?view={view}" style="width:100%;height:100%;border:0" loading="lazy"></iframe>'}}


def dashboard(uid, title, panels):
    for i, p in enumerate(panels, 1):
        p["id"] = i
    write(ROOT / "dashboards" / (uid + ".json"), {
        "uid": uid, "title": title, "schemaVersion": 39, "version": 1, "editable": True,
        "tags": ["stepnoe", "mass-balance", "synthetic"], "timezone": "browser", "refresh": "5s",
        "time": {"from": "now-30m", "to": "now"}, "panels": panels,
        "links": [{"title": label, "type": "link", "url": f"/d/{target}", "keepTime": True} for label, target in
                  [("Executive overview", "stepnoe-overview"), ("Mass balance audit", "stepnoe-audit"), ("Logistics", "stepnoe-logistics")]],
        "description": "Stepnoe fictional iron-ore mine. Synthetic telemetry via MQTT -> Ditto -> Telegraf -> InfluxDB. Dry-tonne basis; opening inventory 3000 t. 3D scene uses a local Canvas renderer, not a compiled Unity scene."})


def dashboards():
    dashboard("stepnoe-overview", "Executive Overview & 3D Mine View", [
        panel("Physical scanned stock", "Stockpile_01", ["weight"], 0, 0, 6, 4, "stat"),
        panel("Book stock", "Stockpile_01", ["book_weight"], 6, 0, 6, 4, "stat"),
        panel("Rail weighbridge shipped", "Rail_Siding", ["shipped"], 12, 0, 6, 4, "stat"),
        panel("Scan volume", "Stockpile_01", ["volume"], 18, 0, 6, 4, "stat", "m3"),
        iframe("Stepnoe 3D mine / synthetic LiDAR", "mine", 4, 17),
        panel("Physical flows", "Processing_Plant", ["raw_inflow", "concentrate", "tails"], 0, 21, 24)])
    status = panel("Accounting status", "Mass_Balance", ["anomaly"], 0, 0, 8, 4, "stat", "none")
    status["fieldConfig"]["defaults"].update({"color": {"mode": "thresholds"},
        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]},
        "mappings": [{"type": "value", "options": {"0": {"text": "BALANCE OK", "color": "green"}, "1": {"text": "CRITICAL ANOMALY: Mass Balance Mismatch", "color": "red"}}}]})
    dashboard("stepnoe-audit", "Mass Balance & Anti-Fraud Audit", [status,
        panel("Unverified ore", "Mass_Balance", ["fraud_offset"], 8, 0, 8, 4, "stat"),
        panel("Unverified / scanned stock", "Mass_Balance", ["discrepancy_pct"], 16, 0, 8, 4, "stat", "percent"),
        iframe("Anti-Fraud Controls — Simulate Paper Fraud", "audit", 4, 11),
        panel("3D scan vs accounting", "Mass_Balance", ["physical_stock", "book_stock"], 0, 15),
        panel("Physical, book and Fe residuals", "Mass_Balance", ["physical_residual", "book_residual", "fe_residual"], 12, 15),
        panel("Plant: feed = concentrate + tails", "Processing_Plant", ["raw_inflow", "concentrate", "tails"], 0, 22),
        panel("Fe grades and recovery", "Processing_Plant", ["feed_grade", "concentrate_grade", "recovery", "tails_grade"], 12, 22, unit="percent")])
    dashboard("stepnoe-logistics", "Logistics & Geofencing", [
        iframe("Live local map — truck and rail tracks / geofences", "map", 0, 17),
        panel("Truck 01 payload", "Truck_01", ["weight"], 0, 17),
        panel("Truck 02 payload", "Truck_02", ["weight"], 12, 17),
        panel("Rail: loaded vs departed", "Rail_Siding", ["loaded", "shipped"], 0, 24, 24)])


if __name__ == "__main__":
    twins()
    dashboards()
