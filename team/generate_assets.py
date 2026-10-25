"""Rebuild reviewed Ditto JSON and Grafana dashboards, using only stdlib."""
import json
import hashlib
from pathlib import Path
from model import Model, NS, MAINTAINABLE, MAINTENANCE_TOPIC, SCENARIOS, SCENARIO_TOPIC

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
               "Rail_Siding": "Stepnoe", "Mass_Balance": "Stepnoe", 'Weighbridge_01': 'Processing_Plant',
               'LiDAR_01': 'Stockpile_01', 'ERP_Register': 'Processing_Plant', 'Moisture_Sensor_01': 'Stockpile_01',
               **{f"Wagon_{i:02}": "Rail_Siding" for i in range(1, 6)}}
    kinds = {"Stepnoe": "mine", "Pit": "pit", "Processing_Plant": "plant", "Stockpile_01": "stockpile",
             "Rail_Siding": "rail-siding", "Mass_Balance": "audit", "Excavator_01": "excavator",
             'Weighbridge_01': 'weighbridge', 'LiDAR_01': 'lidar-scanner', 'ERP_Register': 'accounting-register',
             'Moisture_Sensor_01': 'moisture-sensor'}
    data = {}
    for name, features in Model().telemetry().items():
        attributes = {"name": name, "_isType": False, "kind": kinds.get(name, "truck" if name.startswith("Truck") else "wagon"),
                      "data_source": "synthetic deterministic simulation", "fictional": True}
        feeds = {'Pit': ['Excavator_01'], 'Excavator_01': ['Truck_01', 'Truck_02'],
                 'Truck_01': ['Processing_Plant'], 'Truck_02': ['Processing_Plant'],
                 'Processing_Plant': ['Stockpile_01'], 'Stockpile_01': ['Rail_Siding'],
                 'Rail_Siding': [f'Wagon_{i:02}' for i in range(1, 6)]}
        attributes['feeds_to'] = [f'{NS}:{target}' for target in feeds.get(name, [])]
        attributes['feature_contracts'] = {key: {
            'type': 'array' if isinstance(p['properties']['value'], list) else 'number' if isinstance(p['properties']['value'], (int, float)) else 'string',
            'unit': p['properties'].get('unit', '1'),
            'mass_basis': 'wet' if key.startswith('wet_') else 'dry' if p['properties'].get('unit') in ('t', 't/h') else 'not_applicable',
            'role': 'simulation_reference_only' if key in ('physical_stock', 'ground_truth_mass', 'reference_volume', 'physical_residual', 'fe_residual', 'stock_residual', 'inventory', 'wet_stock', 'dry_stock') or (name == 'Stockpile_01' and key in ('weight', 'height')) else 'telemetry',
        } for key, p in features.items()}
        attributes['time_contract'] = {'unit': 's', 'basis': 'virtual_seconds_since_model_start',
                                      'timestamps': ['simulation_time'] + [key for key in features if key.endswith('_timestamp')]}
        if name in parents:
            # OpenEgiz 1.0.4 consumes _parents as a string, not parents[]!
            attributes.update({"_parents": f"{NS}:{parents[name]}", "parents": [f"{NS}:{parents[name]}"]})
        if name == "Stockpile_01":
            attributes.update({"density_t_m3": 2.7, "geometry": "elliptic cone", "radius_x_m": 25, "radius_z_m": 15, "sensor": "synthetic LiDAR"})
            attributes.update({"mass_basis": "dry", "moisture_basis": "water/dry solid", "lidar_noise_bound_pct": .5,
                               "scan_volume_source": "delayed synthetic surface ray integration with bounded Gaussian noise",
                               "physical_stock_source": "simulation ground truth; excluded from reconciliation",
                               'max_stock_capacity_t': Model().max_stock_capacity,
                               'measured_by': [{'thingId': f'{NS}:{sensor}', 'feature': feature}
                                               for sensor, feature in (('Weighbridge_01', 'weight_scale_reading'),
                                                                       ('LiDAR_01', 'volume'), ('Moisture_Sensor_01', 'moisture_pct'))]})
        if name in ('Weighbridge_01', 'LiDAR_01', 'ERP_Register', 'Moisture_Sensor_01'):
            attributes.update({'independent_instrument': True, 'measurement_source': 'synthetic instrument model',
                               'calibration_semantics': 'gain multiplier; density separately calibrated for LiDAR',
                               'mass_basis': 'dry_except_wet_reading', 'moisture_basis': 'water/dry solid'})
            if name == 'Weighbridge_01':
                attributes['observes'] = [f'{NS}:Truck_01', f'{NS}:Truck_02', f'{NS}:Stockpile_01']
                attributes['measurement_semantics'] = 'stock-equivalent accumulator from physical weighbridge transfer pulses'
            if name == 'ERP_Register':
                attributes['observes'] = [f'{NS}:Processing_Plant', f'{NS}:Stockpile_01']
        if name.startswith('Truck_0'):
            attributes['measured_by'] = [f'{NS}:Weighbridge_01']
        if name in MAINTAINABLE:
            attributes["maintenance_control_topic"] = MAINTENANCE_TOPIC + name
        if name == "Processing_Plant":
            attributes.update({"raw_inflow_semantics": "cumulative dry tonnes", "instantaneous_flow_feature": "raw_inflow_rate",
                               'max_buffer_capacity_t': Model().max_buffer_capacity,
                               'interlocks': ['stock_full_stops_processing', 'buffer_full_blocks_truck_unloading']})
        if name == 'Mass_Balance':
            attributes['audit_inputs'] = [f'{NS}:{sensor}' for sensor in ('Weighbridge_01', 'LiDAR_01', 'ERP_Register', 'Moisture_Sensor_01')]
            attributes['ground_truth_used_for_decisions'] = False
            attributes['scenario_control_topics'] = {name: SCENARIO_TOPIC + name for name in SCENARIOS}
        if name.startswith("Truck"):
            attributes["capacity_t"] = 90
        if name.startswith("Wagon"):
            attributes["capacity_t"] = 70
        data[f"{NS}:{name}"] = {"policyId": "default:basic_policy", "attributes": attributes, "features": features}
    # Create parents before children regardless of telemetry iteration order.
    order = ["Stepnoe", "Pit", "Excavator_01", "Truck_01", "Truck_02", "Processing_Plant", "Stockpile_01",
             'Weighbridge_01', 'LiDAR_01', 'ERP_Register', 'Moisture_Sensor_01', "Mass_Balance", "Rail_Siding"] + [f"Wagon_{i:02}" for i in range(1, 6)]
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
    labels.update({'ground_truth_mass': 'Эталон симуляции (отладка)', 'lidar_dry_stock': 'Измерено LiDAR',
                   'measured_stock': 'Накопительные весы', 'audit_residual': 'Сверка ERP / весы',
                   'sensor_disagreement': 'Разность измерительных каналов'})
    colors = {"book_stock": "#ed9aa5", "book_residual": "#ed9aa5", "tails": "#dfbc83", "raw_inflow": "#8faee0", "fe_residual": "#8faee0"}
    for i, field in enumerate(fields):
        p["fieldConfig"]["overrides"].append({"matcher": {"id": "byFrameRefID", "options": chr(65 + i)}, "properties": [
            {"id": "displayName", "value": labels.get(field, field)}, {"id": "color", "value": {"mode": "fixed", "fixedColor": colors.get(field, ["#69cbb6", "#8faee0", "#dfbc83", "#bfa8dc"][i % 4])}}]})
        if field == 'ground_truth_mass':
            p['fieldConfig']['overrides'][-1]['properties'].append({'id': 'custom.hideFrom', 'value': {'viz': True, 'legend': False, 'tooltip': False}})
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
        iframe("Сценарии и аудит · Simulate Paper Fraud", "audit", 0, 46),
        panel("LiDAR, учет ERP и отладочный эталон", "Mass_Balance", ["lidar_dry_stock", "book_stock", "ground_truth_mass"], 0, 46, 12, 9),
        panel("Сверка измерений и документов", "Mass_Balance", ["audit_residual", "sensor_disagreement"], 12, 46, 12, 9),
        panel("Фабрика: руда = концентрат + хвосты", "Processing_Plant", ["raw_inflow", "concentrate", "tails"], 0, 55, 12, 8),
        panel("Содержание Fe и извлечение", "Processing_Plant", ["feed_grade", "concentrate_grade", "recovery", "tails_grade"], 12, 55, 12, 8, unit="percent")])
    dashboard("stepnoe-logistics", "Logistics & Geofencing", [
        iframe("Маршруты техники и геозоны", "map", 0, 32),
        panel("Самосвал № 1 · загрузка", "Truck_01", ["weight"], 0, 32, 12, 8),
        panel("Самосвал № 2 · загрузка", "Truck_02", ["weight"], 12, 32, 12, 8),
        panel("ЖД: погрузка и подтвержденная отгрузка", "Rail_Siding", ["loaded", "shipped"], 0, 40, 24, 8)])


if __name__ == "__main__":
    twins()
    web_assets()
    dashboards()
