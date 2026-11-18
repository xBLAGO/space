# Foundation 10 — Sovereign Decentralized Network

Дата проверки: 18 ноября 2026. Статус: Draft, audit / architecture / planning only.
База этой документации: `3c218dc5757879c26b3aec520af3636b2d702715` (`main`). Проверенный исследовательский runtime: PR #37 `4706a60e7ec675ed1ae16b9c9cd551cde291cfd3`. Данные — только синтетические. Код, схемы, production и release gates не изменены.

Доказательства различаются: **CODE** — прочитанный код, **LOCAL** — исполненный в этом аудите тест, **CI** — проверенный существующий запуск Actions, **DESIGN** — предложение, **UNKNOWN / NOT EXECUTED** — доказательства отсутствуют. Проверка выполнена повторным анализом исходников и GitHub; независимый внешний аудитор и независимые операторы не участвовали.

## Статус и границы

Весь новый сетевой дизайн ниже — **DESIGN**, без реализации, зависимостей или миграций. BLAGO_VISION и ECONOMIC_CONSTITUTION находятся в #35–37, не в main; их принципы добровольности и равного достоинства сохраняются. Ни основатель, ни оператор, ни агент не получают универсального права изменения баланса. Контроль инфраструктуры всё ещё даёт возможность отказа в обслуживании; криптографические подписи сами по себе не устраняют этот риск.

## A. Sovereign Identity

Human — носитель прав, не session и не уникальный hardware fingerprint. Identity — случайный идентификатор и проверяемая история controller keys; подпись доказывает владение ключом, не уникальность человека. Agent — заменяемый исполнитель scoped мандата. Node — оператор транспортного/учётного сервиса с собственным ключом. Federation — согласованные правила доверия, а не общая database или общий owner key.

Расширять `blago_user_id`/IdentityRuntime; будущий mapping к sovereign identity приватный. #12 содержит domain `core/sovereign.py` и additive `sovereign_schema.py`; #13 — только signed recovery ceremony, но их не содержит #37. [identity_recovery.py:319](https://github.com/xBLAGO/space/blob/d96ade9375f3499ad93095b2307abd750f0a8578/src/xblago/core/identity_recovery.py#L319) всегда блокирует activate_identity(): восстановление действующей owner authority не реализовано. Сессия Web/Telegram разрешает локальный вход, не issuance глобального мандата. Owner keys должны оставаться у человека; WebAuthn local UV не доказывает «один физический человек». Recovery только заранее утверждёнными независимыми контроллерами с задержкой/оспариванием и свежими retained controls; при их утрате hold.

Пакет переноса должен содержать versioned manifest, owner/controller history, подписанные отзывы/causal references, допустимые события и checkpoint proofs, audience/retention/consent scope. Не экспортировать cookies, CSRF, server HMAC keys или чужие сессии. Import на узлеB требует explicit owner proof, OOB trust, актуальных отрицательных решений и отдельного acknowledgement от источника; старый snapshot не возрождает authority. Полный протокол cutover и key custody пока отсутствует.

## B. Local-First Agent

Локальный зашифрованный vault, ключи и выборочная синхронизация — будущая работа. PWA cache и SQLite на HF не равны local-first. В текущем `integrations/static/sw.js` кешируются только icon/manifest, authenticated API и navigation исключены; offline history/agent этим не реализованы. Разрешённые offline действия: частный черновик, просмотр локально доступных фактов с меткой freshness, локальный отзыв согласия с очередью доставки. Нельзя offline публиковать, расходовать общий резерв или выдавать положительное разрешение на основании устаревшего frontier.

Личная память не реплицируется узлам по умолчанию. Каждый производный summary/retrieval index зависит от исходных consent/version/deletion. Удаление должно удалять payload и derived indices; минимальные отрицательные controls сохраняются отдельно по утверждённой политике. Local-first LLM может быть заменяемым локальным provider; сейчас LocalPlanningProvider — ограниченный fallback, не доказанная полноценная автономная модель. Browser eviction/утрата устройства требуют отдельного backup, export и revocation сценария; backend не должен принимать cached session как authority.

