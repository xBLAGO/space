# Foundation Integration 01 — isolated nonfinancial candidate

**Draft. GO для последующей изолированной разработки Foundation Help 01.
NO-GO для реальных участников, production или deployment.**

## Проверенная база и границы

Перед началом свежие GitHub GET подтвердили неизменные main, #10, #18 и цепочку
#3–#7/#9. Main: `3c218dc5757879c26b3aec520af3636b2d702715`; audit #18:
`6f83b66db0ef8fc87a93dd0a7de70fdcecca4428`; source #10:
`d11a2a555efe57e6818450d534655020bf47b9ca`.
Новая ветка `integration/foundation-01-06-1` создана непосредственно от #10.
Все runtime source pins, migration versions/checksums, dependencies, code/workflow
hashes и schema hash: [candidate manifest](../integration/candidate.json).
Audit — reference, sibling of runtime; его commit не объявлен runtime ancestor.

Главная корректировка аудита: в **main** deploy запускается при push, но в exact
#10 он **уже** переведён на workflow_dispatch. Integration переиспользует это
решение, усиливает его SHA/default-deny/environment guards и не создаёт второй
независимый release mechanism. Main/production по-прежнему не изменены.

Foundation 01–06.1 входят существующим Git ancestry; никаких merge/cherry-pick
экспериментальных веток. Sovereign Identity, Genesis, federation, wallets/Solana
и новые financial operations отсутствуют. Старый payment simulation code остаётся
как совместимость источника; guarded HTTP integration surface блокирует весь
`/api/v1/economy` до выполнения операций. Реальных переводов нет ни в одном пути.

## Что реализовано

- Новый `xblago-integration` и Docker CMD с обязательным `synthetic` mode. Без
  заранее подготовленной SQLite и обязательных secure/privacy/pilot flags отказ.
  Никакой автоматической migration/init/rebootstrap или чтения legacy JSON из HOME.
  В synthetic mode конструкторы SQLiteBlagoStore/WebApp также проверяют baseline
  вместо выполнения legacy startup DDL.
- До запуска listener проверяются актуальная SQLite библиотека, full structural
  schema, frozen 05.x DDL/checksums, integrity/FK, original journal и retained
  witness. Guarded writer replay-ит уже durable denials до обслуживания запросов.
- Базовые legacy таблицы сравниваются структурно, включая columns/defaults/PK/FK/
  unique indexes. Это сохраняет совместимость старого main с ALTER-added колонкой
  response_json, порядок которой отличается от fresh CREATE. Для versioned 05.x
  migrations остаётся exact SQL verification. Потерянная таблица не создаётся
  заново ради успешного старта.
- Explicit synthetic provisioner принимает только **новый** root; повторное
  использование существующего root отклоняется. Он не является production migration
  tool. Journal и witness в одном тестовом volume — simulation, не offsite custody.
- Deployment policy: только manual event, refs/heads/main, exact reviewed SHA,
  confirmation DEPLOY, opt-in repo variable BLAGO_DEPLOYMENT_ENABLED=true и
  `production-release` environment. Pure guard повторяет проверки до credentials.
  Push/merge/PR не вызывают deployment. Environment reviewer/protection settings
  **не настраивались и UNKNOWN**; код не выдаёт их за подтверждённый approval.
- Foundation Help 01: dataclasses/Protocol для exact-text/audience/revision approval,
  publication/withdrawal/offer/accept/completion. Нет реализации, storage, routes,
  LLM tools, финансов или автоматического превращения разговора в действие.
