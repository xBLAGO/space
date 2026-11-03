# Operator readiness and reproducible runs

## Executable same-host rehearsal

Python 3.12, cryptography==48.0.1, PyNaCl==1.6.2; no application dependencies,
production configuration, HF credentials or existing keys are required.

```sh
python -m venv /tmp/blago082-venv
/tmp/blago082-venv/bin/python -m pip install cryptography==48.0.1 PyNaCl==1.6.2
/tmp/blago082-venv/bin/python docs/protocol_0_1/conformance/run_conformance.py
/tmp/blago082-venv/bin/python experiments/federation_08_2/run_lab.py /tmp/blago082-evidence.json
/tmp/blago082-venv/bin/python experiments/federation_08_2/demo.py --new-directory /tmp/blago082-new-demo
```

The last directory **must not exist**. Demo creates A/B app, retained, witness and
public config files plus test-only node private keys (0600). Human/Agent keys are
fresh and remain inside separate spawned signer processes. Do not publish the
whole directory: only `public-evidence.json` and reviewed public configs are
shareable. Terminating the demo stops all nodes/signers. It is a reproducible
synthetic rehearsal, not a long-running identity service. Network binds only
127.0.0.1. A uses reference A; B uses reference B. Both share the storage lab code.

## Independent two-operator experiment — blocked, NOT EXECUTED

Do not expose the loopback HTTP adapter to the Internet or use an SSH tunnel as
proof of independent retention. This version deliberately refuses non-loopback
Transport URLs. There is no production start command or approved remote adapter.
The following checklist is the reproducible acceptance procedure after those
adapters are independently implemented and reviewed; it is not a claim that the
current demo can execute S12 on two hosts.

1. Operators A and B independently check out the same reviewed source SHA and
   verify schema hash `64667aa8fb6abd535e4a2f93d17d6ed56c46d31ab548c84eb56f4cc80a86b85a`.
   Run all 73 vectors on both implementations and all local fault tests.
2. Provision separate hosts, administrator accounts and app volumes. Each node
   needs independently operated retained and witness domains, outside its app
   backup lineage. Record a diagram of admin/storage/cloud-account dependencies.
3. Each Human controls a distinct offline signing process/device under separate
   access control from Agent. Generate fresh keys; never reuse committed fixtures.
   Neither node nor agent receives Human private key. Record key-custody review.
4. Exchange public cards via authenticated out-of-band contact. Compare exact
   profile, schema revision/hash, network/policy, owner/genesis, agent/node handles,
   keys, retained-service pins and allowed `note.deliver` scope. Both Humans approve.
   A packet cannot establish or replace those pins.
5. Qualify clocks (uncertainty ≤5 seconds), fsync/rename semantics and rollback
   independence. Implement nonce-bound signed control responses/CAS membership
   proofs with pinned control-service keys, authenticated transport and no reset.
6. Execute A→B using a new explicit one-use Human A grant. Verify all receipt
   bindings independently at A, including committed retained membership. Archive
   signed public messages/receipts, chronology and environment/source hashes.
7. Repeat S01–S11 across actual network partitions, process kills, storage failure,
   old app restore, individual control-copy loss/rollback and delayed ACK. Do not
   fabricate disk power-loss evidence from an injected Python OSError.
8. Execute B→A using a new Human B grant and distinct keys. Receiving a note grants
   no reply authority. Confirm old grant deliveries fail after durable revoke ACK.
9. Both operators sign separate execution records. A reviewer independent of the
   implementation authors verifies source, pins and evidence. Mark S12 PASS only
   then; otherwise it remains NOT EXECUTED and independent lab is NO-GO.

No real users, personal memory, payments, mainnet, production identity, SQL,
HF Space, deployed API or tokens may enter this experiment. Existing 05.4/06.1
pilot blockers remain open and must be separately resolved for any future MVP.
