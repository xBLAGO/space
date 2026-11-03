# BLAGO Open Protocol 0.1 — Requirements

**Статус: Draft requirements for discussion. Не реализация, не принятый governance
и не замена Genesis Draft из PR #14.** Основание —
[архитектурный аудит](BLAGO_FEDERATION_AUDIT.md), с закреплёнными SHA и доказательствами.

Цель 0.1: два независимо управляемых агента могут обмениваться ограниченными
проверяемыми событиями и проверять права отправителя без универсального секрета
основателя, сохраняя возможность человека пользоваться hosted MVP и сменить
провайдера. Отдельный сервер на каждого человека не требуется.

## 1. Границы нормативности

- **Инварианты:** контроль человека, explicit consent, scope/authority, отсутствие
  неявных экономических действий, защита от replay/rollback, private-by-default,
  честные неопределённость/отзыв/удаление. Локальная политика не может их ослаблять.
- **Механизмы:** serialization, signatures, transport, storage, clock/nonce/checkpoint
  implementation, key custody и recovery. Выбор требует проверяемых test vectors,
  а не предположения, что один SDK или один storage обязателен всем узлам.
- **Локальные политики:** admission, risk/constraints, discoverability, abuse limits,
  retention в допустимых границах, стоимость hosting/compute. Они явно объявляются,
  имеют version/hash и могут отказать в действии, но не расширить мандат.

Термин MUST далее — предлагаемое условие совместимости 0.1 после принятия текста.
Он не утверждает, что текущий BLAGO уже выполняет это условие. Разногласия по
инвариантам и acceptance criteria решаются до включения interop effects.

## 2. Principals и authority

**R-ID-01 (P0; A1/A3/B6):** owner identity, local account, agent principal, node
operator и transport endpoint MUST быть разными понятиями. `blago_user_id` остаётся
внутренним ID существующего MVP; его нельзя автоматически публиковать как глобальный
идентификатор. Миграция сохраняет проверяемую связь, не переопределяя старые аккаунты.

**Acceptance:** A и B импортируют синтетическую owner identity без disclosure имени,
Telegram ID или памяти; local mappings различаются, owner history проверяется
одинаково. Wrong-owner mapping и collision отвергаются; импорт не создаёт hidden admin.

**R-ID-02 (P0; B6/A4):** key transitions MUST проверяться по полномочиям на момент
transition, предыдущей версии и разрешённому purpose. История ротации/отзыва
проверяется отдельно от possession proof. Свежая подпись отозванным ключом не
означает действующее право управления.

**Acceptance:** valid rotation, bad signature, wrong purpose, stale predecessor,
competing rotations, revoked/expired/unknown authority; restore не возвращает старую
authority. Наличие двух одновременно действующих owner epochs не допускается.

**R-MANDATE-01 (P0; B7):** мандат MUST явно связывать owner/issuer, agent subject,
разрешённые действия/объекты, audience/context, срок и limits, delegate/redelegate
policy и revocation reference. По умолчанию нет redelegation или wildcard powers.
LLM-generated intent, web session и доступ к personal memory не являются мандатом.

**Acceptance:** независимо написанный verifier принимает минимальный разрешённый
мандат и отклоняет неверного actor/recipient, превышение лимита, scope escalation,
несанкционированную redelegation и разрешение на другой object revision.
Модель не может выдать себе права через prompt injection или tool-looking JSON.

**R-REVOKE-01 (P0; B8/A4/E19):** MUST определить доверенное происхождение, monotonic
ordering и значение «подтверждённого отзыва». Receiver проверяет fresh authority
перед authority-bearing effect; обработанный отзыв не откатывается local restore.
При неизвестном current state sensitive writes запрещены. Offline-права, если их
вообще допускают, имеют отдельный явный bounded policy; нельзя обещать одновременно
неограниченный offline write и мгновенную глобальную revocation.

**Acceptance:** revoke-before-action, action-before-revoke, racing ACK, partition,
unknown/stale frontier, slow/fast clocks, old backup и replay at another node.
Protocol указывает, какие nodes дали durable ACK и какую гарантию он создаёт;
время/lease bounds и допустимые виды offline read записаны явно.

