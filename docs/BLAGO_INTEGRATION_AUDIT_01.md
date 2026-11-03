# BLAGO Integration Audit 01 — Foundation Dependency & Integration Readiness

**Draft; 2026-11-03 UTC. Verdict: NO-GO для реального MVP сегодня.**
Исследовательская ветка `docs/integration-audit-01` от exact main
`3c218dc5757879c26b3aec520af3636b2d702715`. Изменяются только четыре документа.
Merge, deployment, реальные данные, production SQLite, секреты и активные API
не изменялись. Этот аудит независимо перепроверяет исходные данные и код прежних
этапов; авторская проверка не является внешним независимым security audit.

## 1. Ответ на главный вопрос

Объединению мешает не отсутствие ещё одного слоя identity/federation. Основные
разработки **не включены в main**; их безопасное принятие требует решения по
интеграционной базе и release-процессу. Минимальный полный runtime-кандидат —
кумулятивный #10 / `d11a2a555efe57e6818450d534655020bf47b9ca`, а не main,
Genesis или 08.2. Даже этот кандидат ещё не обеспечивает проверенный реальный
межличностный нефинансовый цикл помощи и эксплуатационные гарантии 05.4/06.1.

Блокеры ограниченного пилота:

1. Неподтверждённая целевая инфраструктура: фактическая SQLite, POSIX/WAL/fsync,
   внешнее хранение journal/witness/ключей, восстановление при потере сервера.
2. Нет принятой политики retention/delete для участников с экономическими
   событиями; реализация намеренно блокирует такое удаление.
3. Нет native нефинансового пути discovery/offer/accept/fulfill между участниками.
   `community` на need/resource не даёт другому участнику read/discovery API.
4. Не подтверждены качество, доступность и конкурентная работа реального LLM
   на выбранном release candidate. Синтетические provider mocks этого не доказывают.
5. Нет сквозного acceptance на реальных поддерживаемых устройствах Telegram/WebView
   и подтверждённого release/rollback rehearsal. Main автоматически запускает
   HF deployment при push, поэтому принятие кода и rollout сейчас связаны.

Точные критерии снятия: [release gates](BLAGO_MVP_RELEASE_GATES.md).
Федерация и Sovereign Identity не нужны для первого централизованного добровольного
пилота и не должны расширять его полномочия.

## 2. Метод, inventory и доказательства

