"""Rebuild reviewed Ditto JSON and Grafana dashboards, using only stdlib."""
import json
import hashlib
from pathlib import Path
from model import Model, NS, MAINTAINABLE, MAINTENANCE_TOPIC

ROOT = Path(__file__).resolve().parent
DS = {"type": "influxdb", "uid": "opentwins-influxdb"}
WEB_VERSION = ""


def web_assets():
    """The Three.js distribution is vendored; no CDN/build step is required."""
    global WEB_VERSION
    required = ["index.html", "style.css", "app.js", "api.js", "controls.js", "telemetry.js", "charts.js", "models.js", "scene.js", "layout.js", "motion.js", "routes.js", "haul-road.js",
                "vendor/three.module.js", "vendor/three.core.js", "vendor/OrbitControls.js", "vendor/THREE-LICENSE.txt"]
    checksums = {name: hashlib.sha256((ROOT / "web" / name).read_bytes()).hexdigest() for name in required}
    WEB_VERSION = hashlib.sha256(json.dumps(checksums, sort_keys=True).encode()).hexdigest()[:12]
    write(ROOT / "web" / "manifest.json", {"version": WEB_VERSION, "three_version": "0.180.0", "sha256": checksums})


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
            attributes.update({"mass_basis": "dry", "moisture_basis": "water/dry solid", "lidar_noise_bound_pct": .5,
                               "scan_volume_source": "bounded Gaussian raw measurement",
                               "physical_stock_source": "dry inventory from physical weighbridge transfers"})
        if name in MAINTAINABLE:
            attributes["maintenance_control_topic"] = MAINTENANCE_TOPIC + name
        if name == "Processing_Plant":
            attributes.update({"raw_inflow_semantics": "cumulative dry tonnes", "instantaneous_flow_feature": "raw_inflow_rate"})
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
    p = {"title": title, "type": kind, "datasource": DS, "transparent": True,
         "description": "Телеметрия Eclipse Ditto через InfluxDB. Все массы приведены к сухому веществу; начальный запас 3000 т учитывается отдельно.",
         "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "fieldConfig": {"defaults": {"unit": unit, "decimals": 1, "color": {"mode": "fixed", "fixedColor": "#69cbb6"}}, "overrides": []},
         "targets": [{"refId": chr(65 + i), "datasource": DS, "query": query(thing, f, kind == "stat")} for i, f in enumerate(fields)]}
    p["options"] = ({"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value", "graphMode": "area", "wideLayout": True}
                    if kind == "stat" else {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}})
    if kind == "timeseries":
        p["fieldConfig"]["defaults"]["custom"] = {"drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 2,
            "fillOpacity": 7, "gradientMode": "opacity", "showPoints": "never", "spanNulls": False,
            "axisBorderShow": False, "axisGridShow": True, "axisCenteredZero": False, "hideFrom": {"tooltip": False, "viz": False, "legend": False}}
        if "physical_residual" in fields:
            # Floating-point noise must not look like a material discrepancy.
            # Soft limits still expand automatically for the 300 t fraud signal.
            p["fieldConfig"]["defaults"]["custom"].update({"axisSoftMin": -1, "axisSoftMax": 1})
    labels = {"physical_stock": "Физический склад", "book_stock": "Учетный отчет", "physical_residual": "Баланс массы", "book_residual": "Баланс учета",
              "fe_residual": "Баланс Fe", "raw_inflow": "Сырая руда", "concentrate": "Концентрат", "tails": "Хвосты", "weight": "Груз",
              "loaded": "Загружено", "shipped": "Отгружено", "feed_grade": "Fe на входе", "concentrate_grade": "Fe концентрата", "recovery": "Извлечение", "tails_grade": "Fe хвостов"}
    colors = {"book_stock": "#ed9aa5", "book_residual": "#ed9aa5", "tails": "#dfbc83", "raw_inflow": "#8faee0", "fe_residual": "#8faee0"}
    for i, field in enumerate(fields):
        p["fieldConfig"]["overrides"].append({"matcher": {"id": "byFrameRefID", "options": chr(65 + i)}, "properties": [
            {"id": "displayName", "value": labels.get(field, field)}, {"id": "color", "value": {"mode": "fixed", "fixedColor": colors.get(field, ["#69cbb6", "#8faee0", "#dfbc83", "#bfa8dc"][i % 4])}}]})
    return p


