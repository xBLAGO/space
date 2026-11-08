# Privacy 01.3 — synthetic migration, retention and recovery

**Только новые или offline синтетические копии.** Эти команды не разрешают production migration/deployment. Исходник `9abb7cc7b22346be0a9b095e7f13b1dc81928e28`, base #23 `7807f5aa1c2d1818427057c6ce072ffb6166e3d3`. Старые checksums и journal lineage не переинициализировать.

## Подготовка

1. Проверить ancestry, source identity, lock и `tools.freeze_integration` без drift. Не подменять runtime-source документационным HEAD.
2. Создать isolated Python venv, установить `requirements-integration.lock`. Использовать проверенный pinned SQLite, не системную неизвестную библиотеку: Linux build по CI/Docker; macOS `tools/build_macos_sqlite_driver.py` по прежнему runbook. Проверить фактически импортированный Python extension/source-id, не доверять имени image.
3. Не загружать production `.env`, keys, DB, webhook или provider tokens. Не запускать bot polling. Директории journal/key, witness и app должны быть явно назначены; same-volume directories — local simulation, не независимая custody.

```sh
export PYTHONPATH=.:src
python -m xblago.core.sqlite_safety --require-safe
python -m tools.freeze_integration
python -m tools.integration_fixture --new-synthetic-root /tmp/blago-life013-new --help-enabled --context-fencing --lifecycle
python -m tools.lifecycle_inventory --output /tmp/blago-life013-inventory.json
python -m tools.lifecycle_check --output /tmp/blago-life013-check.json
python -m unittest tests.test_data_lifecycle -v
python -m unittest discover -s tests -v
```

На этой macOS финальный повтор использовал `/tmp/blago-life013-venv/bin/python`, `PYTHONPATH=/tmp/blago-sqlite-driver-3541:.:src`, SQLite3.54.1. Venv/driver не являются переносимым артефактом: заново собрать из pinned source/lock. CI использует Ubuntu и отдельный Docker build.

## Миграция existing certified synthetic copy

Остановить ingress и **всех** writers/maintenance workers. Сохранить consistent SQLite backup API copy и original controls отдельно. Не копировать один main DB при живом WAL. Сверить актуальный retained witness и authenticated custody bundle по 05.4.

На новой offline copy выставить whitelisted config (`public-config.json` созданного fixture — только ориентир). Нужны `XBLAGO_INTEGRATION_MODE=synthetic`, original `XBLAGO_SECURITY_JOURNAL_DIR`, original `XBLAGO_JOURNAL_WITNESS_DIR`, secure/pilot flags, `XBLAGO_DATA_DIR` этой copy. Никаких новых init-journal/init-witness поверх старой lineage.

```sh
python -m xblago.core.lifecycle_schema /tmp/offline-synthetic/blago.sqlite3
python -m xblago.core.lifecycle_schema /tmp/offline-synthetic/blago.sqlite3
```

Первый вызов `applied`, второй `unchanged`. Existing certified baseline до Help/context получает их канонические схемы атомарно вместе с новым слоем; replay выполняется до publication frontier. Unknown baseline/DDL/checksum/control loss => остановка и разбор, не обход checksum.

Включить `XBLAGO_CONTEXT_FENCING_REQUIRED=true`, `XBLAGO_LIFECYCLE_REQUIRED=true`, прежние identity/privacy flags. Выполнить `preflight(path)` до любого чтения/HTTP. Проверить manifest, seven lifecycle tables, `PRAGMA integrity_check`=`ok`, `PRAGMA foreign_key_check` пустой, WAL и synchronous=FULL. Выполнить synthetic chat/forget, old-backup replay, Help cycle и consent negatives на **копии**.

## Явная очистка и backups

```sh
python -m tools.lifecycle_maintenance sweep --database /tmp/offline-synthetic/blago.sqlite3
python -m tools.lifecycle_maintenance backup --database /tmp/offline-synthetic/blago.sqlite3
python -m tools.lifecycle_maintenance retire-backup --database /tmp/offline-synthetic/blago.sqlite3 --backup-id SYNTHETIC_ID_FROM_PREVIOUS_COMMAND
python -m tools.lifecycle_maintenance retire-backup --database /tmp/offline-synthetic/blago.sqlite3 --backup-id SYNTHETIC_ID_FROM_PREVIOUS_COMMAND --unlink
```

Сначала logical retirement, затем optional unlink. При изменённых bytes/unsafe path unlink отказывается; logical denial уже может быть durable. Ответ не доказывает erasure других copies. Managed backup7d автоматически **retired при sweep**, но физическое удаление требует явного `--unlink`; sweep не обходит filesystem.

Проверить counters, integrity/FK, отсутствие истёкшего plaintext и сохранение recent data/active passkey. Нет scheduler: до отдельной реализации запрещено обещать автоматический deadline. Future/invalid now, неизвестная policy, legacy financial rows, unresolved merge, expired unpaired legacy history => вся sweep-транзакция отклоняется. Не удалять blockers вручную ради зелёного отчёта. Оператор должен зафиксировать безопасный stop, category/count без text/ID в diagnostics, и подготовить отдельное решение.

## Restore старой копии

1. Остановить все app instances. Нельзя обслуживать одновременно primary и восстановленную copy одного control journal.
2. Получить актуальные оригинальные journal/key/head через прежнюю authenticated custody procedure и retained witness из независимого rollback domain. Если актуальность/целостность/ключ неизвестны — fail closed; не создавать новые «чистые» controls.
3. Работать только с offline copy. Retired exact managed bytes не восстанавливать. Для копии до lifecycle (или до Help/context) применить explicit `lifecycle_schema` migration до открытия store: она replay-ит уже сохранённые denials, не новые grants.
4. Выполнить существующее controlled restore:

   ```sh
   python -m xblago.core.privacy_maintenance restore --database /tmp/offline-restore/blago.sqlite3 --journal /tmp/original-controls/journal
   ```

5. Restore отклоняет PREPARED, меняет revision/epoch, удаляет sessions/challenges/link codes, включает Telegram quarantine. Проверить denial replay, deleted user status, missing private Help, stale worker rejection, dedupe и integrity/FK. Не восстанавливать user grants из старого snapshot поверх актуальных denials.
6. При journal failure после durable intent SQL может быть rollback, но следующий guard обязан завершить удаление; повторное обслуживание до reconcile запрещено. Сохранять исходное доказательство ошибки, не менять journal sequence вручную.

## Stop / rollback

Закрыть ingress, остановить workers, сохранить controls/current consistent app state. Не rollback controls и не удалять lifecycle events/holds, чтобы запустить старый binary. Старое ПО не понимает новую lineage: безопасен совместимый forward fix либо supervised offline restore с replay и повторной проверкой. Destructive downmigration отсутствует.

## Что нельзя публиковать

DB/WAL, ключи, HMAC rows, witness secret material, cookie/proof/challenge, частные prompts/ответы, сырые exports и путь production не включать в PR/evidence. Проверенные synthetic JSON содержат только schema, versions, counters и boolean checks. Физические устройства, provider deletion и independent custody не становятся PASS из этих команд. G04 UNKNOWN, общий пилот NO-GO.
