# Threat model delta 09.2

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| Один контроллер захватывает управление | Fixed electorate, >2/3 supermajority, distinct keys/UIDs, delay | Сговор полного кворума или Sybil участников не исключён |
| Снижение quorum через массовый exit | Denominator и security floors не уменьшаются | Возможна долговременная потеря доступности при потере достаточного числа keys |
| Proxy захватывает кворум | Один principal и один фактический signer, scoped TTL/amount, no GOV delegation/no chains | Реальная независимость ключедержателей не доказана |
| Старый policy/approval после активации | CAS + exact policy hash + invalidation незавершённого | Правила нового governance всё ещё требуют социальной легитимности |
| Узел выдаёт себя за человека | Отдельные domains/key, privacy certificate разрешает только exit | Компрометация узла может вызвать отказ в обслуживании/ложные отрицательные claims |
| Старый backup возвращает exit | Оригинальный privacy replay, UID unlink, frozen fund hold, retained UEL frontier | Без новых доказательств положительные операции не возобновляются |
| Удаление одного стирает фонд остальных | v2 запрещает общий erase членом, удаляет только binding и отзывает зависимые grants | Псевдонимные signatures/public keys сохраняются. Реальная политика retention ещё не утверждена |
| Независимый verifier повторяет ошибку runtime | Отдельный код без runtime imports, differential positive/negative tests, subprocess | Оба написаны одним автором по общему контракту. Внешний аудит не выполнен |
| Подмена UEL/header/history | Signed checkpoint + inclusion + source causal chain + actor signatures + exact state hashes | Private HMAC payload binding доверяет отдельно pinned node attestation |
| Усечение / самоприсланные pins | Обязательные external checkpoint hash, policy, stream, custodian/node keys | Без независимого канала закрепления текущая свежесть не доказана |
| Новая отрицательная authority меняет экономику | Exit/revoke только снимает доступ/незавершённый резерв, не подтверждает деньги | Документ не определяет юридическую долю/возврат собственности при выходе |
| Clock manipulation | Source nondecreasing recorded_at, signed expiry, activation delay | Часы узла остаются trust assumption, независимого времени нет |
| Утечка via audit | Private fresh identity + direct GOV, opaque чужие leaves | Количество событий, суммы и pseudonyms имеют reidentification risk |

Все данные/ключи синтетические. Никаких реальных consent, платежей, социальных рейтингов или нового сбора активности. G04/G09 и прежние release gates не повышаются этим отчётом.
