# Custody protocol 0.3-lab.1

Source baseline #37:4706a60e7ec675ed1ae16b9c9cd551cde291cfd3.

Canonical serialization: existing capital-compatible sorted keys, compact JSON,
ASCII escapes, no NaN. Ed25519 using cryptography; separate domains
`capital-custody-0.3-lab.1/<kind>\0`. Packet/state bound8MiB, maximum64 custody
checkpoints/16 rotations. Human/Node/origin keys have distinct public keys.
Private packets are synthetic operator audit material; never public events.

## Bootstrap

Existing governor-only UEL proof and pins are independently checked. Config has
profile, fund, epoch1, zero previous, exactly A/B/C keys, origin_pins and delay60.
An existing GOV quorum signs this exact config. Genesis config hash is pinned
out of band. Signed keys do not prove physical independence or unique people.

## Normal delivery

1. Every node validates original capital proof, full old event prefix and UEL
   leaf prefix, counters and fixed source keys/stream/policy genesis pin.
2. Under process-safe flock + SQLite BEGIN IMMEDIATE, prepare one exact successor.
   Commit DB, fsync signed retained state; only then return checkpoint signature.
3. All3 votes form certificate. Each node checks body binding and its durable
   prepare, commits certificate, updates retained state, then returns receipt.
4. Caller succeeds only after all3 commits; no rollback of a partial commit.
   Identical latest-checkpoint delivery returns original receipt; older replay after advancement is rejected without effects. Lost response retries exact bytes.
5. Any incomplete prepare is visible as pending in fresh status. It is not
   silently forgotten after restart. Unresolved prepare during origin loss means
   hold; no automatic abort or replacement with a different successor.

Checkpoint body: profile/fund/epoch/config/index/parent/package/history_count/
history_head/uel_checkpoint. It certifies custody of a particular existing proof,
not truth of source claims, source completeness beyond that snapshot, nor spend.
Source CapitalLedger may create newer unreplicated events; those are not claimed
as distributed-confirmed. No execution guard is installed in application runtime.

## Fresh status

Receiver chooses32 random bytes, keeps challenge+external floor and never resumes
an old challenge after crash. Probe is single use even on failure. Require at
least2 distinct pinned current nodes with identical current frontier, no pending
state and issued/expiry within30s. Clock is an explicit laboratory assumption.
Offline verification always reports freshness NOT_CHECKED. Missing floor fails.
Floor binds index/tip and epoch/config; future imports cannot lower either.

## Custody rotation / recovery

Body binds old config, exact certified parent/index, new config epoch+1 and old
config hash, same origin fund pins, not-before/expiry. Existing direct live GOV
quorum signs; delegates do not count. Default fixture4/5; keys missing/exited/
revoked/expired are not replaced by operator keys. Remaining2 old nodes durably
prepare the same request. Their votes include rotation hash, own alias and
prepared_at. Effective time=max(not_before, latest prepare+60), strictly before
expiry. Replay preserves the first prepare timestamp. Clock skew/power loss and
real operator ceremony are unqualified.

Activation changes custody config only; surviving nodes must keep their own keys
or use an explicitly new store. New A2 imports verified archive with independently
retained floor. Historical certificates retain old keys. A new custody certificate
must use the current committee; historical duplicate ACK remains historical.
Expired/conflicting/pending recovery is held, not automatically reset. No
unilateral cancellation, no threshold reduction, no old source-key resurrection.

## Privacy/source boundary

Old public formats, canonical domain lifecycles and migration checksums remain
unchanged. Full private audit proof exposes pseudonymous keys, fund metadata,
opaque global UEL leaf counts and signed event amounts. It is NOT anonymized.
Application account deletion/revocation is governed by existing PrivacyControl;
remote replica erasure and real legal retention are NOT qualified. Only synthetic
material may enter this lab. No sessions, UID mappings, HMAC/private keys,
conversations or semantic memory are exported to replica stores.