def iframe(title, view, y, h):
    # Panels share the UI; its /stepnoe/api/ CORS requests bypass Grafana auth.
    return {"title": title, "type": "text", "transparent": True, "gridPos": {"x": 0, "y": y, "w": 24, "h": h},
            "options": {"mode": "html", "content": f'<iframe title="{title}" src="/stepnoe/index.html?view={view}&amp;v={WEB_VERSION}" style="display:block;width:100%;height:100%;border:0;border-radius:16px;background:#101722" allow="fullscreen" loading="lazy"></iframe>'}}


def dashboard(uid, title, panels):
    for i, p in enumerate(panels, 1):
        p["id"] = i
    write(ROOT / "dashboards" / (uid + ".json"), {
        "uid": uid, "title": title, "schemaVersion": 39, "version": 2, "editable": True,
        "tags": ["stepnoe", "mass-balance", "synthetic"], "timezone": "browser", "refresh": "5s",
        "time": {"from": "now-30m", "to": "now"}, "panels": panels,
        "links": [{"title": label, "type": "link", "url": f"/d/{target}", "keepTime": True} for label, target in
                  [("Executive overview", "stepnoe-overview"), ("Mass balance audit", "stepnoe-audit"), ("Logistics", "stepnoe-logistics")]],
        "description": "ГОК «Степное». Пространственная Three.js-диорама с телеметрией Ditto, независимый баланс массы и Fe. Синтетические датчики, начальный запас 3000 т; физический склад и учетный объем отображаются раздельно."})


def dashboards():
    dashboard("stepnoe-overview", "Executive Overview & 3D Mine View", [
        iframe("Рудник в реальном времени · 3D-диорама", "mine", 0, 28),
        panel("От сырья к концентрату", "Processing_Plant", ["raw_inflow", "concentrate", "tails"], 0, 28, 14, 9),
        panel("Качество обогащения", "Processing_Plant", ["feed_grade", "concentrate_grade", "recovery"], 14, 28, 10, 9, unit="percent")])
    dashboard("stepnoe-audit", "Mass Balance & Anti-Fraud Audit", [
        iframe("Anti-Fraud Controls — Simulate Paper Fraud", "audit", 0, 30),
        panel("3D-скан и учетный остаток", "Mass_Balance", ["physical_stock", "book_stock"], 0, 30, 12, 9),
        panel("Независимые балансы массы и железа", "Mass_Balance", ["physical_residual", "book_residual", "fe_residual"], 12, 30, 12, 9),
        panel("Фабрика: руда = концентрат + хвосты", "Processing_Plant", ["raw_inflow", "concentrate", "tails"], 0, 39, 12, 8),
        panel("Содержание Fe и извлечение", "Processing_Plant", ["feed_grade", "concentrate_grade", "recovery", "tails_grade"], 12, 39, 12, 8, unit="percent")])
    dashboard("stepnoe-logistics", "Logistics & Geofencing", [
        iframe("Маршруты техники и геозоны", "map", 0, 32),
        panel("Самосвал № 1 · загрузка", "Truck_01", ["weight"], 0, 32, 12, 8),
        panel("Самосвал № 2 · загрузка", "Truck_02", ["weight"], 12, 32, 12, 8),
        panel("ЖД: погрузка и подтвержденная отгрузка", "Rail_Siding", ["loaded", "shipped"], 0, 40, 24, 8)])


if __name__ == "__main__":
    twins()
    web_assets()
    dashboards()
