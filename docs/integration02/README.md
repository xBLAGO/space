# Evidence — Integration02

Exact base#25 `0772e5bc5919aefac2c209f4e40fe5ea790d6792`; source `395ecf10f9187663fddb64795597e7efe0f014fa`. [Draft PR26](https://github.com/xBLAGO/space/pull/26), [source CI42680305703](https://github.com/xBLAGO/space/actions/runs/42680305703). Documentation HEAD проверяется отдельно и указывается в PR.

| Файл | Scope |
|---|---|
| pr-inventory.json | Actual GitHub PR10–25 base/head/state/file list/HEAD checks, public metadata snapshot |
| repository-audit.json | Независимый read-only Git ancestry/tree/main diff и conflict simulation |
| source-identity.json | Git/source manifest/dependencies/SQLite/migrations; no deployment approval |
| journey-local.json | One SQLite cross-channel HTTP/Bot/synthetic Mini App path38 + crash/replay/recovery; synthetic text only |
| browser-local.json / mobile-local.json | Local Chrome17 и synthetic Mini App14 checks; not physical WebView |
| main-upgrade.json / main_upgrade_check.py | Reproducible synthetic main-seeded upgrade and safe refusal of legacy out-of-order Help rerun |
| public-site.json | Unauthenticated live GET statuses/hash/signals; no source/deployment attestation |
| provider-status.json | Explicit test credential approval absent;0 external requests; NOT EXECUTED |
| test-results.json / ci-source.json |472 full regression,8 added tests,40 dialogue scenarios, Docker/image/artifact/browser/ops/G09 results |
| gates.json | Inherited security gate scopes + readiness gaps; G04 UNKNOWN/G11 FAIL |
| evidence-index.json | SHA256 artifact index, not independent attestation |

Source CI jobs: tests129372724288, docker129372724455. Artifact13702596677 / pilot-browser-evidence, digest `sha256:2b6384eba0edfd1489dd1e379ec6e2694de9ef288fd52633b20d8f45a156ab5b`, expires2027-02-07T19:07:27Z. Expiry не proof of deletion; raw DB/journal/keys/cookies в PR не публикуются.

Первый draft journey ожидал нормализованное «Казань», но memory сохраняет дословное «Казани»; исправлен test expectation, runtime не менялся. Первоначальный upgrade harness слишком рано включил REQUIRED flags; safe refusal сохранён, порядок offline activation исправлен. Legacy Help stack-idempotence limitation **не** скрыта как успешный повтор. Full local472/472 и source CI472/472 относятся к итоговому source.

Все наблюдения различают: implemented code, local synthetic verification, integrated Docker/browser verification, real target-host/provider/operator/physical evidence. Последние остаются UNKNOWN/NOT EXECUTED. Нет независимого security audit или реальных участников. Реальный пилот NO-GO; техническая база пригодна для дальнейшей изолированной доработки.
