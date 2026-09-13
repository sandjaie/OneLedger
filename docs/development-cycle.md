# Development cycle

The single ordered reference for how work moves from a plan to a merge, and for the gate that
stands at each step. [docs/git-workflow.md](git-workflow.md) owns branches, integration and
releases; this file owns **when a gate runs, who runs it, and what it produces**. When the two
disagree about a branch, git-workflow.md wins; when they disagree about a gate, this file wins.

The cycle is deliberately staged to find defects once and prove the final head once — owner
decision, 13 September 2026: **plan → focused TDD → comprehensive local review → consolidated
fix → final verification → narrow confirmation → merge.** External (Codex) review is disabled.
The local review finds defects; the final suite proves the reviewed result. Repeating the whole
suite or the whole review without a changed risk surface is waste, not an additional gate.

## The review skills report

`/oneledger-plan-review`, `/oneledger-code-review` and `/oneledger-code-hygiene` **produce
findings another agent can patch. They do not modify code** unless the owner explicitly commands
fix mode. A reviewer that fixes what it finds hides the finding from whoever needed to learn it,
and reports on a tree that no longer matches the one it reviewed.

| Skill | Reads | Produces | Never |
|---|---|---|---|
| `/oneledger-plan-review` | a plan or spec, the ADRs and design documents, the current tree | findings, a size forecast per outcome, a revised task and branch table | edits the plan, dispatches a task |
| `/oneledger-code-review` | the branch diff against `develop`, the tree around it | findings triaged blocking / non-blocking, with file and line | edits code, fixes a finding, pushes, opens a PR |
| `/oneledger-code-hygiene` | the whole tree at a phase boundary | findings grouped mechanical / judgement, and a proposed `chore/` branch | deletes anything, edits a migration, touches a test |

`/verify` is the other kind of gate: `focused` and `database` tiers support implementation, while
the `full` tier is the final branch gate. It reports green or red and changes no tracked files.

## The cycle

### 1. Plan the phase

The Phase N.M outcome's dated execution-map plan goes in `docs/superpowers/plans/`, shaped per
`CLAUDE.md`. The canonical sequence lives in
`docs/product/master-implementation-plan.html`; each unimplemented outcome gets its dated plan
written immediately before implementation, against the tree at that time.

### 2. Gate — `/oneledger-plan-review`, once per new plan

**Trigger: a phase plan or spec has just been written, or materially changed, and nothing has
been dispatched from it yet.** One pass: apply the findings, then dispatch. It does not re-run
per task, and it does not run at all for work an existing reviewed plan already covers.

### 3. Implement the planned outcome

One implementation agent at a time, on the plan's declared branch, TDD in the plan's order. One
task branch and PR normally occupy one implementation session; after merge, start the next task
from a compact handoff instead of carrying the phase's entire working context forward. A deviation
from the plan is legitimate where repository reality requires it, and is recorded in the PR and
`progress.md` — never by editing the plan after dispatch.

**Nothing is weakened to reach green.** Not a loosened assertion, not a deleted case, not a
skipped test, not a softened invariant. A red suite is a finding that already found itself, and
an implementer who edits the test has removed the finding rather than the defect. If an
invariant is genuinely wrong, that is a superseding ADR. The same rule binds the gates, and for
the same reason — "What a gate never does", below.

**The commit hook is the fast feedback loop, not the full gate.** It checks staged-file secrets,
configuration syntax, whitespace, real-data exclusion, documentation structure, identifier rules,
and file-scoped Ruff/format and ESLint. It performs no network fetch. Whole-project type systems,
Markdown lint, generated API contracts and the diff budget run at pre-push. Application tests,
database migrations and SQL invariants run through the tiered verification runner and CI, not in
Git hooks.

During implementation, run only the smallest test that can prove the RED/GREEN step. Use the
single runner so the command, exact Git head and result remain together as evidence:

```bash
scripts/verify.sh focused -- <affected-test-command>
scripts/verify.sh focused --database -- <affected-database-test-command>
```

The database form starts this checkout's `db-test` only if it is absent, then leaves it running for
the next focused check. Do not run every API, web and SQL test after each patch. Focused red is not
reviewed: a failing affected test is a finding that already found itself.

### 4. Gate — `/oneledger-code-review`, one comprehensive round

**Trigger: the planned behavior is implemented and its affected tests are green.** The order is:

> focused TDD → comprehensive review → consolidated fix → full `/verify` → narrow confirmation →
> **first push** → PR

