# Independent recheck: Foundation 05.4 working tree

Reviewer: separate review agent; no repository source modifications.
Branch reviewed: feat/foundation-05-pilot-safety-gate, working tree following baseline
224ad9d2a1775a8372354af35d542f433cb8bdde. This review is not a human external audit.

Command executed:
PYTHONPATH=/tmp/blago-sqlite-driver-3541:src:tests /tmp/blago-identity052-venv/bin/python -m unittest test_pilot_safety -v

Final result: **17/17 PASS, 14.737s**, with the default macOS TMPDIR.
Full output: /tmp/blago-recheck-tests-default-final.log.
The discovered /var versus /private/var test-instrumentation comparison was corrected
with resolved paths; it is no longer an open finding. Repository sources were changed
only by the implementation author, not this reviewer.

## Previously reproduced failures

- Historical legacy update/delete resurrection: corrected for operations covered by
  explicit 05.4 migration. Exact OLD fingerprint -> NEW fingerprint lineage is signed;
  deletion uses reverse transitive closure with sequence boundary and visited set.
- Merge/history/occupied telegram.city.2 allocation: regression passes without replaying
  guessed merge allocation or matching by fact value. Other participant remains isolated.
- Re-remember after deletion and lineage cycle/future-edge boundaries: tests pass.
- Cleared history remains in cached Telegram response: scrub now removes cached response
  and error while preserving update dedupe; replay regression passes.
- Signed negative/bool/float head indices and lock-open failures: tests pass.
- Frozen 05.1–05.3 schema/checksums retained; 05.4 coverage marker checked in pilot mode.
- Unsafe SQLite gate fails before opening/creating application DB; actual patched
  version/source-id verified by the test environment.

## DR / witness checks

Reviewed AES-GCM packaging, file allowlist, per-file digest checks, private temporary
staging, fresh independent signed high-water validation, no-overwrite destination,
wrong wrapping key and tampered ciphertext rejection. Provided stale-package and
independent-process server-B recovery tests pass with synthetic participants.
The source primary app/journal/key/head are removed in the DR test; the recovered
instance authenticates the survivor and retains the deleted principal tombstone.

Independent witness detects rollback of journal.sqlite3 AND its local head when the
fresh retained checkpoint survives. Missing witness fails closed. Path separation
itself does not establish different physical fault domain or immutable IAM.

## Witness durability finding: corrected and independently rechecked

The previously reported MEDIUM failure is closed in the reviewed working tree:
- Checkpoints are written to a private temporary file, file-fsynced, then atomically
  published with os.link without replacing an existing retained checkpoint.
- Unpublished temporary files are cleaned up on error.
- A matching existing maximum checkpoint is file-fsynced and its directory synced
  again before a retry can treat it as acknowledged.
- New journal/witness directories and init-wrap-key now sync their parents.

Independent updated repro: /tmp/blago_recheck_witness_durability_fixed.py
Observed:
post_publish_append_rejected True
durable_intent_present True
witness_directory_fsync_retried True
unpublished_tmp_files 0

Additional injected checkpoint file-fsync failure did not publish a final checkpoint
and left zero .checkpoint temporary files. The new automated regression's intended
third-sync failure ordering is correct. Its resolved-path instrumentation is now
portable across the default macOS temporary-directory alias.

## SQLite build/runtime evidence independently checked

The build tool selects the official upstream SQLite 3.54.1 amalgamation URL and pins
both its archive SHA256 and sqlite3.c SHA3-256 before compilation. This reviewer
recomputed both hashes from the local archive and compared the C source hash with
/tmp/blago-sqlite-3541/build-evidence.json:

- Archive SHA256: 0a9c8e34352dad46006aa82836d5383bff9dc6bc9ce5a60553c05aa30e6ad1f9
- sqlite3.c SHA3-256: e2b869d3003b33c942b7dd7063604fbc53553e1b66609570df39b9b7b558ee81
- Actual Python sqlite_version: 3.54.1
- Actual sqlite_source_id(): 2026-10-16 14:45:20 555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76
- Python extension: /tmp/blago-sqlite-driver-3541/_sqlite3.cpython-312-darwin.so
- require_safe_sqlite(): wal_reset_fix_verified=true

This verifies the tested Python library, not an unchanged live Hugging Face deployment.
Chrome virtual-authenticator/browser and HF integration results collected separately
by the primary agent are separate evidence; they were not independently audited by
this reviewer and must not be described as physical-device or hosting attestation.

## Scope and acceptance limits

- 05.4 is not retroactive repair for untracked pre-05.4 legacy transitions. The marker
  is coverage metadata, not proof that historical deletions were audited. Operator
  must choose a reviewed current primary and create certified baselines afterward;
  running migrate_pilot on an arbitrary old copy does not establish historical safety.
- Fresh designated witness storage and wrapping-key escrow need actual off-host,
  retention/IAM and loss-of-server acceptance evidence. A same-host synthetic directory
  demonstrates software behavior, not independent infrastructure durability.
- Deleting every external checkpoint or rolling back app+journal+all witnesses+keys
  together remains outside local detection. Key/root compromise is outside this model.
- Journal is not replication of all ordinary post-backup writes/new credentials.
- No real browser/WebView/physical authenticator, HF or hardware power-cut testing
  was performed by this reviewer.

## Final conditional verdict

**The reproduced original HIGH failures and witness durability MEDIUM are fixed in
the reviewed 05.4 prospective coverage; all 17 safety regressions pass independently.**
The cache-erasure, numeric-anchor and lock-failure findings are also closed.
No remaining reproducible HMAC/AEAD bypass was found within the stated trusted-host,
key-custody and local-POSIX filesystem assumptions.

This is conditional technical approval of the reviewed corrections, not approval to
start a pilot. Actual independent custody/immutable retention/IAM, a reviewed historical
primary and certified golden baseline, real-device acceptance and hosting/runtime
attestation remain operational safety gates. Existing pre-05.4 untracked deletions are
not retroactively proven safe by applying a new marker. No merge, deployment, production
SQLite or production secret actions were performed by this reviewer.
