# Help 01.1 evidence index

Отчёт: [BLAGO_FOUNDATION_HELP_01_1.md](../BLAGO_FOUNDATION_HELP_01_1.md). Команды: [RUNBOOK.md](RUNBOOK.md). Всё относится к синтетическому source candidate `d2294c1b1c7b082c6dfcfe1eb788bb6e8d12285b`, а не к реально развёрнутому приложению.

| Evidence | Смысл |
|---|---|
| `candidate-identity.json` | Local exact source/tree/dependencies/migration manifest/фактическая SQLite; независимость storage не подтверждена |
| `ci-evidence.json` | Exact source CI, 356 tests, Docker/library/cycle/lab, image ID и artifact digest; job logs прочитаны |
| `test-results.json` | Local full 355 + final focused 16; это не 371 разных тестов. Final CI full 356 |
| `browser-results.json` | Две isolated desktop Chrome contexts: полный synthetic Help lifecycle |
| `mobile-results.json` | Chrome touch/mobile + synthetic SDK/HMAC server exchange; НЕ настоящий Telegram |
| `synthetic-mini-app-mobile.png` | Просмотренный mobile screenshot, искусственный текст и invitation codes |
| `worker-probe.json` / `worker-probe-ci.json` | Два настоящих процесса: FAIL stale context/history after deletion, local и CI |
| `provider-results.json` | Live test-provider NOT EXECUTED, zero requests; cached/production credentials не читались |
| `local-stand.json` | Local SQLite/WAL/FULL/integrity; real hosting UNKNOWN |
| `gates.json` | NO-GO; G09 и G11 FAIL; UNKNOWN не повышены до real-pilot PASS |

Исторические `../help01/` и `../integration01/` не изменены. Raw application DB, wrapping/journal keys, cookies, экспорт аккаунта, реальные identifiers и полный HTTP/CI logs в PR не входят. Hash логов позволяет сверить сохранённый результат, но сам по себе не заменяет доступ к test assertions/CI run.

Официальные источники ограничений: [SQLite WAL](https://www.sqlite.org/wal.html), [HF Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker). Не переносят local guarantees на HF storage, proxy, independent custody или physical devices автоматически.