The reviewer reads the diff against this repository's own rules: identifier leakage, money
handling, the authorization boundary, reporting views, migrations, invariants, TDD evidence.
**Blocking findings** — correctness, security, an invariant or contract breach, missing test
evidence — are fixed on the branch before the push. **Non-blocking findings** — hardening,
style, scope-widening suggestions — are recorded in the PR's Notes and become their own later
task if they are worth one. The first review reports its complete, deduplicated set of findings at
once; it does not drip findings through sequential passes. When a change spans independent risk
domains, read-only database/security and API/financial reviewers may inspect it in parallel and
one controller merges their reports. The one-writer rule remains absolute.

For a branch that contains one reviewed task, that clean task review **is** the branch review. Do
not dispatch another reviewer over the same base-to-head range. A fix receives one confirmation
over the fix range. A branch containing several separately reviewed tasks gets one final review
only for their combined integration surface, not a repetition of each task review.

Nothing reaches `origin` unreviewed. A documentation-only branch carries no code for this gate
to read: `scripts/check-docs.py` and the commit hooks are its gate, and the PR says so.

### 5. Fix once, then run the final verification once

The implementation agent applies the review's blocking findings as one consolidated repair and
runs only affected focused checks while doing so. When those checks are green, run exactly one
final gate on that head:

```bash
scripts/verify.sh full
```

For a database-only branch, `scripts/verify.sh database` is the final gate when the plan and review
explicitly establish that no API or web surface can be affected. The runner records the starting
Git head, refuses evidence from a head that changes underneath it, reuses the branch's test server,
and tears that server down once at the final boundary. A changed head invalidates old full-suite
evidence; re-running after an actual repair is required, but a second scheduled run on an unchanged
head is forbidden.

### 6. Confirm only the repair

After the final gate is green, the reviewer confirms the original findings against the repair
range. It does not re-review the base-to-head diff and does not re-run the full suite. If the
confirmation is clean, push that unchanged head.

If confirmation uncovers a new architectural, authorization, privacy, idempotency or concurrency
boundary, **stop the patch loop**. Produce one consolidated design/threat review covering that
boundary before any more edits. After its repair, focused tests, the full gate and one narrow
confirmation run on the new head. Repeated one-finding/one-fix rounds are not an accepted delivery
strategy.

### Financial database-operation review matrix

Every new or changed financial write operation — SQL function, route transaction, replay,
correction or import commit — states and tests this matrix before review:

- owner, member and foreign-household behavior;
- refusal of unauthorized direct DML, with RLS and immutability behavior;
- expected-state or idempotency conflicts on retries and stale requests;
- input precision validation before normalization, hashing or deduplication;
- active correction leaves and preserved historical records;
- lock acquisition order and the concurrent duplicate/conflict path;
- atomic rollback after each late failure, with no partial financial state.

Unsupported cases fail closed. No price, transaction, cost basis, residual or corporate action is
invented to make an operation complete.

### One test server for the branch lifecycle

**The live tests need a database of their own, and refuse any other.** Every `*_live.py`
fixture empties `transaction`, `import_batch`, `document`, `statement_layout` and
`classification_rule`. Start theirs and export it in one step:

```bash
eval "$(scripts/test-db.sh up)"
```

That brings up the `db-test` container — its own port, its own volume, behind the `test`
compose profile — migrates it, seeds it, and exports both `ONELEDGER_TEST_DSN` (which the
fixtures read) and the `POSTGRES_*` the routes under test connect through. Without it the live
tests skip; with `.env` exported instead they **fail**, because `tests/live_database.py`
refuses a database holding anything those fixtures would empty and refuses a split where the
fixtures and the routes point at different databases.

**Binding agent rule: one task branch uses one dedicated `db-test` server for its entire
lifecycle.** `scripts/verify.sh focused --database -- ...` starts it only when absent and every
later focused check reuses it. A fresh shell may use `eval "$(scripts/test-db.sh env)"` to export
the already-running server, never `up` again. API tests and `scripts/hooks/db-tests.sh` both use
that server. The final `database` or `full` tier tears it down exactly once, including on failure.
A separate scratch Compose stack or a second isolated server is not part of the delivery cycle.

Migrations and fixtures still get fresh databases on that server. Therefore an unpushed migration
may change between focused runs without carrying a stale applied checksum: the next API or SQL
run creates a new database and migrates it from empty on the same server. Recreate the test server
only when the server itself is unhealthy, not as a routine second verification environment.

