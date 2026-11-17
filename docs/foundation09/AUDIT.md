# Foundation 09 — архитектурный аудит

Проверенная база PR #32 имеет HEAD `06b3889afa906f1ac3af8eca6130f0e6fce6cec9`. Main при проверке остаётся `3c218dc5757879c26b3aec520af3636b2d702715`. [dependency-snapshot.json](dependency-snapshot.json) фиксирует открытые и закрытые PR с base/head SHA. Наличие документа или Draft PR само по себе не означает включения в main.

| Компонент | Фактические файлы | Статус и повторное использование |
|---|---|---|
| Foundation 02, legacy экономика | integrations/web.py, core/storage.py | SQLite users, needs/resources/assets, payment requests, fulfillment/contribution records. Объекты разных поколений имеют разные ID. Согласованного физического mapping нет. Автоимпорт запрещён |
| Identity 05 | integrations/identity.py, core/identity_schema.py, identity_runtime_schema.py | Включены в integration candidate, fresh sessions, active status, WebAuthn и Telegram linking. Используются существующие blago_user_id |
| Identity 07 | PR12 b79cb603, core/sovereign.py и sovereign_schema.py в этой ветке | Отдельный эксперимент с human-signed proofs. В базе #32 отсутствует, Integration preflight исключает его schema. Не подключать как побочный эффект экономики |
| Recovery 07.2 | PR13 d96ade93 | Отдельная ветка с консервативными holds, отсутствующие ключи не реконструируются. Независимая инфраструктура требует доказательства |
| Conversation 06.1 | agents/personal_assistant.py, core/llm.py, semantic_memory.py | Включён из source d11a2a55. Память и диалог используют общую идентичность. Новый калькулятор не меняет prompt и не добавляет LLM-запросы |
| Privacy 05.4/01.2–01.4 | privacy_control.py, data_lifecycle.py, retention_ops.py, context_schema.py | Существующие holds, deletion/export, journal/witness и G09. Применяются без изменения |
| Help 01 | domain/help_service.py и core/help_schema.py | Единственный используемый жизненный цикл нефинансовой помощи. Публикация и изменения требуют existing preview/confirm |
| Asset Registry 09.1 | domain/asset_registry.py, core/asset_registry_schema.py | Канонический blago_assets + связанные версии. Доступный RESOURCE — часть того же объекта. Публикация подтверждена владельцем, достоверность остаётся SELF_DECLARED |
| Calculator 01 | domain/collective_calculator.py | ValueEngine с Decimal, offline rates и неизменной исторической оценкой пригоден для reuse. LabRegistry memory-only с собственными fixture principals и лимитом 32 не расширяется в новую регистрацию |
| UEL 0.1 | docs/ledger01 | 72 frozen offline-примера. Wire conformance не означает полномочий или достоверности |
| UEL 0.2/0.3 | uel_schema.py, uel_local.py, uel_checkpoint.py, uel_recovery.py, domain/local_ledger.py | Производный локальный индекс поддерживает только ASSET и HELP. HMAC journal + witness + optional explicit custodian. Нужна отдельная версия контракта для капитала |
| Federation 08 | PR16 0a6acd91 и PR17 44a5d52a | experiments/federation_08_2/runtime.py — отдельная файловая лаборатория со своими trust anchors и receipt schemas. В app не импортируется, independent operator S12 не подтверждён |
| Web/PWA/Bot/Mini | integrations/web.py, telegram.py, static/index.html, sw.js | Общая identity/history. Legacy financial API закрыты nonfinancial integration scope. Новых UI или bot-команд Foundation 09 пока нет |
| HF | README.md, deployment workflow, публичные Hub metadata RomanMaloev/space | Docker Space в metadata обновлён 16 октября. Фактический production-runtime и его данные не испытывались. Deployment остаётся ручным, обновление не выполнялось |

## Противоречия и ограничения

1. В проекте уже есть историческое имя Foundation 09/09.1 для Calculator/Asset. Эта итерация называется Collective Balance 01 и сохраняет предыдущие артефакты.
2. Legacy needs/resources и управляемые Asset/Help имеют неоднозначное соответствие. Они показываются как разные источники с неполным охватом. Пересчёт legacy вкладов в новый достоверный баланс не реализован.
3. UEL source_kind ограничен ASSET/HELP. Записывать денежные операции под видом вещи или Help означало бы нарушить контракт. Долговечный капитал отложен до отдельной миграции.
4. Help не хранит измеренное количество потребности, срочность, цену и зависимости. Эти поля в стенде являются входными предположениями. Реальные результаты Help показываются отдельно.
5. Семантический профиль и сумма имущества не используются как полномочия или рейтинг достоинства. Исторические economic/UI представления требуют отдельного review перед включением. Код не переписывается только ради терминологической согласованности.
6. Сессия подтверждает доступ в данной системе. Переносимый human mandate, независимая проверка имущества и согласованное управление фондом остаются отдельными требованиями.
7. G04, реальные устройства, качество живого HF и независимость операторов остаются открытыми. Успех этой лаборатории не меняет их статусы.

Точные точки входа и линии кода собраны в code-map.json. Старые DDL, checksums, источники Help/Asset, API и deployment workflow сравниваются с exact base.