**R-RECOVERY-01 (P0; A3/B6):** failure recovery MUST NOT выдавать новые powers по
password, Telegram session, биографии или admin decision. Без проверенного
независимого control state действие остаётся заблокированным. Availability и
суверенное lost-key recovery — разные свойства; их нельзя смешивать в отчёте.

**Acceptance:** потеря primary server, части custody, journal/head, всех доказательств;
никакой автоматической замены identity или молчаливой реактивации. Предлагаемый
threshold protocol проходит отдельный review, не включается только ради 0.1.

## 3. Сообщения, события и conformance

**R-MSG-01 (P0; E18/E19):** envelope MUST иметь явные type/schema version,
issuer/subject/audience, уникальное событие/команду, domain/purpose, связанные
object versions/causal references, payload/disclosure metadata и подписываемый
контекст. Time, expiry, nonce и idempotency semantics определяются для каждого
типа, а не добавляются как декоративные поля.

Не фиксируются заранее JSON vs CBOR, конкретный HTTP endpoint, DID method или
брокер. Нужно выбрать один минимальный однозначный wire profile до interop и
опубликовать точные signing bytes. RFC/SDK совместимость нельзя заявлять на
основании похожего названия `canonical`.

**Acceptance:** минимум две реализации читают одни golden vectors; Unicode,
number/precision, duplicate keys, unknown fields/types/versions, null/empty,
serialization malleability, domain/network substitution и oversize payload имеют
одинаковое решение accept/reject. Несовместимая версия не исполняется молча.

**R-EVENT-01 (P0; A4/D16):** authenticity, integrity, causality, authority и truth
MUST проверяться как разные утверждения. HMAC с общим серверным секретом или
пересчитываемая SHA цепочка сами по себе не доказывают независимое авторство.
References обязаны однозначно указывать версию и privacy scope.

**Acceptance:** изменённые payload/link/order, fork, отсутствующий predecessor,
подменённая authority и syntactically valid but unauthorized event отвергаются;
неполная история выдаёт Unknown/needs-evidence, а не fabricated truth.

**R-REPLAY-01 (P0; E19/B8):** event ID, command idempotency key и delivery receipt
MUST иметь разные документированные роли. Receiver durable дедуплицирует effect,
включая восстановление/повторную доставку/другой transport. Exactly-once доставка
сетью не предполагается; обеспечивается idempotent application effect в границах
определённой authority и restore frontier.

**Acceptance:** duplicate/reorder/multichannel delivery, same key different body,
concurrent receivers, commit-before-ACK crash, rollback после ACK, replay у B вместо
A. Нет второго платежа/обязательства/публикации или scope escalation.

**R-INTEROP-01 (P1; A2/A4):** conformance MUST быть воспроизводимым без founder
infrastructure и доступа к его credentials. Наличие двух контейнеров одного
оператора с shared keys — только local integration, не независимый federation proof.

**Acceptance:** A/B с отдельными admin, keys, storage и LLM configurations, минимум
один independently implemented verifier; founder offline. Сохраняются transcripts,
config hashes без секретов, negative cases и список общих external dependencies.
DNS/TLS/transport outages, remote storage и custody permissions проверяются отдельно.

## 4. Переносимость, приватность и выход

**R-PORT-01 (P0; A1/A3/F25):** export/import MUST иметь versioned manifest,
ownership/authority evidence, coverage, integrity, retention/deletion state и
compatibility limits. Personal data export и privileged DR secret package MUST
быть разными форматами/процедурами. Import не должен требовать выдачи новому
оператору чужих current sessions, CSRF tokens или universal founder key.

**Acceptance:** export A→import B→restart B без потери owner continuity, memory
scope, consent и требуемой истории. Corrupt/partial/colliding/stale/foreign bundles
отклоняются атомарно. Private credentials не появляются в public export.
Сохраняются economic records, обязательные по утверждённой retention policy.

**R-MEM-01 (P0; C10/C13):** private memory, private draft, public assertion,
evidence и collective evaluation MUST быть различимы по типу и disclosure scope.
`confirmed` personal preference и LLM confidence не становятся общественным фактом.
Перенос или inference новым LLM не даёт права публиковать исходный диалог.