**The stage's checkout owns its one test server.** Each other checkout, including a worktree under
`.claude/worktrees/`, is its own compose project, so `up` there starts `<worktree>-db-test-1` on
the `<worktree>_pgdata_test` volume, but one delivery verification stage never spans those
checkout servers. The port is `TEST_DB_PORT` if set, then whatever this
project's running `db-test` already holds, then 55434, then any free one. `up` exports it as
`TEST_DB_PORT`, so your own compose calls in that shell keep the container where it is. `env`
reports the port this checkout's running container holds, and exports no `TEST_DB_PORT`. When
none is running, `env` falls back to 55434 and whichever server holds it, if any. That is safe:
pytest and the SQL invariant suite each create and drop a database of their own there. When the
container does not start or never accepts a connection, `up` exits non-zero, says why on stderr
and prints `false`, so the `eval` fails too.
It never exports a database that is not there. On 10 September 2026 a worktree's `up` could not
bind the main checkout's port and printed nothing, so the live tests skipped and looked green.

A new worktree has no `.env`, and compose refuses to load the project without one. Claude Code
copies it into each worktree it creates, listed in `.worktreeinclude`. For a worktree made any
other way, copy it from the main checkout yourself. Copy nothing else: `pdf-statements/` and
`backups/` hold real data and stay in the main checkout.

Here, “the same testing database” means the same isolated `db-test` **server** for the stage, not
one shared mutable database. **Each pytest run gets a database of its own on that server.**
`api/tests/conftest.py` creates `pytest_<pid>_<n>` at the session start, migrates it from empty
with the checkout's own
migrations, seeds it, points the fixtures and the routes at it, and drops it at the end. Several
sessions can therefore run the live suites at once without emptying each other's tables — the
state the repository was in on 10 September 2026, with five parallel sessions sharing one
`oneledger_test`. The database hook creates and drops its own throwaway database on that same
server, including for real migration-upgrade rehearsals, so concurrency and data isolation remain
unchanged.

This is not a precaution in the abstract: on 10 September 2026 exporting `.env` to make the
live suite run truncated the working ledger, taking 4,184 imported transactions and four layout
profiles with it.

**Tear the test stack down with `scripts/test-db.sh down`, never with a compose command of your
own.** `--profile test` selects which services *start*; it does not scope a teardown, and
`docker compose --profile test down -v` operates on the whole project. Later the same day that
command removed `pgdata` and `documents` — the ledger and every original statement it traces
back to. Both volumes are **external** now (`docker-compose.yml`), so `down -v` is refused on
them whatever project it runs in; create them once with `docker volume create oneledger_pgdata`
and `docker volume create oneledger_documents` before the first `up`. `pgdata_test` is
deliberately not external: it exists to be destroyed. `down` removes this checkout's own
`<project>_pgdata_test` and no other. It used to remove `oneledger_pgdata_test` by name, which
from a worktree was the main checkout's volume.

### 7. Push — static hooks are the enforcement

`pre-push` runs mypy, both TypeScript compilers, Markdown lint, the generated-type check and
`scripts/check-diff-size.sh` (soft 800 / 1,600, hard stop 1,200 / 2,400; the
`ONELEDGER_SIZE_OVERRIDE` reason exists for what genuinely cannot be split, and it goes in the
PR). Pytest, Vitest, migrations and SQL invariants are deliberately absent from commit and push
hooks because implementation and `/verify` already run them; CI remains the remote backstop.
`--no-verify` is not an option for the static hooks that remain.

### 8. Open the PR, then merge

One PR per branch, from `.github/PULL_REQUEST_TEMPLATE.md`, carrying this cycle's evidence
honestly — including checks that did not run, and why. **The orchestrator merges** once the
local gates are green and the template is honest, then deletes the branch. The tracker entry in
`docs/superpowers/progress.md` rides on the task branch, in the head that gets merged, written
to read correctly after the merge.

Two cases stay the owner's, and an agent that reaches one says so and stops:

- **A PR whose Verification table records a required check as `Not run`.** Merging it would
  cover a gap nothing looked at.
- **Anything targeting `main`.** That moves only through `release/*` on a separate owner
  request ([docs/git-workflow.md](git-workflow.md)).

### 9. Sweep at the phase boundary

At the end of a phase, run `/oneledger-code-hygiene`. Its findings ship as their own `chore/`
branch, sized to the same budget, through this cycle from step 3. They are never folded into a
feature PR.

## What a gate never does

- Reports. It does not fix, commit, push or merge unless the owner commands fix mode.
- Never silences its own finding to reach a clean report. A gate exists to say the inconvenient
  thing early, while it is still cheap.
- Never weakens a test, an assertion or an invariant to make a finding go away. If an invariant
  is genuinely wrong, that is a superseding ADR.
