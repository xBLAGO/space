# Governance migration / recovery runbook

Только изолированная копия exact #36 `c3c6896076c9e24932ebdb0ee14e7c04065b91dd`. Остановить writers, сохранить SQLite/WAL и original journal/witness, обеспечить доступность текущего independently retained custodian. Не применять к production.

1. Проверить `sqlite_safety --require-safe`, source manifest, predecessor schema/checksums и original recovery evidence.
2. На существующей синтетической базе с capital_local_001 явно выполнить governance_schema.migrate(path). Миграция создаёт только govcap_migrations и govcap_holds, транзакционно и идемпотентно. Нет автоматического импорта v1 фондов.
3. Проверить integrity_check/foreign_key_check, прежние UEL headers и checksum ledgers. Ошибка DDL должна оставить старую схему и данные.
4. Оставить XBLAGO_CAPITAL_GOVERNANCE_ENABLED OFF до отдельного запуска лаборатории. Для нового v2 фонда нужны подписи всех начальных участников над точной политикой и действующие fresh sessions существующей identity.
5. В стенде включаются synthetic/local-provider/secure identity/privacy/UEL/capital/governance flags. Setup создаёт новые test keys. Реальные keys и credentials не используются.

```sh
PYTHONPATH=.:src python -m unittest tests.test_governance -v
PYTHONPATH=.:src python -m tools.governance_lab --output /tmp/governance092-lab.json
PYTHONPATH=.:src python -m tools.freeze_integration
PYTHONPATH=.:src python -m tools.uel03_lab_check --full --repeat 2 --output /tmp/governance092-regression.json
PYTHONPATH=src python docs/ledger01/generate_vectors.py --check
PYTHONPATH=src python docs/ledger01/check_vectors.py --output /tmp/governance092-vectors.json
python tools/independent_capital_verifier.py PRIVATE_BUNDLE --pins SEPARATELY_PINNED_CONFIG
```

Проверяющий не должен брать pins из самого недоверенного bundle. Согласовать custodian key, stream, node key, genesis policy hash и текущий checkpoint hash по независимому каналу. Без этого результат является только проверкой внутренней согласованности старого снимка. Bundle не публиковать.

## Exit / deletion

Signed MemberExited удаляет только приватную UID-binding и зависимые полномочия. Для удаления аккаунта используется существующий privacy preview/challenge, без обхода проверок. V2 account purge выпускает отрицательный node certificate и сохраняет проверенную историю остальных. Нельзя использовать legacy erase всего фонда. Privileged node key не является ключом восстановления/выдачи положительных полномочий.

## Crash и rollback

До journal intent — SQL rollback. После journal до commit — source mismatch создаёт permanent frozen hold v2-фонда, сохраняя его записи и остальные bindings. После commit до ACK повтор возвращает прежнюю квитанцию; повтор signed exit после удаления binding безопасно отказывается без нового эффекта.

Восстанавливать backup только в отдельный файл без WAL исходного экземпляра, с original controls. Штатный privacy_maintenance.restore отзывает sessions. Утрата checkpoint или подтверждённой истории => UNKNOWN_RECOVERY. Старые позитивные события не реконструируются из hashes. Отрицательный user-status replay удаляет UID binding даже у frozen фонда. Фонд остаётся закрытым, пока не появится отдельно проверенный механизм восстановления. Самостоятельного снятия holds нет.

Свежий backup после exit сохраняет источник и баланс. Repair повреждённого производного UEL index выполняется прежним offline uel_recovery, без восстановления личных payload и без выдачи новых votes.

## Откат кода

Выключить флаг, сохранить совместимый код обработки schema/privacy/recovery. Runtime #36 не знает govcap schema и должен отказать. Не удалять новые таблицы или control evidence для запуска старого кода. Down-migration и перенос реальных данных не предусмотрены. Непроверенный frozen фонд не переводить в новый active фонд и не считать его капитал свободным.
