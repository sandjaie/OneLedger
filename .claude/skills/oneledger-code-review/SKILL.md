---
name: oneledger-code-review
description: Review a completed OneLedger task before final verification and first push. Report one consolidated finding set without modifying code; after repairs, confirm only the findings and repair range.
---

# OneLedger code review

Perform a senior, evidence-based review of the task branch against `develop`. This skill is
report-only unless the owner explicitly requests fix mode.

## When to run

- **Comprehensive pass:** implementation is complete and affected tests are green.
- **Confirmation pass:** the implementation agent repaired blocking findings. Confirm those
  findings and inspect only the repair range.

If no repair was needed, skip confirmation. `scripts/verify.sh` runs once after the review is
clean; reviewers do not repeat its suites. Nothing reaches `origin` before review and final
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
| Every change | Task scope and contracts; logic and failure paths; idempotency; concurrency; cleanup; backward compatibility; tests that would fail if behavior regressed |
| Financial data or SQL | `NUMERIC`/`Decimal`/wire strings; signs and rounding; approved reporting views; no fabricated facts; forward-only migrations; schema docs; RLS; the full operation matrix below |
| API or authorization | `require_owner()`; household isolation; input validation; atomic transactions; stable camelCase contracts; generated types; safe errors and logs |
| Identifiers or documents | Never-store boundary; ordinary responses, validation details, exceptions, logs, filenames, fixtures and serialized models; no real data or complete identifiers |
| Web | No money arithmetic; generated API types; privacy-safe rendering; loading, empty, error and retry states; no server secret in the client |
| Documentation or workflow | One authoritative owner; valid links and commands; no duplicate progress tracking; executable behavior matches prose |

For every new or changed financial write, require tests for:

- owner, member and foreign-household behavior;
- unauthorized direct DML, RLS and immutability;
- stale expected state and repeated idempotency requests;
- precision refusal before normalization, hashing or deduplication;
- active correction leaves and preserved history;
- one lock order plus concurrent duplicate and conflict paths;
- atomic rollback after late failure.

Unsupported cases fail closed. Never fabricate a price, transaction, cost basis, residual,
valuation or corporate action.

## Test audit

Tests must prove behavior, not implementation details. For each changed contract, identify the
happy path, refusal path, boundary or malformed input, retry/idempotency case and regression
assertion. A status-only assertion, over-mocking, a skipped negative case or a test that still
passes when production behavior is removed is a finding. Never accept weakened tests or
invariants as a repair.

On the comprehensive pass, inspect focused RED/GREEN evidence. Run only a narrow reproducer needed
to prove or disprove a possible finding. On confirmation, inspect the repair range and evidence for
its affected tests. The final verifier belongs after confirmation.

## Findings

Report only issues that are specific, actionable, reproducible, caused or exposed by this change,
and verified in the tree. Do not report formatter-owned style or speculative possibilities.

- **CRITICAL:** security/never-store breach, destructive behavior or corrupted money.
- **HIGH:** likely functional failure, invariant/contract violation or invalid migration.
- **MEDIUM:** real edge-case/reliability defect or missing regression proof.
- **LOW:** minor engineering issue worth recording.

Mark each finding blocking or non-blocking. Fix every in-scope blocking finding before push.
Record useful out-of-scope or hardening work once in PR Notes rather than widening this task.

## Output

```markdown
# OneLedger Code Review

## Scope
Branch: <branch>
Base: develop (<merge-base>)
Pass: COMPREHENSIVE | CONFIRMATION
Files / commits / diff size: <facts>
Evidence inspected: <commands and actual results>

## Verdict
READY FOR FINAL VERIFY | CHANGES REQUIRED | DESIGN/THREAT REVIEW REQUIRED

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
names the risk surfaces and evidence inspected. If confirmation exposes a new architectural,
authorization, privacy, idempotency or concurrency boundary, return
`DESIGN/THREAT REVIEW REQUIRED` instead of starting another patch loop.
