# UEL0.1 threat model and trust limits

Status: design review, same-author offline examples; not independent audit.

Trust boundaries: Human key/controller device; Agent proposing process; Node observer/executor; local domain SQLite transaction; original independently retained privacy controls; payload vault; recipient Node; public publisher/chain. Root/server compromise, colluding controllers, dishonest peers and correlated control rollback must be explicit assumptions, not solved by a local HMAC or successful CI.

| Threat | Required control / evidence | Current proof |
|---|---|---|
| Node/session/LLM impersonates Human | Typed signer roles, domain separation, independent pinned owner key and exact disclosure/action endorsement | Wire ROLE/domain negatives; full portable authority NOT IMPLEMENTED |
| Agent expands purpose or publishes inferred wish | Exact typed confirmation, bounded mandate/recipient/object/time/use; no Human key in Agent; proposal not action | Architectural requirement; current source guards retained |
| Signature replay or same ID changed content | canonical signed bytes/ref, domain, durable event ID/ref and source-event occurrence dedup | Wire/arithmetic examples; durable UEL NOT IMPLEMENTED |
| Unicode/duplicate JSON/float ambiguity | bounded ASCII header profile, duplicate detection, no normalization, payload byte hash, decimal strings | Executed W01–W38 |
| Payload prompt injection | inert data, escaped display, no implicit tools/approval or private context enrichment | W05 passes wire only, explicitly NOT authorized; no LLM called |
| Invalid/weak/self-enrolled key | reviewed crypto library + strict encoding/point validation + independent role enrollment; no TOFU from packet | Inspector pins generated known-valid test key only; enrollment unexecuted |
| Revoke race / partition / delayed status | same durable boundary delivery/revoke; receiver nonce/event binding and retained authority floor; UNKNOWN on insufficient evidence |08.2 local analogy, new UEL scenarios NOT EXECUTED; no instantaneous global promise |
| Old app backup resets use/denials/authority | separate retained checkpoints, reconcile all fronts before read/execute, UNKNOWN_RECOVERY on loss | Existing09.1 source565; no new UEL recovery implementation |
| Forged delete or rollback-denial suppression | independently verified erasure authority and lineage; cannot grant while state unknown; retain necessary minimum deny | Design; never accept unauthenticated universal delete as censorship authority |
| Duplicate stock from resource/delivery/attestation | canonical source mapping and occurrence ID, one object stock, one Help fulfillment occurrence | P01–P20 arithmetic examples; global physical uniqueness unproved |
| Second Help lifecycle via imports | observer adapter only; source Help revisions remain authoritative; no remote publish/complete executor | Runtime unchanged; source mapping specified |
| Two authoritative branches same object/version | quarantine, explicit owner-authorized resolution/epoch evidence; no LWW/hash winner | P15 snapshot conflict; full distributed resolution unimplemented |
| Dishonest title/valuation/counterparty collusion | evidence predicates and independence review, unknown/disputed coverage, provenance and exact lot basis | SELF_DECLARED never truth; Sybil/L3 not implemented |
| Hash/memory/biometric public correlation | no private roots or stable subject IDs on-chain; separate public release barrier | M05/M06 local examples; full privacy proof NOT IMPLEMENTED |
| Public small-cohort/differencing leakage | reviewed cohort/suppression/schedule/budget and public-disclosure authority | NOT APPROVED; no publishing mechanism |
| Public proof reused as consent | anchor/receipt is historical observation only; current authority lookup independently required | Design scenario, not a deployed verifier |
| Unbounded event/retention surveillance | meaningful durable actions only, private minimal payload refs, opt-in sharing, approved TTL and tombstones | Design/no new collection |
| Server/key compromise / same-author false confidence | independently managed controllers/trust anchors/custody, external implementation and review | NOT EXECUTED; #17 S12 remains NOT EXECUTED |

## Assurance taxonomy

`WIRE_OK_NOT_AUTHORIZED` ≠ eligible fact ≠ delivery ACK ≠ Human endorsement ≠ truthful evidence ≠ completed assistance ≠ public privacy approval. No exception/timeout/absent history is converted to success. Diagnostic codes are local conformance aids, not an information-leaking external oracle.

Offline arithmetic assumes an already verified source cut and current source snapshots. It neither validates event signatures nor proves an independent operator's honesty.72 passing examples do not establish the atomicity, crash safety, full cryptographic authority or privacy of a future UEL runtime. Existing Foundation08's73 vectors/35 local stateful tests apply only to that frozen note profile, not automatically to UEL.

## Required independent tests before promotion

At least two separately implemented validators/reducers must agree on frozen exact bytes/cuts, using different code paths/libraries where feasible. Separate operators/hosts/volumes must pin keys out of band, keep Human keys out of Agent processes, inject disk/commit/ACK/partition/restore/control-loss/fork failures, and prove no permission or duplicate effect after confirmed revoke. Public anchoring additionally requires independent privacy review and a real release barrier, not just a Merkle root.
