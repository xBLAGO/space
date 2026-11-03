# Conformance matrix — Foundation 08.1

Status: Draft. Executed scope is **pure receiver decision** on fixture bytes and
receiver-owned synthetic preconditions, not live operators, transport or durable IO.

## Executed corpus

73 cases: 9 ACCEPT, 57 REJECT, 7 UNKNOWN. Both separate reference implementations
match every expected verdict/code. A uses a manual encoder + cryptography; B uses
stdlib JSON encoding + PyNaCl. They share no validation helpers/schema imports,
but are same-author implementations with one common corpus, Python JSON parser
and libsodium point-validation primitive. External implementation
and security review remain required.

| ID | Decision | Diagnostic code |
|---|---|---|
| G01-note-ru | ACCEPT | OK |
| G02-reverse-direction | ACCEPT | OK |
| G03-utf8-nfc | ACCEPT | OK |
| G04-utf8-nfd-distinct-bytes | ACCEPT | OK |
| G05-newline-tab | ACCEPT | OK |
| G06-max-payload | ACCEPT | OK |
| G07-quoted-untrusted-content | ACCEPT | OK |
| G08-clock-uncertainty-five | ACCEPT | OK |
| G09-frontier-advance-with-proof | ACCEPT | OK |
| N01-signature | REJECT | ENCODING |
| N02-wrong-signature | REJECT | SIGNATURE |
| N03-grant-signature | REJECT | SIGNATURE |
| N04-status-signature | REJECT | SIGNATURE |
| N05-profile | REJECT | PROFILE |
| N06-network | REJECT | DOMAIN |
| N07-policy | REJECT | DOMAIN |
| N08-recipient-node | REJECT | RECIPIENT |
| N09-recipient-agent | REJECT | RECIPIENT |
| N10-agent-id | REJECT | AUTHORITY |
| N11-owner-session-substitution | REJECT | SCHEMA |
| N12-scope | REJECT | SCOPE |
| N13-action | REJECT | SCOPE |
| N14-object | REJECT | SCOPE |
| N15-grant-ref | REJECT | MANDATE_REF |
| N16-redelegation | REJECT | DELEGATION |
| N17-parent-delegation | REJECT | DELEGATION |
| N18-max-uses | REJECT | LIMIT |
| N19-use-consumed | REJECT | LIMIT |
| N20-event-replay | REJECT | REPLAY |
| N21-payload-too-long | REJECT | LIMIT |
| N22-payload-hash | REJECT | PAYLOAD |
| N23-payload-not-utf8 | REJECT | PAYLOAD |
| N24-payload-nul | REJECT | PAYLOAD |
| N25-unknown-schema | REJECT | SCHEMA |
| N26-empty-payload | REJECT | ENCODING |
| N27-padding-base64 | REJECT | ENCODING |
| N28-expired-envelope | REJECT | TIME |
| N29-future-envelope | REJECT | TIME |
| N30-ttl-envelope | REJECT | TIME |
| N31-expired-grant | REJECT | TIME |
| N32-ttl-grant | REJECT | TIME |
| N33-expired-status | REJECT | TIME |
| N34-status-long-ttl | REJECT | TIME |
| N35-status-wrong-nonce | REJECT | STATUS_BINDING |
| N36-status-another-event | REJECT | STATUS_BINDING |
| N37-confirmed-revocation | REJECT | REVOKED |
| N38-unknown-mandate | UNKNOWN | UNKNOWN_MANDATE |
| N39-old-snapshot-with-retained-floor | UNKNOWN | UNKNOWN_STALE |
| N40-history-fork | REJECT | FORK |
| N41-no-retained-control | UNKNOWN | UNKNOWN_RECOVERY |
| N42-clock-unavailable | UNKNOWN | UNKNOWN_CLOCK |
| N43-clock-uncertainty | UNKNOWN | UNKNOWN_CLOCK |
| N44-key-rotation-not-in-this-profile | UNKNOWN | UNKNOWN_KEY_HISTORY |
| N45-unknown-link | UNKNOWN | UNKNOWN_LINK |
| N46-duplicate-links | REJECT | LINKS |
| N47-weak-agent-key | REJECT | KEY |
| N48-unknown-field | REJECT | SCHEMA |
| N49-trailing-whitespace | REJECT | WIRE |
| N50-duplicate-json-key | REJECT | WIRE |
| N51-float | REJECT | WIRE |
| N52-negative-zero | REJECT | WIRE |
| N53-overflow-int | REJECT | WIRE |
| N54-bom | REJECT | WIRE |
| N55-noncanonical-escape | REJECT | WIRE |
| N56-oversize-wire | REJECT | WIRE |
| N57-non-ascii-header | REJECT | WIRE |
| N58-algorithm-confusion | REJECT | SCHEMA |
| N59-missing-history-prefix | REJECT | HISTORY |
| N60-authority-predecessor | REJECT | HISTORY |
| N61-boolean-timestamp | REJECT | TIME |
| N62-boolean-counter | REJECT | LIMIT |
| N63-no-scope-inference | REJECT | SCOPE |
| N64-fresh-cert-contradicts-history | REJECT | HISTORY |

