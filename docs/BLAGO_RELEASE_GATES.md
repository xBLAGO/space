# BLAGO — Release Gates: координационный аудит

Дата проверки: 18 ноября 2026. Статус: Draft, audit / architecture / planning only.
База этой документации: `3c218dc5757879c26b3aec520af3636b2d702715` (`main`). Проверенный исследовательский runtime: PR #37 `4706a60e7ec675ed1ae16b9c9cd551cde291cfd3`. Данные — только синтетические. Код, схемы, production и release gates не изменены.

Доказательства различаются: **CODE** — прочитанный код, **LOCAL** — исполненный в этом аудите тест, **CI** — проверенный существующий запуск Actions, **DESIGN** — предложение, **UNKNOWN / NOT EXECUTED** — доказательства отсутствуют. Проверка выполнена повторным анализом исходников и GitHub; независимый внешний аудитор и независимые операторы не участвовали.

## Решение по областям допуска

| Scope | Решение | Основание |
|---|---|---|
| Audit/documents | GO | Read-only анализ и синтетические проверки |
| Synthetic single-node development | GO в существующих ограничениях | #37 CI; feature flags OFF по умолчанию |
| Independent node A↔B | NO-GO до S12 | Отдельный оператор и custody не испытаны |
| Limited real nonfinancial pilot | NO-GO | G02–G06/G08/G10 не закрыты, G11 fail |
| Real capital/payments/production federation | NO-GO | Нет допуска; cross-node double-spend не решён |
| Merge этого Draft PR | NO-GO до отдельного guard | main push автоматически запускает HF deployment |

Таблица ниже — оценка допуска в рамках аудита, **не изменение существующих machine-readable gates**. Их исходные статусы и scope остаются в #23–26/#37. Например, G01 PASS в старых отчётах означает код candidate, а не безопасность реального main. G09 PASS относится к установленной fencing migration и действующим privacy controls, а не к старому HF Space.

## Унаследованные G00–G11 и необходимые доказательства

| Gate | Фактические доказательства | Оценка и незакрытое условие | Кто / тест допуска |
|---|---|---|---|
| G00 reproducible candidate | source manifests + exact-head CI synthetic | PASS ограниченной synthetic scope; production image provenance UNKNOWN | Release owner: signed/pinned source→image→config, review и stop drill |
| G01 deployment control | #19–37 manual workflow; main auto push-deploy | FAIL для безопасного merge в текущий main; Environment protection UNKNOWN | Отдельный Release Guard PR + владелец проверяет environment/branch protection |
| G02 SQLite/storage | CI patched SQLite и Docker steps success | UNKNOWN для реального HF volume/WAL/fsync/library | Оператор: точный Python SQLite binary/build, verified fix, power-loss/volume/restore evidence |
| G03 privacy recovery custody | synthetic journal/witness/checkpoint/old backup negatives | UNKNOWN независимое off-host хранение, утеря сервера/ключа, актуальность tip | Два оператора: backup+independent tip restore drill; fail-closed при потере |
| G04 lifecycle/retention | #24/#25 local TTL/deletion/holds + synthetic linkage attack tests | UNKNOWN внешние копии, policy sign-off, реальные operator run logs | Владелец утверждает цели/сроки; оператор проверяет каждый backup/log/provider/browser route |
| G05 devices/auth UI | CI virtual WebAuthn, desktop/browser/mobile emulation | UNKNOWN physical Telegram iOS/Android/passkeys/recovery | Согласованные тестовые устройства; не реальные пользовательские данные |
| G06 live LLM | scripted regression + bounded HF-compatible provider | UNKNOWN фактическое качество/latency/failure/retention маршрута; в этом аудите 0 live calls | Явно разрешённый test token/model, synthetic dialogues и human rubric |
| G07 Help integration | canonical full cycle, mutual confirmation, CI browser/isolation | PASS только synthetic; actual-user acceptance UNKNOWN | Два добровольца после остальных gates; permission/audience/dispute/stop checks |
| G08 security review | same-author negative/race/IDOR/CSRF/replay tests | UNKNOWN независимый review и broader inference/metadata attacks | Отдельный reviewer, threat-model closure, findings disposition |
| G09 cross-worker context fencing | #23 migration, durable revision/epoch, source37 tests/CI | PASS только установленной migration + current retained controls; live HF UNKNOWN | 2–3 independent processes, delete before commit, crash/replay/old backup, channel revoke |
| G10 operational pilot protocol | documented 3–5 volunteer plan | UNKNOWN полноценная репетиция admission/stop/rollback/delete/custody | Оператор+владелец: timed drill и escalation contacts |
| G11 real-user release | source runtime intentionally synthetic/local gated; legacy routes outside allowlist remain in code | FAIL / real users NO-GO | Отдельный reviewed nonfinancial deny-by-default runtime admission proposal + закрытые G02–G10; guard не отключать тестовым env |

