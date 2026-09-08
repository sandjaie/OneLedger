---
name: oneledger-code-review
description: Senior-level review of OneLedger work before it is pushed or merged into develop —
correctness, edge cases, money handling, the never-store list and identifier leakage, the
authorization boundary, reporting views, migrations, invariants, test sufficiency and diff size. It
produces findings for the implementing agent to patch and does not modify code. Run it unprompted
the moment implementation is finished — before the branch is pushed at all, not after — and again
before any later push, including a patch answering a review finding; also whenever asked to review a
branch, a task, or work about to ship.
---

# OneLedger Code Review

## Purpose

Perform a senior-level engineering review of changes before they are pushed or merged into
`develop`. Identify real defects, regressions, security problems, missing edge cases, insufficient
tests, and violations of this repository's own rules.

**Produce findings another agent can patch. Do not modify code** unless the user explicitly
commands fix mode.

**This is the only review.** Cloud review was removed on 8 September 2026, so nothing downstream
catches what this pass misses. That raises the stakes and lowers the tolerance for ceremony in
equal measure: spend the time on the diff and on hostile inputs, not on re-running gates the
implementer already ran. M2 task 3's eight rounds and fifteen fix commits were every one on a
theme this skill names — a raw identifier reaching a response, a log line or an error message —
and that is the kind of thing only reading finds.

## Repository workflow

`develop` is the integration branch. Work arrives on `feature/*`, `fix/*`, `chore/*` or `docs/*`
branches, one task-sized slice each. The flow is:

implement → `/verify` → **this review** → fix every finding → re-review → **first push** →
PR → `develop`

This is gate 5 of [docs/development-cycle.md](../../../docs/development-cycle.md). Its trigger is
**implementation being finished on a green tree** — not "before some push", but before the
branch reaches `origin` at all. Pushing unreviewed work and reviewing it afterwards is the
failure this gate exists to prevent: a finding raised after the push has already cost what the
gate was meant to save. A PR is opened only from a head this gate has passed. There is no path to
`origin` that skips it — not a checkpoint commit, not a one-line follow-up.

**Never review `develop` as though it were a feature branch.**

## Budget the review

**A first pass aims at five minutes; a re-review of fixes aims at three.** Past that the gate
stops paying for itself. The slow parts are almost never the thinking, and a reviewer that spends
nine minutes re-running what the implementer already ran has bought nothing and delayed the fix.

Two shapes, and the dispatching prompt says which:

- **First pass** — a module or route this gate has not seen. Read the diff and its neighbours,
  run the applicable gates once, and spend everything left on hostile inputs and the passes
  below. This is where findings come from.
- **Confirmation pass** — the same branch after fixes. Confirm each earlier finding is closed,
  review the fix commits as their own surface, and stop. Do not re-derive the branch.

In practice, all of this measured on M2 task 5, where rounds ran to fifteen minutes and the two
tightest rounds found as much as the longest:

- **Run only the gates the diff can affect.** An API-only branch needs no `npm run build`, no
  vitest and no TypeScript compiler; a branch with no migration needs no SQL suites. Skipping a
  gate the diff cannot reach is correct — name it skipped and why. A skipped gate is honest; an
  unrun gate reported as passing is not.
- **Run each gate once, at the end.** Not between passes.
- **Do not re-derive the mutation battery.** The implementer runs it and reports it. Verify a
  sample of three to five — the ones guarding money, an invariant or the authorization boundary
  — and spend the rest of the budget hunting for guards the battery never covered. That hunt is
  the value; re-running someone else's twenty mutants is not.
- **Read the sections the prompt names**, not whole documents. The M2 spec alone runs to seven
  hundred lines.

A diff that genuinely needs longer — a migration, a new authorization path, a module handling
identifiers — takes longer. Say in the report why the budget did not fit.

## Determine review scope

Review the current branch against `develop`:

```bash
git fetch origin develop --quiet
git merge-base HEAD origin/develop      # falls back to develop when origin/develop is absent
git diff --stat <base>...HEAD
git diff --name-status <base>...HEAD
git diff <base>...HEAD
git log --oneline <base>..HEAD
```

**Include work that is not committed yet.** This gate runs before the push, so uncommitted changes
are part of what is about to ship:

```bash
git status --short
git diff              # unstaged
git diff --cached     # staged
```

