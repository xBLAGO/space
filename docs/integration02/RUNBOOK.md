# Unified candidate — isolated reproduction

No production migration/deploy. Exact base#25 `0772e5bc5919aefac2c209f4e40fe5ea790d6792`, source `395ecf10f9187663fddb64795597e7efe0f014fa`. Не переключать другие рабочие ветки; использовать отдельный checkout/worktree для воспроизведения. Не читать production `.env` или cached provider tokens.

## Сборка и проверки

Установить `requirements-integration.lock` в новый venv, собрать pinned SQLite по прежнему build/driver runbook. Проверить фактически импортированный SQLite, не только image tag. Linux CI задаёт patched library; macOS здесь использовал `/tmp/blago-sqlite-driver-3541` в PYTHONPATH и SQLite3.54.1. System SQLite не заменялся. Browser testing использует существующий Chrome и отдельный dev-only Playwright; production dependencies не добавлены.

```sh
export PYTHONPATH=.:src
python -m xblago.core.sqlite_safety --require-safe
python -m tools.freeze_integration
python -m tools.integration_evidence --output /tmp/integration02-source.json
python -m unittest discover -s tests -v
python -m unittest tests.test_unified_mvp -v
python -m tools.unified_mvp_check --output /tmp/integration02-journey.json
python -m tools.help_browser_check --chrome /path/to/chrome --output /tmp/integration02-browser
python -m tools.help_mobile_check --chrome /path/to/chrome --output /tmp/integration02-mobile
python -m tools.retention_ops_lab --output /tmp/integration02-ops.json
python -m tools.help_worker_probe --output /tmp/integration02-g09.json
```

Для долгого macOS run допустим временный `caffeinate -i python -m unittest ...`; TTL/auth не менять. Истечение сессии после длительного suspend не повод отключать guard.

Новый единый journey создаёт один lab root, одну SQLite и пять synthetic account principals. Он регистрируется через actual HTTP endpoints, связывает Bot, обменивает signed synthetic initData, общается в трёх каналах, проходит Help и worker crash/replay, затем проверяет offline restore. Tokens/challenges/UID используются внутри fixture, в public result отсутствуют. Синтетические тексты в `turns` не являются real-user dialogues. Все temp data удаляются после завершения.

Docker workflow сохраняет прежние identity/privacy/recovery/SQLite smoke и добавляет unified journey с `--network none`, tools mounted read-only. Loopback внутри контейнера нужен для HTTP; никаких third-party endpoints. Локальный Docker-daemon не требуется для чтения CI evidence; фактический source image digest и jobs в `ci-source.json`.

## Аудит PR и main

```sh
python -m tools.unified_repo_audit --inventory docs/integration02/pr-inventory.json --output /tmp/integration02-repo.json
git diff --name-status 3c218dc5757879c26b3aec520af3636b2d702715 0772e5bc5919aefac2c209f4e40fe5ea790d6792
git diff --name-status 0772e5bc5919aefac2c209f4e40fe5ea790d6792 395ecf10f9187663fddb64795597e7efe0f014fa
```

Нужны Git objects captured PR heads. Если их нет в новом clone, получить exact public objects отдельно, не checkout/merge в main. Перед будущей integration перечитать PR metadata/current main через GitHub; сверить hashes, не переписывать текущий evidence snapshot. `git merge-tree` здесь read-only legacy three-way simulation: markers считаются, tree/index/branches не изменяются; отсутствие markers не совместимость.

## Main-derived SQLite upgrade

```sh
python docs/integration02/main_upgrade_check.py --output /tmp/integration02-main-upgrade.json
```

Скрипт архивирует только `src` exact main во временный каталог, запускает именно тот SQLite store с **новыми synthetic** фактами/историей, затем применяет текущие migrations в offline порядке. Новая control lineage создаётся лишь для этого нового synthetic legacy fixture; никакие existing controls не reset. REQUIRED context/lifecycle flags включаются после их явной установки, до preflight/serving. На реальной системе нужны stopped writers, reviewed original controls и отдельное разрешение; этот script не принимает production DB path.

Существующий Help migrator после Context/Lifecycle отклоняет frozen-baseline mismatch. Rehearsal записывает это как ожидаемый безопасный отказ и проверяет данные/preflight; не называет arbitrary migration loop идемпотентным. Для реального release сначала разработать/проверить ledger-aware dispatch с validation already-installed modules, без checksum edits или catch-and-ignore неизвестного drift.

## Restore и rollback

Только offline copy, original актуальные journal/key/head/witness из квалифицированного custody. Старый snapshot не может публиковать/подтверждать Help заново или возвращать forgotten memory. В rehearsal worker завершён **после** commit без ответа; retry возвращает existing receipt и не повторяет event. При неизвестном frontier — stop, не новый init-journal.

Не обслуживать primary и restored copy одновременно. После controlled restore invalidated sessions/challenges/epochs и Telegram quarantine — ожидаемый результат, не повод отключить guards. Managed backup inventory может потребовать review orphan/missing files после старого catalogue restore. Source/binary rollback не rollback denials. Real target-host DR/physical loss остаётся UNKNOWN.

## Public UI и провайдер

Public site evidence получен только unauthenticated GET указанных URLs, без scripts/trackers/forms/payments/bot messages. Для повтора открыть ссылки из `public-site.json` или проверить GET/HTTP status вручную; content hashes — снимок, не обещание неизменности страниц. Источники сайта/реальный app deployment неизвестны.

`provider-status.json` проверяет только наличие трёх явно тестовых authorization variables; их значений нет в evidence. Default candidate принимает local provider. Если будущий approved live check нужен, использовать прежний test-provider tool только с отдельными test credentials/model/synthetic-only approval, без чтения HF_TOKEN/cached token/production `.env`, затем manual quality review. Текущий NOT EXECUTED не превращать в PASS.

## Evidence и выпуск

Проверить hashes `evidence-index.json`, source identity, полный тестовый summary и одинаковый runtime tree. Browser virtual/HMAC simulation не заменяет physical Telegram/passkeys. Public website availability и зелёные individual PR checks не доказывают deployment candidate. До всех approval/gates: **GO isolated development, NO-GO real pilot**. Никаких автоматических merge/deploy или schema changes в production.
