# Mandate, authority freshness & revocation

Status: Draft `bop-0.1-lab.1`. Scope is a single private note, not generic economic
execution. Identity key rotation/import are designed in PORTABILITY; the executed
wire profile pins owner epoch 1 and refuses to guess new key history.

## 1. Mandate body

Exactly: `kind`, `profile`, `network`, `policy_ref`, `mandate_id`, `issuer`,
`issuer_key`, `subject`, `subject_key`, `recipient_agent`, `recipient_node`,
`scope`, `object`, `not_before`, `expires_at`, `allow_delegation`, `parent`,
`limits`, `authority_epoch`. It is signed in the MANDATE domain by the independently
pinned owner key. No actor can bootstrap this trust from its own packet.

| Field | Meaning |
|---|---|
| kind | `mandate` |
| issuer / issuer_key | Pairwise owner identity handle and current authorized owner key_id |
| subject / subject_key | Agent handle and raw Ed25519 public key (base64url) |
| recipient_agent / recipient_node | Exact receiving agent/node; no bearer-anywhere right |
| scope / object | `["note.deliver"]` and exact channel handle |
| mandate_id | `md:<64 lower-hex>` random identifier; a new grant needs a new ID and signed object |
| not_before / expires_at | Integer time; lifetime ≤3600s |
| allow_delegation / parent | Only `false` / `null`. Delegation chains unsupported and rejected |
| limits | Exactly `max_uses:1`, `max_payload_bytes:1..2048` |
| authority_epoch | Integer 1 in this profile |
| profile/network/policy_ref | Same pinned context as Envelope; cannot reinterpret by local policy |

The owner explicitly approves these fields and intended payload disclosure before
signing. Possession of a web session, Telegram token, server HMAC, passkey login,
semantic profile or valid agent key is insufficient to issue/expand a mandate.
Expired grants do not extend automatically. A root may explicitly issue a different
new grant; this never reactivates the old grant reference.

## 2. Signed authority log

A receiver-pinned genesis head establishes the initial owner identity/key/epoch
binding outside the protocol packet. Each `authority-event` has exactly:

`kind=authority-event`, `profile`, `network`, `policy_ref`, `identity`, `issuer_key`,
`epoch`, `sequence`, `previous`, `operation`, `mandate_ref`.

Owner signature uses AUTHORITY-EVENT domain. Sequence starts at 1, increments by 1;
previous equals genesis head for sequence 1, then H(previous signed event).
Operations only `mandate.issue` and `mandate.revoke`. Issue requires that reference
has never appeared; revoke requires active reference. Revoke is terminal for that
reference; revoke→issue same ref and duplicate issue/revoke reject. Event sequence,
not an untrusted wall-clock timestamp, defines this log's ordering.

The complete supplied prefix (max 64) must contain the receiver's retained
`floor_sequence/floor_head`. If prefix is behind it: UNKNOWN_STALE. Same sequence
with another head or missing retained ancestry: FORK/reject. Different owner/key
history without a verified transition is not TOFU; UNKNOWN_KEY_HISTORY.
Root-signed contradictory histories cannot both replace a known frontier silently.
A malicious owner can equivocate to previously uninformed receivers; detection
requires exchanging retained evidence, not a claim of global consensus here.

## 3. Fresh authority-status

B creates a fresh 32-byte unpredictable nonce **before** receiving a status.
Owner-controlled signer returns signed AUTHORITY-STATUS body with exactly:

`kind=authority-status`, `profile`, `network`, `policy_ref`, `identity`, `issuer_key`,
`epoch`, `sequence`, `head`, `mandate_ref`, `mandate_state`, `event_ref`,
`recipient_node`, `status_nonce`, `issued_at`, `expires_at`.

- sequence/head match the complete verified owner log.
- mandate_state is the deterministic reduction active/revoked/unknown of that ref.
- event_ref = H(exact signed Envelope); recipient_node = B; status_nonce equals
  B's outstanding challenge. It is not a reusable bearer authorization.