Источники на exact runtime: `docs/integration02/gates.json`, `docs/privacy01_4/gates.json`, `docs/privacy01_2/gates.json`, `docs/help01_1/gates.json`; [integration gates](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/docs/integration02/gates.json#L1). Сохраняется различие PASS/FAIL/UNKNOWN/NOT EXECUTED; улучшение CI не повышает эти gates автоматически.

## SQLite: дополнительная проверка официальных оснований

[SQLite release3.51.3](https://sqlite.org/releaselog/3_51_3.html) прямо содержит исправление WAL-reset. В локальном тестовом процессе фактически загружена3.54.1 с source-id `2026-10-16 14:45:20 555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76`; он совпадает с [официальным release3.54.1](https://sqlite.org/releaselog/3_54_1.html) и pinned policy `core/sqlite_safety.py`. [Локальная runtime evidence](master_audit/local_environment.json) содержит путь загруженного extension. Это подтверждает среду теста, но не фактическую SQLite HF/container или свойства production filesystem. G02 остаётся UNKNOWN.

## Экономическая целостность Foundation09

| Инвариант | CODE / existing test evidence | Ограничение допуска |
|---|---|---|
| Один канонический источник | Help lifecycle, Asset Registry; UEL shadow adapters (`core/uel_local.py`) | UEL не подтверждает реальность вещи и не создаёт второй fulfillment |
| No local double-spend | capital versions, BEGIN IMMEDIATE/CAS, idem IDs, lots/reservations | Только единый локальный writer/storage domain, не global consensus |
| Declaration != available | CapitalReceived отдельно CapitalVerified, BudgetReserved отдельно CapitalSpent | Тестовые подписанты подтверждают synthetic evidence, не настоящие деньги |
| Approvals | Политика/сумма/назначение/версия/TTL, independent approvers, revoke/replay checks | #36 v1 single-approval нельзя объявлять equivalent #37 v2 |
| Governance v2 | Фиксированный electorate, quorum и security floors; delayed policy activation, bounded delegation | Sybil/полный сговор/утрата ключей/изменение состава не решены универсально |
| Refund/dispute/revoke | Events + deterministic replay корректируют проекции | Внешнее исполнение не осуществляется; double refund negative tests нужны при любом новом adapter |
| Exit/delete | #37 unlink participant, dependent grants revoked, others retained; legacy v1 differs | Псевдонимная история может позволять повторную идентификацию; policy approval отсутствует |
| Independent recomputation | `tools/independent_capital_verifier.py` отдельная реализация без runtime imports | Same-author, producer pins и node private HMAC binding остаются trust assumptions |
| Restore | UEL checkpoint/Privacy holds; stale snapshot не возвращает approval | Потеря независимого retained evidence = UNKNOWN_RECOVERY/hold, не reset |
| Stock/flow | Asset/Resource same object counted once; Help/Fulfillment не отдельная новая вещь | Неполные данные UNKNOWN; капитал симуляции не баланс сообщества |

Дополнительные domain anchors: [capital.py:284](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/domain/capital.py#L284) связывает fresh authenticated member с key_id, подписью, version/policy hash и idempotent operation; [capital_governance.py:21](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/domain/capital_governance.py#L21) валидирует supermajority, spend quorum, role separation, TTL и distinct keys; [storage.py:699](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/core/storage.py#L699) использует BEGIN IMMEDIATE. Эти проверки не устанавливают физическую независимость людей, держащих ключи.

Источники: [capital_schema.py](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/core/capital_schema.py#L1), [governance_schema.py](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/core/governance_schema.py#L1), [UEL](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/core/uel_local.py#L1), [verifier](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/tools/independent_capital_verifier.py#L1), tests/test_capital.py, tests/test_governance.py, tests/test_asset_registry.py, tests/test_uel*.py. Подробные ограничения #37: [readiness](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/docs/governance092/readiness.json#L1).

## Release / migration / rollback contract для будущих PR

1. Pin base/source/doc HEAD отдельно, manifest всех migrations/checksums и dependencies; не изменять старые checksum. Провести exact ancestry check до работы и перед review.
2. В disposable synthetic clone: upgrade каждой поддерживаемой версии, repeat migration, interrupted migration rollback, integrity_check/foreign_key_check, owner/link/history/memory preservation.
3. Потеря/коррупция SQLite, UEL index, journal/witness и независимого checkpoint по отдельности и совместно. Доказать no resurrection для deletion/revocation и отсутствие second spend.
4. Separate image build, source/image digest и runtime SQLite attestation; никаких startup migrations. В будущем deployment только по отдельному человеческому разрешению и exact artifact.
5. Rollback: остановить записи, сохранить независимо актуальные controls; старый executable допустим только если он понимает новую схему/holds. Нельзя просто вернуть старую SQLite. Если совместимость не доказана — hold и forward repair на проверенной копии.
6. Перед реальными людьми утвердить владельца данных, retention по категориям и external inventory, процедуру доступа/удаления/выхода, incident response и добровольное согласие. Условия провайдера не доказывают фактическое удаление данных BLAGO.

## Следующий конкретный PR

**Release Guard & Baseline Provenance**, от main `3c218dc5757879c26b3aec520af3636b2d702715`, с минимальным diff и тестом «push/PR не деплоит». Это снимает опасность случайного production изменения при любом дальнейшем merge. После него **Agent Context Reliability** поверх проверенной integration-линии даст наибольший эффект для пользователя. Нужны отдельные approval на merge/deploy, даже после зелёного CI. Независимый Node A↔B lab можно вести параллельно без миграции реальных пользователей.
