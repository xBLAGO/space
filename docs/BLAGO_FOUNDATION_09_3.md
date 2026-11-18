# Foundation 09.3 — Private Collective Custody Lab

Статус: Draft, **первый безопасный вертикальный срез 09.3**. База — exact PR #37
`4706a60e7ec675ed1ae16b9c9cd551cde291cfd3`. Ветка
`feat/foundation-09-3-collective-custody`. Реальные люди, средства и production запрещены.

## Что реализовано

Эксперимент `experiments/capital_09_3/` сохраняет и проверяет существующие
приватные экспорты CapitalLedger/UEL на трёх узлах A/B/C с разными Ed25519 ключами,
SQLite-копиями и retained control files. Это контрольные квитанции над UEL,
**не второй финансовый журнал**: нет новых NEED, ownership, Help, денежных
операций, HTTP endpoints или application startup hooks. Базовые форматы и
миграции src не изменены. Флаг `XBLAGO_CAPITAL_CUSTODY_LAB_ENABLED` OFF по умолчанию;
для операций дополнительно требуется `XBLAGO_INTEGRATION_MODE=synthetic`.

Источником экономических событий остаётся CapitalLedger. В стенде используются
реальные вызовы существующих `CapitalReceived`/`CapitalVerified`, исходный UEL
shadow index и governor-only `proof_export`. Создаются 100 синтетических
участников, два лота 6+4 млн RUB, 1 млрд минимальных единиц. Внешних платежей нет.

Новый checkpoint требует **3/3** подписей custody-узлов после durable prepare.
Каждый узел проверяет исходные подписанные события отдельной реализацией
`tools/independent_capital_verifier.py`, неизменность полного предыдущего
префикса, UEL leaves и монотонность checkpoint counters. Сертификат связывает
fund, epoch/config, index/parent, hash полного пакета, history count/head и
существующий UEL checkpoint. Повтор последнего checkpoint возвращает исходную квитанцию; более старый replay безопасно отклоняется, конфликт
того же successor отклоняется. ACK возвращается после SQLite commit и fsync
отдельного signed control. Сбой между ними оставляет UNKNOWN/hold.

`tools/capital_custody_verify.py` отдельно реализует проверку custody-протокола,
не импортируя application или producer runtime. Для старых финансовых форматов
он переиспользует прежний независимый capital verifier. Это **другая реализация,
написанная тем же автором**, а не независимый аудит.

## Свежесть и восстановление

Offline proof проверяет целостность, но не текущую свежесть. Receiver создаёт
одноразовый случайный nonce и получает совпадающие подписанные статусы минимум
двух pinned nodes. Проверяются TTL30s, epoch/config/tip/index и отсутствие pending
prepare/recovery. Это работает при отсутствии исходного узла, если остальные
сохранили честное неоткаченное состояние. Ответы после partition не являются
разрешением потратить средства.

Независимый floor содержит `{index, tip, epoch, config}` и хранится вне rollback
domain источника и реплик. Отсутствие floor — UNKNOWN_RECOVERY, не нулевая история.
Rollback DB относительно control обнаруживается локально. Согласованный откат
DB+control выявляется по независимому floor/другим актуальным узлам. Если все
копии и все внешние доказательства совместно откатили/утратили, криптография не
может обнаружить забытое событие: такой запуск не имеет допуска к проверке.
Свежие nonce сами по себе не доказывают независимость хранения.

После отключения A и удаления его custody key B/C продолжают независимо
проверять **ранее закреплённую историю и проекцию фонда**. После разрешённой
смены custody epoch новая копия A2 импортирует ту же историю и баланс.
Ротация требует действующих прямых GOV signatures исходной политики (в fixture
4/5), двух прежних node votes и минимум60s после durable prepare каждого
подписавшего узла. Новый ключ, состав, parent checkpoint, old/new config,
not-before и expiry подписываются точно. Порог не уменьшается после потерь.

## Принципиальная граница: фонд ещё не независим для исполнения

