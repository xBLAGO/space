# BLAGO MVP Release Gates — Integration Audit 01

**Draft. Текущий ответ: NO-GO для реальных участников.** Это предлагаемые критерии
приёмки ограниченного добровольного MVP, не новые экономические правила и не
свидетельство выполненного release. Статусы привязаны к snapshot 2026-11-03 UTC.
[Аудит](BLAGO_INTEGRATION_AUDIT_01.md) содержит exact code references и наблюдения.

## 1. Минимальный scope

Небольшая приглашённая группа без реальных переводов, токенов, публичной федерации
и экспериментов с crypto identity. Участник может:

- Войти подтверждённым способом и связать Telegram/Web без второго независимого ID.
- Общаться, вернуться после перезапуска, просмотреть/исправить/удалить свою память.
- Сначала обсудить потребность; отдельно подтвердить конкретный текст и аудиторию
  публикации. Отказ или «я передумал» не создаёт обязательство.
- Добровольно предложить нефинансовую помощь, отозвать предложение до принятия,
  согласовать участие и подтвердить результат без фиктивного платёжного статуса.
- Понимать, что видит другой участник, экспортировать своё и инициировать удаление.

Необходимый baseline: #2→03→04→#3→#4→#5→#6→#7→#9→#10, exact candidate
`d11a2a555efe57e6818450d534655020bf47b9ca` плюс reviewed fixes. Последние ещё не
существуют как принятый release SHA. #11–#17 не обязательны для этого scope.
Отсутствующий generic help lifecycle нельзя заменить словом «community» в поле БД.

## 2. Обязательные gates

| Gate | Сейчас | Evidence / условие GO | Ответственность | При отказе |
|---|---|---|---|---|
| G00 — approved build | OPEN | Source SHA, image digest, pinned dependencies и manifest миграций; review ancestry, CI exact candidate | Release owner + reviewer | Не принимать runtime в rollout |
| G01 — deployment boundary | OPEN | Code acceptance не запускает непроверенную миграцию/deploy; отдельное явное approval окружения | Release owner | Никакого push/merge в release path |
| G02 — actual SQLite/storage | CODE + CI PASS; HOST UNKNOWN | actual Python sqlite_source_id одобрен; WAL/fsync/locking/rename на выбранном persistent volume квалифицированы | Runtime operator | Старт pilot runtime fail-closed |
| G03 — external custody/recovery | SYNTHETIC PASS; OPS OPEN | App, journal, witness и wrapping key имеют документированные независимые rollback/access domains; lost-server drill на отдельном экземпляре | Operator + independent reviewer | Блокировка приёма, без reset/rebootstrap |
| G04 — baseline/retention/delete | OPEN | Legacy merge lineage и prior-delete baseline разобраны; применимая retention policy принята, тесты не воскрешают данные; удаление не обещает невозможного | Data owner + security reviewer | Не включать затронутый scope/аккаунты в pilot |
| G05 — secure identity/channels | SYNTHETIC PASS; REAL DEVICES OPEN | Secure schema required; signed Telegram initData challenge, csrf/origin, TTL/revoke/IDOR, restart/restore, последнее средство доступа; actual device matrix | Auth owner | Канал отключён, только проверенная альтернатива |
| G06 — natural dialogue/live provider | MOCK PASS; LIVE ACCEPTANCE OPEN | Достаточное успешное покрытие below; no duplicate greetings, complete answers, correct intention, no false save/action claims | Conversation owner + participants | Stop onboarding; исправить/provider fallback review |
| G07 — consent-driven help loop | MISSING | ≥3 synthetic роли: author/helper/unrelated observer; explicit publication, withdrawal, accept/cancel/complete; private rows inaccessible | Product + domain owner | Не представлять личные списки как взаимопомощь |
| G08 — isolation/security | PARTIAL | Cross-user/API/key/CSRF/replay negatives, sensitive-data filtering, no model execution; одинаковая small-cohort policy всех analytics routes; review threat model выбранного scope | Security reviewer | NO-GO при утечке/неподтверждённом действии |
| G09 — concurrency/availability | OPEN | Параллельные участники, Web+Telegram одной личности, timeouts/restart: нет lost/reordered exchanges или unbounded global queue | Runtime owner | Ограничить topology/concurrency; повторить acceptance |
| G10 — monitoring/support/stop | OPEN | Redacted diagnostics, incident procedure, tested stop onboarding/readonly, operator availability и согласованное информирование участников | Operator | Остановить запись/доступ по incident plan |
| G11 — final release rehearsal | NOT EXECUTED | Все gates на одном exact candidate + одной synthetic baseline, repeat migration, backup restore, stale keys/sessions denied; independent sign-off | Release owner + reviewer | Никакого rollout, forward-fix в isolated copy |

