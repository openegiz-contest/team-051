"""Synthetic instruments and reconciliation. No access to Model/ground-truth mass.

Sensors receive physical transfer pulses and ray/surface samples. Accounting is
time-aligned separately. All state, including RNG and latency queues, is persisted.
"""
import math


def gaussian(state, key):
    uniforms = []
    for _ in range(2):
        state[key] = (6364136223846793005 * state[key] + 1442695040888963407) & ((1 << 64) - 1)
        uniforms.append(((state[key] >> 11) + .5) / (1 << 53))
    return math.sqrt(-2 * math.log(uniforms[0])) * math.cos(2 * math.pi * uniforms[1])


# Integrate a raster of downward rays over an elliptic footprint, rather than
# turning mass directly into a measured volume. Unit-cone calibration removes
# raster discretization bias; a real device would need its own surveyed target.
RAYS = [(x / 12, z / 12) for x in range(-12, 13) for z in range(-12, 13)
        if x * x + z * z < 144]
UNIT_SCAN = sum(1 - math.hypot(x, z) for x, z in RAYS)


def scan_surface(height, noise_fraction, head_errors=(0., 0., 0.), bound=.005):
    returns = []
    for x, z in RAYS:
        # Three heads observe different sectors. Apply range/gain errors to
        # individual surface returns before integrating, not to a known volume.
        head = 0 if x < 0 else 1 if z < 0 else 2
        error = max(-bound, min(bound, noise_fraction + head_errors[head]))
        returns.append(max(0., height * (1 - math.hypot(x, z)) * (1 + error)))
    calibration = (math.pi * 25 * 15 / 3) / UNIT_SCAN
    return sum(returns) * calibration


def new_sensors(opening, now=0.):
    return dict(integral=opening, pulses=[], scans=[], scale_rng=918273,
                lidar_rng=2026, head_rng=671233, coincidence_rng=71031, next_scan=now,
                scale_reading=opening, scale_time=now, lidar_volume=opening / 2.7,
                lidar_time=now, noise_fraction=0., coincidence_events=0)


def advance_sensors(s, now, height, transfer, noise_pct, scale_bias_pct=0., false_alarm_probability=1e-6, moisture_pct=3.):
    # Pulses originate at actual feed/discharge scales, never the ERP ledger.
    s['pulses'].append([now + 5, now, transfer, moisture_pct])
    while s['pulses'] and s['pulses'][0][0] <= now:
        packet = s['pulses'].pop(0)
        _, sampled_at, pulse = packet[:3]
        s['integral'] += pulse
        s['scale_time'] = sampled_at
        s['scale_moisture'] = packet[3] if len(packet) > 3 else moisture_pct
    if now >= s['next_scan']:
        bound = noise_pct / 100
        noise = max(-bound, min(bound, gaussian(s, 'lidar_rng') * bound / 3))
        s.setdefault('head_rng', 671233)
        head_errors = tuple(gaussian(s, 'head_rng') * bound / 6 for _ in range(3))
        volume = scan_surface(height, noise, head_errors, bound)
        # Diagnostic calibration error; not used by reconcile().
        baseline = scan_surface(height, 0.)
        effective_noise = volume / baseline - 1 if baseline else 0.
        scale_error = max(-.06, min(.06, gaussian(s, 'scale_rng') * .02))
        # Rare coincident outliers explicitly model imperfect calibration.
        # Probability is per scan, not per HTTP read. Never force an alarm flag.
        gaussian(s, 'coincidence_rng')
        uniform = (s['coincidence_rng'] >> 11) / (1 << 53)
        coincident_error = 3. if uniform < false_alarm_probability else 0.
        if coincident_error:
            s['coincidence_events'] += 1
        s['scans'].append([now + 10, now, volume + coincident_error / 2.7, effective_noise])
        s['scale_error'] = scale_error + coincident_error
        s['next_scan'] = now + 10
    s['scale_reading'] = s['integral'] * (1 + scale_bias_pct / 100) + s.get('scale_error', 0.)
    s['wet_scale_reading'] = s['scale_reading'] * (1 + s.get('scale_moisture', moisture_pct) / 100)
    while s['scans'] and s['scans'][0][0] <= now:
        _, s['lidar_time'], s['lidar_volume'], s['noise_fraction'] = s['scans'].pop(0)


def reconcile(scale, lidar, scale_book, lidar_book, noise_pct, plant_stopped=False, erp_moisture_pct=None):
    """Decision uses only instrument packets and the independent ERP register."""
    measured = (scale['wet_value'] / (1 + scale['moisture_pct'] / 100)
                if 'wet_value' in scale and 'moisture_pct' in scale else scale['value'])
    lidar_mass = lidar['value'] * 2.7
    uncertainty = abs(lidar_mass) * (noise_pct / 100) / max(.99, 1 - noise_pct / 100)
    scale_error = scale_book - measured
    lidar_error = lidar_book - lidar_mass
    validated = math.copysign(max(0., abs(lidar_error) - uncertainty), lidar_error)
    # Scale accuracy is 0.06 t, plus the existing >1 t material threshold.
    residual = math.copysign(max(0., abs(scale_error) - .06), scale_error)
    critical = abs(residual) > 1 or abs(validated) > 1
    # Subtract the ledger's change between timestamps to compare like with like.
    disagreement = (measured - scale_book) - (lidar_mass - lidar_book)
    channel_fault = abs(disagreement) > uncertainty + 1.06
    stale_moisture = erp_moisture_pct is not None and abs(scale.get('moisture_pct', erp_moisture_pct) - erp_moisture_pct) > .1
    reason = ('SENSOR_MISMATCH' if channel_fault else
              'MOISTURE_ERP_MISMATCH' if critical and stale_moisture else
              'ERP_DURING_DOWNTIME' if critical and plant_stopped else
              'ACCOUNTING_MISMATCH' if critical else 'CONSISTENT')
    recommendation = ('Проверить измерительный канал' if channel_fault else
                      'Требуется пересчет влажности / ошибка ERP' if reason == 'MOISTURE_ERP_MISMATCH' else
                      'Остановить ложное начисление ERP и сверить выпуск' if reason == 'ERP_DURING_DOWNTIME' else
                      'Сверить документы и независимые измерения' if critical else 'Измерения и учет согласованы')
    return dict(measured_stock=measured, weight_scale_reading=measured,
                lidar_volume_reading=lidar['value'], lidar_dry_stock=lidar_mass,
                scale_timestamp=scale['time'], lidar_timestamp=lidar['time'],
                audit_residual=residual, lidar_residual=-lidar_error,
                lidar_uncertainty_t=uncertainty, lidar_validated_residual=validated,
                sensor_disagreement=disagreement, audit_reason=reason,
                recommendation=recommendation, anomaly=int(critical))
