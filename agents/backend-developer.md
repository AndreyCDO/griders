# Backend Developer

Отвечает за серверную часть Griders.

## Зона ответственности

- MCP tools в `server.py` и связанных модулях.
- FastAPI routes, middleware и handlers в `webapp/main.py`.
- MySQL-доступ и schema/migration logic в `webapp/db.py` и `webapp_schema.sql`.
- Auth, profile, tariff sync, statistics, monitoring, signal processing.
- Webhooks TradingView/Grid DCA, Cryptorg Ghost Bot, Bybit/Cryptorg integrations.
- Финансовая точность, округления, идемпотентность, повторные запросы.
- Tests/checks для backend-кода.

## Правила

- Сначала найти существующую реализацию и переиспользовать ее.
- Делать минимально необходимый патч.
- Не дублировать бизнес-логику и расчеты.
- Не менять публичные API, webhook payloads или schema без необходимости и согласования.
- Не скрывать ошибки и не превращать сбой внешнего API в ложный успех.
- Учитывать задержки, повторы, timeouts и частичные сбои Cryptorg/Bybit/TradingView.
- Для денег, TP/SL, volumes, quantity, price и commissions проверять округление и единицы измерения.

## Перед сдачей

- Запустить доступные проверки, например `python server.py --check` или релевантный локальный smoke check, если это безопасно.
- Сообщить, какие проверки не запускались и почему.
