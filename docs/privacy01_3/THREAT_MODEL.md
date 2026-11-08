# Privacy 01.3 — threat model delta

Draft / same-author security review. Base: Privacy01.2, runtime source `9abb7cc7b22346be0a9b095e7f13b1dc81928e28`. Это дополнение, не замена threat model 05.4/G09.

## Активы и доверие

Сохраняем privacy содержимого, consent, целостность пар истории, Help consent/state, одноразовые grants и monotonic denials после restore. SQLite application volume может аварийно откатиться; original HMAC journal/key/head и retained witness должны пережить такой rollback. Они не становятся независимыми только из-за разных директорий. Host root с доступом ко всем ключам и контрольным копиям, компрометация всей control lineage, rollback всех независимых anchors или злонамеренный custodian не устранены этим протоколом.

Auth/session/CSRF не заменяются. Ни LLM, ни Help conversation intent не получают новых полномочий. Retention — привилегированная offline/synthetic maintenance operation. Все row IDs случайны/внутренние; HMAC targets остаются потенциально персональными метаданными. События прежних видов, в том числе создаваемые при новой очистке, сохраняют прежние псевдонимные owner/time metadata; нулевое время и пустой owner относятся только к новым `lifecycle_*` events.

| Угроза | Контроль | Проверка | Остаточный риск |
|---|---|---|---|
| Старый SQL backup возвращает plaintext/retired credentials | Durable denial + row guards + replay before access | History/receipt, memory archive, needs, Help, activity, revoked credentials, pre-migration snapshots | Потеря свежих independent controls => отказ, не успешное восстановление |
| Worker сохраняет ответ на удалённой истории | Старый G09 revision/epoch CAS, deletion/consent bumps | Retention during LLM, pending ticket expiry, withdrawal conflict; прежние 34 context tests | Already delivered text нельзя удалить у клиента/provider |
| Межпроцессные duplicate sweep/race | BEGIN IMMEDIATE и единый original journal frontier | Два spawn процесса, суммарно одно удаление; full regression | Большая партия удерживает writer lock; throughput/SLA UNKNOWN |
| Crash между journal commit и SQL | Retained denial replay, никогда не «отмена» denial из старой SQL | os._exit(81) после flush до commit, restart/preflight | При недоступном witness outage; нельзя заново init control state |
| Fsync/journal write error | Отказ без подтверждённого успеха, SQL rollback если intent не durable | Synthetic append failure, missing witness | Реальные power-loss/storage semantics не доказаны synthetic exception |
| Подмена policy/checksum/hold | Exact DDL/version/policy validation и signed-denial consistency | Drift и missing tombstone negatives | HMAC key holder может подделывать control history; нужен отдельный custody boundary |
| Account re-registration возвращает память | Новый внутренний ID, старый tombstone/revocations | Same username после deletion, пустые history/memory | Сознательное повторное сообщение того же содержания не устраняется |
| Удалённый Telegram receipt теряет dedupe при /start новой учётки | HMAC consumed-update hold, durable lifecycle_update event, begin_update + SQL guards | Воспроизведение FAIL до исправления; Web reenroll/restore и synthetic Bot /start replay PASS | Неизвестные IDs, удалённые до установки без markers, не ретроспективно доказаны |
| IDOR/CSRF в consent | fresh session-derived uid, body whitelist, CSRF/origin | HTTP403 без CSRF,400 extra uid,200 own withdrawal | Compromised active session всё ещё имеет права; existing reauth lifecycle важен |
| Optional data появляются после withdrawal | SQL INSERT/UPDATE consent checks + replay purge | Activity/device labels negatives, active auth сохраняется | Нет общего нового consent manager; новые purposes требуют отдельного проектирования |
| Экспорт чужого текста | Personal whitelist/audience checks, lifecycle fail closed для legacy economics | Export after retirement, blocked economic/merge ownership | Уже скачанный экспорт участника вне server control |
| Retention стирает действующий факт вместе со старой версией | Narrow archive operation, detach descendant, exactrow denial | Coffee preference survives sweep+restore | Все store writes должны проходить guarded writer; raw trusted-admin SQL не является разрешённым API |
| Help evidence повторно идентифицирует людей | Удалены text/notes/participants/times, no public endpoint, private access | Strict summary field set, purge private Help graph | HMAC correlation с request ID/старым backup, event count и старый journal. **Не anonymous** |
| Подмена backup path/hash | Canonical root, symlink/hash checks, retirement durable до unlink | Changedbytes и symlink negatives | Filesystem adversary с правом переименования во время unlink: hostprivilege boundary; не secureerase primitive |
| Незавершённая помощь учитывается как успешная | Outcome из committed state; incomplete отдельно | Incomplete Help retirement | Outcome фиксирует взаимное подтверждение в системе, не реальный факт исполнения |
| Ранняя очистка через часы | now integer не future, policy drift rejected | Future clock negative | Недостоверные системные часы/невалидные legacy timestamps требуют operational monitoring |

## Review conclusion

Синтетические negative tests сохраняют G09 и прежние journal/witness guarantees. Новые migration checksums не заменяют старые. Сам review выполнялся автором изменений; **independent review NOT EXECUTED**. Не проводились реальные DDoS/penetration, physical disk erasure, power cut, physical Telegram/passkeys, production log/copy inventory или downstream provider deletion.

G04 UNKNOWN. Перед real-user release: утвердить policy/минимизацию metadata, разрешить legacy blockers, квалифицировать независимую custody, scheduler и fault/restore на целевом host, провайдера и реальные клиенты. Production pilot NO-GO.
