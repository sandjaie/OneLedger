# Development cycle

This file owns when OneLedger's gates run and what they produce.
[git-workflow.md](git-workflow.md) owns branches, commits, PRs, releases and the diff budget.

OneLedger is a single-owner personal application. Each branch gets one defect-finding pass and one
exact-head proof:

> phase plan reviewed once → TDD on money, authorization and database paths → local commit →
> one review → one repair → `scripts/verify.sh` once → first push → PR → merge

## Safety floor

No schedule pressure or process change relaxes these:

- money is Postgres `NUMERIC`, Python `Decimal` and strings on the wire; TypeScript only formats;
- every financial route requires the owner and every household table keeps row-level security;
- Flyway migrations are forward-only and an applied migration is never edited;
- real financial data, filenames, complete identifiers and secrets never enter code, fixtures,
  commits, logs, errors or agent messages;
- money, authorization and database behavior is test-driven;
- each branch receives one code review and one `scripts/verify.sh` run.

## Gate responsibilities

| Gate | Trigger | Produces |
|---|---|---|
| `/oneledger-plan-review` | A new phase specification and plan, once, before its first branch | Blocking contract, safety and size findings |
| `/oneledger-code-review` | A branch is implemented and its affected tests pass | One deduplicated finding set |
| Confirmation | A repaired blocking finding concerned money, authorization/RLS or a migration | Closure of those findings only |
| `scripts/verify.sh` | Review is resolved and the intended head is committed | One comprehensive pass and an exact-head receipt |
| `/oneledger-code-hygiene` | The end of a phase group, such as Phase 1.x | Findings for one `chore/` branch |

Review skills are report-only unless the owner explicitly requests fix mode. The implementation
agent applies findings. Nothing is weakened, deleted or skipped to make a gate green; a genuinely
wrong invariant requires an owner decision, usually a superseding ADR.

## 1. Plan a phase once

Write the phase specification and dated plan against the current repository. Each plan task is one
branch with the fields required by `CLAUDE.md`, sized to the budget in
[git-workflow.md](git-workflow.md). Group related work into one branch instead of splitting it
thin. Run `/oneledger-plan-review` once; the planner applies its blocking findings. There is no
second plan review and no confirmation pass.

The plan's forecast is made once. There is no dispatch-time reforecast. If a branch nears the hard
stop, finish its coherent part, name the remainder as the next branch in
`docs/superpowers/progress.md` within the same PR, and continue. Splits, re-baselines, deviations
and status updates never need a documentation-only PR or another plan review.

## 2. Implement with focused feedback

Use the branch declared by the plan and one implementation writer. For money, authorization and
database behavior:

1. Write the smallest failing test and observe the intended failure.
2. Implement the minimum behavior.
3. Run that affected test until green.

Screens and plumbing may be tested after they are written, provided each test fails when its
behavior is removed. Commit the coherent branch locally.

Run affected tests directly; `scripts/verify.sh` is reserved for the final gate. A necessary
deviation from a plan is recorded in the PR and `docs/superpowers/progress.md`, not retrofitted
into the plan.

Commit hooks remain fast and staged-file-scoped: secrets, configuration, real-data exclusion,
documentation structure, identifier rules, Ruff/format and ESLint. They are feedback, not the
final proof.

## 3. Review once, repair once

Run one `/oneledger-code-review` over the branch diff after affected tests are green. The reviewer
reads the plan task, changed code, relevant callers, tests and owning documents; it does not rerun
the verification suite.

Only CRITICAL and HIGH findings block. Fix them in one consolidated repair and rerun the affected
tests. Fix a MEDIUM finding in that repair when it is small; otherwise record it in PR Notes. LOW
findings go to PR Notes or are dropped. Out-of-scope work enters the project register only when it
would otherwise be lost.

Confirmation runs only when a repaired blocking finding concerned money, authorization/RLS or a
migration, and it checks only those findings. If confirmation still finds a blocking defect, stop
and ask the owner instead of starting another repair loop.

Documentation-only changes receive owner review rather than code review. The final verifier
selects their narrower executable scope automatically.

## 4. Run the final verifier once

After review and any confirmation are clean, commit the intended head and run:

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

## Financial write floor

Every new or changed financial write operation tests:

- owner success, and member and foreign-household refusal, with RLS on any new table;
- refusal of excess precision before storage, and exact round trips;
- an identical retry returning the original result, and a conflicting retry refused;
- no partial financial rows after a late failure;
- immutable imported facts and append-only corrections.

Stale-state and concurrency tests are required only where the operation's contract exposes them,
such as an `expectedState` field. Unsupported cases fail closed. Never fabricate prices,
transactions, cost bases, residuals, valuations or corporate actions.

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

At the end of a phase group, run `/oneledger-code-hygiene`; deliver its accepted findings on one
`chore/` branch through this same cycle.
