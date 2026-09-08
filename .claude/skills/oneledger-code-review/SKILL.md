---
name: oneledger-code-review
description: Senior-level review of OneLedger work before it is pushed or merged into develop — correctness, edge cases, money handling, the never-store list and identifier leakage, the authorization boundary, reporting views, migrations, invariants, test sufficiency and diff size. It produces findings for the implementing agent to patch and does not modify code. Run it unprompted once implementation is finished and before every push or pull request, including a patch answering a review finding, and whenever asked to review a branch, a task, or work about to ship.
---

# OneLedger Code Review

## Purpose

Perform a senior-level engineering review of changes before they are pushed or merged into
`develop`. Identify real defects, regressions, security problems, missing edge cases, insufficient
tests, and violations of this repository's own rules.

**Produce findings another agent can patch. Do not modify code** unless the user explicitly
commands fix mode.

The Codex round is not the review — it is the audit of a review that should already have happened
here. M2 task 3 took eight Codex rounds and fifteen fix commits, every one on a theme this skill
now names: a raw identifier reaching a response, a log line or an error message.

## Repository workflow

`develop` is the integration branch. Work arrives on `feature/*`, `fix/*`, `chore/*` or `docs/*`
branches, one task-sized slice each. The flow is:

`<type>/<name>` → **this review** → findings patched → `/verify` → push → PR → Codex → `develop`

This is gate 5 of [docs/development-cycle.md](../../../docs/development-cycle.md). Its trigger is
implementation being finished, a push about to happen, or a PR about to be opened — including a
patch answering a Codex finding. There is no path to `origin` that skips it.

**Never review `develop` as though it were a feature branch.**

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

# Review passes

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

# Validation

Run the repository's own checks — `/verify`, or the individual commands it lists. Do not introduce
a new dependency to review.

**Never claim a command passed unless you actually ran it and it succeeded.** If something cannot
be run here — no database, no browser, no network — say so explicitly and mark it `NOT RUN` with
the reason. A gate that reports checks it did not run is worse than no gate.

---

# Finding quality

Every finding must be specific, actionable, demonstrable, related to this change, and backed by
code evidence you can point at.

- **Verify each finding against the tree before reporting it.** A reviewer's first instinct is a
  hypothesis: open the file, read the surrounding code, confirm the failure is real.
- Do not report a speculative issue because something "could possibly" happen.
- Prefer few high-confidence findings over many low-value observations.
- Do not praise the code, and do not write filler.

---

# Severity

**CRITICAL** — a security or never-store violation, identifier leakage, a broken authorization
boundary, destructive or data-losing behaviour, corrupted money.

**HIGH** — likely functional failure, an invariant breach, a schema or contract mismatch, a
migration that will not apply, a major regression, missing validation on a financial path.

**MEDIUM** — a real defect in an edge case, a reliability or maintainability problem, or a
behaviour change with no regression protection.

**LOW** — a minor engineering issue worth fixing, not required to merge safely.

Do not inflate severity.

**CRITICAL, HIGH and MEDIUM block the push** and are patched on this branch. **LOW does not** — it
is recorded in the PR's Notes and becomes an issue if it matters. Patching low-value findings is
what widens a diff and buys another review round.

---

# Output format

Produce exactly this structure.

```
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

CHANGES REQUIRED if any CRITICAL, HIGH or MEDIUM finding is open; otherwise READY TO PUSH, with
LOW findings listed for the PR's Notes.
```

Order findings CRITICAL → HIGH → MEDIUM → LOW.

A review that finds nothing on a 700-line diff is a review that did not happen.

---

# Constraints

Do not:

- modify source files, or fix a finding
- stage, commit, push, or open the PR
- resolve a review comment or update `docs/superpowers/progress.md`
- expand the review into unrelated cleanup
- report a hypothetical finding with no evidence

The implementing agent applies the report, re-runs `/verify`, and re-runs this gate on the new
diff — a fix has its own surface. If that second pass finds new blocking problems inside the first
pass's fixes, it stops and raises it with the owner: that is a design problem, not a patch problem.
