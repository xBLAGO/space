# Foundation 05.4 — безопасная миграция и disaster recovery

Это инструкция для будущего санкционированного оператора. В рамках PR production
миграция, deployment и изменение secrets **не выполнялись**. Сначала воспроизвести
все действия на изолированных синтетических копиях. Не использовать реальные
идентификаторы или ключи в LLM/issue/CI logs.

## 1. Условия допуска

1. Проверить конкретный SHA и зелёные tests/Docker jobs draft PR. Зафиксировать
   image digest, Python version, `sqlite_version`, `sqlite_source_id()` и источник
   library. Выполнить `python -m xblago.core.sqlite_safety --require-safe` внутри
   фактического candidate runtime. Старый Python/утилита sqlite3 не заменяют probe.
2. Получить подтверждённые POSIX locks, SQLite WAL, atomic link/rename и fsync
   свойства application/journal/checkpoint volumes. Не считать HF bucket mount
   пригодным без проверки. Не отключать SSH host key verification ради probe.
3. Назначить независимых custodians: application backup storage, journal DR
   storage, wrapping-key escrow/KMS, witness storage с отдельным retention/IAM.
   Основному runtime не предоставлять возможность удалить/откатить сохранённые
   независимые checkpoints. Копия на соседнем пути того же диска не решает DR.
4. Утвердить retention, legacy merge audit, разрешённый pilot scope и device/HF
   acceptance. Пока таблица блокеров содержит OPEN — запуск пилота запрещён.

## 2. Offline подготовка проверенной копии

Остановить writers санкционированной процедурой, создать согласованный SQLite
online backup, а не копировать один `.sqlite3` при активном WAL. Зафиксировать
hash, размер, schema versions и manifest содержимого без персональных текстов.
Исходник оставить неизменным. Все дальнейшие шаги — на отдельной offline копии.
Не запускать web/Telegram server до завершения recovery validation.

Проверить `PRAGMA integrity_check` (`ok`) и пустой `foreign_key_check`; сравнить
BLAGO IDs, Telegram links, количество и digests memory/history/economic rows.
Миграции 05.1/05.2/05.3 выполнять только если отсутствуют, существующим кодом и
с исходным journal ID/key. Уже используемый журнал нельзя заменить новым.
Существующие migration checksums должны совпасть; конфликт — остановка.

Проверить прежние удаления и merge на самой старой предполагаемой recovery
копии. 05.4 не может реконструировать неизвестные старые переходы. Если audit
неполон, такая копия quarantine; нельзя легализовать её запуском migrate_pilot.
Определить **reviewed current primary copy**, выполнить 05.4 только на ней,
записать новую generation и lineage_start_seq в подписанный baseline manifest.
Retire/исключить старые неаттестованные recovery copies, включая ручные экспорты.

Пример команд для уже подготовленной тестовой копии (пути — placeholders):

```sh
export XBLAGO_SECURITY_JOURNAL_DIR=/trusted/control-journal
export XBLAGO_REQUIRE_PRIVACY_JOURNAL=true
# Pilot пока не включён: explicit offline provisioning, а не обход runtime gate.
export XBLAGO_PILOT_MODE=false
python -m xblago.core.sqlite_safety --require-safe
python -m xblago.core.pilot_schema /offline/reviewed-current.sqlite3
python -m xblago.core.pilot_schema /offline/reviewed-current.sqlite3
```

Второй запуск обязан дать `already applied`. Новая схема — additive 05.4,
изменения таблиц экономических правил не выполняются. При любой ошибке оставить
службу закрытой, сохранить исходник/диагностику и расследовать; не переписывать
migration checksum и не делать ручной частичный rollback triggers.

## 3. Независимый checkpoint и escrow

На новом, проверенном независимом storage инициализировать witness один раз.
Переменная `XBLAGO_JOURNAL_WITNESS_DIR` до первоначальной инициализации не должна
указывать на ещё не существующий witness: это намеренно fail-closed.

```sh
python -m xblago.core.journal_custody init-witness \
  --journal /trusted/control-journal --witness /independent/checkpoints
python -m xblago.core.journal_custody init-wrap-key \
  --wrap-key /separate-escrow/wrapping.key
export XBLAGO_JOURNAL_WITNESS_DIR=/independent/checkpoints
python -m xblago.core.journal_custody export \
  --journal /trusted/control-journal --wrap-key /separate-escrow/wrapping.key \
  --package /independent-dr/journal-bundle-new.json
```