## C. Independent Nodes

Минимальный узел: воспроизводимая сборка; отдельный оператор, signing keys и конфигурация trust anchors; bounded verifier; durable inbox/outbox + idempotency; nonce/authority status и ревокации; canonical source adapters; independent retained checkpoint; private payload vault; наблюдаемость без персональных текстов; явные repair/hold процедуры.

Для первого lab достаточен BOP #17 exact `44a5d52a4ac8022478a247f4e1bbd2eaea551142`. Его receiver пишет state с fsync/atomic rename и подписывает receipt после commit; это отдельный файловый lab под POSIX assumptions, не SQLite-runtime приложения. Два процесса на ноутбуке — local integration. Два независимых оператора — отдельные управления host/volume/ключами и OOB key pinning; S12 пока NOT EXECUTED. Same-author alternative verifier не заменяет внешний security review.

## D. Decentralized Ledger

UEL остаётся shadow-index канонических Help/Asset/Capital, не второй state machine. Проверяемый event требует canonical source ref, version, causal dependencies, domain-separated signature/proof, privacy-scoped payload commitment и защищённого payload reference. Реплика не должна превращать SELF_DECLARED в independently verified.

Раздельные порядки: per-source sequence, causal order и локальная durable receipt order. Wall clock не является глобальным порядком. Equal inputs/rules дают equal projection; конфликт версий даёт UNKNOWN/CONFLICT, не last-write-wins по расходам. Подпись checkpoint доказывает обязательство подписанта, не полноту мира; две противоречивые подписанные вершины — equivocation и hold, не повод выбрать удобную.

**Двойное расходование между независимыми nodes сейчас не решено.** Доступный SQLite баланс #37 сериализован в одном rollback domain. Две копии могут независимо зарезервировать один lot. До будущего consensus/escrow допускается один явный authority writer на конкретный lot; никакого offline нового расхода общего пула. Альтернатива — заранее ограниченные непересекающиеся spending rights с доказуемым conservation и последующим settlement; это новый отдельно проверяемый контракт, не разрешение его внедрить. CRDT подходит для некоторых монотонных фактов, но не решает отзыв и общий бюджет автоматически.

Публичный blockchain не нужен для первого эксперимента. Будущие anchors должны быть privacy-reviewed randomized commitments с minimization: голый hash имени/телефона/памяти подвержен перебору и корреляции. Не раскрывать stable personal pseudonym в общей статистике без основания. Удаление приватного payload не требует переписывать допустимую минимальную контрольную историю, но необратимую публикацию нужно ограничить до её появления.

## E. P2P / Mesh: выбор после протокола

| Transport | Исследовать | Ограничения / критерий |
|---|---|---|
| Direct authenticated connection | pinned peer, bounded envelope, mTLS/other reviewed channel | Не заменяет application signature/mandate; первый lab без нового transport stack |
| libp2p | peer identity, QUIC/TCP/WebRTC, discovery, DCUTR/relay | NAT требует coordination/relay fallback; peer key не Human key; ограничения браузера |
| LAN | явные peer addresses, optional discovery | Не доверять multicast объявлению; encrypted/authenticated protocol |
| Store-and-forward / USB | подписанный пакет + durable delivery receipts, TTL/replay/size limits | Receipt после реального commit; доставка не равна актуальному разрешению |
| Wi-Fi Direct | отдельный native Android experiment | Device support, permissions, compatibility; не универсальная WebView API |
| Bluetooth/BLE | локальная передача bounded packets при explicit permission | GATT/браузерные ограничения, низкая скорость/фон/батарея; не глобальный mesh |
| Multi-hop mesh | отдельный threat/abuse model, quotas, routing/relay incentives | Нет автоматической глобальной достижимости, консенсуса или защищённости от цензуры |

