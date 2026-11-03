# Portable identity export/import — design `bop-portability.1`

**Draft design, not implemented importer.** This is not a 07.1 personal download,
server DR secret bundle, raw SQLite copy or supported `note.deliver` payload schema.
It must be reviewed and tested before any runtime imports. The executed 73-vector
corpus covers wire/mandate/freshness predicates, **not** this import/handoff design.

## 1. What must survive

- Owner identity continuity, trusted genesis binding and complete authorized key
  transition history, including revoked keys and exact authority epochs.
- Selected events with their references, causal links, signatures and explicit
  coverage/redaction markers. Redacted content is not fabricated as complete history.
- Revocations, terminal mandate states, retained frontier and recipient ACK evidence.
- Authorized private mappings and optional selected memory/history with the same
  ownership/isolation/deletion semantics. No automatic scope expansion at B.

The export does **not** transfer a Human's private keys by default. The Human keeps
control and signs a recipient-bound transfer; B generates its own agent/node keys.
Source sessions, CSRF tokens, password hashes, Telegram proofs/bot token, private
passkeys, HF/API keys, foreign users' data, server HMAC/wrapping keys and custody
credentials MUST NOT appear in the payload, manifest, logs or debug output.

An existing 07.1 `identity_id` is preserved byte-for-byte inside an authorized
private `legacy_refs` mapping; it is not truncated to the lab pairwise handle.
`blago_user_id` also remains a local reference. A new provider may use another local
account ID but must retain the validated mapping, rather than inventing a new owner.
No public global hash of name/biometrics/memory is generated.

## 2. Proposed container

Transport-independent bounded container with these parts; closed schemas and
canonical signing rules must be finalized in the import implementation PR:

```json
{
  "format":"bop-portability.1",
  "manifest":{
    "export_id":"random-256-bit-id",
    "owner_identity":"pairwise-owner-handle",
    "issuer_key":"current-owner-key-id",
    "recipient_node":"previously-authenticated-node-B",
    "recipient_key":"pinned-import-key-id",
    "source_node":"node-A",
    "authority_epoch":1,
    "authority_head":"hash-of-current-signed-history",
    "authority_sequence":1,
    "created_at":1700000000,
    "expires_at":1700000300,
    "import_nonce":"fresh-challenge-issued-by-B",
    "coverage":["key-history","revocations","selected-events"],
    "private_scopes":[],
    "blob_manifest":[],
    "transfer_mode":"copy-no-new-authority"
  },
  "manifest_signature":"owner-signature-in-export-domain",
  "public_evidence":{
    "genesis_binding":{},
    "key_history":[],
    "authority_history":[],
    "retractions":[],
    "recipient_receipts":[]
  },
  "private_payload":{
    "algorithm":"A256GCM",
    "key_delivery_ref":"out-of-band-recipient-authenticated-channel",
    "nonce":"base64url-12-bytes",
    "ciphertext_and_tag":"base64url-ciphertext-followed-by-16-byte-tag"
  }
}
```

This is a field catalogue/example, **not** a valid golden signed packet. Placeholder
strings are not schema-conforming handles. Detached signed manifest binds hashes,
lengths and logical IDs of every evidence/private blob, encoding/profile version,
recipient and scope. AES-256-GCM uses a fresh 32-byte transfer key and unique 12-byte
nonce; AAD binds format, manifest hash, recipient and scope. Standard reviewed
library only; separate key delivery is authenticated against the pinned recipient,
not included as a plaintext key in this container. No universal founder wrapping key.
If authenticated key delivery is unavailable, do not export private data.

Suggested defensive bounds for the first importer: manifest ≤64 KiB, total ≤64 MiB,
≤64 logical blobs, each ≤1 MiB. Reject mismatched lengths/hashes, unknown schemas,
path-like blob IDs, traversal, symlinks, compressed expansion and partial coverage
presented as complete. These are importer design limits, not an extension of the
16384-byte wire profile; they require independent schema vectors before activation.

Public evidence here means independently verifiable **by authorized recipients**,
not an instruction to publish all keys/history on the Internet. Even event hashes
can correlate people. Private memory/default `private_scopes:[]` is not transferred
until the Human selects and approves exact recipient, purpose, data and retention.

## 3. Key history proof

A portable chain must include signed genesis evidence anchored in receiver trust,
and every transition needed to reach the declared current key/epoch. A rotation
binds identity, old/new key IDs, new public key, role/purpose, previous transition
hash, old/new epoch and nonce/context. The old authorized owner signs the transition;
the new key supplies separate proof of possession over the same bound action.
Revocation is terminal for the old authority; epoch monotonically increases.

A missing predecessor, wrong purpose, stale epoch, unauthorized signer, reused
revoked key, duplicate/forked transition or contradictory active-owner claim fails.
An unfamiliar self-signed genesis is not automatically the same Human. Enrollment
or another explicit trust procedure must anchor it outside the untrusted package.