**Acceptance:** отсутствие private data в public JSON/events/model prompt для
неразрешённого recipient; cross-user/cross-node tests, refusal-to-remember,
исправление/удаление и repeat restore. Scope нельзя расширить изменением label.

**R-DISCLOSE-01 (P0; C13/A5):** перед selective disclosure MUST быть определены
recipient, purpose, содержимое/версия, срок и retention policy; минимизация и
конфиденциальность охватывают backups, provider logs и caches. Per-user encryption
может быть механизмом, но не подменяет управление ключами и разрешениями.

**Acceptance:** unauthorized recipient, изменённый purpose, inference provider
switch и cache replay не раскрывают новые данные. Документированы данные, которые
провайдеру всё же передаются, и пределы их последующего удаления.

**R-EXIT-01 (P0; E22/A3):** выход MUST иметь проверяемые состояния подготовки,
export/optional handoff, revocation delegations, cessation старых effects и
retention/deletion receipts. Не должно возникать двух owner authorities после
частично завершённого handoff. Стирание локального account не равно выходу.

**Acceptance:** normal exit, interruption/resume/cancel, malicious old node,
старый snapshot, недоступный recipient и conflicting handoffs. Участник может
завершить безопасный отказ без потери всей identity; невозможно тайно сохранить
права старого агента через stale lease или «поддержку» администратора.

**R-RETRACT-01 (P0; E21/C13):** published assertion MUST поддерживать проверяемый
retract/supersede/expiry, отличимый от 404/outage. При удалении private content
сохраняется лишь законно/продуктово необходимое минимальное evidence; его состав
и linkability обсуждаются заранее. Нельзя обещать уничтожение уже скачанных
public copies или безусловное удаление immutable внешних записей.

**Acceptance:** B различает active/retracted/superseded/unknown при тех же старых
bytes; повторный import не возвращает отозванное как current. Тексты, требующие
удаления, не оставляются в вечном журнале только ради красивого hash chain.

## 5. ISTINA и экономические события

**R-ASSERT-01 (P1; C11):** assertion MUST сохранять субъект, автора, status и
разрешённые source/evidence references, связь с исправлениями и тип epistemic
claim: declared/observed/inferred/contested, с определённой семантикой, не только
числом confidence. Точная vocabulary должна быть обсуждена, не закрепляется этим
списком примеров автоматически.

**Acceptance:** source integrity и provenance проверяются без получения private
биографии/всего диалога; revoked/disputed evidence не делает assertion автоматически
истинным или ложным. LLM inference никогда не превращается в скрытую authority.

**R-ATTEST-01 (P1; C12/E20):** ATTEST MUST ссылаться на exact subject version,
issuer authority, purpose/meaning, evidence, validity и возможность retract/dispute.
Verification receipt, human agreement, bank verification и мнение о достоверности
не считаются взаимозаменяемыми утверждениями.

**Acceptance:** два независимых issuers; плохая signature, чужой subject hash,
другой purpose, expired/revoked signer, contradictory evidence и Sybil amplification.
Нет автоматического truth/vote finality до принятой trust policy. Поле signature
в dataclass или WebAuthn attestation не считается положительным ATTEST тестом.

**R-ECON-01 (P1; D14/D15):** NEED, RESOURCE, constraints, OFFER, accepted COMMITMENT
и FULFILLMENT MUST иметь разные доменные значения и жизненные циклы. Внутренние
таблицы MVP могут остаться read models. Conversation, declared money и exposure
предложения не означают transfer, verified liquidity или финансовое обязательство.

**Acceptance:** старые IDs/state сохраняются; каждый значимый transition требует
прав нужной стороны и explicit consent. Cancellation/expiry/dispute не стирают
необходимую history. Экономические правила меняются только отдельным решением.

**R-REBUILD-01 (P0; D16):** authoritative event coverage и deterministic reducer
MUST доказывать восстановление согласованного экономического состояния, прежде
чем SQL projection объявляется производной от журнала. Симуляция и manual
confirmation сохраняют свои labels и не становятся доказательством bank transfer.

**Acceptance:** complete event stream → тот же state hash; повтор/reorder/fork,
concurrent acceptance, double fulfillment, old-backup и unknown schema не создают
новых обязательств. До parity event adapters только shadow/read-only.

