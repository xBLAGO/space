# Threat model — Living Deeds 01

| Угроза | Control | Остаточный риск |
|---|---|---|
| Заметка об unregistered третьем лице становится ресурсом | NOTE owner-only, отсутствует глобальный поиск / автопрофиль; draft не пишет | Автор мог включить сторонние сведения в свою частную заметку; PII-политика требует отдельного review |
| B узнаёт Лену из отказа или inbox | Bridge не виден B; summary не считает invitations; отказ даёт тот же B view | Авторизация аудитории сама по себе не доказывает анонимность малой группы |
| A раскрывает закрытую задачу без разрешения B | Автор B явно утверждает invitation_text; Help version binding | B должен осмысленно проверить preview; модель не доказывает полноту согласия |
| A согласился вместо Лены | Приём invitation только recipient fresh session; ResourceOffered только asset owner | Compromised session / server остаются угрозами базового IdentityRuntime |
| Отказ забывается после backup | Retained HMAC contact deny в прежнем journal/witness/checkpoint | Совместная потеря всех независимых controls не считается восстановлением |
| Старый или подменённый challenge / receipt | HMAC seal, exact fields, expiry, session, revision/epoch, source versions, one-time receipt | Same-author implementation, не independent audit |
| Два процесса берут один ресурс | BEGIN IMMEDIATE + проверка пересечения interval + общий Help accept | Не поддержаны дробные количества / multiple-capacity resources |
| Прямой Help accept обходит ресурс | Встроенный guard требует slot для bound offer | Действия вне новой привязки остаются обычной нефинансовой помощью Help |
| Ресурс отозван после подготовки | Повторная проверка active grant и точной canonical asset version | Не отменяет уже произошедшее физическое действие; только новые полномочия |
| Сбой между source и UEL | Одна SQL transaction, прежний write-ahead journal и hold | Durable journal ahead of SQL снижает доступность, не выдаёт разрешение |
| Восстановление потерянного согласия из hash | Только UNKNOWN_RECOVERY; никакого positive replay | Нет восстановления полного payload из UEL без private source backup |
| Повторный stock / fulfillment | Asset ID один; Help request ID один; LIVING исключён из old balance items | Нельзя считать authorized partial view переписью сообщества |
| Удалённая заметка остаётся в preview | Committed previews очищаются; erasure отменяет зависимые previews; G09 binding | Носитель старого backup должен независимо retired / очищен оператором |
| Подбор удалённого текста по technical digest | Idempotency body digest — keyed HMAC с owner scope, не открытый hash приватной заметки | Оператор с journal key и копией старых данных остаётся в trust boundary |
| Удаление occupancy освобождает непроверенный ресурс | Conservative recovery hold; положительные действия закрываются | Требуется manual review; локальная доступность может снижаться |
| Выход / deletion участника | Existing PrivacyService + purge ссылок и payloads + retained tombstones | Юридические основания удержания доказательств не утверждены |
| Репутационная иерархия | Только количество разрешённых результатов / споров; нет score/leaderboard | Позднейшая аналитика потребует отдельного fairness review |

Новые HMAC tags — локальные контрольные обязательства, не подписи человека и не
независимая верификация навыка. Подписанные checkpoint receipts подтверждают
целостность контрольной истории при живом custodian, не физическое исполнение.
Текущий custodian в lab — отдельный процесс/хранилище на одном host; независимость
операторов не доказана. Root metadata также может позволять корреляцию; не
публиковать streams/anchors автоматически.

В LLM-контекст не добавляются notes, inbox или private grants. Перед такой
будущей интеграцией нужны G09 invalidation для всех затронутых читателей,
prompt-injection review, consent-purpose review и живые cross-channel tests.
Флаг OFF и isolation сохраняются; нельзя использовать CI как допуск людей.
