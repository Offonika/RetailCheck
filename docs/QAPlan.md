# QA & Test Plan

Документ фиксирует базовый чек-лист тестирования RetailCheck MVP (unit, интеграция, ручные сценарии).

## 1. Unit-тесты
- `pytest` покрывает:
  - Google Sheets клиент (моки, ретраи).
  - FSM шага (`tests/bot/test_status_helpers.py`, валидации чисел/дельт).
  - RunService (lock + назначение ролей).

Запуск: `poetry run pytest`.

## 2. Интеграционные проверки
1. `make install && make test` на чистом окружении.
2. `poetry run python tools/import_templates.py templates/opening_v1.json templates/closing_v1.json` — загрузка шаблонов.
3. `poetry run python tools/seed_sheets.py` — заполнение демо-данных.
4. `/start shop_1__open` → проход шагов `open`, загрузка фото, проверка Audit.
5. `/start shop_1__close` → закрытие смены, `/summary shop_1`.
6. Шаги `terminal_choice`/`photo_terminal`: выбрать терминал (Т-Банк/Сбербанк/третий), затем загрузить одно фото сверки выбранного терминала; без выбора/фото шаг не закрывается (проверить для opener и closer отдельно).

## 3. Регрессионные сценарии
- `/handover shop_1 open @username`.
- `/return_run shop_1 2025-01-10 исправить кассу`.
- `/export shop_1 2025-01-10`.
- Проверка листа `Export`: строки содержат полный набор колонок (`shop_name`, `run_date`, opener/closer поля, totals_json, attachments_summary). Сравнить значения с Runs/RunSteps на ту же дату.
- `tools/generate_qr.py` + проверка ссылок.
- `tools/reminder_scheduler.py` в тестовом режиме (проверить слоты open −15 мин / close −30 мин) и `tools/delta_alerts.py`.
- `tools/metrics_report.py` — обзор метрик.

## 4. Приёмка пилота
- Два магазина → по одной смене в день.
- Проверить:
  - Время реакции на напоминания ≤ 30 мин.
  - Отсутствие зависших локов (`tools/lock_monitor.py`).
  - Экспорт совпадает с Runs/RunSteps.
- Оформить результаты в `docs/PilotReport.md`.

## 5. Dual cash mode (shop_1)
1. **FSM.** Проверить, что во `/start` появляется кнопка «↔ Продолжить смену» и closer видит только свои шаги (`owner_role = closer`).
2. **Summary.** `/summary shop_1` недоступна, пока обе роли не закрыли обязательные шаги; итог выводит блоки «Касса opener/closer».
3. **Reminders.** `tools/reminder_scheduler.py` создаёт слоты `12:00/13:00/13:30/15:00/16:00/16:30/17:30`; напоминания уходят соответствующей роли и в общий чат.
4. **Delta alerts.** При превышении порога сообщение указывает, какая касса дала основную дельту.
5. **Export.** `totals_json` содержит вложенные блоки `opener`/`closer`, а `attachments_summary` помечает роль (`closer:fin_z_photo=...`).
