# Threat model delta — Living Deeds 02

Trust boundary: один локальный оператор приложения, SQLite, server-HMAC, privacy journal/witness и pinned checkpoint. Подтверждённая session не равна уникальности физического человека. Администратор с доступом к приватной SQLite/ключам входит в доверенную границу; защита от такого полного компрометационного доступа не доказана.

| Угроза | Контроль | Доказательство / предел |
|---|---|---|
| LLM публикует частную зацепку | candidates read-only, owner session, MATCH_PRIVATE, нет автоматического memory→NOTE→invite | owner-memory isolation test; живой LLM NOT EXECUTED |
| Подмена UID / agent / модели | UID из существующей session; mandate owner + agent match; model_label не участвует в ACL | owner isolation / model-independent test |
| Исправленный/удалённый memory claim | чтение актуальных memory_facts, semantic status, revision/epoch source_tag; копии не создаются | correction/deletion/restart test; G09 generation test |
| Приглашение без подтверждения | одноразовый session-bound prepare/commit, review task/recipient/referrer, версия permission/bridge | minimal preview test + inherited Living01 challenges |
| Утечка личности Лены автору потребности | BRIDGE/mandate/delivery owner-private; requester's read model не меняется | сравнение полного B view до/после приёма/отказа |
| Избыточный recipient payload | только approved task, referrer code, receipt, contact_only, resource_available=false | exact key set test; operator metadata остаются доступны оператору |
| Retry / lost ACK | одно использование мандата, stable delivery ID/receipt, local ACKED event один | restart/lost-ACK и two-process pull tests |
| Поздний ACK оживляет отказ | каждый pull проверяет BRIDGE INVITED, active mandate, lifetime, holds; receipt не полномочие | decline/revoke/cancel/expiry tests |
| Молчание приводит к спаму | expiry deny сразу; явный silent-close retained no-contact; запрет queue при старом expired unanswered bridge | silence test; безопасное снятие suppression пока отсутствует |
| Crash до commit / отказ диска | исключение выходит без ACK; existing atomic journal/UEL boundary | injected disk / crash-before-SQL tests |
| Откат SQL после revoke | retained frontier, no positive reconstruction, UNKNOWN_RECOVERY | old-backup restore test |
| Удаление Лены оставляет адресацию | purge_member + dependency retire bridge/mandate/delivery | recipient deletion test |
| Старый binary обходит новую schema | preflight проверяет optional schema/ledger; старые checksums не изменяются | migration parity + fail-closed baseline; downgrade не поддержан |

Приватный источник не является утверждением доступности ресурса. Последующее ResourceOffered требует собственного согласия носителя. Результат помощи подтверждается только каноническим Help, не receipt доставки. Нет социального рейтинга, платежа или публичного профиля третьего лица.

Остаточные риски: некриптографическая аутентификация самого агента (он не получает исполнительных прав); тайминги и размер локальной БД видны оператору; ошибочный одобренный человеком forwarding text; локальная SQLite без шифрования at rest; недоказанная независимость checkpoint operator; неизвестные реальные политики внешних каналов. Telegram/email transport, push idempotency и partition safety не реализованы и не заявлены.
