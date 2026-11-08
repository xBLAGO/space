# Evidence — Privacy 01.3

Runtime `9abb7cc7b22346be0a9b095e7f13b1dc81928e28`, exact base #23 `7807f5aa1c2d1818427057c6ce072ffb6166e3d3`. [Draft PR #24](https://github.com/xBLAGO/space/pull/24), [source CI](https://github.com/xBLAGO/space/actions/runs/42512292499). Документационный HEAD публикуется отдельно; не заменяет source.

| Артефакт | Происхождение / проверяемое утверждение |
|---|---|
| `source-identity.json` | `tools.integration_evidence` на exact source; tree/manifest/dependencies/SQLite/migration checksums; без operational qualification |
| `inventory.json` | `tools.lifecycle_inventory` на exact source: 56 table schemas новой синтетической DB; не production inventory |
| `rehearsal-local.json` | `tools.lifecycle_check` на exact source: expired need удаляется после old backup restore, recent Help сохранён, FK/integrity, no finances |
| `test-results.json` | 42 индивидуальных lifecycle scenarios из source CI; full regression counts и локальные результаты; фактические source assertions в `tests/test_data_lifecycle.py` |
| `reenrollment-replay.json` | Дополнительный audit finding: прежний update повторно принимался после удаления/регистрации. Воспроизведён FAIL, затем direct SQL guard, restore и synthetic Bot /start PASS |
| `ci-source.json` | Из GitHub API/jobs decoded logs: 432 tests, exact SHA, jobs/steps, image ID, artifact digest и синтетические browser/G09/lifecycle результаты |
| `gates.json` | Копия каждого прежнего gate с точной provenance; новый G04 остаётся UNKNOWN, G09 не расширен на real/legacy runtime |
| `evidence-index.json` | SHA256 committed evidence; это контроль целостности артефактов, **не** независимая аттестация правильности |

Полные журналы CI доступны по job links source run: tests `128827758592`, Docker `128827758863`. Artifact `13629597698` / `pilot-browser-evidence`, digest `sha256:50a551601709e7c4690215cccfe50639d2068499b0992e288ecbfddabba850ef`, expiry `2027-02-06T18:54:09Z`. Доступность GitHub logs/artifacts ограничена сроком платформы; поэтому selected machine-readable результаты сохранены в Git. Сырые logs/DB/journal/keys/cookies не приложены.

## Как воспроизвести

Команды — [RUNBOOK](RUNBOOK.md). Сверить exact source, `integration/candidate.json` и actual SQLite; после этого full unittest, lifecycle check, inventory, browser и Docker workflow. Не использовать предыдущие seeds/реальные user data. Новые сценарии задают возраст записей на synthetic DB, проверяют фактические удаления, replay, отказ unsafe actions и сохранение актуального состояния. Crash использует отдельный процесс, два maintenance workers — реальные spawn processes на одном host; это local integration, не independent infrastructure audit.

## Decision / что ещё нужно

**GO: isolated synthetic development. G04 UNKNOWN. Real pilot NO-GO.**

| Следующее действие | Ответственная роль | Требуемое доказательство перед PASS |
|---|---|---|
| Утвердить Data Lifecycle Matrix и сроки/основания/исключения | Privacy/product owner | Версионированное утверждение по всем SQLite/external категориям, права участников и legacy disposition |
| Разработать контролируемый scheduler, overdue alert и stop-процедуру | Runtime/operator | Target-host drill: deadline, outage, повтор, crash/restore, переполненный диск; отсутствие обслуживания после недоказанного restore |
| Учесть все copies и custody boundaries | Operator + independent custodian | Off-host recovery с отдельным управлением ключами и retained frontier; logs/exports/snapshots retirement evidence |
| Определить конкретный LLM route и processing obligations | Provider/privacy owner | Provider-specific retention/deletion/region/contract, synthetic test без production credentials; никаких предположений из общей HF страницы |
| Проверить re-identification metadata и deletion completeness | Независимый reviewer | Отдельный audit с проверкой журнальных owner/time tags, Help summary и legacy merges/economics |
| Закрыть прочие release gates | Release owner | Physical Telegram/passkey tests, live HF quality, operational admission/stop/rollback drill, approved runtime/image |

Ни один отсутствующий provider/physical/off-host результат не заменён на PASS. Никаких merge, deployment, production changes, real payments или изменения webhook.
