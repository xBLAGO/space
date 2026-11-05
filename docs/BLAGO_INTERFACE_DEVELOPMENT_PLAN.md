# BLAGO — план развития единого интерфейса

Draft. Этот план не разрешает merge, deployment или подключение реальных участников. Исходная runtime-база — candidate PR #19 `d7f619a1e6a7f15564facb5e8e84b184a3f24658`, проверенный audit HEAD `837b2bfd5d3db4842530d30ccf0f417bfe718737`. Перед каждым новым PR перепроверять ancestry и различать runtime, documentation и deployed SHA.

## Backlog и зависимости

| Этап / отдельный PR | Приоритет и результат | Зависимости | Обязательная приёмка | Откат |
|---|---|---|---|---|
| 0. Согласование продукта | P0: утвердить scope нефинансового MVP, инвентаризацию страниц и словарь утверждений | Этот audit, gates #19, отдельный владелец `web2` | Каждое обещание имеет состояние implemented/tested/deployed/planned и доказательство; approved target URL | Отказаться от неподтверждённой CTA; никаких runtime изменений |
| 1. Минимизация аналитики | P0: устранить UI02/UI03, отдельный серверный PR | #19; privacy/security review | API field allowlist; private/cross-user/malformed request tests; cohort/differencing tests; отсутствие financial aggregates в nonfinancial mode | Вернуть synthetic candidate; до исправления закрыть реальный rollout, не отключать privacy guards |
| 2. Site content correction | P0/P1: отдельный PR в `web2`, согласованные EN/RU/другие языки и metadata | Контент-решение этапа 0; deployment только отдельным разрешением | Financial claims review; source→staging hash; link checks; consent/tracking/accessibility; никакого автоматического production deploy из этого плана | Статическая версия/ревью; возврат контента не должен возвращать недостоверные обещания |
| 3. UI foundation | P1: единая навигация, tokens/components, состояния отправки, optional memory/access tools | После API safety; существующие auth/history guarantees | Keyboard/focus/zoom; Web-only unavailable SDK; expired session/lost response; DOM injection; reload/history; no new persistence of private drafts | Вернуть старый shell, не менять storage/auth; не запускать повторные действия |
| 4. Help integration | P1: добровольные обращения и предложения в чате | Отдельное решение по #20 exact source/CI, schema/recovery review; не cherry-pick «по похожему экрану» | A→B synthetic cycle; exact text/version/audience; IDOR/concurrency/replay/revoke/dispute/restore; Telegram и Web одинаково проверяют доступ | Отключить новые действия через согласованный release control; не скрывать события/обязательства и не resurrect revoked content |
| 5. Личные и общественные результаты | P1/P2: обзор без рейтингов и скрытого профилирования | Help outcome model, consent, data dictionary, privacy review | Provenance/denominators; unknown ≠ 0; малые группы; deletion/export/recovery; no individual inference | Убрать визуализацию; не переписывать outcome events и экономические правила |
| 6. Device / operational acceptance | P0 gate перед реальными людьми, не «косметический этап» | Все обязательные исправления и release gates | Настоящие Telegram iOS/Android WebView, Safari/Chrome, физические passkeys; pilot host SQLite; independent journal/witness restore; permitted live provider quality; incident/rollback drill | Stop rollout/revoke access по действующим controls, восстановление только с актуальным checkpoint |

Этапы 2 и дизайн части 3 можно готовить параллельно после контент-решения; реальная публикация/пользовательские испытания зависят от всех обязательных gates. Наличие красивого интерфейса не даёт основания обходить этап 1 или 6.

## Контракт между чатом и сервером

Новый UI вызывает только разрешённые существующие API либо новые endpoint из отдельного reviewed PR. Agent intent — рекомендация, session — доступ к аккаунту, explicit confirmation — разрешение конкретного действия. Это разные вещи. Экспериментальные cryptographic mandates не нужны для оформления старых собственных записей и не включаются ради редизайна.

