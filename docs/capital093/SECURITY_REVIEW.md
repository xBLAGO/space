# Security review — same author, synthetic scope

Reviewed source:5f171ff12e60ba8a11266b57116b4d7b21a4a877,
base4706a60e7ec675ed1ae16b9c9cd551cde291cfd3. No independent reviewer participated.

| Boundary / finding | Evidence / disposition |
|---|---|
| ACK before durable storage | runtime.Node.tx commits SQLite and signed retained control before returning prepare/commit. Crash before/after commit/control and lost ACK tests PASS. Physical fsync NOT QUALIFIED. |
| Normal2/3 permits Byzantine intersection | Normal certificate requires3/3; minority and partition tests reject. Availability sacrificed intentionally. |
| Key replacement without people | New custody epoch binds exact parent/config, requires original direct live GOV quorum plus2 old-node votes. Single operator/minority/expired requests reject. |
| Delay bypass by late preparation | Votes bind durable prepared_at; canonical effective time is latest prepare+60 or not-before. Late-prepare negative and delayed activation positives PASS. |
| Receipt/status replay | Package/config/event/history/UEL bindings and nonce+TTL checked. Probe is one-use; receiver must issue a new nonce after restart. Old historical ACK cannot authorize a new action. |
| Hidden state truncation | Independent floor pins both checkpoint prefix and config epoch/hash. All3 app/control rollback rehearsal rejects against external floor. Entire external knowledge loss is not detectable cryptographically; verification disallowed. |
| Source payload binding still trusted | Existing verifier explicitly returns TRUSTED_NODE_ATTESTATION/source_truth_proven=false. UNRESOLVED for full independent source truth. |
| Fund authority after origin loss | Read-only projection verifies with old public keys; no owner/evidence/privacy key reset. UNRESOLVED positive fund failover; DESIGN_ONLY. |
| Unreplicated source revocation | Freshness covers custody-certified snapshot, not unseen later source DB events. UNRESOLVED global freshness; no spending grant or real-data distribution allowed. |
| In-doubt prepare/recovery | Blocks further positive status/cutover. No unsafe automatic abort, threshold lowering or reset. Availability recovery protocol remains future work. |
| Privacy of replicas | Only synthetic private proofs; no memory/history/UID/session/HMAC/private-key export. Metadata not anonymous; encryption, real remote erasure and operator retention NOT QUALIFIED. |
| Canonical domains and old formats | src and existing migration/golden files unchanged. Integration manifest and full source CI preserve previous773 tests;48 new tests added. |
| Signing/host independence | Keys generated separately but by one fixture/author on one host. Independent operators/human key custody/transport review NOT EXECUTED. |

Source CI44337182123:821 tests twice, Docker and new private custody/artifact
verification success. PR event run44338740380 also success at the same source SHA.
Those are reproducible automation, not independent audits or production admission.

Decision: GO isolated local history-custody tests. NO-GO full fund authority
recovery, independent federation, real participants/funds and production.
