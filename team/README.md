# Код команды: ГОК «Степное»

Основные команды, ограничения и формулы — в [README.md](../README.md).
Запуск всего стенда из корня: `make up`; приемка: `make team-smoke`.

`generate_assets.py` воспроизводит `twins.json` и три JSON-дашборда.
`setup_twins.py` создает отсутствующие двойники, обновляет их атрибуты,
сохраняет существующие features. `model.py` не зависит от MQTT или Docker,
`service.py` связывает модель с Paho, HTTP и SQLite.

Необязательный тест реального MQTT-транспорта вне Docker:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r team/requirements.txt amqtt==0.11.3
python team/tests/mqtt_integration.py
```

Этот тест поднимает временный брокер и сервис, проверяет 14 сообщений,
команды MQTT `1/1/0`, HTTP-управление через брокер и восстановление после
перезапуска. Он не подменяет проверки Ditto, Telegraf, InfluxDB и Grafana.
