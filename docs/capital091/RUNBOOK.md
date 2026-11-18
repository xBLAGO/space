# Migration / recovery runbook — synthetic only

База exact #35 `e707a19b0b98324ffd914a28b08601237cd6aea2`. Не применять к действующей базе. Остановить всех writers синтетической копии. Сохранить отдельные копии SQLite/WAL и original privacy controls. Independent custodian должен работать и иметь актуальный frontier вне rollback domain.

## Миграция

1. Проверить текущий source manifest и исправленную SQLite через существующий safety check.
2. Проверить original journal/witness/custodian и отсутствие pending intents/holds, не обходя guards.
3. Выполнить `capital_schema.migrate(path)` на явно выбранной синтетической копии.
4. Миграция проверяет predecessor через integration preflight. В одной транзакции создаёт новые capital tables и расширяет CHECK source_kind существующего uel_events посредством transactional copy. Все старые строки и ссылки остаются побайтно неизменными. Старый uel_local_002 ledger/checksum сохраняется; capital_local_001 отдельно фиксирует расширение.
5. Повтор migration возвращает unchanged. Проверить integrity_check, foreign_key_check и сохранность исходных источников.
6. XBLAGO_CAPITAL_LAB_ENABLED по умолчанию OFF. Включать только в guarded synthetic/local-provider стенде с установленной схемой. Создание схемы при старте отсутствует.

## Команды воспроизведения

```sh
PYTHONPATH=.:src python -m unittest tests.test_capital -v
PYTHONPATH=.:src python -m tools.capital_lab --output /tmp/capital091-lab.json
PYTHONPATH=.:src python -m tools.freeze_integration
PYTHONPATH=.:src python -m tools.uel03_lab_check --full --repeat 2 --output /tmp/capital091-full.json
PYTHONPATH=src python docs/ledger01/generate_vectors.py --check
PYTHONPATH=src python docs/ledger01/check_vectors.py --output /tmp/capital091-vectors.json
```

Для частного audit bundle отдельно сохранить policy hash, count и head через независимый канал. Запуск `python -m tools.capital_verify BUNDLE --policy-hash HASH --events COUNT --head HEAD` не требует серверного секрета. Не публиковать bundle с чужими сведениями.

## Сбои

До external journal — SQLite rollback, повтор допустим после новых проверок. После journal до SQLite commit — original replay устанавливает hold и удаляет непроверенный положительный source, UEL сохраняет историю и retirement. После commit до ACK — повтор тех же подписанных байтов возвращает исходный COMMITTED receipt.

Старый backup восстанавливать только штатным privacy_maintenance.restore с original controls. Устаревшие sessions отзываются. Положительное состояние капитала не воссоздаётся из journal hashes. При несовпадении frontier — фонд закрыт, saldo UNKNOWN. При утрате control/checkpoint доказательств — UNKNOWN_RECOVERY, не отключать guards ради входа.

Повреждённый производный индекс чинить существующим uel_recovery.repair_index после forensic copy и остановки writers. Он понимает установленное расширение, не восстанавливает частные capital payload и не выдаёт права.

## Rollback кода

Выключить новый флаг. После установки новой схемы старый runtime #35 структурно несовместим с расширенным source_kind и должен отказывать. Нельзя откатывать SQLite/control evidence для его запуска. Сохранить совместимый код чтения/удаления/recovery либо восстановить отдельную тестовую среду с доказанным актуальным состоянием. Down-migration и удаление истории для обхода checks не предусмотрены.