A finding in uncommitted work is still a finding. Say which findings sit in committed history and
which in the working tree, because they are patched differently.

Then measure the branch:

```bash
scripts/check-diff-size.sh
```

A branch past the hard stop is not reviewed — it is **split**. Reviewing it wastes the review; the
diff is about to change shape. Report which slices it splits into, name a branch per slice, and
stop there. The exception is the one the budget itself allows: where the owner has invoked
`ONELEDGER_SIZE_OVERRIDE` for a change that genuinely cannot be divided — an indivisible migration
— review it as it stands and record that it ships oversized on that reason. Without that
exception, an approved migration has no path through the cycle at all.

A red tree is not reviewed either: a failing test is a finding that already found itself. If
`/verify` has not been run, or came back red, report that and stop.

Do not review unrelated historical code unless a changed component makes an existing defect newly
relevant.

## Understand intent first

Before reporting anything:

1. Read the task's block in `docs/superpowers/plans/` — its outcome, contracts and acceptance
   criteria are what the branch is measured against.
2. Read the changed files and the implementation around them, not the diff alone.
3. Trace callers and consumers of every changed function, route and table.
4. Read the existing tests for the code being changed.
5. Read the ADRs, `docs/design/` and `docs/operations/` pages the change touches — this
   repository's rules live there, and a reviewer who has not read them invents different ones.
6. Understand the existing pattern before recommending an alternative to it.

On a large diff the passes below may be split across parallel fresh-context reviewers, with the
reports merged and deduplicated. Fresh context is the value: a reviewer that watched the code
being written inherits the reasoning that produced the bug.

---

## Review passes

## 1. Money and the eight invariants

- `Decimal` in Python, `NUMERIC(20,4)` in Postgres, a **string** on the wire. No `float`, no JSON
  number, no `parseFloat`/`Number()` on a money field. TypeScript formats; it never computes.
  Aggregation happens in Postgres.
- Percentages are human-readable: `15.2500` is 15.25%, not `0.1525`.
- **No currency, institution or country name as an identifier** — column, type, enum member,
  function, constant or required seed row (ADR-0012). `amount_base` / `amount_secondary`.
- **Reports read `v_transaction_reportable`** and the frozen `amount_base` / `amount_secondary`.
  Never the base table, never a multiplication by a rate at read time.
- **Invariant 7**: no carried-forward rate, interpolated valuation or residual is invented
  silently. Each is a labelled stored fact, surfaced as one.
- Signs (I2), the household reporting-pair lock, and every other invariant the change touches:
  check `README.md` and its test in `docs/operations/testing-and-data-quality.html` §1.
- Rounding, ordering and tie-breaks are stated and tested, not left to chance. A rounding rule that
  differs between Python and Postgres is a defect even when both look reasonable.

## 2. Privacy, secrets and the authorization boundary

The pass that would have saved eight rounds on PR #34.

- **Never-store list** (`security-and-privacy.html` §1): full account or card number, CVV, PIN,
  OTP, password, full tax or national ID. Masked value plus salted hash, nothing else.
- **Follow every value derived from an identifier to every place it can surface**: a response
  body, a log line, an exception message, a 422 `detail`, a validation echo, a preview payload, a
  filename, a stack trace, a `repr()` on a dataclass, a test fixture's expected output. That is
  the class of finding that took eight rounds.
- `require_owner()` on every financial route. `/health`, `/auth/login` and `/auth/callback` are the
  complete public set (ADR-0013, ADR-0016). The UI is never the boundary.
- Every `SECURITY DEFINER` function calls `assert_is_owner()` first.
- A missing salt or secret **refuses the operation naming the variable** rather than proceeding
  with an empty value.
- No secret behind `NEXT_PUBLIC_`. No string-interpolated SQL. No real financial data, no
  `pdf-statements/` path, no `.env`, on any branch.
- No LLM call (ADR-0003).

## 3. Correctness

Trace the execution paths; do not review syntax.

- incorrect logic, invalid assumptions, wrong conditions, wrong defaults
- unexpected fallthrough, incorrect return values
- swallowed errors, incorrect error propagation, partial-failure handling
- incorrect state transitions; a batch or transaction left half-applied
- concurrency: interleaved requests, the reporting-pair lock, `SELECT … FOR UPDATE` that isn't
- resource leaks, broken cleanup, unclosed files or connections
- regressions in existing behaviour, and backwards compatibility

