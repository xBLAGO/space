# Governance contract 0.2

Политика имеет точный набор `format,id,version,currency,members,evidence_key,privacy_key,governors,governance_quorum,spend_quorum,activation_delay,delegation_seconds`. Формат `capital-governance-policy-2`, RUB integer minor units, версии начиная с 1. Список governors и набор member keys фиксируются начальным единогласным enrolment. Политика не придаёт человеку ценность, рейтинг или привилегию на базовые блага.

Подписи — Ed25519 существующей cryptography library, lowercase hex. Сериализация UTF-8 JSON, sort_keys=True, ensure_ascii=True, separators=(',',':'). Команда содержит fund, global source version/CAS, policy_hash, operation_id, key_id, type, expires_at, data и evidence. Политика, команды, privacy denial, key revoke endorsements и export binding имеют разные domain strings, зафиксированные в коде обеих реализаций. Float/bool в minor units и кворумах отклоняются.

| Событие | Основание | Эффект |
|---|---|---|
| PolicyProposed | Прямая GOV роль | Точная новая политика с old policy hash |
| PolicyVoted / PolicyVoteRevoked | Подпись действующего первоначального governor | Один голос / его отзыв, без proxy |
| PolicyActivated | Сверхкворум живых voters и истёкшая задержка | Version+1, invalidation незавершённых grants/reserves |
| DelegationGranted / DelegationRevoked | Прямая APPROVE роль доверителя | Один proposal, amount ceiling, TTL, без GOV/цепочки |
| ApprovalGranted / ApprovalRevoked | Уникальный principal и уникальный signer | Кворум конкретного предложения, после consumption повторное применение закрыто |
| AuthorityRevoked | Прямая GOV роль и подписи сверхкворума точного действия | Невозобновляемый отзыв ключа |
| MemberExited | Собственная подпись | Необратимый выход и удаление приватной UID binding |
| PrivacyMemberExited | Pinned denial-only node key после штатного удаления аккаунта | Только отрицательное изменение полномочий, никогда grant |

## Инварианты

1. По-прежнему verified = available + reserved + spent - refunded, с отдельными lot bounds.
2. Один principal и один signer дают не более одного голоса в approval round. Роль исполнителя не совмещается с PROPOSE/APPROVE.
3. Владелец предложения не согласует собственное действие даже через proxy. Исполнитель не использует свой approval.
4. Делегирование не передаёт governance vote и не цепляется к другому делегированию. Истёкший/отозванный/вышедший grantor или recipient не даёт quorum.
5. Quorum denominator не уменьшается автоматически после выхода, отзыва или истечения ключей.
6. Изменение политики не подменяет electorate/ключи, anchors и валюту. Пороги и delay не понижаются, delegation TTL не увеличивается.
7. Активация нового policy hash блокирует незавершённые действия старой версии. Уже подтверждённые расходы остаются историей.
8. Revoke/spend и concurrent votes/reserves сериализуются BEGIN IMMEDIATE + CAS. Idempotency receipts выходят после commit.
9. Exit не отчуждает имущество остальных и не создаёт выплату или расход. Это прекращение полномочий, а не юридическое урегулирование доли собственности.
10. Потеря положительного source history не заменяется grants из backup. Hold сохраняет данные и блокирует операции.

Политика может менять roles и key expiry существующих заранее допущенных ключей по защищённой процедуре. Добавление новых участников/ключей, ротация cryptographic anchors, уменьшение порогов, снятие permanent exits/revocations и реорганизация electorate требуют другого будущего протокола. Автоматического v1→v2 import нет.

## История и UEL

Используются прежние capital_funds/members/versions и тот же UEL CAPITAL source. Governance events — версии канонического capital domain, не второй универсальный журнал. Аддитивная govcap_migrations/governance_002 и govcap_holds не изменяет старые migration checksums. Состояния старых v1 фондов не меняются при установке.

Private audit export оставляет не относящиеся к фонду UEL leaves opaque. Он всё же раскрывает общее число UEL events. Поэтому portable proof выдаётся только прямому действующему GOV аудитору, а не публикуется. Для private payload refs нужен node attestation. Наличие контрольной точки не доказывает истину реального поступления, независимость участников или текущую свежесть без внешнего закрепления tip.

В лаборатории все ключи создаёт один fixture-процесс. Раздельные UID, principal и signatures проверяют разграничение полномочий, но не доказывают независимую опеку ключей или независимость физических людей. Сговор полномочного сверхкворума не устраняется программным подсчётом.
