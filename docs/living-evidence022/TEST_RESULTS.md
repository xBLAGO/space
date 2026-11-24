# Результаты проверок

База `2e6e5aab1a2995f6d6c157bf35029f21e834df7b`, источник UI — Codex 02.1. Source/head нового PR фиксируются Git и CI, без самоссылочного SHA.

| Проверка | Фактический результат |
|---|---|
| Offline verifier unit | 21/21 PASS, 25.094 s; полная положительная fixture — искусственные unit-данные, не browser evidence |
| Seed/copy integration | 2/2 PASS; три равных DB SHA, 360 expected / 0 actual; verifier code 2 |
| Guarded startup трёх копий | 3/3 PASS через существующие Lab/preflight/custodian; без HTTP, браузера или действий согласия; guarded-startup.json |
| Local HTTP | 8/8 PASS, включая spoof query/header/cookie для A/B/C; 95.770 s |
| Full regression ×2 | Результат финального HEAD фиксируется в CI и описании Draft PR #45 после завершения; на момент подготовки этого документа CI ещё выполнялся. Промежуточный локальный прогон остановлен после изменения исходников и не считается финальным доказательством |
| Manifest regeneration | PASS; схемы SQLite и старые migration checksums неизменны |
| Docker | Локально NOT EXECUTED; source CI job 138229134176 PASS, включая 20 adapter + 8 HTTP tests |
| Frozen source contracts / UEL offline | PASS / 72 из 72 PASS, ожидаемые результаты не изменены |
| Новый 02.2 browser E2E | NOT EXECUTED; все ветки BLOCKED |
| Независимая сверка DeepSeek | NOT PROVIDED |

Python 3.12.10 x86_64, SQLite 3.54.1 в отдельной тестовой среде Mac. Команды:

```sh
PYTHONPATH=/private/tmp/blago-pr41-live-sqlite:.:src /usr/bin/arch -x86_64 /private/tmp/blago-pr41-live-venv/bin/python -m unittest tests.test_living_bundle tests.test_living_evidence_prepare tests.test_living_consent_lab -v
PYTHONPATH=/private/tmp/blago-pr41-live-sqlite:.:src /usr/bin/arch -x86_64 /private/tmp/blago-pr41-live-venv/bin/python -m tools.uel03_lab_check --full --repeat 2 --output /tmp/lb22-full.json
```

21 unit-проверка покрывает consistent fixture, missing role, empty screen, blank/missing PNG, hash damage, weakened expected, false PASS, missing first-delivery event, hidden DOM marker, extra receive field, refusal reason, auth field, different genesis, reused restart session, forbidden DB/path, pre-mask positive control, disclosure через JSON keys, truncated whole request, shared session между ветками и поле task до открытия даже без маркера. Испытания verifier не считаются пользовательскими исходами consent/decline/revoke.

## Зафиксированная подготовка

Source SHA: `37ad77235f60912ae0a6aba733f47d0ce2b26a01`.
Bundle в `preparation-bundle/` создан на чистой рабочей копии этого коммита. Три одинаковых initial_database_sha256 записаны в manifest; 360 expected / 0 actual. `preparation-verification.json` содержит полный список пропусков, exit 2. Это проверяемое **BLOCKED evidence**, не недостающие пользовательские наблюдения.

После этой подготовки verifier дополнен запретом поля task до открытия даже без контрольного маркера (21-й unit-test). Исторический manifest не переписывается под новый HEAD. Дополненный verifier возвращает тот же BLOCKED на сохранённом bundle. Финальная рабочая версия определяется HEAD Draft PR #45; source SHA подготовки и SHA последующей проверки различаются явно.

Проверен запуск вне BLAGO checkout, в изолированном Python `-I`, с копией только contract/verifier: BLOCKED, exit 2, `sqlite3` и `xblago` в sys.modules отсутствуют. Файлы SQLite и приватная заметка не передавались.

CI подготовки: https://github.com/xBLAGO/space/actions/runs/45330139974 (PR), https://github.com/xBLAGO/space/actions/runs/45330133409 (push). Финальный CI проверять по exact HEAD в https://github.com/xBLAGO/space/pull/45 и Actions. Завершённый результат и ссылки публикуются в описании PR без подмены исходного manifest.

Новых миграций нет. Изменения ограничены tools/tests/docs, CI и производным integration/candidate.json. Файлы src, UI HTML/CSS/JS, dependencies, Dockerfile и deployment workflow совпадают с базой. Существующие gates не повышаются.
