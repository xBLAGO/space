# Context fencing — threat model и границы

Scope: exact synthetic source `47af53e0052a2f67efbef9108c3987b2439efc04`, установленная `foundation_privacy_context_001`, общий canonical SQLite и original journal/witness. Это same-author review, не независимый аудит. Реальный пилот NO-GO.

## Инварианты

1. Генерация, подготовленная на version `v`, не имеет durable effects после сериализованной invalidation `v`.
2. Version check, memory effects, обе реплики, generation terminal state и Telegram receipt фиксируются одной SQL transaction. Нельзя сохранить только user turn при conflict.
3. Во время provider/extractor вызовов нет открытой SQLite write transaction. Все memory mutations — в RAM до final commit.
4. Generation ticket не является session, mandate или Help confirmation. Один rejected/committed ticket не может снова выполнить эффекты.
5. Retained journal frontier и restore invalidation доминируют над version/tickets из старой app copy.
6. Неопределённость, unavailable controls, authority conflict и unexpected control writer не дают ACK старого ответа.

## Доверие и атакующие

| Субъект / отказ | Проверяемая защита | Остаточный риск |
|---|---|---|
| Независимые OS workers с разными locks | Durable revision/epoch + BEGIN IMMEDIATE CAS | SQLite writer throughput, hot users и большая journal chain требуют capacity tests |
| Worker вернул ответ после memory delete/edit | Mismatch отклоняет staged effects и пару; terminal metadata | Уже выполненный внешний LLM call нельзя отменить задним числом |
| Два одновременных turn одного user | Первый commit меняет context; второй отвергается | Потеря работы LLM и необходимость ручного повторения; автоматического retry нет |
| Подмена UID при действующей чужой session | HTTP UID server-derived; authorization повторно сверяется с captured owner | Внутренний `app.chat(uid)` без token — trusted adapter API, не новая публичная точка доступа |
| Отзыв session/channel/credential/consent | Triggers + repeated authority checks; session expiry checked at final boundary | Payload, разрешённый до revocation, может находиться в сети после ACK |
| Telegram duplicate / lost ACK / crash | Atomic event receipt; version-bound replay; no regeneration for claimed/rejected update | При crash до commit возможен безопасный недоставленный ответ; нужен новый user update |
| Old application backup | Replay retained frontiers, REJECTED tickets, restore epoch change | Позитивная история, которой нет в backup, не реконструируется из control journal |
| Отдельная DB copy пишет одновременно | Journal expected_head CAS — fail closed при изменении frontier в commit window | Это не multi-primary protocol; serving двух app copies запрещено процедурой recovery |
| Partial/corrupt schema/version/key/witness | DDL/checksum/FK/frontier validation и существующие HMAC/witness guards | Нельзя считать наличие каталога доказательством независимого custody |
| LLM prompt injection / malformed extraction | Provider не получает DB/Help tools; safe validation, buffered writes, server authorization | Качество/правдивость LLM не доказывается fencing |
| Полный захват сервера или HMAC key | Вне гарантий локального version protocol | Оператор с контролем кода/ключей может обойти проверки; G03/G08 не закрыты |
| Согласованный rollback app+journal+witness | Без свежей внешней вершины локальная проверка не может доказать rollback | Нужны независимые retained controls и operational drill; не инициализировать новую «вершину» вместо старой |

## Приватность и retention

Новые таблицы не содержат prompts, answers, memory values, credentials, cookies или upstream traces. Они содержат внутренний owner FK, random generation ID, channel/update reference, version/epoch и terminal state. Это технические персональные metadata; они не публичны, не используются для fingerprinting и не являются доказательством физической уникальности.

Account deletion удаляет generation rows пользователя и сохраняет минимальный context-version tombstone вместе с существующим deleted user marker/control lineage, чтобы не разрешить stale work. Whitelist personal export не расширен чужими sessions или generation internals. Политика реальной metadata retention/доступа требует отдельного утверждения (G04 UNKNOWN); скрытой новой telemetry нет.

Memory delete не тождественен удалению chat history. Если нужно стереть историю, применяется прежний clear-history/account deletion flow. Fencing запрещает фиксацию ответа по снимку, устаревшему в ходе generation; он не обещает, что новая модель не узнает факт из оставленной пользователем старой истории.

## Атомарность и аварии

`F` начинает writer transaction до сравнения version. При `D < F` mismatch; при `F < D` deletion ждёт SQL commit и затем инвалидирует context/cache. Barrier test фиксирует оба порядка. Deferred SQL work и reply receipt не выходят за transaction boundary. После SQL rollback нет частичной пары.

Control journal append предшествует app SQL commit: эти две БД не становятся общей distributed transaction. Поэтому journal→SQL crash трактуется как uncertainty/denial: PREPARED отвергается, история не реконструируется и success не имитируется. Journal-head CAS проверяется под тем же file lock, что append, и не заменяет independent witness.

## Приёмка и нерешённые gates

До G09 PASS требуются baseline FAIL, after PASS в >=2 actual processes, serialization tests обоих порядков, crash/replay/restore, несколько store instances, Web/Bot/synthetic Mini App и полный regression/Docker CI exact SHA. Эти доказательства перечислены в `test-results.json`, `ci-evidence.json`, `after-probe-ci.json` и отчёте.

G09 PASS scoped: только installed fencing и проверенный synthetic topology. Не даёт production approval, independent operator assurance, physical-device acceptance, live HF quality, capacity qualification или safe-downmigration разрешения. Все прочие gates #22 сохраняют свои ограничения.
