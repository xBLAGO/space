# Runbook — isolated operational retention

Только новый synthetic fixture или сертифицированная offline synthetic copy. Нет разрешения на production migration, system scheduler/service, webhook, HF Space или реальные credentials. Runtime/source и checksums смотреть в отчёте и evidence.

## Подготовка и воспроизведение

Python с `requirements-integration.lock` и фактически verified SQLite. На local macOS использовались `/tmp/blago-life013-venv/bin/python`, `PYTHONPATH=/tmp/blago-sqlite-driver-3541:.:src`, SQLite3.54.1; system library не заменялась. На другом host заново собрать pinned driver по прежнему runbook/CI. Не выводить весь environment.

```sh
export PYTHONPATH=.:src
python -m xblago.core.sqlite_safety --require-safe
python -m tools.freeze_integration
python -m tools.help_readiness_lab init --new-root /tmp/blago-privacy014-new
python -m tools.retention_scheduler init --root /tmp/blago-privacy014-new
python -m tools.retention_scheduler run --root /tmp/blago-privacy014-new
```

Последняя команда без flag обязана вернуть DISABLED и не запускать cleanup. Init — только explicit создание новой private ops directory; не app migration. Нет автоматического ремонта missing/drifted ops schema.

Включение **только на этом fixture**:

```sh
export XBLAGO_RETENTION_SCHEDULER_ENABLED=true
python -m tools.retention_scheduler inspect --root /tmp/blago-privacy014-new
python -m tools.retention_scheduler run --root /tmp/blago-privacy014-new
python -m tools.retention_scheduler run --root /tmp/blago-privacy014-new --ticks 10 --poll-seconds 1
python -m tools.retention_ops_lab --output /tmp/privacy014-operations.json
python -m unittest tests.test_retention_ops -v
python -m unittest discover -s tests -v
```

Default due interval3600s: короткий loop из10 ticks после успешной очистки покажет WAITING, а не ещё десять sweep. Для отдельного synthetic frequency rehearsal можно при **новом** init указать `--interval 1`; не менять вручную подписанный `ops_binding.policy`. Loop ограничен1–1000 ticks, poll1–60s; automatic retry происходит только при следующих ticks и не более configured max attempts. Это не установленный daemon и не настроенный on-call.

Для длинного локального macOS regression можно временно использовать `caffeinate -i python -m unittest discover -s tests -v`. Это сохраняет auth TTL и не меняет power settings; состояние свежей тестовой сессии после многочасовой паузы не должно принудительно считаться действительным. Предыдущие локальные отказы и clock observations сохранены в evidence.

## Нормальное обслуживание

До старта закрыть произвольные manual backup writers. Проверить private marker/config, canonical app/journal/witness и отдельный ops store. App/backups/sqlite — **managed directory**: её фактические файлы должны соответствовать зарегистрированным digest/state. Для отдельных synthetic restore-копий пользоваться другой явной directory и отдельно учитывать её lifetime.

Runner берёт один OS lock; BUSY не является успешной очисткой. Перед удалением создаётся registered/hash-checked backup. Sweep использует прежние guards/denials; eligible retired managed copies удаляются только после durable retirement с проверкой bytes/path. Затем original controls проверяются повторно, и только тогда записывается SUCCESS. Отчёт содержит только aggregate counters, не user/request IDs или filenames.

Inspect/JSON warnings: `RETENTION_OVERDUE` и `RETENTION_UNCONFIRMED`. Exit2 для FAILED последней попытки/UNKNOWN_RECOVERY/EXHAUSTED/CLOCK_ROLLBACK/UNKNOWN_OPERATION. Вывод необходимо подключить к реальной monitoring/on-call системе отдельным approved change и проверить доставку/ack; этого PR недостаточно для SLA.

## Ошибки / ручной разбор

- Backup error/полный диск: cleanup не начинается; освободить место по утверждённой процедуре, сохраняя controls. Не стирать journal/holds для обхода ошибки.
- Unknown/missing/changed managed backup: REVIEW_REQUIRED. Не удалять неизвестный файл автоматически и не переносить его тайно, чтобы получить зелёный результат. Зафиксировать synthetic provenance, scope и отдельное решение inventory/retirement. Приложение из старой копии может не знать более новый backup catalogue: это корректный operational blocker.
- Witness/key/frontier потерян: UNKNOWN_RECOVERY, остановить обслуживание, восстановить оригинальные controls по05.4. Никогда не `init-journal` вместо восстановления.
- Crash: lock освобождается ядром, RUNNING остаётся недоказанным. На следующем verified tick он превращается в INTERRUPTED; повторная попытка идемпотентна по прежним denials. Долгую паузу живого процесса нельзя обходить удалением lock-файла.
- Result store/fsync failure после app commit: отсутствие SUCCESS, но удаление уже могло состояться. Не обещать rollback privacy action по ошибке operational receipt.
- Exhaustion/legacy blockers/clock rollback: review, correct cause, затем explicit action:

  ```sh
  python -m tools.retention_scheduler reschedule --root /tmp/blago-privacy014-new
  python -m tools.retention_scheduler run --root /tmp/blago-privacy014-new
  ```

Reschedule не очищает last_success/overdue и не является разрешением обойти policy. Loss/corruption ops store: сохранить incident evidence; автоматического сброса истории нет. Новый supervised synthetic rehearsal возможен на новом root, но это **не** доказательство восстановления пропавших ops logs.

## Backup / restore / rollback

App backup API не включает отдельный ops DB, journal или witness. Держать их в реально отдельных rollback domains при будущем qualification; локальные directories этого не доказывают. STOP всех writers/ingress перед controlled restore. Следовать Privacy01.3 runbook: свежие оригинальные controls, explicit migration только если schema отсутствует, denial replay, epoch/session/challenge invalidation и Telegram quarantine. Старые checksums не менять.

Rehearsal восстанавливает старую application copy и проверяет отсутствие удалённых записей; это не переключение реального хоста. Перед будущим обслуживанием restored canonical root выполнить explicit inventory/reconcile и scheduler tick, а не считать прежний last_success достаточным. Неизвестные managed files после восстановления старого каталога блокируют работу до review. Не запускать primary и restored DB одновременно с общей control lineage.

Rollback к старому binary не должен откатывать deletion controls. Scheduler можно выключить flag; все уже сделанные denials/retirements остаются. Нет downmigration app schema, потому что она не менялась. Для несовместимого/потерянного ops state — stop + reviewed forward repair; очистка/подмена signed policy вручную не поддерживается.

## Docker / browser / evidence

Tests workflow запускает полный regression, существующие Chrome WebAuthn/Help/synthetic Mini App и новый ops lab. Docker step запускает lab с `--network none`, disposable SQLite и tools mounted read-only. Нет доступа к HF/prod secrets. Physical Telegram/passkeys, реальный диск/power-loss и off-host custody NOT EXECUTED.

Не публиковать DB/WAL, cookies, raw journal/key/head files, экспорты, private prompts/exception strings. Проверять `evidence-index.json`, source SHA и exact CI jobs. G04 из результата команды не выводится: утверждение policy, external copies и реальные target-host доказательства остаются отдельными gates.
