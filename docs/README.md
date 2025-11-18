# Документация RetailCheck

## Как это работает (коротко)
- На магазин/дату создаётся **одна смена**.
- Есть две роли: **opener** и **closer** — их может выполнять один человек или разные.
- Стартовать удобно через **QR-deep-link** на кассе.
- Менеджер может **передать** любую роль.

## Дев-окружение
1. Установите Poetry 1.8+.
2. Выполните `make install` (установит зависимости) и `make test` / `make lint`.
3. Чтобы заполнить Google Sheet демо-данными, настройте `GOOGLE_SHEETS_ID` и `GOOGLE_SERVICE_ACCOUNT_JSON`, затем `make seed-sheets`.
4. Для загрузки актуальных шаблонов «Открытие»/«Закрытие» используйте `poetry run python tools/import_templates.py templates/opening_v1.json templates/closing_v1.json`.
5. Напоминания: используем `poetry run python tools/reminder_scheduler.py` — он сам вычисляет два дефолтных слота (открытие −15 мин, закрытие −30 мин) на основе расписания магазина. Для ручной проверки можно вызвать `tools/send_reminders.py --mode open|close`.
6. Дельта-алерты: `poetry run python tools/delta_alerts.py` проверяет превышение `DELTA_THRESHOLD_RUB`; планировщик также запускает проверку каждые `DELTA_ALERT_INTERVAL_MIN`.
7. Мониторинг локов: `poetry run python tools/lock_monitor.py` выводит ключи `lock:run:*` и их TTL, помогает убедиться, что нет «зависших» ролей.
8. Метрики: `poetry run python tools/metrics_report.py` показывает распределение статусов, среднюю дельту и коэффициент закрытия.
9. QR: `poetry run python tools/generate_qr.py --bot-username <name>` создаёт PNG-файлы в `ops/qr`.

## Контейнеры и деплой
- `docker-compose up --build` поднимет Redis и бот (использует `.env`).
- Для production можно использовать unit `ops/systemd/retailcheck.service` (требует Poetry и Redis на сервере).

## Полезные материалы
- `docs/EmployeeGuide.md` — инструкция для сотрудников (opener/closer) с шагами запуска смены, фото и итогов.
- `docs/TrainingPlan.md` — сценарий тренинга + ссылки на материалы (PDF, видео).
- `docs/QAPlan.md` — чек-лист тестирования (unit, интеграция, пилот).
- `docs/PilotReport.md` — отчёт о пилоте (метрики и выводы).