Каталоги provisioned владельцем; файлы ключей 0600, private directories 0700.
Команды не выводят ключ. Передать wrapping key отдельному custodian по защищённой
процедуре; не включать его в journal bundle, application backup, Git или logs.
Сохранить returned journal ID/seq/head и package hash в подписанном inventory.
Основной сервер не должен быть единственным держателем ключа или последней вершины.

DR bundle обязан включать **все** подтверждённые critical intents. На каждом
подтверждённом изменении нужно либо обеспечивать независимую долговечность самого
журнала/его WAL, либо синхронную репликацию свежего encrypted bundle. Один дневной
bundle при более новом witness не восстановится: это безопасный отказ, но потеря
доступности. Локальный код не предоставляет remote replication service; такую
эксплуатационную гарантию требуется отдельно построить и испытать до пилота.
Bundle export выполнять при согласованном quiescence и сверять current witness.

## 4. Проверка нового экземпляра и включение gate

В отдельном изолированном экземпляре сначала восстановить original journal из
bundle, не генерируя новый key/journal ID. Использовать доверенную самую свежую
вершину из независимого storage; не вершину из восстанавливаемого application
backup и не старую локальную копию.

```sh
python -m xblago.core.journal_custody recover \
  --journal /new-instance/recovered-journal \
  --witness /independent/checkpoints \
  --wrap-key /separate-escrow/wrapping.key \
  --package /independent-dr/journal-bundle-new.json
export XBLAGO_SECURITY_JOURNAL_DIR=/new-instance/recovered-journal
export XBLAGO_JOURNAL_WITNESS_DIR=/independent/checkpoints
export XBLAGO_PILOT_MODE=true
export XBLAGO_REQUIRE_PRIVACY_JOURNAL=true
export XBLAGO_REQUIRE_SECURE_IDENTITY=true
python -m xblago.core.sqlite_safety --require-safe
python -m xblago.core.privacy_maintenance restore \
  --database /new-instance/certified-app-copy.sqlite3 \
  --journal /new-instance/recovered-journal
```

Recover требует новый destination directory; повреждение/неверный ключ/старый
bundle закрывают доступ. Application backup должен иметь проверенный 05.4
baseline. Не выполнять migrate_pilot на старом неподтверждённом backup для обхода
этого требования. Все critical intents проигрываются до доступа к данным;
sessions/challenges/link codes сбрасываются, Telegram recovery quarantine —
3661 секунд. Участнику нужна свежая аутентификация. Journal продолжает расти при
restore revocations: после восстановления снова сохранить актуальный DR bundle.

Проверить повторное replay, удалённых участников/все версии фактов/alias exports,
старые sessions, повтор Telegram update, собственную и чужую память, Web/Mini App
одного ID, restart, `integrity_check`, `foreign_key_check` и точные economic
digests/contribution count. Отдельно проверить signed-head rollback, потерю key,
unavailable witness и отказ диска. Любой ошибочный probe → закрытый runtime,
никакой «временной» новой цепи или отключения pilot flag для обслуживания людей.

При потере последней вершины или всех ключей — incident и ручное решение по
доказательствам; автоматическое восстановление со старой цепью запрещено.
Разрешение запуска — отдельное operational approval после реального offsite
drill, device/HF acceptance и закрытия всех блокеров. Этот PR такого разрешения
не предоставляет и deployment не выполняет.

## Локальное воспроизведение

```sh
python -m pip install -e .
python tools/build_sqlite.py --prefix /tmp/blago-sqlite
# Linux: actual dynamically linked driver, then verify, never assume.
export LD_LIBRARY_PATH=/tmp/blago-sqlite
export LD_PRELOAD=/tmp/blago-sqlite/libsqlite3.so.0
python -m xblago.core.sqlite_safety --require-safe
PYTHONPATH=src python -m unittest discover -s tests -v
python -m pip install playwright==1.64.1
python tools/pilot_browser_check.py --chrome /path/to/google-chrome --output /tmp/browser-evidence
# Opt-in external inference: synthetic data only; costs/network required.
python tools/pilot_hf_check.py --live --output /tmp/hf-integration.json
```

Для статически связанного macOS CPython **3.12.10** отдельно скачать официальный
`Python-3.12.10.tgz`, затем `tools/build_macos_sqlite_driver.py --python-source
/path/Python-3.12.10.tgz --sqlite-prefix /tmp/blago-sqlite --prefix
/tmp/blago-sqlite-driver`; запускать с `PYTHONPATH=/tmp/blago-sqlite-driver:src`.
Другие версии Python требуют отдельной проверенной сборки. Скрипт проверяет
source hash, не изменяет системную установку. Полный git history нужен для
исторического fixture PR #2. Live HF и browser evidence описывают пределы
проверки; hardware и production hosting из локального прогона не следуют.
