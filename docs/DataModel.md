# Google Sheets Data Model

Документ фиксирует структуру листов Google Sheets, которые выступают хранилищем данных для MVP чек-лист-бота. Формат — табличная спецификация с типами, ограничениями и правилами чтения/записи.

## Общие принципы
- Каждая таблица (лист) начинается со строки заголовков, далее — данные. Ведём UTC timestamp в ISO 8601.
- Для ссылочных полей используем технические ID (строковые UUID либо числовые ID, согласуем до старта).
- Пакетные операции выполняем через `batchUpdate` с проверкой `idempotency_key`, чтобы избежать дублей.
- Архивные записи помечаются флагом `is_active = FALSE`, физически строки не удаляются.

## Users

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| user_id | string | ✔ | Уникальный идентификатор пользователя (UUID). |
| tg_id | int | ✔ | Telegram ID. Используется для whitelist. |
| username | string | | Telegram username для удобства. |
| fio | string | ✔ | ФИО / отображаемое имя. |
| role | enum(employee, manager, controller) | ✔ | Роль в боте. |
| shops | string[] | ✔ | Список shop_id через запятую (какие магазины доступны). |
| is_active | bool | ✔ | Флаг активности. |
| created_at | datetime | ✔ | ISO 8601. |
| updated_at | datetime | | Последняя правка. |

## Sheet: `Shops`
- `shop_id, name, timezone, open_time, close_time, manager_usernames, employee_usernames, reminder_slots, allow_anyone (TRUE|FALSE), dual_cash_mode (TRUE|FALSE), is_active`
  - `timezone` — идентификатор из базы IANA (`Europe/Moscow`). Используется для расчёта напоминаний и конвертации дат.
  - `open_time` / `close_time` — плановые часы магазина в формате HH:MM (локальная таймзона).
  - `reminder_slots` — опциональный JSON (например, `{"dual_checks":["12:00","13:00","13:30","15:00","16:00","16:30","17:30"]}`) для магазинов с двумя кассами. Если пусто, используем дефолтные напоминания `open_time−15` и `close_time−30`.
  - `allow_anyone=FALSE` по умолчанию. Значение `TRUE` используем только для тестовых магазинов; рабочие магазины требуют явного перечисления `telegram_username` в manager/employee списках.
  - `dual_cash_mode` — включает режим двух касс (opener и closer ведут параллельные шаги). По умолчанию `FALSE`.
  - Кросс-замены фиксируем через пересекающиеся `employee_usernames`: например, `@Zuuuuhra99` и `@o_lisaaaa` добавлены в оба магазина, чтобы подменять коллег; аналогично `@a1III11`/`@VviVkk` могут выходить в магазине 1.
  - Обновление whitelists/менеджеров выполняется напрямую в листе `Shops` (бот перечитывает список перед назначением ролей).

## Templates

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| template_id | string | ✔ | Идентификатор шаблона (e.g. `evening_shift_v1`). |
| name | string | ✔ | Название («Открытие», «Закрытие»). |
| version | int | ✔ | Версия шаблона. Рост версии = миграция. |
| phase | enum(open, check_1100, check_1600, check_1900, close, finance) | ✔ | Какой фазе смены соответствует шаблон/блок. |
| is_active | bool | ✔ | Флаг доступности. |
| description | string | | Комментарий / заметки по шаблону. |

## TemplateSteps

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| template_id | string | ✔ | FK → Templates. |
| step_order | int | ✔ | Порядок отображения (1..N). |
| code | string | ✔ | Уникальный код шага (`cash_16`, `pos_sber`). |
| title | string | ✔ | Текст шага для пользователя. |
| type | enum(number, text, check, photo) | ✔ | Тип ввода. |
| required | bool | ✔ | Обязательность шага. |
| validators_json | string | | JSON с правилами (`min`, `max`, `regex`, `delta_threshold`). |
| norm_rule | string | | Определение нормы (константа, ссылочный шаг, ручной ввод). |
| hint | string | | Дополнительная подсказка. |
| owner_role | enum(opener, closer, shared) | | Какая роль должна видеть шаг. Пустое значение трактуем как `shared`. |

Пилотный шаблон состоит из четырёх фаз и повторяющихся дневных сверок:
- `open`: `open_checkin`, `open_pos_check`, `open_cash_start`, `open_note`.
- `check_1100`, `check_1600`, `check_1900`: `chkXXXX_start`, `chkXXXX_sum`, `chkXXXX_receipts`, `chkXXXX_non_cash`, `chkXXXX_comment`.
- `close`: `close_start`, `close_done_1c`, `close_form_receipt`, `close_z_sum`, `close_cash_end`, `close_cash_move`, `close_comment`.
- `finance`: `fin_receipts_photo`, `fin_report_dc1c`, `fin_z_photo`, `fin_sberbank_sum`, `fin_tbank_sum`, `fin_comment`.

Для магазинов с `dual_cash_mode = TRUE` эти шаги дублируются/разделяются через `owner_role`: opener заполняет свой набор (касса А), closer — свой (касса B). Общие шаги (например, комментарий менеджеру) отмечаются как `shared`.