Each vector is a separate world with its own receiver preconditions, not a batch
of positive messages to deliver under one grant. Reused fixture IDs do not authorize
multiple real uses. Single-fault vectors pin exact diagnostic codes. Multi-fault inputs must fail closed;
receivers may limit diagnostics to avoid an oracle. UNKNOWN never authorizes effects.
Positive prompt-like text tests only that it is data, not that an actual agent/tool
stack is isolated; the latter is an independent stateful test below.

## Stateful / import design cases — NOT EXECUTED

| ID | Requirement | Acceptance evidence still needed |
|---|---|---|
| S01 | Receiver nonce generation before status | Outstanding nonce record and exact event/recipient binding |
| S02 | Durable one-use commit before delivery receipt | Atomic inbox/use/nonce/frontier record; receipt verification |
| S03 | Concurrent duplicate/conflicting message | One effect / stable original receipt / conflict for changed H |
| S04 | Revoke vs delivery race | Serialized commit order and signed receipts; no post-revoke-ACK delivery |
| S05 | Crash before commit / after commit before ACK / after ACK | No duplicate effects, no false success |
| S06 | App backup rollback with retained inbox/frontier | Reconcile or UNKNOWN; counters and revokes do not disappear |
| S07 | Loss/rollback of independent control copies | Safe lockout; no inferred authority or auto-reset |
| S08 | Network loss / delayed status / lost revoke ACK | Pending/UNKNOWN and correct bounded-staleness semantics |
| S09 | Receipt signature/domain/key/replay | Exact B node binding; never mistaken for Human mandate |
| S10 | Both directions with separate owner grants | No reciprocal authority inferred from receiving one note |
| S11 | Escaping / no model or tool execution from note | Real recipient integration negative tests |
| S12 | Independent teams/operators/storage/key custody | Configuration/evidence showing no shared authority or fault domain |
| P01–P12 | Import/handoff/key history/privacy | See PORTABILITY design matrix; no implicit PASS from wire tests |

## Requirements traceability

| PR #15 requirement | Current evidence | Remaining gate |
|---|---|---|
| R-ID-01 / R-ID-02 | Typed roles, pinned epoch1; unknown epoch rejects | Owner enrollment and multi-epoch history/import |
| R-MANDATE-01 | G01/G02 + N08–N19/N47/N63 | Human signing UX and key custody |
| R-REVOKE-01 / R-RECOVERY-01 | N35–N44/N59/N60/N64 | S04–S09, current retained state and clock qualification |
| R-MSG-01 / R-EVENT-01 | Golden signing bytes, G03–G07, N01–N15/N22–N27/N48–N58 | External implementation review and protocol freeze |
| R-REPLAY-01 | N19/N20/N35/N36/N39 | S02–S07 durable effects and restoration |
| R-INTEROP-01 | Two code paths, G02 reverse fixture | S12 independent operator run |
| R-PORT-01 / R-EXIT-01 / R-RETRACT-01 | PORTABILITY design only | P01–P12 and signed exit/retention receipts |
| R-MEM-01 / R-DISCLOSE-01 | No app/memory imports; synthetic inert note only | Real scoped disclosure/privacy integration |
| R-ASSERT-01 / R-ATTEST-01 | Explicitly outside note profile | Separate schema/trust/evidence/dispute design |
| R-ECON-01 / R-REBUILD-01 / R-FAIR-01 | No financial rights or allocation | Separate shadow reducer/fairness experiments |
| R-POLICY-01 / R-ACCESS-01 | Pinned policy/network and provider-free fixtures | Hosted-client/exit/provider continuity trials |

## Reproduction

Use an existing isolated environment with cryptography 48.0.1 and PyNaCl 1.6.2
(the same libraries already used by the project's pending Identity Core).
No production installation/dependencies, SQLite or model credentials are required.

```sh
python docs/protocol_0_1/conformance/generate_vectors.py
python docs/protocol_0_1/conformance/run_conformance.py
python -m compileall -q docs/protocol_0_1/conformance
git diff --exit-code -- docs/protocol_0_1/conformance/vectors.jsonl docs/protocol_0_1/conformance/golden.json docs/protocol_0_1/conformance/manifest.json docs/protocol_0_1/conformance/results.json
```

Generator seeds are intentionally public deterministic **TEST ONLY** inputs. A/B
keys, UUID-like handles, payloads and clock are synthetic. Reusing these keys or
fixed nonces for real access is forbidden. Known answers are committed before CI;
runner compares independently produced verdicts with manifest/case expectations.
Changing expected outcomes to make a failing validator pass requires specification
review, not an automatic regeneration of the oracle from that validator's answers.

Fixture context and CLI case container are trusted test inputs, not a network API.
Do not let a sender provide clock_ok, retained_available, used IDs or root trust
pins. Both verifiers are prototypes, not general-purpose JSON/identity packages.

The separate conformance workflow has contents:read, no secrets/deployment/app
imports, and only regenerates/checks the synthetic corpus. CI success is not
Docker/MVP regression, independent hosting, import or production readiness.

Current manifest/result hashes and exact CI evidence are linked from the Foundation
08.1 index/PR after validation. Unknown status remains explicit, never scored as PASS.
