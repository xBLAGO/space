# Данные и явная миграция

Новая версия: `living_deeds_001`, модуль `core/living_schema.py`.

| Таблица | Назначение | Приватность / retirement |
|---|---|---|
| living_records | Проверенная текущая проекция NOTE, NEED_PERMISSION, BRIDGE, RESOURCE, ATTACHMENT, RESERVATION | owner FK; выбранные аудитории хранятся только privately |
| living_versions | Неизменяемые версии приватного события; snapshot и causation | UPDATE запрещён; private payload удаляется при retirement |
| living_commands | Sealed confirmation preview / receipt и idempotency key | TTL preview; после commit контент очищается |
| living_slots | Asset ID, Help/offer IDs и интервал | Не источник нового Help; часть атомарного резерва |
| living_holds | Retained HMAC denial и причина | Старый backup не снимает hold |
| living_contact_holds | HMAC запрета повторного обращения | Не публикуется и не становится рейтингом |
| living_help_holds | HMAC запрета подтверждения Help при erasure ресурса | Нельзя обойти ранее выданные ресурсные ограничения |
| living_migrations | Версия и checksum нового расширения | Не заменяет прежние migration ledgers |

В существующем `uel_events` добавляется source kind LIVING. Формат headers и
исходные записи сохраняются byte-for-byte; таблица перестраивается в одной
offline транзакции для расширения CHECK. Новая миграция связывает новый DDL
своим checksum. `uel_migrations` сохраняет старые VERSION/CHECKSUM. Старые DDL
Help, Asset, Identity, privacy, lifecycle, context не изменяются.

Поддержка расширения добавлена в validate/preflight/recovery старого UEL; это
совместимый новый loader, не подмена старого checksum. Старый runtime не должен
открывать расширенную SQLite: использовать отдельную копию до нового допуска.
При ошибке миграции SQLite откатывает перестройку, не оставляя частичные таблицы.

Предшественники: Integration → Help → context fencing → lifecycle → Asset → UEL
→ enrollment текущей signed checkpoint policy. Нужны исходные journal/witness и
live pinned checkpoint custodian. Без любого доказательства миграция закрывается.
Никакого DDL при импорте или запуске приложения и никакого legacy import нет.

Файлы `domain/help_service.py` и `domain/asset_registry.py` прежде были заморожены
целиком. Требование атомарного ресурса требует узких hooks: guard при принятии,
transaction callback и наблюдение канонического результата; erasure hook Asset.
`tools/living_contract_check.py` удаляет только эти точные вставки и проверяет
byte-identical SHA256 с исходными файлами exact #35, дополнительно сверяя pin
через Git. Прежняя CI-проверка byte-identical domain files заменена этим явным
extension proof; проверки frozen migration files и 72 golden vectors сохранены.
Произвольные изменения исходного lifecycle этим checker не разрешаются.

Семантика типов задана в [EVENT_PROFILE.json](EVENT_PROFILE.json). NeedExpressed,
FulfillmentAuthorized и прочие Help-типы — read-only aliases существующих
канонических Help event IDs, не новые записи второго lifecycle. Наблюдения
RESERVATION содержат `canonical_source_ref` на тот же HELP event. Подтверждение
контакта имеет отдельный тип ContactConsentGranted; оно не называется ни
ResourceOffered, ни FulfillmentConfirmed. Разрешённый текст приглашения имеет тип
ReferralScopeAuthorized и не создаёт второй NEED.

Отдельные retained controls `living_help_deny` и `living_recovery_hold` сохраняют
ограничения после erasure occupancy. Нельзя удалить slot, восстановить старый
backup и затем подтвердить бывший resource-bound Help как обычный unbound Help.
Неизвестные living control types отклоняются.

Sibling PR #36/#37 расширяют CHECK UEL типом CAPITAL и **не входят** в выбранную
базу. Эта миграция на CAPITAL-базе не сертифицирована и должна отказать. Для
совместного включения понадобится отдельная согласованная migration/loader
проверка обеих неизменных историй; автоматическое слияние этих SQL-расширений
не разрешается. Это не утверждение о совместимости всех открытых веток.
