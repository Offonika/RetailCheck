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

## Shops

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| shop_id | string | ✔ | Уникальный код магазина. |
| name | string | ✔ | Отображаемое название. |
| timezone | string | | Olson TZ (по умолчанию Europe/Moscow). |
| reminder_slots | string | | Пользовательское расписание (например, `11:00,16:00,19:00`). |
| is_active | bool | ✔ | Магазин участвует в расписании. |
| created_at | datetime | ✔ | Время создания. |

## Templates

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| template_id | string | ✔ | Идентификатор шаблона (e.g. `evening_shift_v1`). |
| name | string | ✔ | Название («Вечерняя смена»). |
| version | int | ✔ | Версия шаблона. Рост версии = миграция. |
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

## Runs

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| run_id | string | ✔ | UUID смены. |
| date | date | ✔ | Дата смены (YYYY-MM-DD). |
| shop_id | string | ✔ | FK → Shops. |
| template_id | string | ✔ | Какой шаблон использован. |
| started_by | string | ✔ | user_id инициатора. |
| status | enum(in_progress, done, returned) | ✔ | Статус жизненного цикла. |
| version | int | ✔ | Служит для оптимистичной блокировки. |
| created_at | datetime | ✔ | Время создания. |
| finished_at | datetime | | Заполняется при статусе `done`. |
| returned_at | datetime | | Когда менеджер вернул смену. |
| reminder_state | json | | Служебный объект (на какие слоты отправлены напоминания). |

## RunSteps

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| run_id | string | ✔ | FK → Runs. |
| step_code | string | ✔ | FK → TemplateSteps.code. |
| value_number | decimal(15,2) | | Значение для числовых шагов (в рублях). |
| value_text | string | | Текстовые ответы. |
| value_check | bool | | Галочки. |
| delta_number | decimal(15,2) | | Разница факт−норма (0 для текстовых). |
| comment | string | | Обязателен при |delta| ≥ порога. |
| status | enum(pending, ok, error) | ✔ | Состояние шага. |
| attachments | string[] | | Список telegram_file_id (для фото шагов). |
| updated_at | datetime | ✔ | Последняя правка. |
| idempotency_key | string | ✔ | UUID батча записи. |

## Attachments

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| run_id | string | ✔ | FK → Runs. |
| step_code | string | ✔ | Какому шагу принадлежит фото. |
| telegram_file_id | string | ✔ | ID файла в Telegram. |
| kind | enum(z_report, pos_receipt, other) | ✔ | Тип вложения. |
| created_at | datetime | ✔ | Момент загрузки. |

## Audit

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| ts | datetime | ✔ | Время события. |
| user_id | string | | Кто инициировал (бот, сотрудник, менеджер). |
| action | string | ✔ | Ключевое событие (`run_created`, `step_updated`, `returned`). |
| entity | enum(run, run_step, export) | ✔ | Тип сущности. |
| entity_id | string | ✔ | ID сущности. |
| details | json | | Полезная нагрузка (дельта, комментарий, стар/нов значения). |

## Export

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| export_id | string | ✔ | UUID операции экспорта. |
| period_start | date | ✔ | Начало диапазона. |
| period_end | date | ✔ | Конец диапазона. |
| shop_id | string | ✔ | Магазин (или `ALL`). |
| run_id | string | ✔ | Смена, к которой относится строка. |
| status | enum(done, returned) | ✔ | Статус смены. |
| totals_json | json | ✔ | Набор агрегатов (касса 11/16/19, терминалы). |
| delta_total | decimal(15,2) | ✔ | Суммарная дельта. |
| attachments_summary | string | | Ссылки/ID фото. |
| generated_at | datetime | ✔ | Время генерации. |

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
