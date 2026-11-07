# Privacy 01.2 — migration, проверки и восстановление

Только offline synthetic copies. Ни одна команда ниже не является разрешением изменить production. Source: `47af53e0052a2f67efbef9108c3987b2439efc04`; exact base #22: `77c7f7f0495a7bb383b1c75c63bf5c97cb984129`.

## Новый стенд

Python с verified SQLite и dependencies из прежнего lock. На этой машине использованы `/tmp/blago08-venv/bin/python`, `PYTHONPATH=/tmp/blago-sqlite-driver-3541:.:src`; system Python/SQLite не заменялись. Linux/Docker используют прежний pinned build/LD_LIBRARY_PATH из CI.

```sh
PYTHONPATH=.:src python -m xblago.core.sqlite_safety --require-safe
PYTHONPATH=.:src python -m tools.integration_fixture --new-synthetic-root /tmp/blago-privacy012-new --help-enabled --context-fencing
PYTHONPATH=.:src python -m tools.freeze_integration
PYTHONPATH=.:src python -m unittest discover -s tests -v
PYTHONPATH=.:src python -m unittest tests.test_context_fencing -v
PYTHONPATH=.:src python -m tools.help_worker_probe --output /tmp/privacy012-after.json
PYTHONPATH=.:src python -m tools.context_fencing_check --output /tmp/privacy012-performance.json
PYTHONPATH=.:src python -m tools.help_mobile_check --chrome /path/to/chrome --output /tmp/privacy012-mobile
PYTHONPATH=.:src python -m tools.help_browser_check --chrome /path/to/chrome --output /tmp/privacy012-desktop
```

Каждый fixture init требует нового root. Config файл содержит только synthetic paths/flags; не копировать из production `.env`. `XBLAGO_CONTEXT_FENCING_REQUIRED=true` в созданной конфигурации требует migration. Включать config по whitelist, не выполнять произвольный JSON как shell code. Existing Help lab CLI и Compose recipe #22 теперь явно создают fencing schema; это остаётся isolated Web harness, не HF deployment и не bot polling.

## Upgrade существующей offline synthetic copy

1. Проверить exact source, ancestry, lock и `freeze_integration`. Проверить отсутствие чужих/экспериментальных schema changes. Согласовать outage: остановить все writers и ingress, включая Bot и фоновые jobs.
2. Сохранить consistent app backup через SQLite backup API, отдельно original journal, signed head и retained witness; wrapping key и encrypted DR custody — по 05.4. Нельзя копировать только main DB-файл, пока WAL жив.
3. Использовать **новую offline copy**. Задать её `XBLAGO_DATA_DIR`, original `XBLAGO_SECURITY_JOURNAL_DIR`, независимый retained `XBLAGO_JOURNAL_WITNESS_DIR` и прежние secure flags. Проверить actual SQLite `--require-safe`.
4. Применить прежние 05.x и, если нужен Help, Help migration в прежнем порядке. Затем явно вызвать:

   ```sh
   PYTHONPATH=.:src python -m xblago.core.context_schema /tmp/offline-synthetic/blago.sqlite3
   ```

5. Повторить: ожидается `unchanged`. Проверить DDL/checksum `foundation_privacy_context_001`, integrity/FK и source manifest. Включить `XBLAGO_CONTEXT_FENCING_REQUIRED=true`; выполнить preflight до обслуживания.
6. Проверить synthetic chat/delete, Telegram replay, Help cycle, restore и positive/negative authorization. Source CI не заменяет эти проверки на конкретном host. Реальных участников не подключать без остальных release gates.

Migration добавляет новые tables/triggers, backfill version metadata, reconciles retained checkpoints и публикует новую version baseline. Никаких новых `blago_user_id` для старых участников; memory/history/economy не переносятся и не переписываются. Partial DDL/unknown shape/collision/checksum error — rollback/fail closed, не repair «по сходству».

Help migration после context migration этим PR не вводится как новый upgrade path. Поддерживаемый порядок — existing Foundation baseline → optional Help → context fencing. Не менять старые checksums или frozen schema ради обхода отказа.

## Restore

Остановить writers. Восстановить original control journal из authenticated encrypted bundle в новый private destination, проверить против сохранённой актуальной witness вершины. Только затем offline app restore. Использовать прежний `privacy_maintenance.restore`, который теперь также отвергает PREPARED и меняет все context epochs/revisions; session invalidation и Telegram recovery quarantine сохраняются.

Если backup взят до context DDL, а control journal уже содержит context events, first preflight обязан отказать. Применить context migration к **offline copy**, не сбрасывая history/frontier; затем выполнить controlled restore. Это покрыто тестом. Если потеряны актуальные key/frontier/witness или не доказана их свежесть, не обслуживать пользователей и не генерировать «новый original journal».

Нельзя одновременно обслуживать primary и восстановленную app copy. Проверка старого worker и journal-head CAS снижает риск stale commit, но не превращает два SQLite-файла в реплицированную систему.

## Rollback / stop

Закрыть ingress и остановить workers; сохранить original controls и last consistent app state. Новые critical `context_version` events нельзя удалить, а epoch/counter нельзя откатить ради запуска #22. Старый binary против upgraded schema/control lineage должен fail closed. Допустим совместимый forward fix либо supervised offline recovery; автоматической destructive downmigration нет.

## Проверка доказательств

`before-probe.json` относится к исходному #22 runtime и содержит FAIL. `performance-local.json` и `after-probe-ci.json` относятся к новому source и должны иметь PASS + `generation_rejected=true` + `stale_context_written_after_delete=false`. Code CI теперь завершается failure, если probe не подтверждает эти условия. Все числа latency — synthetic wall-clock, без внешнего LLM.

Новые diagnostics не содержат частных prompts/answers/UID/session keys. Raw SQLite/control/key/bundle/cookies в PR не публиковать. Физические Telegram/passkeys и live provider NOT EXECUTED; оставшиеся gates не повышать автоматически.