## 4. Edge cases

Actively look for inputs that get forgotten. Ask of every operation:

> **What happens if this runs twice?**

Ingestion, staging and commit should be idempotent wherever it is reasonable.

- missing values, empty strings, empty lists, a statement with one row, a statement with none
- malformed configuration; a layout profile that no longer matches its statement
- a PDF that is scanned, password-protected, corrupt, or has no text layer
- **the same statement imported twice** — and the same statement after a partial commit
- a retry after an interrupted parse, an interrupted commit, a previous run that half-finished
- dates at a period boundary, two-digit years, a value date after the period closes
- an FX rate missing for a weekend, a holiday, or the first day of the range
- a household with no secondary currency; a locked reporting pair; a currency with other decimals
- duplicate operations, repeated execution, conflicting configuration
- special characters, spaces in paths, very long names, unicode look-alikes in an identifier
- external failure: the database unreachable, the rate source down, a timeout, an expired session

## 5. Data, migrations and contracts

- A table change edits the migration, `docs/superpowers/schema.md` and
  `docs/design/data-model.html` **in one commit**. A migration carrying more than `schema.md`
  shows is expected; one that disagrees with it is a finding.
- Migrations are forward-only. **An applied migration is never edited** — Flyway checksums it.
- RLS is enabled and isolating on every new table; a `CHECK` says what it means to say.
- A route change updates `docs/design/api-contracts.html` and regenerates `web/types/api/`.
- The wire is camelCase, the Python is snake_case, and the mapping is explicit.
- The plan's stated contracts and acceptance criteria are met. A deviation is legitimate —
  repository reality wins — but it must be **recorded** in the PR and
  `docs/superpowers/progress.md`, not silently absorbed.
- ADRs decide, `docs/design/` and `docs/operations/` specify, `docs/superpowers/` executes. A
  contradiction is a bug in one of them: say which.

## 6. Test audit

Do not ask whether tests exist. Determine whether they prove the changed behaviour.

**The implementer's mutation battery is evidence, not a substitute for this pass.** Verify a
sample, then hunt for what it missed — a guard nobody thought to mutate is the finding. An
"equivalent mutant" claim deserves one check before it is accepted, and a mutant that dies of a
syntax error or an aborted transaction proves nothing about the semantics it was aimed at.

For each meaningful behaviour change, identify the happy path, the failure path, the boundary, the
malformed input, the external-dependency failure, the idempotency case and the regression that
would catch this bug returning.

Report tests that:

- test implementation details instead of behaviour
- assert weakly — a status code with no body, a length with no contents
- mock everything and prove nothing
- **cannot fail if the production behaviour breaks**
- omit the negative case entirely

Name the assertion that would break if the change were reverted. If you cannot, the change has no
regression protection and that is the finding.

Nothing weakened to reach green: no loosened assertion, no deleted case, no skipped test, no
softened invariant. If an invariant is genuinely wrong, that is a superseding ADR.

## 7. Maintainability and scope

Report these only where they have real engineering impact.

- duplicated logic that already exists elsewhere in the tree
- hidden side effects, giant functions, deeply nested control flow
- inconsistent error handling; an abstraction the new code bypasses
- dead code, unreachable branches, debug leftovers, commented-out code
- a TODO without an owner and a reason
- **anything in the diff not required by the task's stated outcome** — scope creep is what makes a
  branch unreviewable
- commits: `type(scope): a lowercase sentence stating what is now true`, declarative not
  imperative; scopes `db api web ci docs plan`

Do not raise style a linter or formatter already owns.

---

## Validation

Run the gates **this diff can affect**, once each, and report real numbers. Do not introduce a new
dependency to review.

| The diff touches | Run |
| --- | --- |
| `api/` | `ruff check .`, `mypy .`, `pytest -q` |
| a migration or `db/tests/` | `scripts/hooks/db-tests.sh`; migrations from empty only when a migration is added |
| `web/` | `api:types:check`, `typecheck`, `vitest run`; `npm run build` only when the bundle could change |
| `docs/` or any `.md` | `python3 scripts/check-docs.py` |
| anything | `scripts/check-diff-size.sh` |

