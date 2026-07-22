# Agent Workflows

Этот файл задает типовые маршруты совместной работы ролей. Orchestrator может сокращать маршрут, если задача простая, но должен сохранять независимые проверки там, где они обязательны.

## Изменение торговой стратегии

Маршрут:

`Trading Specialist -> Strategy Skeptic -> Data Analyst -> Backend Developer или Pine Script work -> Reviewer`

Примеры задач:

- "Проверь новую версию стратегии Grid DCA".
- "Измени TP/SL для GRID DCA 2.9".
- "Сравни Pine Script сигнал с серверной обработкой webhook".

Обязательные проверки:

- assumptions backtest: period, pairs, timeframe, entry rule, commission, slippage, funding, cooldowns;
- отсутствие look-ahead/data leakage;
- количество сделок и зависимость от выбросов;
- расхождения TradingView, Python backtest и production webhook;
- запрет формулировок о доказанной прибыльности без достаточных данных.

## Новая функция сайта

Маршрут:

`Product Manager / Orchestrator -> Architect -> Backend Developer и/или Frontend Developer -> Reviewer -> Technical Support Specialist -> Documentation Specialist -> Data Analyst`

Примеры задач:

- "Добавь экран истории подключений".
- "Сделай настройку лимита активных сделок в личном кабинете".
- "Добавь админский фильтр по тарифам".

Правила:

- сначала найти существующие routes/templates/db tables;
- не менять backend-contract ради UI без согласования;
- если добавляется метрика, Data Analyst определяет событие и KPI;
- Support и Documentation подключаются, если пользовательский сценарий меняется.

## Новый тариф

Маршрут:

`Product Manager / Orchestrator -> Data Analyst -> Marketing Specialist -> Strategy Skeptic -> Architect -> Backend Developer -> Frontend Developer -> Reviewer -> Technical Support Specialist -> SEO Specialist`

Примеры задач:

- "Добавь новый Plus-тариф".
- "Измени лимиты Start".
- "Проверь, стоит ли делать тариф для большего числа ботов".

Правила:

- не менять тарифные правила без явного задания;
- любые цифры спроса, конверсии и выручки должны иметь источник;
- торговые обещания запрещены;
- изменения БД требуют миграции;
- поддержка должна получить понятное объяснение отличий тарифа.

## Новая публичная страница

Маршрут:

`Marketing Specialist -> SEO Specialist -> Frontend Developer -> Backend Developer, если требуется -> Reviewer -> Data Analyst`

Примеры задач:

- "Добавь новую страницу тарифа".
- "Подготовь SEO-план для griders.ru".
- "Сделай страницу про GRID DCA без обещания доходности".

Правила:

- Marketing отвечает за оффер и понятность;
- SEO отвечает за intent, metadata, structure, sitemap/robots/schema needs;
- Frontend реализует в существующем стиле;
- Backend подключается только при новых данных, routes или forms;
- Data Analyst определяет, как измерять эффективность страницы.

## Техническая ошибка пользователя

Маршрут:

`Technical Support Specialist -> Backend Developer, если причина неясна -> DevOps Specialist, если проблема серверная -> Reviewer, если нужен фикс -> Documentation Specialist, если проблема повторяется`

Примеры задач:

- "Разберись, почему пользователь не получил страховочные ордера".
- "Пользователь говорит, что TP не появился".
- "Почему сигнал TradingView не открыл сделку".

Правила:

- сначала отделить техническую ошибку от нормального торгового результата;
- не обвинять пользователя;
- проверять тариф, депозит, лимиты, настройки, API/webhook, filters, exchange constraints;
- не запрашивать лишние данные;
- повторяющиеся причины переносить в FAQ/документацию.

## Архитектурное изменение

Маршрут:

`Architect -> соответствующие разработчики -> Reviewer -> DevOps Specialist -> Documentation Specialist`

Примеры задач:

- "Проведи архитектурный аудит интеграции с новой биржей".
- "Раздели обработку webhook и отправку команд Cryptorg".
- "Подготовь миграцию таблиц аналитики".

Правила:

- Architect сначала описывает затронутые компоненты и риски;
- реализация идет через профильных разработчиков;
- DevOps нужен, если меняются env, deploy, service model или migrations;
- Documentation обновляет фактические инструкции после реализации.

## Быстрые задачи

Если задача локальная и безопасная, Orchestrator может использовать один режим:

- typo в README: Documentation Specialist;
- CSS-правка без contract changes: Frontend Developer + Reviewer при риске;
- read-only market analysis: Trading Specialist, при claims о прибыльности добавить Strategy Skeptic;
- простой backend bug с очевидной причиной: Backend Developer + Reviewer.

Даже для быстрых задач сохраняются общие правила `AGENTS.md`: изучить код, не менять лишнее, запускать доступные проверки и честно сообщать ограничения.
