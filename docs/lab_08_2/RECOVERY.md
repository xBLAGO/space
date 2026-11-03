# Failure and recovery — isolated lab only

## Commit protocol

1. Lock receiver across processes; load and compare retained head and witness.
2. Walk immutable record chain, check each SHA-256, index, previous and config pin.
3. Verify envelope, owner mandate, fresh receiver-bound status and retained frontier.
4. Stage complete inbox/use/nonce/frontier/receipt-blueprint record; fsync file and
   containing directory. Its hash includes index, previous, config and full state.
5. CAS retained head under its own lock; fsync head and directory. Write/fsync
   witness to the identical head. Success linearizes after both are durable.
6. Only now sign receipt with commit index/state hash; send HTTP response.

A receipt blueprint is stored before its signature. The final immutable record
hash is inserted in the receipt after commit, avoiding a self-referential hash.
Ed25519 signing is deterministic: exact retries recreate the original receipt.
All historical records remain; no compaction/TTL/reset is implemented.

## Expected failure results

- Before commit: no ACK; staged orphan is ignored. Retry may commit once.
- After commit / before ACK or lost ACK: original receipt on exact retry, one use.
- After socket ACK: restart returns same receipt, no second effect.
- EIO/ENOSPC/fsync error: never return success. ENOSPC is deterministic injection,
  not destructive filling of the host disk. A partially advanced checkpoint pair
  locks out; it is not treated as a clean transaction abort.
- Old application backup: missing committed record => UNKNOWN_RECOVERY.
- Complete application backup matching both current retained heads: resume; consumed
  mandates and durable revokes survive.
- Missing/corrupt/mismatched retained/witness: UNKNOWN_RECOVERY. No auto-bootstrap.
- Expired status: reject; missing authority/network: pending, no inferred success.
- Fresh authority fork: reject; receipt history never rewinds authority frontier.

## Safe operator drill

Use only a new directory under `/tmp`; never point this lab at an existing BLAGO
store. Stop receiver; archive **complete** immutable records/config and synthetic
node key separately from control copies. Hash/sign archive manifests externally.
Keep retained and witness copies outside the application backup/rollback domain.
Restore into a separate new instance; preserve config/key pins. Open only after
record chain matches both current retained heads. Re-run duplicate and revoked
mandate tests. Do not replace retained heads with old backup heads to make it pass.
If one checkpoint write succeeded and the other did not, retain all artifacts and
stop: reconciliation needs independent reviewed evidence. This implementation
intentionally has no repair/reset endpoint.

Local tests simulate separate rollback domains by copying only selected directories.
They do not prove survival of disk-controller cache loss, filesystem bugs, whole
server rollback or simultaneous rollback of all witnesses. Those require external
retention and actual fault-domain qualification. No production 05.4 journal,
production backup or migration is opened or altered.
