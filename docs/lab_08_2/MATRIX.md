# S01–S12 results — local scope

Expected results are normative 08.1 `CONFORMANCE.md` S01–S12. Actual evidence:
[test runner output](evidence.json), named assertions in
[tests](../../experiments/federation_08_2/test_runtime.py) and CI artifact
`synthetic-lab-public-evidence`. Each record includes actual PASS/FAIL and public
config/state/frontier hash or explicit UNKNOWN_RECOVERY. Run-specific synthetic
hashes change because keys/nonces are freshly generated. No golden result changed.

| ID | Expected | Actual / executable proof | Scope |
|---|---|---|---|
| S01 | Receiver persists unpredictable event-bound nonce before status | PASS: `test_S01_receiver_nonce`, rejects foreign nonce | Local |
| S02 | Inbox/use/nonce/frontier durable before receipt | PASS: `test_S02_commit_receipt`, complete state and retained receipt membership; write-fault tests produce no ACK | Local POSIX |
| S03 | Concurrent identical requests one effect; changed H conflict | PASS: `test_S03_concurrent_duplicates_and_conflict`, `test_S03_multiprocess_duplicates`; 16 threaded + 4 process requests | Local |
| S04 | Delivery/revoke serialization; no new delivery after revoke ACK | PASS: both serial orders plus concurrent test; duplicate is historical receipt, never new effect | Local |
| S05 | Crash before/after commit/after ACK, no false ACK or duplicate | PASS: real spawned process exit, including HTTP send/flush then exit and exact receipt on restart | Process crash, not power cut |
| S06 | Old app backup cannot resurrect consumption/revoke | PASS: old backup UNKNOWN; complete matching backup resumes, revoked grant stays denied | Simulated app rollback domain |
| S07 | Control-copy loss/rollback locks out | PASS: missing retained/witness, corrupt record, rollback each copy, crash between checkpoint writes | Local control-copy simulation; correlated rollback not proven |
| S08 | Partition/delayed status/lost ACK never false success | PASS: loopback transport drops delivery ACK / partitions, lost revoke ACK model, expired status rejection | Injected network faults; not real WAN partition |
| S09 | Pinned signature/domain/bindings; receipt no Human authority | PASS: delivery/revocation receipt checks, substitution, bad key, wrong kind, boolean counter | Same-author verifier, trusted local membership capability |
| S10 | Reverse requires separate Human grant | PASS: new Human/Agent/Node keys, isolated receiver A/reference A and B/reference B; demo runs two real Node processes | Local integration only |
| S11 | Inert escaped content, no LLM/tools/memory | PASS: role-restricted Agent signer rejects mandate; escaped script text, inbox-only state | Lab recipient; production not integrated |
| S12 | Independently managed operators/keys/hosts/stores | **NOT EXECUTED**: no second operator or independent reviewer available | Independent lab **NO-GO** |

Additional tests: caller-context injection, contradictory signed history, qualified
clock absence, EIO, ENOSPC at app/control/witness writes, fsync errors and uncertain
commit retry. ENOSPC is injected, not a claim of a physically full test volume.

All S01–S11 local assertions must pass for **GO local stateful lab** under the
stated honest filesystem/clock model. These results never mean independent-node
or production GO. Correlated rollback of every copy cannot be detected locally;
external anchors are an explicit gate, not an assumed solved property.