Основания: [libp2p hole-punching specification](https://github.com/libp2p/specs/blob/master/connections/hole-punching.md) описывает NAT/relay и различия browser/native; [Android Wi-Fi Direct](https://developer.android.com/develop/connectivity/wifi/wifip2p) — прямое соединение поддерживаемых устройств; [Chrome Web Bluetooth](https://developer.chrome.com/docs/capabilities/bluetooth) — ограниченный BLE/GATT доступ; [RFC9171](https://www.rfc-editor.org/rfc/rfc9171) — модель delay-tolerant bundles, с опубликованными последующими updates. Это источники для исследования, не выбор библиотек BLAGO. Ничего из этих транспортов здесь не установлено и не испытано.

## F. Distributed Intelligence / ISTINA

Отделить private memory, утверждение владельца и коллективное знание. Минимальный будущий claim: statement handle, source event, subject scope, time/context, evidence refs, attestation/dispute/correction/revocation и disclosure policy. Повторные копии одного источника не считаются независимыми свидетельствами. Запрос поиска получает только разрешённые claims; подпись author не делает текст истинным. ИИ строит предложение и объяснение с источниками; не подтверждает факт от имени человека и не расширяет аудиторию.

Данные от peers — untrusted content, не tool instructions. Внешний LLM не получает private identifiers/контекст сверх согласованного route. Provider interface должен поддерживать замену модели/host без потери записей и новых полномочий. Embeddings/model outputs — тоже персональные производные данные при возможности привязки.

## G. Resilience и компромиссы

| Отказ / атака | Допустимая работа | Запрещённое поведение / приёмочный drill |
|---|---|---|
| HF недоступен | локальный vault/drafts и другой уже доверенный node — когда реализованы | Сегодня hosted client теряет backend; не заявлять offline continuity |
| GitHub недоступен | ранее проверенная сборка + независимый source/release mirror | Не полагаться на GitHub как online authority service |
| Telegram недоступен | Web/passkey при заранее связанном доступе | Не отождествлять по совпадающему имени/телефону |
| DNS/relay censored | pinned alternative addresses и разные relay/operators | Не отключать key/origin verification ради соединения |
| Network partition | private drafts, queued negative revocations; shared capital write hold | Глобальный мгновенный revoke/двойной offline spend не обещаются |
| Старый snapshot всех локальных копий | сверка independently retained tip, hold если недоступен | Локальная копия witness в том же rollback domain не независима |
| Compromised node | signatures/limited mandates, rival evidence and withdrawal | Сервер может украсть данные, доступные ему plaintext; подпись не исправляет утечку |
| Rejoin | verify key/authority history, denials first, dedupe causal DAG, quarantine forks | Не воскресить deleted payload/permission через replay или LWW |

Приватность уменьшает публичную воспроизводимость plaintext, поэтому verification работает с минимальными доказательствами и выборочным раскрытием. Неизменяемость ограничивается допустимой контрольной историей, не бессрочностью частных данных. При partition актуальность revocation и отсутствие double-spend требуют ограничить availability положительных действий. Автономная частная работа возможна без обещания немедленной коллективной финальности.

## Threat model delta и приёмка

Активы: human/node keys, mandates, приватные payload, лоты/резервы, receipts/frontiers и запреты удаления. Противники: compromised node/agent/provider, злонамеренный peer, colluding quorum, Sybil operators, rollback storage, censorship/partition, curious aggregate reader. Trust assumptions: независимость custody не из имени host; storage/fsync и clocks квалифицированы; pins переданы authenticated OOB; human private key недоступен агенту.

Обязательные новые испытания: remote independent S12; revoked mandate during partition; stale signed status; two node conflicting reserve; rollback both DB+local witness vs third custody; missing checkpoint; forged controller recovery; private claim query exfiltration; consent withdrawal before sync; rejoin old peer; offline device theft; provider/relay change. Все **NOT EXECUTED** этим аудитом. Federation GO невозможен до появления доказательств. Последовательность и rollback: [Master Plan](BLAGO_MASTER_INTEGRATION_PLAN.md).
