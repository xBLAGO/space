# Foundation Privacy 01.2 — Cross-worker Context Fencing

Draft, 2026-11-07. **G09 PASS в проверенном fenced synthetic runtime. Реальный пилот остаётся NO-GO.** Merge/deploy, production DB, реальные данные, webhook и секреты не изменялись.

## База и результат

Проверены exact HEAD [PR #22](https://github.com/xBLAGO/space/pull/22) `77c7f7f0495a7bb383b1c75c63bf5c97cb984129` и runtime source `d2294c1b1c7b082c6dfcfe1eb788bb6e8d12285b`. Оба CI документационного HEAD (`42268869991`, `42268859547`) success. Diff runtime→HEAD содержит только docs/evidence; исходный G09 FAIL повторён до правок, [before-probe.json](privacy01_2/before-probe.json) сохранён.

Новый source candidate: **`47af53e0052a2f67efbef9108c3987b2439efc04`**. [Source CI 42412874566](https://github.com/xBLAGO/space/actions/runs/42412874566): tests/Docker success, **390/390 tests**, 62.529s. Это прежние 356 + 34 новых теста. [Candidate identity](privacy01_2/candidate-identity.json) содержит точные source/tree, установленную SQLite и dependency/migration manifests; [CI evidence](privacy01_2/ci-evidence.json) — jobs, image ID и artifact digest. Предыдущие gates и отчёты #19/#20/#22 не переписаны.

Исходный probe: `stale_context_written_after_delete=true`. Тот же сценарий после включения migration: `false`, `generation_rejected=true`, PASS в двух настоящих процессах. Дополнительно проверены три процесса, несколько store instances одного процесса, Web API, локальный Bot и synthetic Mini App exchange.

## Жизненный цикл и механизм

Раньше Web/Bot читали память/историю, вызывали LLM, затем отдельно изменяли память и сохраняли пару истории. `conversation_lock` был локальным `RLock` конкретного store, поэтому другой процесс мог завершить deletion между чтением и записью ответа. SQLite атомарность одной вставки пары не защищала весь цикл.

Теперь для БД с fencing schema используется `context_fencing.respond`:

1. Короткая guarded transaction сверяет авторизацию, читает `(revision, epoch)`, память и историю из одного snapshot; создаёт случайный generation ticket `PREPARED`.
2. Агент работает с RAM-снимком `BufferedMemory`. Set/delete/replace/clear и очистка истории лишь накапливают операции. Никаких memory/history writes во время LLM.
3. Каждый вызов основного provider и extractor получает тот же `ChatContext.context_version`. Перед вызовом проверяется ticket/version/authority. Внутренний ограниченный text repair сохраняет bound context. Версия не добавляется в пользовательский prompt, не является credential или разрешением Help.
4. Финальная `BEGIN IMMEDIATE` повторно проверяет version/epoch, `PREPARED`, active user и соответствующую session/channel authority. Затем на **одном SQLite connection** применяются все memory effects, обе реплики и, для Bot, processed response receipt.
5. Journal/witness flush и SQL commit завершаются до возврата ответа. Ticket становится `COMMITTED`; receipt привязана к post-commit revision/epoch. Никаких дополнительных LLM retries от fencing.

```mermaid
sequenceDiagram
  participant W as Worker генерации
  participant DB as SQLite + context version
  participant L as LLM
  participant D as Worker удаления
  participant J as Journal / witness
  W->>DB: Short TX: snapshot v, PREPARED ticket
  DB-->>W: RAM memory + history + v
  W->>L: Context bound to v; SQLite TX closed
  D->>DB: BEGIN IMMEDIATE, delete, v := v+1 / new epoch
  DB->>J: Durable denial + context frontier
  DB-->>D: COMMIT / deletion success
  L-->>W: Candidate answer
  W->>DB: BEGIN IMMEDIATE, compare v
  DB-->>W: Mismatch: REJECTED, no memory/history effects
  W-->>W: Safe conflict, no regeneration
```

Код: [engine](../src/xblago/core/context_fencing.py), [schema](../src/xblago/core/context_schema.py), [transaction reuse](../src/xblago/core/storage.py), [Web](../src/xblago/integrations/web.py), [Bot](../src/xblago/integrations/telegram.py). Переиспользование connection thread-local — способ собрать одну транзакцию, **не механизм межпроцессной блокировки**. Безопасность обеспечивают долговременный version check и SQLite writer serialization.

## Доказательство атомарности относительно удаления

Пусть `v` — snapshot version, `D` — transaction invalidation/deletion, `F` — финальная transaction генерации. Обе используют `BEGIN IMMEDIATE` на одном canonical SQLite-файле. SQLite допускает одного writer; порядок writer transactions задаёт допустимый порядок этих операций. Основание: [официальная документация transactions](https://sqlite.org/lang_transaction.html).

- Если `D` committed раньше `F`, trigger/explicit invalidation уже изменили revision/epoch. `F` видит mismatch и сохраняет только terminal REJECTED metadata. Ни пользовательская, ни assistant-реплика, ни memory mutation не сохраняются.
- Если `F` первой получила write lock, `D` не может изменить память между сравнением и записью пары. `F` commit логически предшествует `D`; после него deletion инвалидирует будущие generations и повторную выдачу старой receipt.
- Ошибка второй вставки истории откатывает первую вставку и все staged memory effects. Никакой самостоятельной nested BEGIN/commit у helpers в этом участке нет: они используют внешний connection.

Оба порядка проверены отдельно: deletion непосредственно перед final commit; и barrier после CAS перед append_exchange, пока другой connection пытается удалить факт. Во втором случае deletion завершается только после снятия barrier и SQL commit пары. Это тест serialization, не таймерная попытка «попасть в гонку».

Для context-enabled stores `SecurityJournal.append(expected_head=...)` дополнительно проверяет прежнюю вершину под journal file lock. Если другой control writer изменил journal между проверкой и flush, старая SQL transaction откатывается с fail-closed ошибкой. Проверен и конфликт двух app copies, разделяющих один control lineage. Это не поддержка active-active split-brain: canonical DB и offline restore остаются обязательными.

## Схема и инвалидирующие операции

Явная аддитивная migration **`foundation_privacy_context_001`**, checksum `79aed28a7444a8a4758d8133033dd636dee6a3a214c877faba338da315434aa0`. Старые Identity/Privacy/Pilot/Help DDL и checksums не меняются.

| Новая таблица | Содержимое |
|---|---|
| `context_versions` | Одна строка на внутренний user: monotonic revision + случайный epoch; FK users |
| `context_generations` | Случайный ticket, owner FK, captured version, terminal state, channel/update ID, committed version; без prompt, ответа или токена |
| `context_schema_migrations` | Версия/checksum явной миграции |

SQL triggers охватывают memory_facts и semantic_memory, dialogue_turns, profile changes, channel links, credentials/passkeys, consent, user status и изменения session authority. Создание/удаление сессии, expiry/CSRF/reauth changes инвалидируют контекст. Обычный `last_seen` — нет. Изменение ownership инвалидирует обе стороны. Merge закрывает pending generations обеих сторон; пользователи и память сохраняются согласно существующим merge controls.

Явные delete/clear memory и clear history повышают version **даже при отсутствии строки**: старый in-flight turn не должен создать факт после уже принятого запрета. Собственные staged изменения и history commit повышают version внутри final transaction; они не конфликтуют сами с собой. Другие pending generations того же пользователя после этого должны быть отклонены. Другой user не меняет его version.

Help entities не добавляются в LLM context и не становятся новыми полномочиями агента. Help prepare/commit, exact text/version/audience, fresh-auth и confirmation semantics не меняются. Полный fenced Help cycle проверен browser tests.

## Conflict, crash и replay

Web/Mini App: HTTP 409, `status=context_conflict`, без `reply`/Help suggestion. Обе реплики отсутствуют в серверной истории. UI убирает optimistic pair, возвращает исходный текст в пустое поле, сохраняет уже набранный новый черновик и показывает безопасный статус. Повтор возможен только по решению человека; автоматического пересоздания контекста/отправки нет.

Bot: response cache, pair и ticket фиксируются одной transaction. Повтор update не вызывает LLM. Cached answer выдаётся только при matching current version и active owner; иначе возвращается content-free conflict notice. Rejected/processing-crash update не перезапускается автоматически и не создаёт вторую пару. Человек может отправить новое сообщение. `/start`/`/link` не превращаются в LLM authorization.

При смерти worker до commit буфер памяти исчезает; PREPARED ticket не даёт права повторно записать результат. После commit и до transport ACK повтор Telegram update возвращает durable bound receipt, без новой generation. После journal append, но до SQL commit retained frontier отвергает pending ticket при reconciliation; позитивный ответ не реконструируется из журнала.

Fencing гарантирует порядок фиксации и повторной выдачи cache. Он не отзывает уже отправленный HTTP/Telegram payload, увиденный текст, upstream provider context или человеческую память. Отдельное удаление semantic fact не обещает удаления всех старых chat turns: правила `/clear-history` и account deletion остаются прежними.

## Recovery и migration/rollback

Новый критический journal event `context_version` содержит keyed owner/target и version/случайный epoch — без memory content. Проверяется retained frontier, а не только число из app backup. Reconciliation отклоняет pending tickets. Explicit restore дополнительно меняет epochs/revisions всех users и закрывает PREPARED, даже если в backup нет нового deletion event. Старый worker на исходном DB-файле также видит новый control frontier и не может завершить прежнюю generation.

Backup до migration при наличии retained context events не обслуживается автоматически. Явная offline migration создаёт новые таблицы и **reconciles старые checkpoints до публикации новой baseline**, а не сбрасывает version в 0. Повтор migration unchanged; DDL failure rollback, integrity/FK checks проверены. Пользовательские ID/history/memory не переносятся в новую регистрацию.

Миграция не запускается при startup. `XBLAGO_CONTEXT_FENCING_REQUIRED=true` требует её наличия. При существующей schema fencing действует независимо от значения этого флага; выключение env не обходит installed fencing. Legacy DB без migration остаётся compatibility path и **не получает G09 PASS**. New Help lab и проверочные mobile/desktop tools включают migration явно. [Runbook](privacy01_2/RUNBOOK.md).

Безопасный rollback — stop writers/ingress, сохранить original controls и исправить совместимым forward release. #22 binary не понимает новые critical events и новый schema shape: его fail-closed отказ правильный. Не удалять migration/events и не откатывать journal/witness вместе с app snapshot ради запуска старого кода. Independent custody, host loss и скомпрометированный оператор остаются ограничениями 05.4.

## Результаты и влияние

- Исходный G09 FAIL и corrected PASS сохранены отдельно. 34 новых tests проверяют 2–3 процесса, несколько store instances, два порядка writer serialization, memory buffer rollback, user isolation, authority changes, account deletion, merge, worker death, restart, Telegram replay и journal/backup crash windows.
- Source CI: **390/390 PASS**, Docker tests/library/cycle/lab PASS. Local полный suite перед последними additions: 383 PASS; focused 32 PASS и два последних targeted cases PASS; окончательный полный source run **390/390 PASS**, 255.595s; детали в [test-results.json](privacy01_2/test-results.json).
- Desktop Help: **11/11** checks PASS. Synthetic mobile/Mini App: **14/14**, включая real handler 409, no orphan pair, draft restoration, exactly one provider call. Physical Telegram iOS/Android, physical passkeys и independent operators не тестировались.
- Local source benchmark, 8 turns каждого режима: median **23.164 → 48.640 мс**, delta **+25.476 мс**; local provider, 0 внешних вызовов. Delete завершился за **26.819 мс**, пока другой процесс удерживал generation в provider. Это не qualification production capacity или реальной network latency.
- Дополнительных LLM calls на успешный ход нет. Цена конфликта — уже выполненная генерация может быть отброшена. Автоматического retry нет. Сканирование/проверка существующего journal и рост metadata увеличивают local I/O; большие базы/load требуют отдельной квалификации.

## Threat model и решение

[Threat model](privacy01_2/THREAT_MODEL.md) различает concurrent trusted workers, untrusted requests/provider output и компрометацию control plane. Это same-author review, не независимый security audit. [Gates](privacy01_2/gates.json): G09 PASS **только для активированной migration и проверенного synthetic topology**; performance/load qualification отдельно UNKNOWN.

Реальный пилот **NO-GO**: G02/G03 host/fsync/independent custody, G04 retention, G05 physical devices, G06 live provider, G08 independent review, G10 operational drill и G11 approved real-user runtime не закрыты. Платежи, wallets, токены, экспериментальная identity/federation не включались.
