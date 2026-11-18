# Fund key loss / authority cutover — DESIGN ONLY

The implemented custody rotation does NOT rotate CapitalLedger v2 anchors.
Version2 deliberately forbids changing member keys, privacy/evidence keys and
initial electorate. A safe continuation needs a separately reviewed version3
contract and independent verifier extension. No such event is activated here.

Proposed required contract, not an approved economic/ownership rule:

1. Explicitly distinguish Human owner/controller keys, limited Agent mandates,
   Node custody keys, private payload binding keys, evidence issuer keys and
   exclusive execution-writer authority. Rotating one must not grant the others.
2. Canonical future KeyRotationProposed binds fund ID, certified history head,
   original policy hash/version, old/new key IDs/roles, owner possession proof,
   activation delay/expiry, nonce, authority epoch and custody cutover plan.
3. Direct live governance quorum signs that exact action. Loss of enough keys
   cannot lower quorum or invent guardians. Recovery guardians must have been
   authorized BEFORE loss under an explicit separately approved policy.
4. During delay notify controllers through independently acknowledged channels;
   allow signed challenge/cancellation; preserve pending reservation/approval
   semantics. An operator notification callback is not human consent.
5. Future atomic canonical KeyRotated/AuthorityEpochChanged revokes old positive
   authority before enabling the replacement. Key history remains verifiable;
   authorizations bind policy and epoch. Include durable approval consume/replay.
6. Writer cutover must exclude two concurrently active issuers for the same lot.
   Commit must bind independently certified parent and unresolved-prepares state.
   Partition => no positive cutover/spend. Replication receipts are not grants.
7. Existing sessions/UID mappings and privacy/G09 controls are revalidated locally.
   No server secrets, missing signed source history, old permissions or deleted
   memory are reconstructed. Evidence/private payload anchors need a reviewed
   transition that does not expose user data or trust one old source signature.
8. Independent verifier must replay every old/new key epoch, reject old-key use,
   detect conflicting successor certificates and enforce out-of-band current tip.
9. Acceptance requires A loss, recovery on B, fresh authorized new synthetic
   command, old A rejoin rejection, no duplicated reserve/spend, deletion/recovery
   and key/approval/role revocation under concurrency. All currently NOT EXECUTED.

Unrecoverable cases: lost required human quorum with no prior recovery policy;
missing independent current floor; contradictory accepted histories; private source
bindings unverifiable; uncertain writer exclusivity. Outcome is hold, never admin
reset. This explains why 09.3's safe implementation is limited to history custody.