**Never claim a command passed unless you actually ran it and it succeeded.** If something cannot
be run here — no database, no browser, no network — say so explicitly and mark it `NOT RUN` with
the reason. A gate that reports checks it did not run is worse than no gate.

**Pytest's exit code is not a mutation-kill signal here.** With `POSTGRES_PASSWORD` exported, six
tests in `api/tests/test_auth.py` and `api/tests/test_oidc.py` fail on `develop` itself. Judge
mutants against a subset asserted green first, and put that baseline in the report. Two M2 rounds
reported kills that were only the pre-existing failures.

**Purge `__pycache__` and set `PYTHONDONTWRITEBYTECODE=1` before any mutation run.** A stale
`.pyc` has produced a false result in this repository before.

---

## Finding quality

Every finding must be specific, actionable, demonstrable, related to this change, and backed by
code evidence you can point at.

- **Verify each finding against the tree before reporting it.** A reviewer's first instinct is a
  hypothesis: open the file, read the surrounding code, confirm the failure is real.
- Do not report a speculative issue because something "could possibly" happen.
- Prefer few high-confidence findings over many low-value observations.
- Do not praise the code, and do not write filler.

---

## Severity

**CRITICAL** — a security or never-store violation, identifier leakage, a broken authorization
boundary, destructive or data-losing behaviour, corrupted money.

**HIGH** — likely functional failure, an invariant breach, a schema or contract mismatch, a
migration that will not apply, a major regression, missing validation on a financial path.

**MEDIUM** — a real defect in an edge case, a reliability or maintainability problem, or a
behaviour change with no regression protection.

**LOW** — a minor engineering issue worth fixing, not required to merge safely.

Do not inflate severity. Severity ranks the work and orders the report; it does not decide what
gets fixed.

**Every finding is fixed before the push, LOW included.** This gate exists so that nothing reaches
Codex that could have been found here. Fixing a finding now costs no round trip; leaving it costs
a Codex round, a push and a re-review — so a LOW left open is the most expensive kind of finding
there is.

The one exception is **scope, not severity**: a finding in code this task does not touch — an
adjacent refactor, a pre-existing defect the change merely made visible — is recorded in the PR's
Notes and raised as its own task, because patching it widens the branch past the task it carries
and past its diff budget. Say that explicitly in the report; do not leave it implied.

---

## Output format

Produce exactly this structure.

```markdown
# OneLedger Code Review

## Review scope

Branch: <branch>
Base: develop (<merge-base sha>)
Files changed: <count>            Commits: <count>
Diff size: <production> production / <tests> test — <within budget | over budget | over hard stop>
Uncommitted work included: <yes, N files | none>

Validation performed:
* <command> — PASS / FAIL / NOT RUN (<reason if not run>)

## Summary

Critical: X   High: X   Medium: X   Low: X

Verdict: READY TO PUSH | CHANGES REQUIRED

## Findings

### OL-001 — [HIGH] Short descriptive title

**File:** `path/to/file.py:120`
**Category:** Money | Privacy | Authorization | Correctness | Edge case | Migration | Contract |
Testing | Maintainability
**State:** committed | working tree

**Problem** — what is wrong.

**Why it matters** — the failure scenario: the input, the path through the code, the wrong result.

**Evidence** — the implementation or execution path that demonstrates it.

**Recommended fix** — the expected behaviour, not a patch.

**Test required** — the regression test that should exist, and the assertion that would fail
today.

## Test coverage gaps

Scenarios not covered by a finding above, or `No significant test coverage gaps identified.`

## Final verdict

CHANGES REQUIRED while any in-scope finding is open, at any severity. READY TO PUSH only when
none is, with any out-of-scope findings listed for the PR's Notes.
```

Order findings CRITICAL → HIGH → MEDIUM → LOW.

A review that finds nothing on a 700-line diff is a review that did not happen.

---

## Constraints

Do not:

- modify source files, or fix a finding
- stage, commit, push, or open the PR
- resolve a review comment or update `docs/superpowers/progress.md`
- expand the review into unrelated cleanup
- report a hypothetical finding with no evidence

The implementing agent fixes every in-scope finding, re-runs `/verify`, and re-runs this gate on
the new diff — a fix has its own surface. If that second pass finds new blocking problems inside the
first
pass's fixes, it stops and raises it with the owner: that is a design problem, not a patch problem.
