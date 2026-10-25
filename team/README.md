# Код команды: ГОК «Степное»

Основные команды, ограничения и формулы — в [README.md](../README.md).
Запуск всего стенда из корня: `make up`; приемка: `make team-smoke`.

`generate_assets.py` воспроизводит `twins.json`, три JSON-дашборда и
`web/manifest.json` с контрольными суммами интерфейса. После изменения
`web/` выполните `python3 team/generate_assets.py` из корня репозитория.
`setup_twins.py` создает отсутствующие двойники, обновляет их атрибуты,
сохраняет существующие features. `model.py` не зависит от MQTT или Docker,
`service.py` связывает модель с Paho, HTTP и SQLite.

Интерфейс не требует npm-сборки: `web/scene.js` управляет Three.js-сценой,
`models.js` собирает технику, `layout.js` задает производственный узел и режимы,
`motion.js` обновляет цели движения и сглаживает технику,
`haul-road.js` задает замкнутую 3D-кривую и профиль съезда/рельефа,
`routes.js` связывает фазы телеметрии с непрерывной петлей,
`charts.js` рисует графики и анимированную двухполосную карту,
`api.js` и `controls.js` сохраняют единый контракт управления для сайта
и встроенных панелей Grafana. Three.js и OrbitControls включены в `web/vendor/`.

Необязательный тест реального MQTT-транспорта вне Docker:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r team/requirements.txt amqtt==0.11.3
python team/tests/mqtt_integration.py
```

Этот тест поднимает временный брокер и сервис, проверяет 14 сообщений,
команды MQTT `1/1/-1/-1/0`, HTTP-управление сокрытием через брокер и восстановление после
перезапуска. Он не подменяет проверки Ditto, Telegraf, InfluxDB и Grafana.