- status lifetime ≤30s; Envelope ≤120s and mandate ≤3600s, all windows must hold.
- Missing issue is UNKNOWN_MANDATE, known revoke is REVOKED. An owner-signed status
  saying active while supplied log says revoked is inconsistent and rejected.

No independently owned infrastructure of a founder is needed: the **relevant
human's** authority is pinned by the parties. But its signer/current state must
be reachable for this online lab profile. If unreachable, do not infer active from
an old cached proof. This availability trade-off is explicit, not a hidden admin
fallback. Production scalable delegated status services require another reviewed
policy and are not supplied by 07.2's test ceremony.

## 4. Clock and delayed delivery

Receiver has local estimate `now` and justified uncertainty `s`, 0≤s≤5 seconds.
A validity interval `[start,end)` is usable only if:

```
start <= now - s
now + s < end
```

This conservative rule may delay a newly issued status by up to the clock bound.
It does not silently add grace to expiration. Freshness nonce alone does not bound
delivery delay; a delayed otherwise correct certificate expires. Clock unqualified,
rolled back or uncertainty >5s → UNKNOWN_CLOCK. Lab vectors use a synthetic fixed
clock; they do not qualify NTP/real-host clock security.

## 5. Revocation confirmation is receiver-scoped

There is no honest promise of instantaneous global revocation across partitions.
For the single recipient B in this mandate:

1. Owner durably signs/logs revoke sequence r and stops issuing active statuses
   for that old ref. Its state is **revocation issued**, not yet B-confirmed.
2. B verifies the prefix, takes the same serialization boundary used by delivery,
   durably advances its revoke/replay frontier and fences pending candidates.
3. Only after durable commit B signs an ACK binding network/policy, node ID,
   identity/epoch, revoke event hash, sequence/head, challenge/request reference
   and decision `revocation-recorded`.
4. Owner verifies this ACK using previously pinned B node key. Only now may it
   say **revocation confirmed at B**. A lost ACK keeps confirmation pending; a
   duplicate request returns the same durable result, not a rollback.
5. Every subsequent delivery at B using that old ref fails, even with an earlier
   valid signature/status and even after application DB restore.

If delivery commits first, a later revoke ACK does not retroactively erase the
already received note. If revoke commits first, an older pending delivery is
fenced. This ordering and receipts are necessary evidence; timestamps alone cannot
settle the race. A peer that ignores its own committed revoke is Byzantine, not
made safe by running the reference validator elsewhere.

Before B receives revoke, a still-valid event-bound status may remain usable
within its ≤30s window. This is the declared bounded-staleness window, not a
violation of a B-confirmation that never happened. Any stronger all-recipient
semantics must define a recipient set/ACK quorum and partition behavior separately.

## 6. Durable inbox / backup contract (not implemented here)

A pure ACCEPT candidate is rechecked under B's current transaction/serialization
lock. B atomically stores `{event_id,H(envelope),mandate_ref,use_count,status_nonce,
authority_epoch,frontier,delivery_decision}` together with the private note;
then emits a signed receipt. Mandate count max1 and event ID uniqueness prevent
duplicate effects. Same event ID/different hash is conflict. No receipt before
persistence, and no execution of payload instructions.

Retained anti-rollback checkpoint must survive an application snapshot restore.
Its custody is independent of the rollback domain; sharing founders' HMAC keys
between nodes is forbidden. On restart, B must reconcile the inbox/used IDs/counts
and revoked frontier against this checkpoint. If state is missing, older, forked
or cannot be proven current: UNKNOWN_RECOVERY, no new delivery. It is unsafe to
clear dedupe and request a fresh status for a one-use grant: that permits a second
use after old backup. Safe recovery may require replay from retained inbox records
or conservatively invalidating outstanding mandates and issuing new ones explicitly.

Reference fixtures model the **verified inputs** and decisions, not the disk/fsync/
commit/ACK implementation. Crash after write, before/after ACK, independent backup
restore and concurrent revoke/delivery remain mandatory stateful experiment tests.
