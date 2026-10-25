export const number = (value, digits = 1) => Number(value || 0).toLocaleString('ru-RU', {maximumFractionDigits: digits});
export const value = (data, thing, field, fallback = 0) => data?.twins?.[thing]?.[field]?.properties?.value ?? fallback;
export const stateNames = {
  loading: 'Погрузка', hauling: 'К фабрике', unloading: 'Разгрузка', returning: 'Возврат',
  queued: 'Ожидание погрузки', outbound: 'В пути к потребителю', idle: 'Ожидание',
  maintenance: 'Техническое обслуживание', MAINTENANCE: 'Техническое обслуживание'
};
export const stateName = state => stateNames[state] || state || 'Нет данных';
export const displayNames = {
  Stockpile_01: 'Склад концентрата', Processing_Plant: 'Обогатительная фабрика',
  Excavator_01: 'Экскаватор № 1', Truck_01: 'Самосвал № 1', Truck_02: 'Самосвал № 2',
  Rail_Siding: 'Железнодорожная отгрузка',
  ...Object.fromEntries(Array.from({length: 5}, (_, i) => [`Wagon_0${i + 1}`, `Вагон № ${i + 1}`]))
};
export const isFresh = data => !!data?.mqtt_connected && Number(data.telemetry_age) + (data.clientReceivedAt ? Math.max(0,(Date.now()-data.clientReceivedAt)/1000) : 0) < 15;
/** Same 0.04 lerp at 60 FPS; equal elapsed time produces equal damping. */
export const interpolationAlpha = dt => 1 - Math.pow(.96, Math.max(0, Math.min(dt, .1)) * 60);
export const fraudDirection = data => Math.sign(data?.audit?.fraud_offset || data?.fraud_direction || 1);
export const signedNumber = amount => (amount > 0 ? '+' : '') + number(amount);
export const virtualTime = seconds => {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  return `${Math.floor(seconds / 3600)} ч ${Math.floor(seconds % 3600 / 60)} мин`;
};
export const stockHeight = mass => Math.max(0, mass) / 2.7 * 3 / (Math.PI * 25 * 15);
/** Schematic warehouse: one common scale preserves physical/book volume ratio.
 * Normalize to current inventory so long-running accelerated demos fit the shed.
 * Real tonnes, m³ and the 2.7 density remain unchanged in telemetry and audit.
 */
export function stockDisplayScales(physical,book) {
  const reference=Math.max(3000,physical);
  return [physical,book].map(mass=>{
    const scale=Math.cbrt(Math.max(0,mass)/reference);
    return [19*scale,8*scale,11*scale];
  });
}
export const truckProgress = (lat, lon) => Math.max(0, Math.min(1, ((lat - 52.10) * .02 + (lon - 63.10) * .03) / (.02 ** 2 + .03 ** 2)));
export const railProgress = lat => Math.max(0, Math.min(1, (lat - 52.12) / (65 / 111.32)));
