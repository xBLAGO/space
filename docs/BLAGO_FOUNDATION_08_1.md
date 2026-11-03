# Foundation 08.1 — Protocol Baseline & Conformance Design

**Draft; profile `bop-0.1-lab.1`. GO для offline conformance и подготовки
синтетического эксперимента. NO-GO для production, экономических действий,
публичной федерации и заявления о работе независимых операторов.**

Основание: [аудит PR #15](BLAGO_FEDERATION_AUDIT.md) и
[требования Open Protocol 0.1](BLAGO_PROTOCOL_0_1_REQUIREMENTS.md),
SHA `03cdbf82ec45ee10ec45567ba60f3bfb06fbca5f`.
Новая ветка: `docs/foundation-08-1-protocol-conformance`, base — ветка аудита.
Runtime `src/`, production SQLite, текущие API и deployment workflow не меняются.

## Спецификации

1. [Baseline и сущности](protocol_0_1/BASELINE_AND_ENTITIES.md): актуальная цепочка,
   безопасные срезы, разные Human/Identity/Agent/Node/Mandate/Federation.
2. [Envelope и signing bytes](protocol_0_1/ENVELOPE.md): закрытый JSON-профиль,
   domain separation, payload, ссылки, limits и порядок проверок.
3. [Mandate, freshness и отзыв](protocol_0_1/MANDATE_AND_AUTHORITY.md): выдача,
   одноразовый scope, owner log, fresh status challenge, ACK, partition/restore.
4. [Переносимость](protocol_0_1/PORTABILITY.md): отдельный signed export/import
   manifest, key history, privacy и безопасный handoff без чужих sessions/secrets.
5. [Threat model](protocol_0_1/THREAT_MODEL.md).
6. [Conformance matrix](protocol_0_1/CONFORMANCE.md): что проверено и что остаётся
   design-only. [Golden decoded example](protocol_0_1/conformance/golden.json),
   [wire vectors](protocol_0_1/conformance/vectors.jsonl).
7. [Первый эксперимент A ↔ B](protocol_0_1/EXPERIMENT_AB.md): разные keys/stores,
   независимое управление и конкретные условия допуска.

## Реальная степень проверки

Подготовлены **73 исполняемых вектора**: 9 ACCEPT, 57 REJECT, 7 UNKNOWN.
Две отдельные reference-реализации проверяют одинаковые bytes/ожидания:
A — ручной ASCII encoder и cryptography/OpenSSL;
B — stdlib JSON encoder и PyNaCl/libsodium. Код не импортирует production BLAGO,
не открывает БД и не выполняет сетевые запросы. Криптография не реализуется вручную.

Обе реализации написаны в этой же сессии и используют общую нормативную corpus;
они также разделяют Python JSON parser и libsodium valid-point primitive;
это **не** независимая команда-аудитор, разные операторы или доказательство
свободы от общей ошибки спецификации. Внешние implementers должны проверить
векторы и реализовать verifier без копирования этих helpers.

[Результаты](protocol_0_1/conformance/results.json): 73/73 совпадений для каждой
реализации, parity PASS. Они являются pure decision functions, не durable inbox:
ACCEPT ещё не означает commit/доставку. Stateful ACK/crash/network/handoff cases
выделены как NOT EXECUTED, а не записаны в успешные unit tests.

Profile умышленно ограничен: частный `note.deliver`, одно применение, ≤2048 bytes,
один конкретный agent/node recipient, owner epoch 1, отсутствие redelegation,
платежей, публикаций, tool execution и передачи памяти. Ротация ключей и import
спроектированы отдельно, но этот wire verifier их **не активирует**: неизвестный
key epoch даёт UNKNOWN_KEY_HISTORY. Это предотвращает имитацию готового identity runtime.

Добавлен отдельный read-only Actions job только для synthetic corpus. Он не
импортирует приложение, не получает production secrets и не вызывает deploy.
Он использует уже применяемые проектом cryptography/PyNaCl как test-only пакеты;
`pyproject.toml` и production dependencies не изменяются. Его CI — conformance
fixtures, не GitHub/Docker/реальный A↔B acceptance всего приложения.

## Однозначное условие первого сообщения

Первое сообщение может быть принято **как частная неисполняемая заметка**, только
если одновременно выполнены следующие условия:

1. A и B согласовали именно этот профиль/политику и изолированный network ID;
   проверили независимые keys и owner identity binding вне входящего сообщения.
2. Человек отдельно подписал ограниченный mandate для agent A → agent B/node B;
   web/Telegram session и текст LLM не используются как authority.
3. Receiver B получил полную непротиворечивую owner authority chain и подписанный
   свежий status на свой непредсказуемый nonce, exact envelope hash и recipient.
   Mandate active; scope, object, recipient, TTL и use limit совпадают.
4. Bytes каноничны; подписи и все hash references проверены независимым кодом;
   unknown/unsupported/stale/fork не преобразуются в разрешение.
5. У B доступны trustworthy clock bounds и retained replay/revocation frontier
   вне откатываемого application snapshot. После восстановления без такого
   состояния B блокирует приём, пока не выполнено безопасное reconciliation.
6. B может атомарно и durable записать inbox event, consumption мандата, nonce и
   checkpoint **до** подписанного ACK. Revocation и delivery используют одну
   сериализованную границу: после ACK отзыва никакая новая доставка по старому
   mandate не принимается. Потерянный ACK не создаёт второй effect.
7. В согласованном payload только синтетический текст; никакого платежа,
   публичной публикации, автоматического tool call или скрытого private disclosure.

**Если хотя бы одно условие отсутствует, действие не выполняется.** Криптографическая
подпись без свежего mandate state недостаточна. До реализации durable receiver,
проверки независимыми операторами и negative drills условия 5–6 не считаются
доказанными — значит текущие fixtures сами по себе не разрешают живую федерацию.

Блокеры Foundation 05.4/06.1 не сняты. Merge, deployment, SQL migration, реальные
keys/пользователи, HF/model calls, платежи и токены в этом задании не выполняются.
