# Synthetic operator / recovery runbook

Baseline4706a60e7ec675ed1ae16b9c9cd551cde291cfd3. Do NOT run on production paths.
No migrations of existing application stores, no HF or public endpoint.

## Reproduce the local lab

Install the repository's pinned integration dependencies in a disposable venv.
Build the verified SQLite driver with tools/build_sqlite.py according to CI;
record actual version/source-id. Keep custody flags OFF in every normal runtime.
The following command explicitly creates only disposable synthetic fixture stores:

```sh
PYTHONPATH=.:src python -m tools.capital_custody_lab --output /tmp/custody-result.json --archive-output /tmp/custody-archive.json --pin-output /tmp/custody-pin.json --floor-output /tmp/custody-floor.json
PYTHONPATH=.:src python -m unittest tests.test_capital_custody -v
PYTHONPATH=.:src python -m tools.uel03_lab_check --full --repeat 1 --output /tmp/custody-full.json
PYTHONPATH=.:src python -m tools.freeze_integration
```

Output files are create-exclusive; use new paths on repeat. Evidence contains
public synthetic keys/signatures and fund audit metadata, never private keys,
sessions or personal memory. Do not publish real proofs using these commands.

Independent offline verification (replace GENESIS_HASH with authenticated pin,
not a value accepted blindly from the source):

```sh
PYTHONPATH=.:src python -m tools.capital_custody_verify /tmp/custody-archive.json --genesis-pin GENESIS_HASH --floor /tmp/custody-floor.json
```

Offline PASS is not current freshness. Probe must choose a new random32-byte
nonce, obtain signed statuses from at least2 current nodes and validate both
before30s. Never resume a pre-crash Probe/nonce. Keep floor outside every source
and replica rollback domain; do not regenerate it from a possibly stale archive.

## Three independent operators: NOT EXECUTED

Prerequisites: three separately administered hosts, volumes and backup accounts;
separate locally generated node keys; independent GOV controllers and security
review; authenticated OOB comparison of config hash, pins and source proofs;
qualified clocks, fsync/power-loss and encrypted private packet transport. Same
host/same operator cannot satisfy this requirement even with3 processes.

The lab includes an operator-local packet CLI; it is not an authenticated
network service. Each operator can generate its own synthetic key on its host:

```sh
export XBLAGO_CAPITAL_CUSTODY_LAB_ENABLED=true
export XBLAGO_INTEGRATION_MODE=synthetic
PYTHONPATH=.:src python -m experiments.capital_09_3.operator keygen --key ./node.key
```

Only the public key is exchanged. Independent humans review/sign the exact
bootstrap configuration using their own existing synthetic GOV keys; signatures
use `.../bootstrap\0` domain. The CLI does not own Human signing keys and never
obtains them from an Agent. The fixture's single-process signing is only a test.
Human approval hardware/client and non-equivocating signing ceremony remain
unqualified; do not replace them with an operator signing on behalf of people.

Before any checkpoint, use explicit Node.create with the quorum-approved genesis
archive and pinned genesis hash (reference Python API). After certified history
exists, independently verify floor and import it on a genuinely new local store:

```sh
PYTHONPATH=.:src python -m experiments.capital_09_3.operator import --key ./node.key --root ./replica --control ./independent-volume/head --genesis-pin GENESIS_HASH --alias B --input archive.json --floor independent-floor.json
```

A/B/C use the same *approved public configuration*, different private keys and
paths. Node.import never creates new CapitalLedger authority. Send existing proof
package JSON privately via an authenticated operator channel. On each host run
`operator prepare` with the same --key/--root/--control/--genesis-pin and --input
package.json; collect3 signed votes into the checkpoint certificate described in
PROTOCOL.md. Deliver it with `operator commit`. Retain all durable receipts and
an independent floor. An offline peer or pending phase means no global success.
The process-local Transport is an injectable adapter, not encrypted P2P.

## Origin loss / node rotation

1. Stop new replication and record independent certified floor, config and receipts.
2. Fetch/verify archives and fresh nonce-bound statuses from B/C. They must agree
   and have no unresolved prepare. Verify capital signatures without original
   server/private keys. Missing evidence => UNKNOWN_RECOVERY, no bootstrap reset.
3. Review a custody-only rotation body: exact certified parent, epoch+1, new A key,
   unchanged fund/source anchors, not-before/expiry. Collect direct live existing
   GOV quorum signatures; roles/expiry/revocation are derived by replay, not names.
4. B/C execute `operator prepare-rotation --input request.json` (body+approvals).
   Preserve returned votes. Wait at least60s after both durable preparations.
5. Build rotation certificate with canonical activated_at=max(not_before,
   latest prepared_at+60). `operator activate-rotation --input certificate.json`
   runs on surviving stores. No approval/key is inferred from Telegram sessions.
6. A2 has a freshly generated key; verify archive against independent floor, then
   import into a new store with aliasA. Never copy an old session or HMAC secret.
7. Recheck epoch/config and unchanged economic projection. Keep old public keys
   for historical verification; destroy retired synthetic private keys separately.
8. **Do not resume spending.** Original CapitalLedger authority cutover is not
   implemented; opening cloned application DBs is prohibited.

## Failure and backup handling

- DB/control mismatch after interrupted commit: hold, preserve files for review;
  never overwrite control from DB just to start the process.
- Recover a read-only replica on a new root from an independently verified archive
  and floor/current statuses. Preserve failed evidence; no in-place automatic repair.
- DB+local-control rollback: compare with independent floor and peers. Restore of
  all local copies together is insufficient evidence of freshness.
- Lost external floor or all independent copies: stop verification; recover a
  previously retained signed certificate out of band. Do not assert success.
- Pending prepare with unavailable signer, expired/conflicting rotation: hold.
  No automatic cancellation, quorum reduction or resurrection of authority.
- ENOSPC/EIO: no ACK; preserve ambiguous post-commit state. Injected fault tests
  are not evidence that real hardware/storage honors fsync.
- Rollback code: disable experiment; do not alter canonical stores/old formats.
  Retain certified archives/negative controls. Older experimental code must not
  read a newer epoch as writable. No production rollback or deployment is supplied.

Real operator drill acceptance: separately signed host/custody manifests, OOB
pins, packet hashes/receipts, source-outage capture, prefix/fork/replay negatives,
retained-floor custody-loss drill and explicit conclusion by every operator.
None of those independence claims are established by CI artifacts alone.
