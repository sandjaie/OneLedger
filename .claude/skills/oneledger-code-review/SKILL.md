---
name: oneledger-code-review
description: Review of implemented OneLedger work against this repository's own rules — money handling, the never-store list and identifier leakage, the authorization boundary, reporting views, migrations, invariants, TDD evidence and diff size. It produces findings for the implementing agent to patch and does not modify code. Run it unprompted once implementation is finished and before every push or pull request, including a patch answering a review finding, and whenever asked to review a branch, a task, or work about to ship.
---

# oneledger-code-review

The Codex round is not the review. It is the audit of a review that should already have happened
here. M2 task 3 took eight Codex rounds and fifteen fix commits, and every one of those fixes was
findable locally: identifier leakage into responses, logs and error text, and extraction
ambiguity. This skill is where those get found.

**Produce findings another agent can patch. Do not modify code** unless the user explicitly
commands fix mode.

**Nothing is pushed until this reports no blocking findings.** It is gate 5 of
[docs/development-cycle.md](../../../docs/development-cycle.md); its trigger is implementation
being finished, a push about to happen, or a PR about to be opened — including a patch answering
a Codex finding.

## 1. Establish the ground

```bash
git fetch origin develop --quiet
scripts/check-diff-size.sh                    # the budget; a hard-stop failure ends the review
git diff --stat origin/develop...HEAD
```

A branch past the hard stop is not reviewed — it is **split**. Reviewing it wastes the review;
the diff is about to change shape. Report which slices it splits into, name a branch per slice,
and stop there.

A red tree is not reviewed either: a failing test is a finding that already found itself. If
`/verify` has not been run on this tree, or was run and came back red, report that as the finding
and stop.

## 2. Dispatch four reviewers, in parallel, with fresh context

Each gets `git diff origin/develop...HEAD`, the changed file list, and **one** checklist. Fresh
context is the point — a reviewer that watched the code being written inherits the reasoning that
produced the bug. Tell each one: read the tree, not the implementer's summary; quote the line you
are objecting to; if you cannot point at a line, you do not have a finding.

**Reviewer A — privacy and the authorization boundary.** The one that would have saved eight
rounds.
- Never-store list (`docs/operations/security-and-privacy.html` §1): full account or card number,
  CVV, PIN, OTP, password, full tax or national ID. Masked value plus salted hash, nothing else.
- **A raw identifier must not reach a response body, a log line, an exception message, an error
  `detail`, a validation echo, a preview payload, a filename or a test fixture's expected
  output.** Follow every value the diff derives from one: does it survive into a 422 body? Into
  a proposal preview? Into `repr()` on a dataclass? That is the class of finding that ate PR #34.
- `require_owner()` on every financial route. `/health`, `/auth/login`, `/auth/callback` are the
  complete public set (ADR-0013, ADR-0016). The UI is never the boundary.
- Every `SECURITY DEFINER` function calls `assert_is_owner()` first.
- No secret behind `NEXT_PUBLIC_`. No string-interpolated SQL. No real financial data, no
  `pdf-statements/` path, no `.env`.
- Salts and secrets read from configuration, and a missing one **refuses the operation naming the
  variable** rather than proceeding with an empty value.

**Reviewer B — money and the eight invariants.**
- `Decimal` in Python, `NUMERIC(20,4)` in Postgres, a **string** on the wire. No `float`, no JSON
  number, no `parseFloat`/`Number()` on a money field. TypeScript formats; it never computes.
  Aggregation happens in Postgres.
- Percentages are human-readable: `15.2500` is 15.25%.
- No currency, institution or country name as an identifier — column, type, enum member, function,
  constant or required seed row (ADR-0012). `amount_base`/`amount_secondary`.
- Reports read `v_transaction_reportable` and the frozen `amount_base`/`amount_secondary`. Never
  the base table, never a multiplication by a rate.
- Invariant 7: no carried-forward rate, interpolated valuation or residual is invented silently —
  each is a labelled stored fact, surfaced as one.
- Rounding, ordering and tie-breaks are stated and tested, not left to chance.
- Check the invariant list in `README.md` and each one's test in
  `docs/operations/testing-and-data-quality.html` §1 for anything this diff touches.

**Reviewer C — contracts, migrations and documentation.**
- A table change edits the migration, `docs/superpowers/schema.md` and
  `docs/design/data-model.html` **in one commit**. A migration with more than `schema.md` shows is
  expected; one that disagrees with it is a finding.
- Migrations are forward-only. **An applied migration is never edited** — Flyway checksums it.
- A route change updates `docs/design/api-contracts.html` and regenerates `web/types/api/`.
- Wire shape is camelCase; the Python side is snake_case; the mapping is explicit.
- The plan's stated contracts and acceptance criteria are actually met. A deviation is legitimate
  — repository reality wins — but it must be **recorded** in the PR and
  `docs/superpowers/progress.md`, not silently absorbed.
- ADRs decide, `docs/design/` and `docs/operations/` specify, `docs/superpowers/` executes. A
  contradiction is a bug in one of them: say which.

**Reviewer D — tests, structure and the diff itself.**
- Every new behaviour has a test that **would fail without the change**. Assert that, don't assume
  it: name the assertion that breaks.
- Nothing weakened to reach green — no loosened assertion, no deleted case, no skipped test, no
  softened invariant.
- Error paths, empty inputs, boundaries and the refusal cases are tested, not only the happy path.
- No dead code, no unreachable branch, no duplicated helper that already exists elsewhere in the
  tree, no debug leftovers, no commented-out code, no TODO without an owner and a reason.
- Commits: `type(scope): a lowercase sentence stating what is now true`, declarative not
  imperative; scopes `db api web ci docs plan`.
- Anything in the diff that is not needed by the task's stated outcome — scope creep is what makes
  a branch unreviewable.

## 3. Verify before reporting

Take each finding and check it against the tree yourself. A reviewer's claim is a hypothesis:
open the file, read the surrounding code, and confirm the failure is real. **Report the failure
concretely** — the input, the path through the code, the wrong output. A finding you cannot state
that way is dropped. Merge duplicates across reviewers.

## 4. Triage

**Blocking** — correctness bug, security or never-store violation, invariant breach, contract or
schema mismatch, missing or fraudulent test evidence, budget hard stop. These are fixed before
the push.

**Non-blocking** — hardening beyond the task, style, naming, "you could also handle X", a
refactor of code the diff did not touch. These get a line in the PR's Notes and, if they matter,
a follow-up issue. **They are not fixed on this branch.** Fixing them is what widens a PR and
buys another review round.

## 5. Report, and hand back

State the diff size, what each reviewer covered, every blocking finding with its file and line and
the concrete failure it causes, and every non-blocking finding in one list. Say plainly whether
the branch is clear to push.

The implementing agent takes it from there: it fixes every blocking finding, re-runs `/verify`,
and **re-runs this gate on the new diff** — a fix has its own surface. If that second pass finds
new blocking problems inside the first pass's fixes, it stops and raises it with the owner: that
is a design problem, not a patch problem.

Report honestly. A review that finds nothing on a 700-line diff is a review that did not happen.
