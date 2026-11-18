# Evidence index and reproduction

Snapshot date: 2026-11-18. Scope: read-only repository/public metadata review,
synthetic temporary tests, documentation. No deployment, live LLM calls, secret
inspection, production storage reads or migrations.

- `github_snapshot.json`: main, all 38 branches and all 35 visible PR base/head
  metadata before creation of this audit PR. 34 open; 33 Draft. Closed #8 unmerged.
- `git_inventory.json`: exact Git ancestry, every changed path and commit per PR;
  reads base..head, not working-tree guesses.
- `ancestry.json`: cross-line merge bases and left/right counts. #10 is already
  an ancestor of #19; Sovereign07 and Federation08 are not ancestors of #37.
- `ci_snapshot.json`: exact-head PR-triggered runs returned by connector wrapper.
- `ci_supplement.json`: all-event exact-head REST lookup for missing wrapper
  results; catches #9 browser failure. No runs != no tests ever existed.
- `ci3536_verified.json`: повторно прочитанные unit log counts #35/#36.
- `schema_surface.json`: static DDL source inventory, no migration execution.
- `ci37_verified.json`: exact #37 run, job conclusions and full-suite log counts.
- `hf_public.json`: running/repository revision and SHA256 comparisons of four
  allowlisted public source files. Never fetch a whole HF repository containing
  potential data. Matching individual files does not attest the whole image.
- `reproduce_context.py` / `context_probe.json`: deterministic synthetic probe of
  the reviewed #37 modules; no writes to production or source modules.
- `local_environment.json`: actual local Python/SQLite source-id and driver, not HF attestation.
- `local_checks.json`: actual local outcomes, environment limits and commands.

Recreate Git checks on a read-only clone with the referenced objects fetched:

```sh
git rev-parse main
git merge-base --is-ancestor d11a2a555efe57e6818450d534655020bf47b9ca 837b2bfd5d3db4842530d30ccf0f417bfe718737
git merge-base b79cb603a58adbab1e8a5313a06393a2feb5532f 4706a60e7ec675ed1ae16b9c9cd551cde291cfd3
git diff --name-status c3c6896076c9e24932ebdb0ee14e7c04065b91dd 4706a60e7ec675ed1ae16b9c9cd551cde291cfd3
git log --format='%H %s' c3c6896076c9e24932ebdb0ee14e7c04065b91dd..4706a60e7ec675ed1ae16b9c9cd551cde291cfd3
```

GitHub REST read-only sources: `/repos/xBLAGO/space/branches?per_page=100`,
`/pulls?state=all&per_page=100`, `/commits/main`,
`/actions/runs?head_sha=<exact SHA>&per_page=100`, `/actions/runs/<id>/jobs`.
Repeat pagination if a response reaches the page size. Record date, exact head
and event type. A workflow result cannot be transferred to a different commit.

Main baseline reproduction: in a disposable checkout of main3c218dc, install
its pyproject dependencies into a venv and run `PYTHONPATH=src python -m unittest
discover -s tests -v` (27 tests). Direct invocation without package installation
or PYTHONPATH initially failed module discovery; corrected invocation passed.

#37 reproduction needs its own dependencies and verified patched SQLite driver,
not the minimal dependencies from main. Commands are recorded in the context
report. Original sandbox denied localhost bind; the approved rerun allows local
HTTP, without any production service. Temporary test DBs remain synthetic.

This documentation branch intentionally inherits main's lack of PR test workflow.
Existing #37 CI is external evidence, not an assertion of CI for this branch.
**Do not merge this PR while main still has automatic HF push deployment.**
