# Результаты проверок

База `2e6e5aab1a2995f6d6c157bf35029f21e834df7b`, источник UI — Codex 02.1. Source/head нового PR фиксируются Git и CI, без самоссылочного SHA.

| Проверка | Фактический результат |
|---|---|
| Offline verifier unit | 17/17 PASS; полная положительная fixture — искусственные unit-данные, не browser evidence |
| Seed/copy integration | 2/2 PASS; три равных DB SHA, 360 expected / 0 actual; verifier code 2 |
| Local HTTP | 8/8 PASS, включая spoof query/header/cookie для A/B/C; 95.770 s |
| Full regression ×2 | RUNNING — результат будет дополнен после завершения |
| Manifest regeneration | PASS; схемы SQLite и старые migration checksums неизменны |
| Docker | Локально NOT EXECUTED; CI PENDING |
| Новый 02.2 browser E2E | NOT EXECUTED; все ветки BLOCKED |
| Независимая сверка DeepSeek | NOT PROVIDED |

Python 3.12.10 x86_64, SQLite 3.54.1 в отдельной тестовой среде Mac. Команды:

```sh
PYTHONPATH=/private/tmp/blago-pr41-live-sqlite:.:src /usr/bin/arch -x86_64 /private/tmp/blago-pr41-live-venv/bin/python -m unittest tests.test_living_bundle tests.test_living_evidence_prepare tests.test_living_consent_lab -v
PYTHONPATH=/private/tmp/blago-pr41-live-sqlite:.:src /usr/bin/arch -x86_64 /private/tmp/blago-pr41-live-venv/bin/python -m tools.uel03_lab_check --full --repeat 2 --output /tmp/lb22-full.json
```

17 unit-проверок покрывают consistent fixture, missing role, empty screen, blank/missing PNG, hash damage, weakened expected, false PASS, missing first-delivery event, hidden DOM marker, extra receive field, refusal reason, auth field, different genesis, reused restart session, forbidden DB/path and pre-mask positive control. Испытания verifier не считаются пользовательскими исходами consent/decline/revoke.

Новых миграций нет. Изменения ограничены tools/tests/docs, CI и производным integration/candidate.json. Файлы src, UI HTML/CSS/JS, dependencies, Dockerfile и deployment workflow совпадают с базой. Существующие gates не повышаются.