Для GO все обязательные gates должны быть PASS в пределах **одной и той же**
выбранной конфигурации. Нельзя сложить PASS разных branches/images/volumes и
назвать это end-to-end доказательством. UNKNOWN имеет тот же блокирующий эффект,
что FAIL. Code/CI PASS не закрывает OPS/REAL DEVICES/LIVE quality части gate.

## 3. Предлагаемая программа acceptance

Пороговые значения ниже — **новое предложение для review**, не действующие SLO
и не показатели, уже достигнутые проектом. Перед испытанием их нужно утвердить.

1. Не менее трёх consenting людей: requester/helper/наблюдатель; сначала synthetic
   staging. Web, Mini App и Bot: один ID после linking, общий контекст, повторный
   initData/update не дублирует операции. Участник знает ограничения ИИ.
2. ≥50 разнородных русскоязычных реплик за ≥2 сессии/перезапуска, включая correction,
   veto, deletion, чужую потребность, отказ, возврат к теме, factual uncertainty.
   Считать каждый provider call, timeout, repair, output completion и полную latency.
   Предложение: ≥95% завершённых ответов, p95 ≤20s в согласованном test window;
   отдельно оценить cost/token caps. Старый 12-turn smoke недостаточен.
3. Нулевая допустимость cross-user disclosure, скрытой публикации, финансового
   действия или ложного подтверждения сохранения/отзыва. Любой случай — NO-GO,
   даже если latency хорошая. Provider failure не повреждает history/memory.
4. Независимое ручное review synthetic transcripts по связности, естественности,
   повторам, завершённости и сохранению намерения. Не отправлять чужую память или
   идентификаторы в сторонний LLM для такой оценки. Real transcripts только по
   отдельному согласию и согласованной retention policy.
5. Chrome virtual authenticator остаётся regression; реальные Chrome/Android,
   Safari/iOS и Telegram WebView перечисляются в поддерживаемой matrix отдельно.
   Неподдерживаемый WebView не должен блокировать альтернативный ранее подтверждённый
   вход. Не обещать local biometric uniqueness или passport verification.
6. Crash/backup tests: delete/correct memory, revoke session/passkey/link, account
   merge, new help intent/withdrawal, TTL, disk errors, lost server/key, old backups.
   После restore нет воскрешения удалённых фактов или отозванного доступа.

## 4. Что не требуется для первого MVP

Sovereign Identity 07.1, threshold recovery 07.2, Genesis public proposals,
ISTINA/ATTEST, Consensus 7, Solana, federation mandates, separate Node operators,
wallets и real payment rails остаются вне scope. Их flags OFF, endpoints и команды
не должны незаметно включаться миграцией. Это не снятие их security требований.

| Экспериментальный scope | Текущий предел | Условие отдельного GO |
|---|---|---|
| 07.1 | isolated_test, flags OFF | Device custody + reviewed ownership/bootstrap/rotation/recovery |
| 07.2 | Evidence protocol, authority activation blocked | Independent atomic authority commit и notice/delay/dispute infrastructure |
| Genesis #14 | Local declared assets/proposals, explicit approval | Public privacy/withdrawal/retention review и independent provenance |
| 08.1 | Offline conformance | 73 vectors/reference остаются regression |
| 08.2 | Local stateful lab, S12 NOT EXECUTED | Independent operators, authenticated remote transport, qualified retention anchors |

Федеративная production-ready система требует дополнительных gates и отдельного
решения. Наличие этих незавершённых задач не делает централизованный pilot
невозможным навсегда; сегодня его блокируют G00–G11, а не необходимость mainnet.
