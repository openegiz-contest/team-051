/** Stable control contract shared by the standalone view and Grafana iframes. */
export const API_BASE = '/stepnoe/api';
export function createAPI(fetcher = (...args) => fetch(...args)) {
  return async function apiJSON(endpoint, options = {}) {
    const url = `${API_BASE}/${endpoint}`;
    const response = await fetcher(url, {
      cache: 'no-store', credentials: 'same-origin', mode: 'cors', redirect: 'manual', ...options,
      headers: {Accept: 'application/json', ...options.headers}
    });
    if (response.status === 401 || response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400)) {
      throw new Error('Войдите в Grafana и повторите действие: требуется действующая сессия.');
    }
    const type = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
    if (type !== 'application/json' && !type.endsWith('+json')) {
      throw new Error(`API ${url}: ожидался JSON (HTTP ${response.status}). Проверьте прокси Stepnoe.`);
    }
    let data;
    try {data = await response.json();}
    catch {throw new Error(`API ${url}: некорректный JSON (HTTP ${response.status}).`);}
    if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
    return data;
  };
}
export const apiJSON = createAPI();
export const setPaperFraud = value => apiJSON('fraud', {
  method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({value})
});
