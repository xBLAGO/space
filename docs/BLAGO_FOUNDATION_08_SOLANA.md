# Foundation 08 — Solana contract interfaces

**Draft specification only. No deployed programs, transactions, wallet or RPC.**
Consensus 7 и общественное принятие Genesis пока не определены. Имена ниже — границы
будущих программ, не доверенные адреса и не обещание исполнения.

## Общая модель

Все программы должны иметь versioned discriminator, bounded payload lengths,
явные authority keys, owner/discriminator/signer checks и защиту от replay.
Применять canonical PDA bump и domain-separated seeds фиксированной длины;
проверять каждый account, связь с protocol/proposal revision и разрешённые CPI.
Основание: официальная [PDA documentation](https://solana.com/docs/core/pda) и
[account ownership](https://solana.com/docs/core/accounts/account-structure).

Предложенные seeds — **проект BLAGO**, не требование Solana. У каждого domain
свой фиксированный ASCII prefix; хэши 32 bytes; revision/round — фиксированный
u64 little-endian. Не складывать неоднозначные переменные строки в seeds.

| Program/account | Предложенный PDA domain | Минимальные поля / операции |
|---|---|---|
| ProtocolRegistry | `protocol-v1`, protocol_hash | schema version, content hash, predecessor hash, governance configuration hash, status Draft/accepted/superseded; register_draft; accept только по ещё не утверждённой процедуре |
| ProposalRegistry | `proposal-v1`, proposal_nonce, revision_u64 | protocol hash, exact public revision hash, previous revision hash, author authority, publication consent digest; register_revision; append_withdrawal (не стирает chain) |
| JuryAttestation | `jury-v1`, proposal_revision_hash, round_u64, juror_pubkey | protocol/policy hash, attestation decision/evidence hash, validity interval, nonce; attest/revoke; уникальность одной текущей позиции juror на round |
| EventAnchor | `anchor-v1`, public_stream_nonce, sequence_u64 | public root hash, previous anchor hash, export schema, authorized publisher, scope marker `public-approved-only`; anchor/verify |

`proposal_nonce` и `public_stream_nonce` — random public handles, не blago_user_id,
Telegram ID, биометрия, semantic hash или глобальный идентификатор человека.
Авторский account pseudonym v08 не равен Solana signing authority: отдельный
reviewed ownership/consent bridge обязателен. Серверу не выдаётся universal signer.

## Инварианты и запреты

- Новый predecessor/sequence точно продолжает текущую ветку, конфликтующие anchors
  не принимаются молча. Повтор идентичного запроса идемпотентен; конфликт отклоняется.
- Attestation относится к неизменному proposal revision hash и protocol policy,
  а не к изменяемому URL/заголовку. Повтор для другой сети/program/action отклоняется.
- Signer и owner account проверяются отдельно; произвольная запись с похожим
  layout не является допустимым registry или quorum certificate.
- Обновление program/governance authority, timelock, emergency pause, retirement
  и challenge resolution требуют отдельной принятой спецификации и аудита.
- Ни одна attestation или публикация в текущем Draft не исполняет платёж,
  передачу ресурса, mint/burn, lock средств или принятие общественного решения.
- Не хранить raw text, personal identifiers, private declarations, mind profiles,
  receipts/session data или hashes частных текстов on-chain. Даже публичный hash
  может коррелировать людей; отдельно оценить consent, minimization и deletion.
- Не заявлять удаление immutable chain data. Withdrawal обозначает отзыв,
  а не физическое уничтожение прошлой записи.

## Необходимые будущие испытания

Local-validator tests: account substitution, wrong owner/program/PDA/bump,
неподписанный authority, duplicate sequence, fork, replay в другой сети/round,
истёкшая attestation, несовместимая schema, program upgrade и freeze, исчерпание
storage/compute, удаление off-chain авторства и сопоставление anchors после restore.
Независимый аудит обязателен до devnet с чужими ключами и до любого mainnet.

В Foundation 08 `UnconfiguredSolanaAnchor` и `UnconfiguredConsensus` всегда
возвращают отказ. Отсутствие кода программ и governance — блокер, который не
закрывается зелёными unit tests обычного BLAGO приложения.
