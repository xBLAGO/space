# Threat model — Foundation 08.1

Status: Draft. Models are not security certifications. Independent review and
stateful two-operator drills are required before a live federation claim.

## Trust boundaries and assets

Human owner key ≠ agent key ≠ node ACK key ≠ user session ≠ storage/custody key.
A trusted bootstrap identifies exact owner genesis, node keys, profile/policy and
relationship handles. No peer packet can install its own trust context. Private
payloads, signing keys, mandate scopes, retained frontiers, inbox use counters and
receipts are protected assets. LLM output is always untrusted data, never authority.

Reference validators assume authentic receiver context and honest execution of the
specified checks. They do not defend a fully compromised receiver that decides to
ignore them, exfiltrate plaintext or falsely ACK a write. Owner key compromise
permits forged grants; a protocol cannot infer the real Human from biography.

## Attack matrix

| Attacker / failure | Property and mitigation | Evidence / remaining gap |
|---|---|---|
| Network changes body/signature/domain/recipient | Closed canonical schemas; domain-separated Ed25519; exact pins | Executed negative vectors |
| Serialization disagreement | ASCII headers, exact re-encoding, integers, duplicate-key rejection, byte-exact Unicode payload | Two encoders and 73 vectors; not exhaustive parser audit |
| Agent key stolen | Only explicit one-use note scope, fixed recipient/object/limits/TTL | Scoped checks executed; real key custody not tested |
| Agent self-grants / session passed as identity | Mandate must be owner-signed; role-separated handles; no session input | AUTHORITY/SCHEMA failures; deployment must protect owner signer |
| Prompt injection in payload | Text is inert note; no tool execution/inference required | Positive inert-text vector; receiver display/tool isolation is stateful future test |
| Owner/status unreachable | No fresh event-bound status: UNKNOWN/no action | Missing/stale predicates executed; actual timeout/partition planned |
| Cached status / replay to another event/node | Receiver nonce, event_ref, recipient pin, short validity; durable consume | Binding tests executed; live nonce/inbox store not implemented |
| Repeated delivery / crash before ACK | Event ID→hash, max-use1 and atomic note/inbox/frontier commit | Pure used-state vectors; fsync/CAS/ACK drills NOT EXECUTED |
| Revocation races message | Same receiver serialization boundary; confirm only after durable receiver ACK | Semantics specified; concurrent race test NOT EXECUTED |
| Old app snapshot loses revoke/dedupe | Independent retained replay frontier; reconcile or UNKNOWN_RECOVERY | Pure missing/floor vectors; rollback-domain custody not proven |
| Rollback of all copies / operator collusion | No claimed detection if every anchor/trust store compromised | Explicit NO-GO; no fake recovery or universal founder key |
| Owner log fork / split view | Previous refs, monotonically retained heads, fork quarantine/evidence | Known-frontier checks executed; global non-equivocation not promised |
| Owner itself malicious / key compromised | Cannot forge Human intent distinction after legitimate authority key compromise | Separate detection/recovery/admission policy; not solved by signatures |
| Multiple fake identities | No unique-person claim; quotas/admission local, no voting/economic rights | Sybil resistance outside first-message profile |
| Resource exhaustion | 16KiB objects, bounded depth/arrays/history/payload; no repair/retry loops | Boundary vectors; transport quotas/load/DoS tests pending |
| Private export interception / source keeps copy | Recipient-bound signed manifest, standard AEAD, minimization and explicit retention | Portability DESIGN_ONLY; malicious source erasure cannot be guaranteed |
| Key-rotation/import confusion | Epoch/history proof or UNKNOWN; no TOFU replacement | Epoch1 wire limit executed; P01–P12 need importer implementations |

## Freshness assumption, stated narrowly

An honest owner signer stops issuing active statuses after recording revoke.
An already issued event-bound status may still arrive within its validity window
before B commits the revoke. Do not describe this as a globally confirmed revoke.
B-confirmation requires B's verifiable durable ACK and monotonic later rejection.
If B cannot prove that its ACKed frontier survived restore, it cannot accept a new
message just because a new nonce/status was requested.

Clock bounds are an operational assumption. Synthetic `now` and boolean fixture
preconditions are not a secure clock, availability monitor or retained-storage
implementation. A real receiver must derive them from verified local state; it
must not accept sender JSON assertions `clock_ok=true`/`retained_available=true`.

The `note.deliver` scope does not authorize reading memory. In the first exercise,
payload is explicitly selected synthetic text, not a hidden dump from the personal
agent prompt. A compromised agent already holding real data can still leak it through
an allowed text channel; cryptographic scope alone cannot classify private meaning.
Real disclosure/data-flow and LLM isolation require separate integration tests.

## What remains unproven

Independent team implementation, real node/network transport, authenticated bootstrap
ceremony, owner signer custody, durable atomic receiver, ACK signing/verification,
backup anti-rollback storage, handoff/import, multi-epoch rotation, stale/corrupt
configuration recovery, public abuse/SLO/device acceptance and Foundation 05.4/06.1
blockers. CI parity of reference decisions closes none of these by itself.

No tokens, blockchain, global person registry, identity recovery through an admin,
or production API changes are introduced as a shortcut to those gaps.
