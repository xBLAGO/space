# Envelope — `bop-0.1-lab.1`

Status: Draft **experimental interoperability profile**, not a generic federation
runtime. A later incompatible choice of serialization requires another profile;
no silent negotiation/downgrade. This is a deliberately narrow response to the
serialization ambiguity identified by PR #15.

## 1. Wire/canonical representation

The profile uses a strict subset of [JSON / RFC 8259](https://www.rfc-editor.org/rfc/rfc8259).
Its canonical form **C(x)** is defined here, not claimed to implement all of RFC 8785:

1. Wire is ASCII bytes (therefore valid UTF-8), no BOM, whitespace outside strings,
   trailing newline or trailing data. Limit **16384 bytes per signed object**.
2. Allowed values: object, array, printable ASCII string U+0020..U+007E, boolean,
   null, integer **0..9007199254740991**. Reject floats, exponent notation, negative
   numbers, NaN/Infinity, unpaired surrogates and non-ASCII header strings.
3. Object keys are strings, sorted by ascending ASCII bytes. Duplicate keys at
   **any nesting depth** reject before conversion to a map. Unknown schema fields
   reject; no last-key-wins or extension fields in this profile.
4. No insignificant spaces. Integers decimal, no leading zeros or negative zero.
   Strings escape only `"` as `\"` and `\` as `\\`; `/` is literal. Alternative
   `\u`/slash escapes are valid general JSON but noncanonical here and reject.
5. Arrays preserve order. At most 64 entries/members per array/object, max nesting
   depth 8 (root depth 0), max string length 4096 characters. Event links additionally
   have ≤8 entries, distinct and sorted. Unicode text is carried as payload bytes.
6. Receiver re-encodes decoded input and requires **C(decoded) == received bytes**.
   Never repair, normalize or silently translate a signed packet.

Binary fields are canonical RFC4648 base64url without padding: chars A–Z, a–z,
0–9, `_`, `-`, no `=`, and re-encoding equality. Nonce/public key 32 bytes;
Ed25519 signature 64 bytes. Hash reference: `sha256:` + 64 lowercase hex digits.

## 2. Signed object and object references

Every object has exactly `alg`, `body`, `signature`; `alg` is only `Ed25519`.
Algorithm substitution (`none`, HMAC, another key type) rejects. Body `kind` determines
schema and signature domain. Pure Ed25519 verification follows the selected
well-tested libraries ([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)); no custom
curve/signature code. Keys must pass strict valid-point checks; weak/invalid keys reject.

```
signing_bytes = ASCII("BOP/0.1-lab.1/" + UPPERCASE(body.kind)) || 0x00 || C(body)
signature     = Ed25519.Sign(key_for_role, signing_bytes)
H(object)     = "sha256:" + lowerhex(SHA256(
                ASCII("BOP/0.1-lab.1/OBJECT") || 0x00 || C(object)))
key_id        = "sha256:" + lowerhex(SHA256(raw_public_key_32_bytes))
```

Kinds validated by the current receiver fixture implementations: `envelope`, `mandate`, `authority-event`, `authority-status`.
The sender cannot choose an arbitrary domain string; the verifier reconstructs it.
Required experiment receipt kinds are defined separately in EXPERIMENT_AB and are
not yet implemented by these pure validators. No prehash Ed25519ph/ctx variant or algorithm negotiation is implied.

H includes the complete canonical signed object; event ID is a separate opaque
command identifier. A changed signature/payload gives a different H, but reusing
an old event ID with changed H is a conflict, never a second authorized delivery.
The durable receiver must retain both ID→H and mandate-consumption indexes.

The pinned policy commitment is independently reproducible:
`policy_ref = "sha256:" + lowerhex(SHA256(ASCII("BOP/0.1-lab.1/POLICY") ||
0x00 || C(policy_document)))`. The corpus uses
[lab-policy.json](conformance/lab-policy.json); both Humans must review and pin it
along with the exact agreed Draft revision before any operator experiment. A policy
hash cannot change mandatory profile invariants or grant more than the Mandate.

## 3. Envelope schema

Body has exactly these fields:

| Field | Type / constraints |
|---|---|
| `kind` | `envelope` |
| `profile` | `bop-0.1-lab.1` |
| `network` | Pinned `fd:<32 lower-hex>` experiment namespace |
| `policy_ref` | Pinned hash of agreed lab policy/profile |
| `type` | Only `note.deliver` |
| `event_id` | `ev:<64 lower-hex>` random 256-bit identifier |
| `issuer` | Agent handle `ag:<32 lower-hex>` from mandate subject |
| `issuer_key` | key_id of mandate subject key |
| `subject` | Owner identity `id:<32 lower-hex>`, not a session |
| `recipient_agent`, `recipient_node` | Exact `ag:` / `nd:` handles; no wildcard |
| `scope` | Exactly `["note.deliver"]`, matching mandate |
| `object` | Exact `ch:<32 lower-hex>` agreed channel/object, matching mandate |
| `mandate_ref` | H(complete signed Mandate) |
| `links` | Sorted distinct known hash refs, ≤8; first note may have `[]` |
| `nonce` | 32 unpredictable sender bytes, canonical base64url |
| `issued_at`, `expires_at` | Integer Unix seconds; 0 < lifetime ≤120s |
| `payload_schema` | Only `text.utf8.v1` |
| `payload_b64` | Canonical base64url of non-empty UTF-8 bytes |
| `payload_hash` | `sha256:` + SHA-256 of exact decoded payload bytes |

Payload max **2048 bytes**, further restricted by mandate. Strict UTF-8 decoding;
reject invalid encodings/surrogates and C0 controls except LF and TAB. Text NFC and
NFD remain different byte strings/hashes; no Unicode normalization is performed.
A note is inert data: JSON-looking text, links and model prompts are **not** tool
calls, approvals or commands. Display escaping and model isolation belong to the
receiver implementation and must be verified before a live experiment.

Header ASCII avoids implicit normalization/number differences across implementations;
it does not prohibit Russian or other Unicode payload text. Private context is
not inferred from message text. This profile is not a carrier for identity bundles,
mandate issuance by an LLM, payments, public proposals or arbitrary binary data.

## 4. Attachments and trusted input separation

Receiver needs the signed mandate, complete signed authority-event prefix and
fresh signed authority-status, all separately canonical and size-bounded. Full
history prefix is ≤64 events in this lab. Beyond that, receiver returns UNKNOWN /
requires reviewed new profile; never truncate and treat the suffix as genesis.
`vectors.jsonl` uses hex encodings only to preserve exact wire bytes. Hex/case file
is a fixture container, **not** the selected network transport.

The fixture `context` represents receiver-owned preconditions: pinned owner key,
identity/epoch/genesis head, expected recipient/network/policy, fresh status nonce,
clock bounds, retained frontier, known refs, used IDs and mandate uses.
**None of this is trusted because an incoming peer included it in a JSON packet.**
A production verifier must load it from reviewed enrollment/retained state and
outstanding receiver challenges, never from attacker-controlled attachments.

## 5. Deterministic validation boundary

Pipeline: bounded parse/canonical/schema → pinned profile/domain → clock/recovery
preconditions → owner signature/mandate → agent signature/recipient/scope → object
refs/replay/limits/payload/time → signed history/frontier → fresh status binding
and active mandate. Any failure prevents effects. Detailed predicates are fixed
by this document, [authority spec](MANDATE_AND_AUTHORITY.md) and golden vectors;
reference code is an executable cross-check, not permission to omit prose requirements.

| Verdict | Meaning |
|---|---|
| ACCEPT / OK | Candidate passes pure verification against **given** current context. Not a delivery ACK or durable commit |
| REJECT | Invalid, unauthorized, revoked, replayed or forbidden candidate; do not execute |
| UNKNOWN | Missing/current-state/clock/history evidence; do not execute; may reconcile then retry with a new challenge |

UNKNOWN codes: UNKNOWN_RECOVERY, UNKNOWN_CLOCK, UNKNOWN_KEY_HISTORY,
UNKNOWN_STALE, UNKNOWN_LINK, UNKNOWN_MANDATE. Known revocation is REVOKED, not an
invitation to retry as a new agent. REPLAY may return a retained original receipt
from the durable receiver, but never repeats the effect. Malformed/multi-fault
packets need not expose a rich oracle; fixtures fix expected diagnostic codes for
single-fault cases. Never turn an exception/timeout into ACCEPT.

## 6. Golden example

[golden.json](conformance/golden.json) contains exact decoded signed objects,
payload bytes, signing bytes and envelope H for `G01-note-ru`.
[vectors.jsonl](conformance/vectors.jsonl) is the byte-for-byte corpus;
[manifest.json](conformance/manifest.json) pins its SHA-256. All signer keys are
publicly reproducible **test keys**. They must never appear in real authorization.