Свежие GET GitHub API: все PR state=all, все ветки, изменённые файлы каждого PR,
Actions по exact head SHA, job steps и выбранные logs. Вторая страница collections
пустая; пагинация каждого PR files/run не достигла 100. Получено **16 PR**,
**19 веток**, **15 open / 1 closed unmerged (#8) / 0 merged**.
[Полный inventory, SHA, файлы, CI и граф](BLAGO_FOUNDATION_DEPENDENCY_MAP.md).
Не использовать виртуальный `merge_commit_sha` как свидетельство принятия PR.

Git objects сверены локально с API SHA; для 15 доступных PR выполнен
`git merge-base --is-ancestor base head`: true, `head main`: false.
Object закрытого #8 отсутствовал; его один HTML diff проверен API, отдельного
локального ancestry-доказательства для него не заявляем. Source архивы main,
#12, #13, #14 изучены через `git archive`; никаких пробных merge не выполнялось.

| Evidence | Получено в этом аудите | Что не доказывает |
|---|---|---|
| Main suite | 27/27 PASS, 0.408s, exact `3c218dc…`, Python 3.12.10 | Пригодность новых модулей, которых нет в main |
| PR #12 cumulative suite | Итоговый результат — раздел 9; exact `b79cb603…`, patched SQLite 3.54.1 | Принятие #12 или production readiness |
| Новый main→#12 upgrade probe | ID, memory, history, Telegram dedupe сохранены; 05.1→05.2→05.3→05.4 повторно идемпотентны; delete→old backup restore не воскресил факт; integrity_check=ok, foreign_key_check=[] | Полную историю прежних реальных удалений и merges |
| Два синтетических участника | Community need A отсутствует в `rows('needs', B)`, community resource B отсутствует у A | Отсутствие будущего модуля; это текущий ограниченный API |
| #12 head Actions | [40568551246](https://github.com/xBLAGO/space/actions/runs/40568551246): tests и Docker SUCCESS; logs: 344 tests, SQLite 3.54.1, smoke identity/privacy/natural/sovereign | Фактическую deployed image или реальную биометрию |
| #9 head Actions | [40016287991](https://github.com/xBLAGO/space/actions/runs/40016287991): Chrome profile button timeout; Docker SUCCESS | Системный дефект WebAuthn crypto; это несовместимость UI acceptance selector |
| #10 correction | Diff открывает tools drawer, [40075042228](https://github.com/xBLAGO/space/actions/runs/40075042228) SUCCESS | Реальные iOS/Android Telegram WebView |
| 08.1 / 08.2 | [41704965826](https://github.com/xBLAGO/space/actions/runs/41704965826), [41714974472](https://github.com/xBLAGO/space/actions/runs/41714974472): 73 vectors/reference и 35 lab tests SUCCESS | Совместимость с application SQLite или независимых операторов |

Live HF, production filesystem, реальные пользователи, physical authenticators и
WAN-узлы в этом аудите **не тестировались**. Прежние JSON artifacts используются
только как исторические наблюдения с пометкой, не как текущая аттестация.

## 3. Точное сравнение main и целевой архитектуры

Main содержит 25 tracked paths, включая три test modules; #10 меняет относительно
main 89 путей (15,551 insertions / 183 deletions), #12 — 98 путей. Количество
файлов не является оценкой качества. Целевой MVP ниже — предлагаемый scope,
частично реализованный в ветках, а не утверждение о готовом target build.

| Компонент | В exact main | В существующих ветках | Требуется для целевого MVP |
|---|---|---|---|
| Пользователь | UUID `blago_user_id`, users/profiles/channel_links | Тот же ключ в Web, sessions и Identity Store | Сохранить IDs; проверить linking и восстановление |
| Каналы | CLI, Telegram, webhook health | Web/PWA/Mini App в #2+, общий store | Один authenticated principal и согласованная история |
| Auth | Telegram Bot transport; Web/session отсутствуют | Password, initData challenge, passkeys, CSRF в #5+ | Secure Identity migrations mandatory, без legacy fallback |
| Память | key/value memory_facts, явные remember/forget | Semantic versions, correction, veto, filtering в #3+ | Scoped исправление/удаление, проверенный consent и tombstones |
| Диалог | `_append_history` отдельно для двух ролей; greeting templates | #10 atomic exchange, history projection, intent guidance, bounded repair | Качество/latency/live failure acceptance, multi-user continuity |
| Потребности/ресурсы | Таблиц и Web API нет | `blago_needs`, `blago_resources`, private/community, own lists | Voluntary cross-person nonfinancial lifecycle; не подменять payment simulation |
| Экономика | Нет | Assets/offers/payment requests/fulfillment/contribution rows | Real transfers OFF; история без рейтинга или фиктивной банковской проверки |
| Privacy recovery | Raw delete, локальный SQLite backup semantics | Journal/outbox/replay/witness/custody/TTL #6/#7 | Независимое хранение, approved retention и actual restore drill |
| Sovereign Identity | Нет | #12 flags OFF, explicit isolated_test; session≠human authority | Не требуется; сохранить OFF |
| Коллективные предложения | Нет | Genesis #14 отдельный event/publication store | Для минимального MVP не требуется; не копировать personal memory в public |
| Федерация | Нет | #16 offline + #17 filesystem lab на docs-ветке от main | Не production-зависимость; только отдельная лаборатория |
| CI/release | Только HF deploy workflow на main push | tests+Docker+virtual browser в runtime stack | Разделить одобрение кода, миграции и rollout; gates перед доступом людей |

Проверяемые исходники main: [storage:28][M-store], [agent:66][M-agent],
[deploy workflow:3][M-deploy]. Целевая branch-карта не означает, что deployed
Space соответствует main или любому PR: фактический image digest здесь UNKNOWN.

## 4. Совместимость идентификации и авторизации

**Identity Store → WebAuthn: Partial, реализовано и проверено в synthetic scope.**
05.1 не создаёт второй user store: переиспользует channel_links/web_sessions,
добавляет устройства, credentials, challenge, consent/security/activity tables.
05.2 расширяет существующие web_sessions и строго проверяет checksums; нет
автоматической миграции на старте. [05.1 DDL][C-identity-schema],
[05.2 migration][C-runtime-schema], [session security][C-session].
WebAuthn library `webauthn==2.7.1` проверяет origin/RP/signature и требует UV;
биометрические шаблоны не хранятся. UV не означает уникального физического человека.

**Telegram Bot / Mini App / Web: Partial.** Binding идёт через существующий
blago_user_id; initData exchange использует одноразовый challenge и proof ledger
в одной транзакции. Повтор открытия с новым initData допустим; повтор того же
proof отвергается. [Telegram exchange][C-tg-auth]. TelegramBot перечитывает shared
history; WebApp использует тот же store. Recovery сбрасывает sessions/challenges
и вводит Telegram quarantine. Проверено mocks/HTTP; actual Telegram WebView UNKNOWN.

**Identity Runtime → Sovereign: deliberate restriction, не конфликт схемы.**
[Sovereign guard][C-sovereign] требует отдельный flag, isolated_test, patched
SQLite, journal/witness и fresh session. Управление key требует подписи controller;
[merge][C-merge] отказывает при существующем core/hold. Нельзя обещать слияние двух
crypto identities обычным Telegram linking. Agents получают только allowlisted
boolean capabilities, не keys или memory другой личности.

07.2 [activation][R-activation] всегда fail-closed: recovery ceremony собирает
подписи/уведомления, но не меняет live authority. Это не универсальное восстановление
потерянного owner key. Оставить #12/#13 OFF в MVP; пароль/Telegram могут восстановить
доступ к приложению, но не чужое криптографическое владение.

## 5. Память, диалог и интерфейсы

Semantic `need` — кандидат личной памяти, не строка blago_needs. Extractor имеет
отдельный bounded provider pass только после conservative may_extract; regex и
relation checks дополняют model output. Это не «один LLM call всегда».
[Extractor][C-extractor], [agent save path][C-agent-memory], [history exchange][C-history].
06.1 intent hints ephemeral и не авторизуют действия. Repair ограничен одной
попыткой генерации; transport failure не запускает бесконечный retry. Реальное
качество ответа/knowledge correctness остаётся отдельным gate.

UI #9 скрывает tools drawer и убирает frontend greeting, но сломал старый browser
acceptance selector. #10 исправляет именно selector. Это конкретный integration
дефект, уже исправленный в child; не переносить красный #9 отдельно без исправления.
PWA имеет manifest и public-shell icons cache; [service worker][C-sw] не кэширует
API. Offline personal assistant, durable offline send queue и conflict resolution
не реализованы. Слово PWA не должно создавать обещание офлайн-работы.

Общий [conversation_lock][C-lock] — process-local RLock одного store; Web и Telegram
держат его во время LLM call. Следствие по коду: медленный user A задерживает B;
несколько процессов/несколько store objects не имеют общей context serialization.
Нужен нагрузочный/ordering test и выбранная worker topology. Это риск throughput
и stale context, не установленная утечка чужой памяти.

Коллективные утверждения не равны personal semantic memory. Genesis #14 хранит
собственный typed/versioned event stream, previews/approval/publications и дополняет
privacy replay. [Genesis journal guard][G-guard]. Но это sibling #13, с собственным
HMAC/journal trust и без ATTEST независимых узлов. Полноценная ISTINA с независимым
подтверждением/оспариванием не реализована. Для первого MVP не включать этот scope.

## 6. Экономика, потребности и будущая федерация

[Own rows/create_item][C-items] создают долгоживущие потребности и ресурсы;
`visibility=community` сохраняется, но list endpoint всё равно фильтрует owner.
[Community analytics][C-analytics] даёт counts, не discovery и не разрешение
читать профиль/need другого человека. Воспроизведено на двух synthetic principals.

Существующий [matching][C-matching] выбирает available community **external_payment**
resource offers, фильтрует amount/currency и случайно перемешивает candidates.
[Accept][C-accept] использует CAS status, [simulate][C-simulate] — явную симуляцию;
manual confirmation не является bank attestation. Это не generic volunteer-help
workflow. Отдельные blago_resources и blago_resource_offers/благо assets — разные
модели: описание наличия ≠ доступное предложение ≠ обязательство ≠ исполнение.
Их нельзя слить по совпадению текста/amount.

Contribution rows могут служить локальным свидетельством подтверждённого действия,
но не подписью участника/независимым ATTEST. История в SQL с event_key обеспечивает
локальную идемпотентность конкретных transitions, а не универсальный подписанный
Event Envelope. `events` (Telegram dedupe), identity_events, privacy HMAC journal,
Genesis events и 08.2 inbox ledger имеют разные semantics и trust roots.
Нельзя назвать их одним federation event log без явного adapter/мандата.

Риски распределения: случайность не доказывает fairness, eligibility/payment-risk
filters не измеряют человеческую потребность; валютные/суммовые ограничения могут
исключать людей из неподходящей категории помощи. Нет доказанных Sybil controls,
appeals или fairness evaluation. Для MVP без автоматического распределения помощи
достаточно добровольного выбора человеком и прозрачных локальных правил.

Будущий adapter должен связывать immutable public event ID и scoped consent с
локальным object ID, сохранять provenance/withdrawal/retention, не экспортировать
sessions/secrets/private memory. Не генерировать human-signed mandate из chat intent.
08.2 принимает только inert `note.deliver`, epoch 1, one-use; это не выполнение
needs, contribution attestation или actual sovereign account migration.

## 7. Разрывы и приоритеты

| ID | Приоритет / обнаружение | Риск | Требуемое решение |
|---|---|---|---|
| IA01 | P0, ancestry + API | Разработки отсутствуют в main; нет принятой сборки | Freeze exact release candidate и review cumulative chain |
| IA02 | P0, deploy workflow | Push main сразу запускает deploy без новой approval границы | Отдельный release approval и migration rehearsal до принятия runtime |
| IA03 | P0, privacy/witness code + отсутствие live attestation | Correlated rollback/неподходящий FS разрушает recovery assurance | Независимые control domains, escrow и actual lost-server drill |
| IA04 | P0, deletion_blockers | Участник с любым экономическим следом не может полностью удалить аккаунт | Принять retention policy и реализацию minimization, или исключить такой scope до людей |
| IA05 | P0, новый probe + route scan | Человек не может найти общую нефинансовую потребность/предложить помощь native | Explicit consent-driven nonfinancial lifecycle; не открыть private lists |
| IA06 | P0, provider code / historical failure evidence | LLM unavailable/unfinished/плохие ответы при реальных ожиданиях | Live synthetic + consenting-user acceptance на exact candidate |
| IA07 | P1 → P0 при Telegram-only login | Непроверенные реальные WebViews/authenticator recovery | Поддерживаемая device matrix + альтернативный подтверждённый доступ |
| IA08 | P1, lock code | Общая очередь LLM и process-local ordering | Multi-user/cross-channel load + cancellation/serialization decision |
| IA09 | P1, schema implementation | Startup executescript legacy, отдельные explicit migrations, unknown FK guard | Единый versioned manifest, expected schema snapshots и rollback policy |
| IA10 | P1, config guard | Secure runtime остаётся optional до schema/require flag | Fail closed deployment contract, нельзя оставить legacy mode у реальных людей |
| IA11 | P1, package/Docker source | Floating Python base и dependency ranges, тесты только 3.12 | Pin image/dependency manifest + actual-library attestation |
| IA12 | P2 для MVP, P0 для federation | Нет federated identity/mandate/import/current revocation adapter | Отдельные conformance/independent operators gates; 08.2 не переносить в app |
| IA13 | P2 для MVP, P0 для public deliberation | Personal/Genesis/public proof scopes различны | Отдельное approved disclosure/withdrawal/ATTEST проектирование |
| IA14 | P1, два analytics code paths | Small-cohort suppression есть только у нового privacy endpoint | Единая политика и regression старого community endpoint |

Не обнаружено доказанного конфликтующего DDL имени между 05.1/05.2/07.1: проверены
порядок migrations, checksum guards и explicit reuse. Отсутствие text conflict
не доказывает semantic совместимость siblings. #13+#14 совместно не запускались.

### Несогласованные и повторяющиеся контракты

| Контракт | Фактическое различие | Интеграционное решение |
|---|---|---|
| Аналитика | `PrivacyService.analytics` (integrations/privacy.py:158–184) подавляет cohort <5, но старый `WebApp.analytics(community=True)` (web.py:613–624) выдаёт точные community counts и demand sum без такого порога | P1: согласовать единый privacy contract; проверить inference на малой группе, не считать новый endpoint исправлением старого |
| Auth fallback | IdentityRuntime.enabled зависит от migration table; без required flag остаётся legacy путь | P0 gate конфигурации: один secure runtime contract, отсутствие схемы закрывает доступ |
| Human identity vs session | WebAuthn/account login подтверждает вход; Sovereign controller proof отдельно; 08.2 Human keys вообще synthetic и не связаны с production ID | Не конвертировать одну authority в другую, не требовать crypto core для обычного login |
| Схема | Legacy SQLiteBlagoStore/WebApp используют startup executescript; 05+ — explicit checksum migrations | Нужен единый manifest/ordering/recovery, не второй ORM/user store |
| Личные и общие факты | memory_facts — active projection; semantic_memory — version history; Genesis — отдельная публикация | Это разные жизненные циклы, не дубли для механического объединения |
| Описание ресурса и обязательство | blago_resources, blago_assets и blago_resource_offers различаются permissions и economic context | Не объединять по имени; generic help требует отдельного согласованного перехода |
| Журналы | SQL dedupe/event_key, HMAC privacy journal, Genesis stream и signed lab receipts имеют разные trust roots | Не подписывать старый SQL export задним числом как будто он human-attested |

Подавление малых когорт на новом endpoint не распространяется автоматически на
legacy route; это выявленный по коду integration gap, а не доказанная атака или
утечка личности. Для будущего пилота решить его до обещания privacy-preserving
community analytics. API/production-код этим аудитом не исправляется.

## 8. Сохраняющиеся блокеры 05.4 и 06.1

05.4 закрывает кодовые механизмы, но проверка пути witness вне journal tree не
доказывает отдельный диск/IAM/rollback domain. [Witness sync][C-witness] использует
тот же journal MAC key; это retained control copy, а не независимо подписавший
человек или Byzantine-resistant узел. [Custody restore][C-custody] отвергает старый
DR package относительно актуального witness; потеря всех контрольных копий/ключей
остаётся fail-closed, recovery не придумывает старые удаления.
[Legacy deletion plan][C-delete-plan] блокирует неразобранные merges. Эти меры нужны
централизованному MVP независимо от федерации.

[Официальная SQLite WAL документация](https://sqlite.org/wal.html)
указывает исправление WAL-reset в 3.51.3 и позже, с отдельными backports.
Код #7+ проверяет конкретные 3.54.1/source-id; это подтверждено локальным shim и
CI Docker, а не актуальной библиотекой production host. Оставить gate обязательным;
переход на другую исправленную версию потребует обновления reviewed attestation.

06.1 реализовал историю/repair/intent separation. В историческом raw
`validation06_1/hf-after.json` на exact #10 видны четыре timeout turn и отдельный
неуспешный pressure call (всего 5 HTTP failures на 13 вызовов); live repair не
подтверждён. Примеры фактических объяснений и формальной эмпатии требуют review.
Это исторические synthetic outputs, не утверждение о сегодняшней доступности HF.
Новой live-проверки нет: текущие availability/quality остаются UNKNOWN, а gate OPEN.

Дополнительные требования федерации (independent operators, signed mandates,
remote revocation freshness, correlated rollback domains, two-node interoperability)
не являются оправданием задержать исправление перечисленных MVP проблем и не
должны быть скрыто объявлены решёнными privacy HMAC-журналом.

## 9. Воспроизведение и пределы проверки

Архивы под `/tmp/blago-integration-audit-01/`; реальные файлы БД не копировались.
Unit/integration suite использует temporary SQLite, mocked providers и localhost.
Import source фиксирован exact `b79cb603…`; patched `_sqlite3` соответствует
3.54.1/source-id `2026-10-16 14:45:20 555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76`.

```sh
# Из Git checkout, содержащего исторические commits для fixture git archive:
# SNAPSHOT — новый disposable git archive exact b79cb603…; не production checkout.
PYTHONPATH="$SQLITE_SHIM:$SNAPSHOT:$SNAPSHOT/src" python -m unittest discover -s "$SNAPSHOT/tests" -v
```

Первый sandbox-прогон получил EPERM на localhost; архив без Git context сломал
исторический fixture git archive. При запуске из main checkout без snapshot root
в PYTHONPATH также не импортировался tools.pilot_fixture (302 вместо 344 tests).
Это ошибки harness, не замаскированные PASS. Исправленный окончательный прогон
с root/source path и socket permission записан ниже. Никакой код тестов не изменён.

**Окончательный локальный прогон: 344/344 PASS, 156.159s**, exact #12,
Python 3.12.10 + verified SQLite 3.54.1. Main 27/27 PASS проверен отдельно.
Main→#12 migration/recovery probe PASS; это новые результаты текущего аудита,
а не переписанный итог старого отчёта. Физическая независимость storage не проверена.

Docker заново локально не собирался: проверены source workflow и actual logs exact
#12 CI. Live deployment/FS/device/HF результаты не воспроизводились. Документальный
PR не запускает deploy workflow, поскольку его head не main; новый CI не добавлялся.
[План интеграции](BLAGO_INTEGRATION_PLAN.md) определяет следующие доказательства,
а не разрешает rollout.

[M-store]: https://github.com/xBLAGO/space/blob/3c218dc5757879c26b3aec520af3636b2d702715/src/xblago/core/storage.py#L28
[M-agent]: https://github.com/xBLAGO/space/blob/3c218dc5757879c26b3aec520af3636b2d702715/src/xblago/agents/personal_assistant.py#L66
[M-deploy]: https://github.com/xBLAGO/space/blob/3c218dc5757879c26b3aec520af3636b2d702715/.github/workflows/huggingface-space.yml#L3
[C-identity-schema]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/core/identity_schema.py#L17
[C-runtime-schema]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/core/identity_runtime_schema.py#L47
[C-session]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/identity.py#L78
[C-tg-auth]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/identity.py#L129
[C-sovereign]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/core/sovereign.py#L120
[C-merge]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/identity.py#L347
[C-extractor]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/core/semantic_memory.py#L25
[C-agent-memory]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/agents/personal_assistant.py#L160
[C-history]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/agents/personal_assistant.py#L362
[C-sw]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/static/sw.js#L1
[C-lock]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/core/storage.py#L35
[C-items]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/web.py#L575
[C-analytics]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/web.py#L613
[C-matching]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/web.py#L827
[C-accept]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/web.py#L903
[C-simulate]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/web.py#L949
[C-witness]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/core/journal_witness.py#L47
[C-custody]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/core/journal_custody.py#L62
[C-delete-plan]: https://github.com/xBLAGO/space/blob/b79cb603a58adbab1e8a5313a06393a2feb5532f/src/xblago/integrations/privacy.py#L33
[G-guard]: https://github.com/xBLAGO/space/blob/483475fd237ba3400881699c407be5b91d53dcdd/src/xblago/core/genesis.py#L59
[R-activation]: https://github.com/xBLAGO/space/blob/d96ade9375f3499ad93095b2307abd750f0a8578/src/xblago/core/identity_recovery.py#L319

## 10. Локальные evidence digests

SHA-256 выполненных логов/результата; stdout не содержит реальных диалогов или
секретов. Эти digests служат идентификацией локальных артефактов, а не подписью
независимого проверяющего. Логи сохранены в `/tmp` текущей сессии; репозиторий
содержит только итог, команды и hash, а не приватные runtime dumps.

- `blago-integration-main-tests.log`: `993847a55e881d9bf779a8480074e9982d32023d482010d17e63cfa34f30a98c`.
- `blago-integration-core-verified.log`: `890562e2058203a7d1ec0f09f24add4ddcc1071d08309217834c6bbe63463ea2`.
- `blago-integration-probe-result.json`: `4e7aa861f664c057bfd5e9c997d251cb1306997641c365606571af434f9eb509`.

Проверка документов: 24 exact-SHA code anchors существуют и номера строк допустимы;
9 локальных cross-links разрешаются; production paths diff пустой, pip check PASS.
Inventory фиксирует состояние до создания собственного audit PR; новый docs PR
не является ещё одним Foundation и не меняет приведённый ancestry snapshot.
