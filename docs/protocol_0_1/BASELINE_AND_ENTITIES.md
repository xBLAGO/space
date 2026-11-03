# 08.1 — Baseline & entities

Status: Draft. Checked against PR #15 and GitHub open-PR snapshot; no branches merged.

## 1. Интеграционная основа

| Срез | SHA | Роль в будущих испытаниях |
|---|---|---|
| main | `3c218dc5757879c26b3aec520af3636b2d702715` | Старый работающий local/Telegram MVP. Не считать его Web/Identity Core baseline |
| PR #15 audit | `03cdbf82ec45ee10ec45567ba60f3bfb06fbca5f` | Base этой docs/conformance ветки; runtime не включается |
| PR #7 / 05.4 | `8634e94a6dddd2b8cfff415b112f6b895eb0f4ae` | Требования SQLite, journal/witness, lineage и retention сохраняются |
| PR #10 / 06.1 | `d11a2a555efe57e6818450d534655020bf47b9ca` | Conversation/history baseline; naturalness/availability blockers открыты |
| PR #12 / 07.1 | `b79cb603a58adbab1e8a5313a06393a2feb5532f` | Рекомендуемый **кандидат** disposable runtime stack: уже содержит 05.4→06.1→07→07.1. Не принятая integration branch |
| PR #13 / 07.2 | `d96ade9375f3499ad93095b2307abd750f0a8578` | Не требуется для первого сообщения. Recovery evidence не активирует authority |
| PR #14 / Genesis | `483475fd237ba3400881699c407be5b91d53dcdd` | Не требуется для первого сообщения. Public proposals и fixtures — иной scope |

Остальная цепочка остаётся открытой: #2 Web/economy → #3 semantic corrections →
#4 Identity Store → #5 secure runtime → #6 privacy → #7 safety → #9 UI → #10 →
#11 identity architecture → #12. #13 и #14 — siblings от #12, их нельзя считать
совместно интегрированными. Все exact heads перечислены в аудите; нового merge
или разрешения на rollout этот документ не создаёт.

**Безопасный порядок:** сначала pure corpus без application state; затем
отдельно разрешённый disposable checkout exact #12 с прежними flags OFF;
только после review — adapter за новым experimental flag, а не замена текущих API.
Не накатывать миграции, не переносить production SQLite и не использовать original
production journal/key. Пока proto identity bindings отсутствуют, использовать
синтетические enrollment records, не выдавать local session за owner consent.

## 2. Формальная модель

| Сущность | Определение | Чем не является |
|---|---|---|
| Human | Человек, принимающий решения; в протоколе действует через явно подтверждённые ключи/согласия | Не база биографии, паспортный hash, IP или WebAuthn uniqueness |
| Identity | История owner authorities и приватных mappings; один owner epoch в каждой принятой ветке | Не web session, login name, node URL или «единственный человек во всём мире» |
| Agent | Software principal с собственным signing key и ограниченными мандатами | Не Human и не обладатель всех прав owner |
| Node | Операторский runtime/storage/transport; может хостить несколько agents/users | Не личность и не автоматический доверенный issuer |
| Mandate | Подписанное разрешение конкретному agent на ограниченное действие в контексте | Не intent, bearer session, prompt или произвольный API token |
| Federation | Согласованный versioned набор invariants/profiles/trust relationships между узлами | Не единый обязательный registry/admin, список Telegram chats или token network |

`blago_user_id` и 07.1 `identity_id` сохраняются как внутренние значения. Profile
использует отдельный pairwise synthetic identity handle `id:<32 lower-hex>`;
это не truncate/hash legacy identity ID. Реальный binding требует отдельного
owner-signed enrollment с обоими opaque references в private scope, проверкой
полномочий и uniqueness локального mapping. Он пока не реализован.

Прочие handles: `ag:` agent, `nd:` node, `fd:` experiment/federation namespace,
`ch:` object/channel — по 32 lower-hex. Event `ev:` и mandate `md:` имеют по
64 lower-hex цифры (32 случайных байта, 256-bit random value).
Key ID — `sha256:` + SHA-256 raw Ed25519 public key. Это key fingerprint, не
доказательство personhood. Разные relationships должны иметь отдельные keys/
handles, если требуется unlinkability; один глобальный ключ коррелирует участника.

## 3. Кто что подписывает

- Human-controlled owner key: mandate, authority.issue/revoke history и свежий
  authority-status. В lab это отдельный синтетический signer, не ключ агента.
- Agent key: Envelope. Он не подписывает себе mandate, status или transfer authority.
- Receiver node key: технический delivery/revocation ACK после durable commit.
  ACK подтверждает состояние receiver, не истинность текста и не банковскую операцию.

Первый профиль намеренно не вводит online delegated status signer: он потребовал бы
собственной политики полномочий/компрометации. Owner test signer может обслужить
один challenge вручную; production owner keys нельзя класть в agent/node для удобства.

## 4. Invariants / mechanisms / policies

Invariants: explicit authority, no silent escalation, terminal revoke для старого
mandate ref, replay/restore safety, private-by-default, no dual owner epochs.
Mechanisms выбранного lab profile: closed canonical JSON, Ed25519, SHA-256,
receiver nonce, bounded clock uncertainty и retained frontier.
Local policies: отказ в сообщении, abuse quotas, storage retention, способ hosting.
Policy может отказать, но не расширить signed scope или скрыть UNKNOWN.

Поле `policy_ref` — hash заранее согласованного profile/policy документа, pinned
обеими сторонами. Первый corpus использует явно synthetic policy commitment;
он не заявляет, что Genesis принят сообществом. Federation/network admission,
Sybil/personhood, Consensus 7 и любые экономические права не выводятся из обмена.
