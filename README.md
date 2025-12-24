# RetailCheck

RetailCheck — Telegram-бот для ведения сменных чек-листов в магазинах. Сервис назначает роли opener/closer, валидирует ввод по шагам (числа, фото, комментарии), синхронизирует данные с Google Sheets и отправляет менеджерам итоговые сводки, алерты и напоминания.

## Что входит в MVP
- single-run на магазин и дату с ролями opener/closer и статусами `opened → in_progress → ready_to_close → closed/returned`;
- шаблоны «Открытие»/«Закрытие» + промежуточные проверки (`phase=open|check_1100|...|close|finance`);
- обязательное Z-фото, контроль дельт (требование комментария при |Δ| ≥ `DELTA_THRESHOLD_RUB`), алерты менеджерам;
- напоминания по слотам (до назначения роли — всем whitelisted сотрудникам магазина, после — конкретному исполнителю);
- экспорт смен в лист `Export`, Audit-лог для расследований, Redis-локи на `run:{shop_id}:{date}`.

## Основные компоненты
- `src/retailcheck` — Aiogram-бот, сервисы для Runs/RunSteps/Attachments/Audit/Export, интеграции с Google Sheets и Redis.
- `tools/` — консольные утилиты (seed, импорт шаблонов, напоминания, алерты, QR, метрики, монитор локов).
- `templates/` — JSON-шаблоны для импорта шагов «Открытие»/«Закрытие».
- `docs/` — продуктовые материалы: PRD, OnePage, BotFlows, QA-план, гайды для сотрудников и др.
- `ops/` — готовые файлы для QR и unit `ops/systemd/retailcheck.service`.
- `docker-compose.yml` / `Dockerfile` — локальный запуск бота + Redis.

## Быстрый старт для разработки
1. Установите Python 3.11, Redis 7+, Poetry 1.8+.
2. Скопируйте `.env.example` → `.env` и заполните ключевые значения:
   - `TELEGRAM_BOT_TOKEN` — токен бота;
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — путь до JSON сервисного аккаунта с доступом «Редактор» к таблице;
   - `GOOGLE_SHEETS_ID` — ID файла (листов `Users/Shops/Templates/...`);
   - `REDIS_URL`, `MANAGER_NOTIFY_CHAT_IDS`, overrides шаблонов (`DEFAULT_TEMPLATE_*`), настройки дельт/напоминаний.
3. Установите зависимости: `make install`.
4. Проверьте качество: `make lint`, затем `make test` (pytest). Перед сдачей кода прогон `make lint` обязателен, а изменения Python/бот-логики требуют `make test`.
5. Поднимите Redis (`redis-server` или `docker-compose up redis`) и запустите бота:
   - локально: `make run` (выполнит `poetry run python -m retailcheck`);
   - через Docker Compose: `docker-compose up --build`.

## Работа с данными
- `make seed-sheets` заполнит Google Sheet демо-данными (использует `GOOGLE_SHEETS_ID`/`GOOGLE_SERVICE_ACCOUNT_JSON`).
- `make import-templates` загрузит шаблоны из `templates/opening_v1.json` и `templates/closing_v1.json`.
- Напоминания работают циклично до завершения обязательных шагов: `tools/reminder_scheduler.py` запускает `pending_steps` каждые 5 минут (A: 15/30/45→10 после 18:00, B: 15/25/30→10 после 20:00, закрытие: 10/20/30). Разовая отправка — `tools/send_reminders.py --mode pending_steps`.
- Скрипты контроля:
  - `tools/delta_alerts.py` — проверка превышения `DELTA_THRESHOLD_RUB` (учитывает `DELTA_ALERT_COOLDOWN_SEC`).
  - `tools/lock_monitor.py` — текущее состояние Redis-локов (`lock:run:*`).
  - `tools/metrics_report.py` — распределение статусов, средняя дельта, доля закрытых смен.
  - `tools/generate_qr.py --bot-username <name>` — PNG с deep-link `?start=shop_<id>__open|__close` (файлы в `ops/qr`).

## Make-команды
- `make install` — Poetry install (`--no-root`).
- `make run` — запуск `python -m retailcheck`.
- `make lint` / `make format` — Ruff (чек/фикс + формат).
- `make typecheck` — mypy.
- `make test` — pytest.
- `make reminders`, `make reminders-scheduler` — разовые/фоновый запуск напоминаний.
- `make seed-sheets`, `make import-templates` — операции с Google Sheets/шаблонами.

## Документация
- `docs/OnePage.md` — краткий питч проекта (что, зачем, сроки).
- `docs/PRD.md`, `docs/Tasks.md`, `docs/BotFlows.md` — требования, сценарии диалогов и список задач.
- `docs/EmployeeGuide.md` — пошаговая инструкция для сотрудников магазина.
- `docs/Architecture.md`, `docs/DataModel.md` — описание компонентов, листов Google Sheets и API.
- `docs/QAPlan.md`, `docs/PilotReport.md`, `docs/DeliveryPlan.md` — тесты, метрики пилота, план поставки.
- `docs/README.md` — краткое резюме + список полезных утилит (используется как readme пакета).

## Деплой
- Docker: `docker-compose up --build` поднимает Redis + бот (порт `BOT_PORT`, по умолчанию 8080).
- Systemd: см. `ops/systemd/retailcheck.service` — запускает `poetry run python -m retailcheck`, читая `.env`, и зависит от `redis.service`.
- В проде бот живёт за HTTPS-прокси на `https://bot.offonika.ru/checklist/webhook`, хранит данные только в Google Sheets и Telegram (`telegram_file_id`), использует Redis для локов и напоминаний.

## Дополнительно
- Новые внешние зависимости (Python/system) добавляйте только после согласования с заказчиком.
- Замеченные рассинхроны между кодом и документацией сначала фиксируйте в `docs/` и только после этого меняйте реализацию.
