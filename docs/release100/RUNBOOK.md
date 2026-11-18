# Build / review / stop / rollback

## Reproduction

Use a disposable checkout. Runtime dependencies remain unchanged in pyproject.

```sh
PYTHONPATH=.:src python -m unittest discover -s tests -v
python tools/release_guard.py build --output /tmp/blago-source-candidate-NEW
```

Build reads committed Git objects and the source-pinned baseline/config. It never
runs application entrypoints. Output must be a new directory. Four files:
candidate.tar.gz, manifest.json, baseline.json, config.json. Source files and tar
metadata/order/gzip mtime are deterministic; source SHA/tree and dependency bounds
are explicit. An uncommitted runtime edit does not silently enter the artifact.

## Future manual preparation (not executed here)

After BOOTSTRAP is completed and separately permitted: select workflow on main,
current exact40-hex SHA, successful Safe CI **push/main** run ID, unexpired artifact
ID and GitHub SHA256 digest, config SHA256 and PREPARE_SOURCE_ONLY confirmation.
Review environment approval independently. No direct interpolation into shell.
Only the trusted workflow main code performs validation. Downloaded artifacts are
never executed. PR artifacts cannot be promoted.

On success, inspect the annotated release-preparations/<source> tag and its exact
source/artifact/config/run binding. Scope remains SOURCE_PREPARATION_ONLY with
production_authorized=false. No deployment is available in this implementation.
A new source commit may produce the same runtime tar; the metadata records this,
but it does not represent another publication. Real publication needs a separate
reviewed publisher and durable deployment-state contract.

## Duplicate / conflict / partial failure

Do not rerun the workflow to override a failure: run_attempt>1 is rejected.
A new dispatch for a reserved SHA is rejected. Check tag/CI/run state before any
manual intervention. A timeout after ref creation can leave a valid reservation
without a returned receipt; treat it as UNKNOWN and preserve evidence. Orphan
annotated tag objects from a failed CAS are not an authorization. Never delete or
force-update an intent to make a test or release pass. No automatic rollback of
GitHub control history.

## Emergency stop

1. Owner disables manual preparation workflow and sets enabled variable false.
2. Cancel all active/queued runs, including old commits/workflow definitions.
3. Inspect reservations and any external integrations. Existing job environment
   variables do not dynamically change when the repo variable changes.
4. If production credentials were exposed: independent owner incident procedure,
   revoke write access and inspect HF/GitHub logs. Agent does not rotate secrets.
5. Preserve metadata/run/artifact hashes. There is no HF publisher to roll back
   in this code, and no DB migration to undo.

## Rollback

Keep the guard while reverting unrelated changes. **Do not revert to the old
push-to-HF workflow.** If the guard itself is faulty, disable it, keep legacy
workflow disabled and preparation variable false, and submit a reviewed forward
fix. Never restore an old SQLite or alter participants' permissions as release
rollback. Source bundles are not production backups. Emergency repair does not
authorize deployment. Production rollback/image promotion is outside this PR.

## Future Foundation compatibility

Runtime drift intentionally fails baseline verification. A future integration PR
must provide a reviewed new baseline/versioned schema/migration manifest,
dependency/image pins, negative upgrade/restore tests and explicit configuration
review. Preserve prior checksums and privacy/recovery controls. Do not flip
publication_enabled or reinterpret UNKNOWN fields to bypass that work.

Future integration must explicitly review new workflow files and runtime payload
types (e.g. static assets/schema JSON) before extending the current allowlist.
The baseline tests intentionally reject unreviewed runtime drift or workflow
addition; this is a review gate, not permission to rewrite old migration checksums.