**R-FAIR-01 (P1; D17/B9):** allocation/admission/risk policies MUST быть явными,
versioned, объяснимыми и оспоримыми. Условия resource provider учитываются до
соответствующего действия. Разные аккаунты/число offer rows не доказывают разных
людей и не должны незаметно определять «общественный вес».

**Acceptance:** counterfactual языковые/групповые tests, substring false positives,
unknown amount/currency, repeated offers, invitation exposure, rate limits,
exclusion reasons/appeal и privacy-minimized audit trail candidate selection.
Реальную fairness нельзя вывести только из случайной перестановки строк.

## 6. Локальные политики и доступность

**R-POLICY-01 (P1; E23):** MUST публиковать compatibility profile, mandatory
invariants и отличия local policies. Повышенные местные требования могут дать
прозрачный отказ, но не тихое переопределение чужой подписи/обязательства.

**Acceptance:** разные nodes могут иметь разные risk/hosting policies и всё же
одинаково проверять protocol invariants; несовместимость объявляется до effects.

**R-ACCESS-01 (P1; F24/F25/A2/A5):** человек MUST иметь вариант без своего
постоянно работающего сервера, со сменой hosted provider/LLM без потери контроля.
Протокол не зависит от единственного HF Space, model router, bot token, RP domain
или admin основателя; выбранные пользователем внешние зависимости объявляются.

**Acceptance:** lightweight client/relay и independent node; host A недоступен,
B принимает перенос с preserved control и явным reauthentication. Не обещается
автоматический перенос WebAuthn между несвязанными RP IDs. Смена inference
сохраняет state, но качество/latency/privacy проверяются отдельно.

## 7. P0/P1/P2 и минимальная поставка

| Gate | Что достаточно для 0.1 | Что не является достаточным доказательством |
|---|---|---|
| P0: authority | Scoped mandate + fresh revoke verifier + golden negative cases | Session, L1 PoP или «агент понял намерение» |
| P0: portability | Owner-authorized complete import/handoff/exit | ZIP SQLite, personal JSON download, shared founder HMAC |
| P0: events/privacy | Signed versioned envelope, replay/rollback rules, private/public split | REST endpoint, SHA chain или `confirmed` memory |
| P1: independent interop | Two operators/implementations, separate keys/control state | Два процесса/клиента одного сервера |
| P1: collective/economy | Typed ATTEST/commitment + reducer parity + fairness/abuse review | Аргументы аккаунтов, random shuffle, simulated payment |
| P2: extensions | Selective disclosure optimizations, discovery, advanced recovery after proof | Blockchain/token по умолчанию |

**Не фиксируется этим документом:** blockchain/network, DID method, serialisation
standard, database replacement, число jurors, voting threshold, global uniqueness
или universal reputation score. Их необходимость должна следовать из threat model,
user needs и conformance experiments, а не из уже выбранного SDK.

## 8. Traceability и будущие PR

| Аудит | Требования |
|---|---|
| A1/A3 | R-ID-01, R-PORT-01, R-EXIT-01 |
| A2/A4/A5 | R-INTEROP-01, R-EVENT-01, R-REVOKE-01, R-ACCESS-01 |
| B6/B7/B8/B9 | R-ID-02, R-MANDATE-01, R-REVOKE-01, R-FAIR-01 |
| C10/C11/C12/C13 | R-MEM-01, R-ASSERT-01, R-ATTEST-01, R-DISCLOSE-01 |
| D14/D15/D16/D17 | R-ECON-01, R-REBUILD-01, R-FAIR-01 |
| E18/E19/E20/E21/E22/E23 | R-MSG-01, R-REPLAY-01, R-ATTEST-01, R-RETRACT-01, R-EXIT-01, R-POLICY-01 |
| F24/F25 | R-ACCESS-01, R-PORT-01 |

Порядок: согласовать baseline и invariants → fixtures/pure verifier → gated shadow
adapters → mandate/revocation/handoff → two-operator interop → collective/economic
shadow → independent review/pilot decision. См. пошаговые exit gates в аудите.
Каждый будущий PR содержит scope, negative vectors, privacy disclosure, restore/
rollback cases и явное перечисление непроверенного. Успешный CI не заменяет
межоператорскую проверку и не даёт разрешение на deployment.
