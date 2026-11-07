# Privacy 01.2 — evidence

Source: `47af53e0052a2f67efbef9108c3987b2439efc04`; base #22 runtime `d2294c1b1c7b082c6dfcfe1eb788bb6e8d12285b`, documentation HEAD `77c7f7f0495a7bb383b1c75c63bf5c97cb984129`. [Отчёт](../BLAGO_FOUNDATION_PRIVACY_01_2.md), [threat model](THREAT_MODEL.md), [migration/recovery commands](RUNBOOK.md).

| Файл | Доказательство и граница |
|---|---|
| `before-probe.json` | Неизменённый результат FAIL до правок, на #22 runtime; 2 actual processes |
| `after-probe-ci.json` | PASS на новом source; generation rejected, no stale history effects |
| `performance-local.json` | Local 8+8 turns, latency + availability; nested cross_worker PASS; no external LLM |
| `performance-ci.json` | Такой же benchmark на Linux CI; числа не сравнивать как одинаковое hardware |
| `test-results.json` | 390 tests local/CI, 34 новых case-level results; no production approval |
| `ci-evidence.json` | Exact source jobs, Docker image, SQLite, artifact digest; decoded logs прочитаны |
| `candidate-identity.json` | Git/tree, lock/runtime-library и migration checksums; no independent storage qualification |
| `browser-results.json` | 11 desktop Help checks на installed fencing |
| `mobile-results.json` | 14 emulated mobile/synthetic Telegram checks; physical WebView NOT EXECUTED |
| `mini-app-conflict.png` | Просмотренный synthetic UI с восстановленным draft; no real user data |
| `gates.json` | Scoped G09 PASS, остальные блокеры не закрыты; real_pilot NO-GO |
| `evidence-index.json` | SHA256 файлов; source mapping before/after |

Старые evidence #19/#20/#22 сохранены в прежних каталогах. Raw DB, journal/witness, wrapping key, cookies, sessions, provider bodies/diagnostics и private exports не включены. Новые generation metadata не дают права действовать от имени пользователя. Успешные CI/виртуальные tests не являются физическими или независимыми эксплуатационными испытаниями.
