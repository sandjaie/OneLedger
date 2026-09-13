---
name: oneledger-code-review
description: Review a completed OneLedger branch once before final verification and first push. Report one consolidated finding set without modifying code; confirm only repaired money, authorization or migration findings.
---

# OneLedger code review

Perform a senior, evidence-based review of the task branch against `develop`. This skill is
report-only unless the owner explicitly requests fix mode.

## When to run

- **Review:** once per branch, when implementation is complete and affected tests are green.
- **Confirmation:** only when a repaired blocking finding concerned money, authorization/RLS or a
  migration. Confirm those findings and inspect only their repair range.

No other finding gets a confirmation pass. `scripts/verify.sh` runs once after review and any
confirmation; reviewers do not repeat its suites. Nothing reaches `origin` before review and final
verification.

## Establish scope

Resolve the merge base with `origin/develop`, falling back to `develop`. Inspect:

```bash
git status --short
git diff --stat <base>...HEAD
git diff --name-status <base>...HEAD
git diff <base>...HEAD
git log --oneline <base>..HEAD
scripts/check-diff-size.sh
```

Include staged and unstaged work. Read the task contract, changed files, relevant neighboring code,
callers, tests and the owning ADR/design/operations documents. Do not review unrelated historical
code. If affected tests are red or the branch exceeds the hard size limit without an owner
override, report that blocking condition and stop.

## Adaptive review checklist

Apply the first row to every change and only the additional rows its surface needs.

| Surface | Review |
|---|---|
| Every change | Task scope and contracts; logic and failure paths; idempotency; cleanup; backward compatibility; tests that would fail if behavior regressed |
| Financial data or SQL | `NUMERIC`/`Decimal`/wire strings; signs and rounding; approved reporting views; no fabricated facts; forward-only migrations; schema docs; RLS; the financial write floor |
| API or authorization | `require_owner()`; household isolation; input validation; atomic transactions; stable camelCase contracts; generated types; safe errors and logs |
| Identifiers or documents | Never-store boundary; ordinary responses, validation details, exceptions, logs, filenames, fixtures and serialized models; no real data or complete identifiers |
| Web | No money arithmetic; generated API types; privacy-safe rendering; loading, empty, error and retry states; no server secret in the client |
| Documentation or workflow | One authoritative owner; valid links and commands; no duplicate progress tracking; executable behavior matches prose |

For every new or changed financial write, check the financial write floor in
`docs/development-cycle.md`. Unsupported cases fail closed. Never fabricate a price, transaction,
cost basis, residual, valuation or corporate action.

## Test audit

Tests must prove behavior, not implementation details. A money, authorization or database path
whose tests still pass when its behavior is removed is HIGH. Elsewhere, status-only assertions,
over-mocking or a skipped negative case are MEDIUM. Never accept weakened tests or invariants as a
repair.

On the review pass, inspect focused RED/GREEN evidence. Run only a narrow reproducer needed
to prove or disprove a possible finding. On confirmation, inspect the repair range and evidence for
its affected tests. The final verifier belongs after confirmation.

## Findings

Report only issues that are specific, actionable, reproducible, caused or exposed by this change,
and verified in the tree. Do not report formatter-owned style or speculative possibilities.

- **CRITICAL:** security/never-store breach, destructive behavior or corrupted money.
- **HIGH:** likely functional failure, money/authorization contract violation, invalid migration,
  or a major money/authorization/database write with no test.
- **MEDIUM:** real edge-case defect or thin regression proof elsewhere.
- **LOW:** minor engineering issue.

CRITICAL and HIGH block. MEDIUM and LOW never block. Do not raise hardening a single-owner
application does not need — speculative races, lock-order proofs, exotic input beyond the existing
redaction helpers — unless a reproducer shows corrupted money or exposed private data.
Record useful out-of-scope or hardening work once in PR Notes rather than widening this task.

## Output

```markdown
# OneLedger Code Review

## Scope
Branch: <branch>
Base: develop (<merge-base>)
Pass: REVIEW | CONFIRMATION
Files / commits / diff size: <facts>
Evidence inspected: <commands and actual results>

## Verdict
READY FOR FINAL VERIFY | CHANGES REQUIRED | ESCALATE TO OWNER

## Findings
### OL-001 — [HIGH] <title>
File: `path:line`
Category: <category>   Blocking: yes | no
Problem: <demonstrated defect>
Impact: <failure scenario>
Required behavior: <fix outcome>
Test: <regression assertion>

## Coverage gaps
<gaps not already captured, or None>
```

Order findings by severity and deduplicate them before returning the report. A clean report still
names the risk surfaces and evidence inspected. If confirmation still finds a blocking defect,
return `ESCALATE TO OWNER` instead of starting another repair loop.
