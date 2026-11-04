# Independent Privacy Recovery review

Reviewer: separate review agent, not the implementation author.
Reviewed baseline: PR #6, commit 224ad9d2a1775a8372354af35d542f433cb8bdde.
Scope: security_journal.py, privacy_control.py, privacy_schema.py,
privacy_maintenance.py, storage transactions, alias/deletion interactions.
No repository source changes, production access, secret access or HF changes.
Frozen source/test copy: /tmp/blago-independent-base-053 (git archive of baseline).

## Verdict on baseline

NOT ready for a closed pilot: two reproduced HIGH privacy-recovery failures.
Ordinary HMAC tamper/key-loss/crash handling passed focused tests under the
stated trusted-writer/POSIX/local-filesystem threat model. This is an independent
agent review, not an external human audit or storage hardware certification.

## Reproduced findings

### HIGH 1 — historical legacy value resurrects after update then deletion

Locations: privacy_schema.py:64-65; privacy_control.py:152-153;
storage.py:set_memory (ON CONFLICT UPDATE value_json/updated_at).

1. Save legacy city=Old.
2. Back up application SQLite.
3. Set the same key to New.
4. Delete city.
5. Open old application backup against current independent journal.

Observed: live memory={}, restored memory={city:Old}. DELETE tombstone targets
new value/updated_at, so it cannot identify the old version in the backup.
Repro: /tmp/blago_independent_repro_versions.py.

### HIGH 2 — merge changes fingerprint and defeats later memory/history erase

Locations: privacy_schema.py:64-68; privacy_control.py:136-161;
integrations/privacy.py:25-31 and 115-116 (exports alias family).

1. Source Telegram account has city and dialogue.
2. Backup before merge.
3. Merge source into canonical Web account.
4. Delete canonical city and clear canonical history.
5. Restore old backup and log in as canonical account.

Observed: live export memory/history empty; restored canonical-family export
contains the source's erased city and dialogue. Fingerprints contain owner/key;
replayed alias metadata does not transform stored rows.
Repro: /tmp/blago_independent_repro.py.

A recomputed merge allocation alone is insufficient if post-backup occupied keys
differ. Example: target.city + source.city in backup, then create target.telegram.city,
merge gives source -> telegram.city.2, delete .2; old backup recompute gives .city.
Proposed correction reviewed conceptually: signed exact oldFingerprint -> newFingerprint
lineage, inverse transitive closure limited to events before deletion. Preserve scope,
cycle protection and allow re-remembered new generations; no matching by fact value.

### MEDIUM / policy gap — clear-history retains old Telegram response cache

Locations: storage.py:clear_history and cached_update_replies; events.response_json.

A cached /history response containing the city is still returned on duplicate old
Telegram update after /clear-history. Live DB retains a copy, not just a backup.
Repro: /tmp/blago_independent_repro_cache.py prints True.
If the product promise is only cleared LLM session context, explicitly document it.
If history erasure is promised, scrub cached content while retaining update dedupe and
journal a cache-clear intent so restore cannot return it.

### LOW — signed negative head index accepted

Location: security_journal.py:122-125.
A head signed with seq=-1 and head=last_chain_hash passes Python negative indexing,
then gets promoted to the current positive sequence. Requires the signing key;
not an unauthenticated bypass. Strict non-bool integer/range checks are appropriate
for independently supplied checkpoints. App cursor should receive equivalent checks.
Repro: /tmp/blago_independent_repro_journal.py.

### LOW — journal lock open failure is not normalized

Location: security_journal.py:97 (outside try).
PermissionError opening journal.lock escapes as PermissionError, not JournalUnavailable.
HTTP therefore takes generic 500 path rather than expected recovery-unavailable 503.
Business transaction remains fail-closed; no authorization bypass reproduced.
Repro: /tmp/blago_independent_repro_journal.py.

## Executed focused baseline checks

Eight frozen-baseline tests passed (6.014s): lost key/wrong journal, tail rollback,
append/fsync failure rollback, durable-intent-before-app-commit failure, real process
os._exit, cross-user semantic supersedes FK, pre-05.3 restore, stale writes after deletion.
Synthetic repro scripts were rerun against the frozen baseline copy.

## Remaining assumptions / limits

- Root/key compromise or rollback of every journal/key/head copy together is outside
  local HMAC detection. Independent fresh monotonic checkpoint storage is necessary.
- POSIX advisory lock and truthful fsync/local disk semantics are assumed.
- Tests kill a process; they do not prove power-loss/storage firmware behavior.
- Journal is not replication of all normal post-backup writes/new credentials.
- Any alias replay design must either reconstruct exact signed lineage/state or deny
  opening inconsistent state. Do not guess identity/memory correspondence.
- Legacy ambiguous aliases, economics retention and physical byte erasure need explicit
  reviewed procedures. Full account deletion correctly refuses unsafe FK dependencies.
- No real browser/WebView/WebAuthn hardware or HF behavior was assessed in this subtask.