## Sheet: `Runs`
- `run_id` (uuid)
- `date` (YYYY-MM-DD, в таймзоне магазина)
- `shop_id` (string)
- `status` (`opened | in_progress | ready_to_close | closed | returned`)
- `opener_user_id` (int64), `opener_username` (string, optional), `opener_at` (ISO datetime)
- `closer_user_id` (int64), `closer_username` (string, optional), `closer_at` (ISO datetime)
- `current_active_user_id` (int64) — кто сейчас ведёт смену; блокирует закрытие и повторные действия.
- `template_open_id` (string), `template_close_id` (string)
- `template_phase_map` (json) — сопоставление `phase → template_id` (например, `{"open":"open_v1","check_1100":"check_morning_v1","check_1600":"check_midday_v1","check_1900":"check_evening_v1","close":"close_v1","finance":"finance_v1"}`)
- `delta_rub` (number), `comment` (string)
- `version` (int), `created_at`, `finished_at` (ISO)

**Инварианты:**
- Уникальность `(shop_id, date)` для статусов `opened|in_progress|ready_to_close|closed`.
- Нельзя ставить `closed` без прикреплённого Z-фото в `RunSteps`.
- `current_active_user_id` очищается при `closed`/`returned`, обновляется при «Открыть/Продолжить/Передача роли».
- Поля `template_open_id` и `template_close_id` дублируют значения из `template_phase_map.open` и `.close` (оставлены для обратной совместимости, но должны синхронизироваться при записи).

## Sheet: `RunSteps`
- `run_id, phase(open|check_1100|check_1600|check_1900|close|finance), step_code, owner_role(opener|closer|shared), value_*, delta_number, comment, status, updated_at, idempotency_key, performer_user_id`

`owner_role` дублирует значение из TemplateSteps и помогает боту/напоминаниям понимать, кому показывать шаг. `performer_user_id` (опционально) фиксирует, кто фактически обновил шаг — важно для сценария двух касс.

## Attachments

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| run_id | string | ✔ | FK → Runs. |
| step_code | string | ✔ | Какому шагу принадлежит фото. |
| telegram_file_id | string | ✔ | ID файла в Telegram. |
| kind | enum(z_report, pos_receipt, other) | ✔ | Тип вложения. |
| created_at | datetime | ✔ | Момент загрузки. |

## Sheet: `Audit`
- `ts, user_id, action(start_open|start_close|handover_open|handover_close|finish_close|step_update), entity(run|run_step), entity_id, details`

## Export

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| export_id | string | ✔ | UUID операции экспорта. |
| generated_at | datetime | ✔ | Время генерации (UTC). |
| period_start | date | ✔ | Начало диапазона. |
| period_end | date | ✔ | Конец диапазона. |
| shop_id | string | ✔ | Магазин (или `ALL`). |
| shop_name | string | ✔ | Официальное название магазина (для бухгалтерии). |
| run_id | string | ✔ | Смена, к которой относится строка. |
| run_date | date | ✔ | Дата смены (в таймзоне магазина). |
| status | enum(closed, returned) | ✔ | Статус смены. |
| opener_user_id | string | ✔ | user_id или tg_id opener. |
| opener_username | string | | username opener. |
| opener_at | datetime | | Время назначения/старта opener. |
| closer_user_id | string | | user_id или tg_id closer. |
| closer_username | string | | username closer. |
| closer_at | datetime | | Время назначения/закрытия closer. |
| totals_json | json | ✔ | Набор агрегатов (касса 11/16/19, терминалы). Для магазинов с двумя кассами добавляем вложенные блоки `opener`/`closer`. |
| cash_total | decimal(15,2) | | Сумма наличных (дублируем из totals). |
| noncash_total | decimal(15,2) | | Сумма безнал/эквайринг. |
| delta_total | decimal(15,2) | ✔ | Суммарная дельта. |
| delta_comment | string | | Комментарий по дельте (если |Δ| ≥ threshold). |
| comment | string | | Общий комментарий по смене (Runs.comment). |
| attachments_summary | string | | Ссылки/ID фото (Z-отчёт, чеки). При dual-mode указываем, к какой роли относится фото. |
| audit_link | string | | Ссылка/ID строки Audit для быстрой навигации. |

## Связи и индексы (логические)
- Runs → RunSteps, Attachments (1:N). Гарантируем по run_id.
- TemplateSteps привязываются к Template через template_id, шаги не удаляем, а помечаем `required = FALSE`.
- Для напоминаний создаём индекс (фильтр) `status = in_progress` + `date = today` + `shop_id`.
- Для Export настраиваем фильтры по периодам, чтобы менеджер быстро строил отчёты без скриптов.

## Правила валидации и бизнес-логика
1. **Числовые шаги:** допускаются только положительные числа до 200 000 ₽ (порог настраиваем в validators_json). При вводе заменяем запятую на точку, округляем до 2 знаков.
2. **Комментарии:** если `abs(delta_number) ≥ delta_threshold` (по умолчанию 300 ₽), поле `comment` обязательно.
3. **Фото:** шаги типа `photo` требуют хотя бы одного `telegram_file_id`. Допускаем несколько файлов за шаг.
4. **Пропуск шага:** возможен, только если `required = FALSE`. В этом случае `status = pending`, но смена может быть закрыта.
5. **Версии шаблона:** `Runs.version` фиксирует, с какой версией шаблона шёл пользователь. При обновлении TemplateSteps увеличиваем `Templates.version` и создаём новый шаблон.

## Подготовка Таблицы
1. Создать Google Sheet с отдельными листами, как описано выше, и заполнить первые строки заголовков.
2. Выдать сервисному аккаунту доступ уровня «Редактор».
3. Добавить примерные записи (1 магазин, 1 шаблон, 3–4 шага) для интеграционных тестов.
4. В README указать ID таблицы и диапазоны, которые использует бот (например, `Users!A:H`).
