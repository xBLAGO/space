# Evidence index — Privacy01.4

Runtime source и base указаны в `source-identity.json`/основном отчёте. [Draft PR #25](https://github.com/xBLAGO/space/pull/25) строго поверх #24. Source CI: [42525416875](https://github.com/xBLAGO/space/actions/runs/42525416875).

| Артефакт | Что доказывает / что не доказывает |
|---|---|
| `source-identity.json` | Exact source/tree, dependency lock, actual SQLite, прежние app migration checksums; не production attestation |
| `operator-schema.json` | Отдельная version/checksum/DDL и default scheduler settings; не app migration и не owner policy approval |
| `test-results.json` | 464 full CI, 32 индивидуальных новых сценария, local results с сохранёнными failures/rechecks |
| `operations-local.json` | 11 expected/actual local scenarios и synthetic linkage probe; same-author/local integration |
| `cli-local.json` | Реальный запуск CLI в новом fixture: explicit init, OFF без очистки, одна enabled очистка и WAITING status |
| `clock-observations.json` | Метаданные длительного wall-clock разрыва двух локальных попыток; это не запись возраста конкретных сессий. Финальный retry выполняется с temporary caffeinate без изменения auth/power settings |
| `ci-source.json` | Jobs/steps/log summaries, Docker image identity, browser/synthetic Mini App, operations evidence и artifact metadata |
| `external-inventory.json` | Документированные controls / наблюдённая metadata / UNKNOWN actual state; не доказательство remote erasure |
| `gates.json` | Точная inherited provenance каждого gate; G04 UNKNOWN и реальный пилот NO-GO |
| `evidence-index.json` | SHA256 committed artifacts; контроль целостности файлов, не независимое подтверждение результата |

CI source jobs: tests128867859323, Docker128867859018. Synthetic artifact13635600278 / `pilot-browser-evidence`, digest `sha256:c9b77b047bfba18980b44b7e46492babdfa353bf3340b7cf910049200b69b531`, expiry2027-02-06T21:28:59Z. API metadata относится к конкретной копии; доступность и expiry не являются доказательством удаления. Полные decoded logs доступны в Actions; сырые DB/keys/cookies/journal не публикуются.

## Воспроизводимость и правила оценки

Сначала exact ancestry/source и `tools.freeze_integration`, actual verified SQLite, затем команды [runbook](RUNBOOK.md). Rehearsal создаёт новые users/keys/files и удаляет synthetic temp root после завершения. Host trust boundary, virtual WebAuthn, synthetic Telegram SDK/HMAC и две локальные программы не доказывают работу независимых hosts/операторов или physical devices.

Проверки negative сценариев должны подтверждать **отказ**, а не успех операции: missing witness -> UNKNOWN_RECOVERY, orphan backup -> REVIEW_REQUIRED, exhausted retries -> EXHAUSTED, old app snapshot -> deleted data absent, crash -> INTERRUPTED/no success. Positive reidentification test ожидает возможность linkage; его PASS означает выявленный риск, не анонимность.

## GO/NO-GO

**GO: дальнейшая изолированная разработка. NO-GO: ограниченный реальный пилот.** Policy approval, target-host scheduling/alert delivery/storage guarantees, external-copy/provider obligations, independent custody/review и physical/live-provider gates не выполнены. Полная таблица каждого gate — в основном отчёте и JSON. Не переименовывать UNKNOWN в PASS на основании успешного scheduler tick.