No 07.1 `identity_events` metadata-only history or retained HMAC tag is silently
promoted to this full signed chain. Legacy records without portable evidence are
marked `legacy-unattested`, kept in private scope or excluded from authority.
A missing original control state remains a block; 07.2 activation is not bypassed.

**Executed lab limitation:** current reference validators pin epoch 1 and return
UNKNOWN_KEY_HISTORY for another epoch. They do not implement these rotations or
claim safe import of a multi-epoch chain. This design must gain its own positive
and negative corpus plus two independent importer implementations.

## 4. Import acceptance sequence

1. B independently authenticates owner trust anchor and recipient binding; issues
   unpredictable import nonce before seeing the export. Granting B import rights
   is separate from granting an agent general runtime rights.
2. Parse/bound the container, verify manifest signature/domain, hashes, selected
   coverage/scopes and exact recipient. No writes, keys or sessions are activated.
3. Verify key/authority history and all already-known retained frontiers. Obtain
   a fresh owner status bound to this manifest hash, nonce and B. A signed old
   package alone cannot prove that later revocations/deletions do not exist.
4. Decrypt only authorized private scopes; validate ownership, schema, ID collisions,
   event links and deletion/retraction precedence. Unknown data is not normalized
   into authority or broadened consent. Retention blockers remain effective.
5. Stage changes in an isolated import workspace; compute coverage/state digest.
   Apply atomically only after all checks and explicit required consent. A crash
   leaves either old complete state or staged incomplete state with no new powers.
6. Produce B-signed import receipt binding manifest hash, resulting authority/state
   frontier, recipient keys, accepted/redacted scopes and status `imported-no-new-authority`.
   Consume nonce/export ID durably; duplicate exact package returns prior receipt,
   same ID/different bytes rejects. A receipt does not prove erasure at A.

Replay-safe import is **not** restoring a receiver's own stale backup. For that,
its original independent retained replay/revocation state must be reconciled first.
A newly imported owner must not inherit someone else's used session or replay cache.

## 5. Copy vs handoff

`copy-no-new-authority` is safe data staging; the package does not give B owner keys
or automatically authorize a new agent. Existing owner may deliberately authorize
multiple limited agents, but that is explicit delegation, not a hidden migration.

A future `handoff` requires distinct states and receipts:

```
PREPARED_AT_B (no new authority)
  -> OLD_MANDATES_REVOKED_AT_NAMED_RECIPIENTS (verified durable ACKs)
  -> OWNER_APPROVES_NEW_AGENT / optional key rotation
  -> ACTIVE_AT_B
  -> SOURCE_RETIRED_WITH_RETENTION_RECEIPT
```

The exact recipient set must be named; no claim that every unknown peer acknowledged.
If a phase is missing/partitioned/forked, new authority is not silently activated.
The Human retains control of owner keys throughout. A source-node failure cannot
be solved by an administrator inventing an owner or reusing server HMAC custody.

Cancellation before activation removes staged data according to consent. After
activation it is another explicit revoke/handoff, not rollback of committed owner
epochs. Minimal content-free revocation evidence may need retention while private
content is deleted; copied public bytes and a malicious source cannot be remotely
erased by cryptographic promise. Exit requires honest limits, receipts and policy.

## 6. Design-only portability scenarios

These cases must be implemented/tested by both independent importers; they are
**NOT counted in the 73 executed wire vectors**:

| ID | Scenario | Required result |
|---|---|---|
| P01 | Valid selected-scope transfer, preserved IDs/history, no sessions | Atomic import-no-new-authority; same validated identity |
| P02 | Wrong recipient / manifest nonce / purpose | Reject, no staged authority |
| P03 | Old manifest behind retained revocation frontier | UNKNOWN_STALE / no import effect |
| P04 | Missing key transition / unauthorized rotation / fork | Reject or UNKNOWN_KEY_HISTORY, no inferred owner |
| P05 | Later deletion/redaction contradicts old exported plaintext | Do not restore deleted content |
| P06 | Included server HMAC/API/session/foreign credential | Reject forbidden scope, no secret logging |
| P07 | Invalid AEAD tag / reused key+nonce / altered AAD | Reject; no partial plaintext exposure |
| P08 | Duplicate exact export; same ID different content | Original receipt / conflict, not duplicate identity |
| P09 | Crash after prepare / before commit / after commit before ACK | Atomic state, no dual owner or duplicate effects |
| P10 | Path traversal / ZIP bomb / unknown schema / omitted coverage | Reject or explicit unsupported; never silent complete import |
| P11 | Source refuses deletion or recipient ACK unreachable | Report incomplete exit; no false erasure or revoke confirmation |
| P12 | Complete two-epoch key history + fresh handoff approvals | Preserve identity; old keys inactive; old snapshot cannot roll back |
