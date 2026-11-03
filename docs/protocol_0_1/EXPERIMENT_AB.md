# First Node A ↔ Node B experiment

**PLAN, NOT EXECUTED.** No public federation/pilot GO. Current executed work is the
offline corpus. This plan requires separate approval/review of a synthetic lab
receiver with durable state; no production endpoints/databases/secrets are reused.

## 1. Scope / operator independence

- Operator A and operator B are different administrators, on separately controlled
  hosts/volumes/credential stores. No shared founder account, HMAC, signing key,
  writable mount, inbox DB or retained checkpoint directory.
- Human test owner A, agent A and node A use three distinct synthetic keys;
  B has its own Human/agent/node keys. Each Human owns its signer; neither agent
  can access its owner key. Use new synthetic keys for a future live lab, **not**
  the publicly reproducible fixture seeds.
- A and B can use different LLMs or no LLM. The first payload is fixed harmless
  test text; no memory/profile export, economic operation or publication.
- A uses validator A code path; B uses validator B code path for the prototype.
  Before claiming independent implementation, B's team must reproduce the spec
  without copying the reference authorization logic and review golden expectations.
  Same-author reference parity alone is insufficient.

Storage A and B are distinct, not merely two clients of the same database. Backend
choice is local; the experiment must identify each append/inbox/retained-checkpoint
implementation and durability assumptions. A possible lab uses fsynced append log
and separate checkpoint volume at A, a different reviewed transactional store and
independent retention backend at B. **No backend is installed or migrated here.**

## 2. Bootstrap artefact

Each operator independently records and compares via an authenticated out-of-band
channel: exact profile and policy hash, network ID, node/agent handles and public
keys, owner identity/genesis binding, initial epoch/head, clock uncertainty,
retention/visibility policy, maximum payload and failure rules. Both Humans
explicitly approve the test scope. No incoming packet may replace these pins.

Archive signed configuration confirmations without private keys. Record all shared
external dependencies. A same-host rehearsal may be useful but must be labelled
local integration, not this independent-operator experiment.

## 3. First direction A → B

1. Human A reviews/signs one-use mandate for agent A → agent B/node B and exact
   channel; A creates/signs Envelope over the chosen test payload.
2. B parses bounded wire and verifies pinned domain, grant/signature/scope/recipient.
   Before authorization, B generates a fresh unpredictable status nonce tied to
   H(Envelope) and registers it as outstanding in receiver-owned state.
3. Human A's controlled signer returns the complete authority prefix and fresh
   status bound to the nonce/event/recipient. No old cached status or server HMAC
   substitutes for it. B independently verifies history/frontier/time/mandate.
4. Under the receiver's serialization lock, B rechecks current frontier, unused
   event ID, unused mandate and nonce. In one durable commit B stores note + replay
   indexes + consumed scope + retained-state commitment. It then signs a receipt.
5. A verifies B's pinned node signature and exact receipt binding. Before that,
   A reports `pending/unknown`, never successful delivery. B displays note as inert
   escaped text; it is not offered to a tool executor.
6. Resend exact bytes: B returns retained receipt, no second use. Same ID/different
   signed bytes: conflict. Route to another recipient: reject. Clear neither
   counter nor nonce simply to make the test pass.

## 4. Required receipt schemas (designed, verifier/IO not implemented)

Delivery receipt body: `kind=delivery-receipt`, profile, network, policy_ref,
`node`, `issuer_key` (pinned B node key_id), `receipt_id`, `event_id`, `event_ref`,
`mandate_ref`, `status_nonce`, `authority_epoch`, `sequence`, `head`,
`inbox_commit_index`, `inbox_state_ref`, `decision=committed`.

Revocation receipt body: `kind=revocation-receipt`, profile, network, policy_ref,
`node`, `issuer_key`, `receipt_id`, `identity`, `authority_epoch`, `revoke_ref`,
`sequence`, `head`, `request_nonce`, `inbox_commit_index`, `inbox_state_ref`,
`decision=revocation-recorded`.

Use the same canonical JSON/Ed25519 primitive but separate fixed signature domains
DELIVERY-RECEIPT and REVOCATION-RECEIPT. IDs/refs/nonces use the established bounded
encodings; counters are unsigned safe integers. Receipt key is a **node** key, not
an owner key authorizing a new mandate. Never include note text or private history.
B retains ID→exact receipt and advances commit index monotonically; restoration
must reconcile it with an independently retained inbox state commitment.

These schemas are required design elements, but current pure validators only check
incoming Envelope plus its authority evidence. Receipt vectors/verifier, durable
receiver and ACK storage are open work before executing steps 4–6. They must not
be reported PASS because the 73-vector envelope corpus is green. Finalizing their
schema requires freezing the agreed Draft revision/hash before any operator run.

## 5. Revoke and partition phase

- A signs mandate revoke; B commits it durably and ACKs exact revoke/frontier.
  Resend previously signed Envelope, or a new Envelope with that old mandate:
  no new delivery after this ACK, even if signatures and old timestamps are valid.
- Race delivery vs revoke under B's shared lock. Preserve both commit receipts;
  demonstrate exactly one permitted order, no ACK-before-write window.
- Drop network before status: UNKNOWN, no note. Delay status beyond expiry:
  reject/no note. Drop revoke/ACK: report issued/pending, not B-confirmed.
- Disconnect A after a valid short certificate: acceptance is allowed only inside
  its declared event-bound window and before B-confirmed revoke. Do not claim
  instantaneous global revocation. No certificate reuse for another event.

## 6. Restart, rollback and reverse direction

- Kill B before commit, after commit before ACK, after ACK; resend and verify at
  most one note and stable receipts. Test fsync/permission/full-disk failures.
- Restore an older application snapshot while retaining current independent inbox/
  revoked frontier: reconcile or fail closed. Lost retained state/clock proof:
  UNKNOWN_RECOVERY/UNKNOWN_CLOCK; never clear counters and accept again.
- Test authenticated conflicting history at the same sequence: quarantine/reject.
  No silent owner epoch rollback or self-signed key replacement.
- Repeat B → A with a **new B-owner-issued mandate**, different agent key/recipient,
  different event and receiver nonce. Receiving one note gives no implicit reply
  authority. Prove A does not rely on B's parser or shared server secrets.

## 7. State/evidence to retain

Profile/policy/config hashes, independently verified public pins, signed grants,
message bytes/hashes, authority prefixes/statuses, signed delivery/revoke receipts,
clock-bound evidence, durable storage/fsync assumptions, fault-injection chronology,
retry/replay counts, fresh and old snapshot hashes, software/verifier versions,
network/failure-domain map and explicit untested items. No private keys, credentials,
real personal conversations or production DB copies in the evidence bundle.

A reviewer must distinguish: pure crypto conformance → same-host IO integration →
two-host controlled integration → independently governed operators. Only the last
meets this experiment's independence criterion; none alone implies production GO.

## 8. Go/no-go

GO for the first real lab delivery only when pins/mandate/signed evidence pass both
verifiers, independent stores/clock/retained replay state are qualified, receipt
verification and atomic receiver are implemented, and crash/revoke races pass.
If these are absent, only offline signed **candidate exchange** is permitted,
without a claim that delivery/authorization state survived failure.

NO-GO for any real funds, public data, user migration or pilot until independent
security/privacy review, importer/key-rotation coverage, supported-device tests
and Foundation 05.4/06.1 operational/quality blockers are resolved separately.
