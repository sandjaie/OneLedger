# Development cycle

This file owns when OneLedger's gates run and what they produce.
[git-workflow.md](git-workflow.md) owns branches, commits, PRs, releases and the diff budget.

OneLedger is a single-owner personal application. Each branch gets one defect-finding pass and one
exact-head proof:

> phase-group plans reviewed once → implement with tests for major behavior → local commit →
> one review → one repair → scope-appropriate verifier once → first push → PR → merge

## Safety floor

No schedule pressure or process change relaxes these:

- money is Postgres `NUMERIC`, Python `Decimal` and strings on the wire; TypeScript only formats;
- every financial route requires the owner and every household table keeps row-level security;
- Flyway migrations are forward-only and an applied migration is never edited;
- real financial data, filenames, complete identifiers and secrets never enter code, fixtures,
  commits, logs, errors or agent messages;
- major money, authorization and database behavior has tests;
- each branch receives one code review and one scope-appropriate final verifier run.

## Gate responsibilities

| Gate | Trigger | Produces |
|---|---|---|
| `/oneledger-plan-review` | A new phase group's specifications and plans (such as Phases 2.2–2.4), once, before the group's first branch | Blocking contract, safety and size findings |
| `/oneledger-code-review` | A branch is implemented and its affected tests pass | One deduplicated finding set |
| Confirmation | A repaired blocking finding concerned money, authorization/RLS or a migration | Closure of those findings only |
| `scripts/verify.sh` | Review is resolved and the intended head is committed | Changed-surface checks, named affected tests and an exact-head receipt |
| `scripts/verify-web.sh` | Same, for a web-only branch | Web, documentation, privacy and diff-size checks; no API or database suite |
| `/oneledger-code-hygiene` | The end of a phase group, such as Phase 1.x | Findings for one `chore/` branch |

Review skills are report-only unless the owner explicitly requests fix mode. The implementation
agent applies findings. Nothing is weakened, deleted or skipped to make a gate green; a genuinely
wrong invariant requires an owner decision, usually a superseding ADR.

## 1. Plan a phase group once

Write each phase's specification and dated plan against the current repository, and plan the
phases of a group together — Phases 2.2–2.4, or 3.1–3.4. Each plan task is one branch with the
fields required by `CLAUDE.md`, sized to the budget in [git-workflow.md](git-workflow.md). Group
related work into one branch instead of splitting it thin. Run `/oneledger-plan-review` once over the
whole group, not once per phase; the planner applies its blocking findings. There is no second plan
review and no confirmation pass.

The plan's forecast is made once. There is no dispatch-time reforecast. If a branch nears the hard
stop, finish its coherent part, and record the completed implementation and next branch in
`docs/superpowers/progress.md` within that task's PR. Splits, re-baselines and deviations belong
in the same PR; routine status updates need no later documentation-only PR or another plan review.

## 2. Implement with focused feedback

Use the branch declared by the plan and one implementation writer. Write tests for the major
behavior — money totals, authorization, household isolation and database writes — after or
alongside the code; test-first is optional. Obvious code, simple screens and plumbing need no
dedicated test. Run the affected tests until green and commit the coherent branch locally.

Run affected tests directly; the verifier is reserved for the final gate. A necessary
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

Documentation-only changes receive owner review rather than code review. `scripts/verify.sh`
selects their narrower executable scope automatically.

## 4. Run the final verifier once

After review and any confirmation are clean, commit the intended head and run:

```bash
scripts/verify.sh  # or scripts/verify-web.sh for a web-only branch
```

The full runner refuses a dirty tree, selects API, database and web gates from the diff against
`develop`, verifies that the head and tree did not change, and writes
`.git/oneledger-verified-head`. Pass each affected API or database test named by the task so the
local gate does not rerun an unrelated complete suite:

```bash
scripts/verify.sh \
  --api-test tests/test_search_live.py \
  --db-test db/tests/0061_investment_analysis_rules.sql
```

Omitting test arguments retains the complete suite for CI or genuinely cross-cutting changes.
Applicable gates keep documentation structure/privacy checks, API lint and types, migrations from
empty, web lint/types/tests/build, generated API contracts, the client-bundle secret scan and diff
size. Markdown style lint is not a delivery gate.

The web-only runner refuses non-web application or database changes and runs documentation/static
checks, web lint/types/tests/build, the client-bundle secret scan and diff size. It writes the
same exact-head receipt. Use the full runner if its scope guard refuses the branch.

There is no pre-push hook. The owner checks the verifier result and keeps the head unchanged
before the first push; `scripts/verify.sh --check-receipt` remains available for manual inspection.

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
not run honestly. Before final verification on a planned task branch, include that task's truthful
implementation status and exact next task in `docs/superpowers/progress.md`. The orchestrator
merges when all required evidence is green and the tracker has no stale "in progress" claim for
that task, then deletes the branch. Anything targeting `main`, or a required check recorded as
not run, remains an owner decision.

At the end of a phase group, run `/oneledger-code-hygiene`; deliver its accepted findings on one
`chore/` branch through this same cycle.