Для будущих карточек действий определить: opaque operation ID, owner, version, audience, ожидаемый state, consent scope и результат `committed/rejected/unknown`. Повтор не должен создавать дубли. Ни optimistic bubble, ни client-side confirmation не заменяют server authorization или durable commit. Не менять memory/journal/recovery схемы в UI PR.

Для ошибок сохранить русский человекочитаемый текст без provider diagnostics. Не обещать отправку, если ответ потерян. Проверка подтверждённой истории предшествует retry; неопределённость результата показывается явно. Рекомендации не предписывают небезопасный localStorage private chat cache.

## Матрица испытаний будущих PR

| Область | Минимальные проверки | Уровень доказательства |
|---|---|---|
| Auth | Password/passkey/Telegram challenge; replay, expiry, last-method, logout/revoke; cross-user access | Unit + HTTP + physical browser/device отдельно |
| Разговор | Продолжение после reload/restart, no repeated greeting, 503/timeout/empty/truncated response, no automatic action | Synthetic provider regression; live quality отдельный разрешённый прогон |
| Память | Просмотр, correction, contradiction, no-store, deletion, account isolation, restore не возвращает удалённое | Memory suites + UI + journal/witness recovery |
| Потребности/ресурсы | Private default, собственные списки, audience semantics, authorization, no finance | HTTP + UI + отрицательные tests |
| Help | Версии и явное согласие, конкуренция, revoke/dispute, dual confirmation | После отдельно одобренной интеграции #20 |
| Аналитика | Allowed fields, когорты, окна, differencing, no raw identities, deletion propagation | Privacy review + API regression, не только screenshot |
| Mobile/accessibility | Keyboard, screen reader, zoom, safe area, virtual keyboard, contrast, offline, slow SDK | Реальные поддерживаемые устройства; viewport только предварительный этап |
| Release/recovery | Pinned candidate, dependency/schema manifest, repeat migration/old backup, fail closed, no deployment-on-merge | Existing #19 regression + Docker + operational evidence |

## Оставшиеся release gates

Исходник — [неизменённый gates.json PR #19](integration01/gates.json). Этот PR не обновляет machine-readable release gates и не снимает блокеры:

- G00/G01 PASS относится к synthetic provenance и candidate workflow code, а не разрешению production release. Текущий `main` этим аудитом не исправляется.
- G02/G03: фактический host/storage/SQLite и независимость journal/witness failure domains не подтверждены этим аудитом.
- G04: historical ownership/legacy merges и retention требуют отдельного решения.
- G05: реальные Telegram/WebView/physical passkey acceptance остаются непроверенными.
- G06: live HF quality/reliability не проверена; Integration launcher использует local provider и не получает новые credentials.
- G07: #19 не имеет полного human-help lifecycle; наличие #20 не делает его автоматически частью #19.
- Остальные gates #19 остаются ровно в исходном статусе, без переинтерпретации результатами UI-аудита.

Новые UI02/UI03 — дополнительные требования до real-data rollout; не «закрытые» проблемы и не повод ослабить прежние требования Foundation 05.4/06.1. Sovereign Identity, wallets, Solana, Genesis и federation не входят в этот план MVP.

## Результат текущей работы и решение

Созданы четыре документа, machine-readable evidence и два безопасных снимка: публичная Home и пустой synthetic Web-чат. Новые тесты и production-код не добавлялись; выполнены целевые существующие тесты и отдельные read-only/synthetic browser checks. Документация воспроизводимо привязана к exact #19, а не к текущему Help HEAD.

**GO: согласование структуры и следующая изолированная разработка. NO-GO: реальный пилот, финансовые операции, включение экспериментальной идентичности, merge/deploy по результатам этого аудита.** Для GO реального пилота нужны не только UI-исправления, но и фактические эксплуатационные доказательства по release gates.
