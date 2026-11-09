# Threat model delta — operational retention

Draft; same-author review. Foundation05.4 journal/witness и Context01.2 остаются корнем privacy authority. Ни scheduler, ни ops logs не выдают разрешение агенту или участнику и не заменяют user consent.

## Границы доверия

1. Synthetic lab marker/config + локальный оператор: explicit initialization, fixed canonical paths, local provider. Маркер не является доказательством отсутствия реальных данных в произвольной вручную скопированной DB; rehearsal создаёт новый fixture, production paths/credentials не использует.
2. POSIX flock и private operations SQLite: сериализация и расписание внутри одного проверенного host/filesystem. Не распределённая lease, не cluster coordinator и не защита от root/key compromise.
3. Application SQLite: только прежний preflight/guarded transaction, original journal/witness, G09 revisions/epochs. Не держится app write transaction на всём LLM call или всём scheduler loop.
4. Original controls вне app snapshots: application rollback допускается при свежих controls; rollback всех controls вместе с независимой вершиной не доказуем. Same-host directories в наших тестах не независимые rollback domains.
5. External storage/log/provider/client boundaries: metadata и публичные terms не remote erasure proof.

| Угроза | Контроль / negative test | Остаточный риск |
|---|---|---|
| Непреднамеренный production cleanup | OFF default, explicit synthetic marker, local-provider configuration, no app startup import | Trusted operator с правами файловой системы может ошибочно подложить реальные данные; это не допускается процедурой |
| Два worker / процесс остановлен дольше lease | Nonblocking flock удерживается через backup/sweep/result; real spawn concurrency | NFS/object mounts с неизвестным locking не квалифицированы; force-unlink lock запрещён |
| Crash до/после app commit | Durable RUNNING/attempt до действия, INTERRUPTED при рестарте; old-copy replay | Процессный crash не имитирует power cut/storage loss; реальный host нужен отдельно |
| Ложный success при потере witness | Проверка до попытки и после app commit; UNKNOWN/FAILED и no last_success | Потеря сразу после проверенного checkpoint — отдельное событие; perpetual availability не обещается |
| Ошибка app backup / ENOSPC / empty receipt | Backup обязателен, registered/hash-checked, отсутствие proof блокирует cleanup | Нет доказательства fsync конкретного production storage из local copy API |
| Orphan после crash между file write и registry | Сверка managed directory/canonical catalogue; unknown/missing/changed files требуют review | Неучтённые offsite/другие directories вне сканируемого scope; нельзя считать их erased |
| Missing/corrupt ops result store | No SUCCESS, persistent last run остаётся неподтверждённым; no silent init | Ops logs не immutable signed ledger. Их rollback/подмена не могут разрешить restore данных, но могут повредить наблюдаемость; G04 не вычисляется из них |
| Clock rollback / повторные retry / false overdue reset | Clock guard, bounded attempts/backoff, reschedule сохраняет last_success/first_due | Значительный forward clock jump и реальное SLA требуют host clock supervision |
| Подмена schedule или чужой control journal | Policy tag HMAC с existing key, version/schema checks, journal-id/frontier binding | Holder original key остаётся доверенным; HMAC не защищает от полного host/key compromise |
| Журнал очистки раскрывает диалог | Только whitelist aggregate integers и reason enums; строки исключений/paths не пишутся | Время/малые counts — metadata корреляции, private TTL7d; host stdout copies отдельно |
| Sweep пишет stale LLM answer | Неизменённый G09; scheduler inside generation + full concurrency regression | Уже доставленный ответ и данные provider/client не отзываются серверной транзакцией |
| Prune ops logs удаляет privacy denials | Раздельные stores/TTL; holds неизменны и replay тестируется | Global pruning controls не реализован; это отдельная доказуемая retirement procedure |
| Re-identification Help | Синтетические positive attacks: key+backup exact link, unique event count side channel | Evidence псевдонимны. Field minimization не является анонимизацией; нет public summary endpoint |
| Existing application logs содержат PII | Inventory отмечает raw `str(exc)` в Telegram diagnostics и отсутствие production evidence | Отдельные canary/redaction и host/proxy logging reviews нужны до real pilot; новые safe ops logs не решают legacy scope |

## Решение

Linkage probe — демонстрация на трёх synthetic requests, не статистическая оценка анонимности популяции или реальной частоты повторной идентификации. Positive result доказывает возможность связывания при заявленной side information, а не измеренный риск для любого реального участника.

Новые tests/rehearsal PASS означают корректный локальный отказ/повтор/логическое удаление в заявленной модели. Это не independent security audit, не выполнение privacy policy, не physical erasure, не independent custody и не provider deletion. G04 UNKNOWN; G09 только inherited activated synthetic scope. Реальные пользователи NO-GO.
