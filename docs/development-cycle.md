# Development cycle

This file owns when OneLedger's gates run and what they produce.
[git-workflow.md](git-workflow.md) owns branches, commits, PRs and releases.

The cycle is:

> plan review once → TDD with affected tests → local commit → comprehensive review → consolidated
> repair → confirmation if repaired → final verification once → first push → PR → merge

The aim is one defect-finding pass and one exact-head proof. Do not repeat a full review or full
suite on an unchanged head.

## Gate responsibilities

| Gate | Trigger | Produces |
|---|---|---|
| `/oneledger-plan-review` | A new or materially changed phase plan, before its first dispatch | Findings, task-size forecast and required splits |
| `/oneledger-code-review` | A task is implemented and affected tests pass | One deduplicated set of blocking and non-blocking findings |
| Confirmation review | Blocking findings were repaired | Closure of the original findings and inspection of their repair range |
| `scripts/verify.sh` | Review is clean and the intended head is committed | One comprehensive pass and an exact-head receipt |
| `/oneledger-code-hygiene` | A phase boundary | Findings for a separate `chore/` branch |

Review skills are report-only. They do not edit code, commit, push or merge unless the owner
explicitly requests fix mode. The implementation agent applies findings. Nothing is weakened,
deleted or skipped to make a gate green; a genuinely wrong invariant requires a design decision,
usually a superseding ADR.

## 1. Plan the phase once

Write the dated plan in `docs/superpowers/plans/` against the current repository. Each task is a
reviewable branch with the fields required by `CLAUDE.md`. Run `/oneledger-plan-review` once before
dispatch. Re-run it only after a material change to the plan or specification.

## 2. Implement with focused feedback

Use the branch declared by the plan and one implementation writer. For each behavior:

1. Write the smallest failing test and observe the intended failure.
2. Implement the minimum behavior.
3. Run that affected test until green.
4. Commit the coherent task locally.

Run the affected test command directly; `scripts/verify.sh` is reserved for the final gate. Do not
run every API, web and database suite after each patch. A necessary deviation from a plan is
recorded in the PR and `docs/superpowers/progress.md`, not retrofitted into the dispatched plan.

Commit hooks remain fast and staged-file-scoped: secrets, configuration, real-data exclusion,
documentation structure, identifier rules, Ruff/format and ESLint. They are feedback, not the
final proof.

## 3. Review once, then repair once

Run one comprehensive `/oneledger-code-review` over the branch diff after affected tests are green.
The reviewer reads the task contract, changed code, relevant callers, tests and owning documents;
it does not rerun the complete verification suite.

Fix every in-scope blocking finding in one consolidated repair and rerun only affected tests.
Record non-blocking or out-of-scope work in the PR Notes or the project register instead of
widening the branch.

If code changed for findings, confirm only the original findings and their repair range. If there
were no findings and no repair, there is no confirmation pass. A new architectural,
authorization, privacy, idempotency or concurrency boundary found during confirmation stops the
patch loop: perform one consolidated design/threat review before further edits.

Documentation-only changes receive owner review rather than code review. The final verifier
selects their narrower executable scope automatically.

## 4. Run the final verifier once

After review and confirmation are clean, commit the intended head and run:

```bash
scripts/verify.sh
```

The runner refuses a dirty tree, determines `docs-only` or `full` scope from the diff against
`develop`, runs every applicable check, verifies that the head and tree did not change, and writes
`.git/oneledger-verified-head`. Full scope includes documentation/static checks, API lint/types and
tests, migrations and SQL invariants, web lint/types/tests/build, generated API contracts, the
client-bundle secret scan and diff size. Docs-only scope includes documentation/static checks,
Markdown and diff size.

The pre-push hook does not repeat those checks. It runs:

- `scripts/verify.sh --check-receipt`, which requires the receipt to equal current `HEAD`;
- `scripts/check-diff-size.sh`, which protects the review budget.

Any commit after verification invalidates the receipt and requires one new final run on the new
reviewed head. Do not rerun the verifier merely for reassurance on an unchanged head.

## Financial database-operation matrix

Every new or changed financial write operation states and tests:

- owner, member and foreign-household behavior;
- unauthorized direct-DML refusal, RLS and immutability;
- stale expected-state and repeated idempotency requests;
- precision refusal before normalization, hashing or deduplication;
- active correction leaves and preserved historical records;
- one lock order plus concurrent duplicate and conflict paths;
- atomic rollback after each late failure, with no partial financial state.

Unsupported cases fail closed. Never fabricate prices, transactions, cost bases, residuals,
valuations or corporate actions.

## One test server per task

Database tests use the task checkout's isolated `db-test` server, never the working ledger:

```bash
eval "$(scripts/test-db.sh up)"
```

Start it once when the first affected database test needs it. Reuse it with
`eval "$(scripts/test-db.sh env)"` in a fresh shell. Each test run creates and migrates its own
temporary database on that server, so a changing forward-only migration is tested from empty
without a second server. The final verifier reuses the server and always tears it down with
`scripts/test-db.sh down`. Do not create a scratch Compose stack or run a Compose teardown command.

## Push, PR and merge

Push only the unchanged verified head, then open one PR to `develop` using the repository template.
Record actual affected-test, review, confirmation and final-verification evidence; mark anything
not run honestly. The orchestrator merges when all required evidence is green and deletes the task
branch. Anything targeting `main`, or a required check recorded as not run, remains an owner
decision.

At the phase boundary, run `/oneledger-code-hygiene`; deliver its accepted findings on a separate
task-sized `chore/` branch through this same cycle.