- Runtime dependency versions зафиксированы lockfile; Python base pinned по digest
  `sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704`,
  ранее реально использованному [source CI #10](https://github.com/xBLAGO/space/actions/runs/40075042228),
  Docker job 120816478070. SQLite source/archive hash проверяются существующим builder.
  Это не утверждение о byte-identical hermetic rebuild: apt repositories, wheels и
  build tools не снабжены полным content-addressed mirror. Actual image ID — в CI.

Legacy entrypoints сохраняются для regression compatibility. Они **не являются**
разрешённым способом обойти integration guards и запустить реальный pilot. Этот
новый launcher намеренно блокирует live HF; отдельная будущая review-процедура
потребуется для provider acceptance. Ни одна настройка рабочего бота не изменена.

## Воспроизводимость

Python 3.12 с проверенной SQLite 3.54.1/source-id из `sqlite_safety.py`; на Linux
использовать существующий tools/build_sqlite.py и LD_LIBRARY_PATH, как в CI.
Все команды ниже относятся только к новому synthetic окружению.

```sh
python -m pip install -r requirements-integration.lock
python -m pip install --no-deps -e .
PYTHONPATH=.:src python -m tools.freeze_integration
PYTHONPATH=.:src python -m unittest discover -s tests -v
PYTHONPATH=.:src python tools/integration_fixture.py --new-synthetic-root /tmp/blago-new-integration
```

Последний путь должен отсутствовать. `public-config.json` содержит пути и required
synthetic settings, без session/key/credential data. Operator задаёт эти значения
в отдельном процессе, а также synthetic invite/bot token, выключенные startup
Telegram checks и webhook registration для localhost smoke. Не направлять launcher
на существующее пользовательское хранилище. Результат `preflight` не доказывает,
что оператор не подставил реальные данные вместо synthetic: это scope discipline,
а не определение «синтетичности» содержимого по криптографии.

Полное воспроизведение image/guarded HTTP smoke и Chrome virtual authenticator —
в `.github/workflows/tests.yml`. Default image без config обязан завершиться с
ошибкой. CI затем provision-ит новый temporary volume, запускает guarded surface,
проверяет health/PWA и прежние identity/privacy/repair smoke через явные test entrypoints.
Последние — compatibility tests, а не production launcher bypass authorization.

`tools.freeze_integration --write` обновляет manifests только в отдельном review
после code/schema change; CI использует read-only check и отказывает при drift.
`tools.integration_evidence` требует clean code/manifest, проверяет source ancestry,
фиксирует exact candidate SHA, CI checkout SHA/tree (включая virtual PR merge tree),
installed dependencies и SQLite runtime. Финальный SHA нельзя включить в hash самого
себя: он фиксируется Git/PR/CI отдельно от content manifest.

## Миграции и recovery

Новых production migrations нет. Existing order:

1. Инициализированный legacy store + WebApp schema на offline copy.
2. foundation05_identity_001.
3. foundation05_runtime_002.
4. Original journal binding + foundation05_privacy_003.
5. Reviewed prospective lineage baseline + foundation05_pilot_004 + retained witness.

Повторный запуск не меняет schema; версии/checksums приведены в manifest. Нельзя
создать новый journal/key для старой базы или обозначить произвольный backup как
reviewed baseline. Старый backup после восстановления обязан reconcile-ить текущие
memory/session/passkey/link denials; sessions/challenges сбрасываются, Telegram
quarantine сохраняется. Независимость actual failure domains остаётся UNKNOWN.

Новые tests используют exact `git archive main` для legacy fixture; проверяют ID,
profile/store/history/memory/update dedupe, repeat migration и restore после delete.
Прежние tests покрывают Web/Telegram linking, WebAuthn replay/races, rollback,
user isolation, provider failure, old backups и journal/witness/key loss.

## Проверки и gates

Локально: **315/315 tests PASS**, 114.551s; compileall, pip check, diff check PASS.
[Local evidence](integration01/local-tests.txt).
[Chrome 150 virtual CTAP2/UV](integration01/browser-results.json): 8/8 PASS,
registration, cookie rotation/old-token denial, login, revoke UI и export, без page
errors. Physical authenticators/biometrics, Safari/Firefox и actual Telegram WebView
не проверялись. Local Docker CLI отсутствует; результат образа берётся только из
фактического CI, не из предположения о build.

[Machine-readable G00–G11](integration01/gates.json). G01 PASS означает кодовую
границу candidate, не configuration audit production environment. G00 требует
реального candidate CI/image evidence. G02–G06/G08–G11 содержат UNKNOWN для
непроверенного operational scope. G07 FAIL: Help 01 ещё не реализован.
Реальный pilot NO-GO даже при полном зелёном CI.

CI и exact source/candidate SHA: дополняются проверенными ссылками в Draft PR;
source commit и последующий evidence-only commit различаются явно.

## Безопасный rollback

Не выполнялся. До real writes можно отбросить disposable synthetic volume.
После любых future writes: stop intake/writers, сохранить app snapshot и все
текущие независимые controls; восстановить только offline copy, replay denials,
сбросить sessions/challenges, проверить integrity/FK и revoked-link/passkey/memory
negatives, затем использовать schema-compatible image или forward-fix.
Нельзя откатить journal/witness вместе с app, выключить guards, понизить seq/head,
реинициализировать ключ или восстановить удалённую память. Raw main image не
является безопасным writer rollback для мигрированной базы. После account merge
нужен полный reviewed reconciliation, а не восстановление одной таблицы users.

## Следующий этап

GO только для изолированной реализации Foundation Help 01 с reviewed confirmation,
аудиторией, отзывом/отказом, конкуренцией accept, nonfinancial fulfillment и новыми
privacy/retention/restore hooks. Затем worker topology/global-lock qualification,
independent custody drill, live provider/device acceptance и environment policy
review. Production, actual participants, merge/deploy остаются NO-GO/не разрешены.