**Не реализованы ротация owner/evidence/privacy anchors CapitalLedger и выдача
новых прав расходования после их потери.** Формат #37 прямо запрещает менять эти
anchors. Необходимы отдельный версионированный контракт, privacy-safe identity
rebinding, независимое подтверждение источника исполнения и authority cutover.
В этой итерации попытка подменить их через custody rotation отклоняется даже при
человеческих signatures. `spending_authority_restored` всегда false.

Следовательно, зависимость сохранности/проверки подтверждённой истории от
исходного сервера уменьшена; **полная цель децентрализации фонда ещё не достигнута**.
Копии не являются разрешением запустить другой CapitalLedger с импортированными
сессиями, HMAC secrets или выдуманными новыми owner grants. Это безопасное
ограничение, а не имитация восстановления фонда.

## Три узла и пределы независимости

Все автоматические проверки — один host, synthetic keys и один автор. Разные
процессы/пути не означают независимых операторов. Реальный трёхоператорский
эксперимент: **NOT EXECUTED**. Подготовлен operator-local packet CLI и отдельный
[runbook](capital093/RUNBOOK.md), без публичного сервиса.

Нельзя использовать 2/3 node signatures как нормальный византийский consensus:
пересечение двух таких кворумов может состоять только из злоумышленника. Поэтому
обычное закрепление требует3/3 и теряет доступность при partition. Исключительная
смена custody требует дополнительно человеческого governance quorum, а не
решения оператора. [Castro/Liskov, OSDI99](https://www.usenix.org/conference/osdi-99/practical-byzantine-fault-tolerance)
описывает иные условия safety/liveness BFT; эта лаборатория **не реализует PBFT**.

## Состав и доказательства

- [Протокол](capital093/PROTOCOL.md), [threat model](capital093/THREAT_MODEL.md), [security review](capital093/SECURITY_REVIEW.md).
- [Conformance matrix](capital093/CONFORMANCE.md), [recovery/operator runbook](capital093/RUNBOOK.md).
- `tests/test_capital_custody.py`: signatures, nonce, replay, partition, fork,
  truncation, quorum, delayed rotation, multiprocess races, crash и restore.
- [Evidence](capital093/evidence.json) и [results](capital093/test-results.json)
  содержат фактические результаты. Финальный SHA и Actions публикуются в Draft PR.
- `integration/candidate.json` расширен только source manifest: новые инструменты,
  тесты, experiment и CI. Старые migration checksums и golden vectors сохранены.

## Решение и следующий PR

**GO только для изолированного local synthetic custody lab при зелёных проверках.**
**NO-GO:** независимая федерация, полное восстановление полномочий фонда,
реальные пользователи, реальные средства и production. G04/G09 и предыдущие
release gates не повышаются.

Следующая итерация09.3B: формальный owner/anchor rotation + single-writer authority
cutover контракт, независимый review, затем отдельный синтетический PR. Не
использовать порог custody как замену полномочий человека или доказательству
реальности внешних средств. Независимая эксплуатация требует трёх операторов,
отдельных hosts/volumes, authenticated OOB pinning и испытанной сохранности floor.

## Подтверждённый source CI

Source commit `5f171ff12e60ba8a11266b57116b4d7b21a4a877`: [Actions44337182123](https://github.com/xBLAGO/space/actions/runs/44337182123),821/821 дважды (443.855/440.782s), Docker и новый custody smoke PASS. Второй PR-triggered run44338740380 того же SHA также success. Локальные48/48 прошли за256.922s; стенд100 участников и отдельный verifier PASS. Эти результаты не доказывают независимость операторов.

Полный локальный regression source SHA `5f171ff12e60ba8a11266b57116b4d7b21a4a877`: **821/821 PASS**, 1519.037s, errors0/failures0/skipped0. [Полный машинный протокол](capital093/full-regression.json) сохраняет expected/actual для каждого теста. Поле base внутри протокола унаследовано от инструмента UEL0.3; фактические база09.3 и checkout SHA указаны отдельно в evidence.json. Прежние773 теста и48 новых сохранены.
