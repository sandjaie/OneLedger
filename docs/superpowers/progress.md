# Implementation progress

This is the single maintained implementation-status file. The orchestrator updates it while
executing Superpowers plans. Plans define the work; the roadmap defines scope and sequence;
dated reports retain verification evidence. Do not copy this status into other documentation.

Updated: 6 September 2026.

## Current phase

**Phase 0: implementation delivered; live Google identity verification pending.**
Integration branch: `feature/phase-0-foundations`.
Active execution plan: [Phase 0 completion](plans/2026-09-06-phase-0-completion.md).

| Completion-plan task | Status | Evidence |
| --- | --- | --- |
| 1 — web scaffold and API client | Complete | PR #8; timeout follow-up #14 |
| 2 — Google OIDC and revocable sessions | Implementation complete | PRs #9–#11 and #16; live identity gate below |
| 3 — exact money, configured shell and FX inspector | Complete | PRs #12, #13 and #15 |
| 4 — phase exit verification | Partially complete | Automated/synthetic checks and review passed; live identity gate pending |

## Delivered work

| GitHub PR | Completed scope | Original task numbers | Original PR ordinal |
|---|---|---|---|
| #3 | Compose definition, environment template, secret hygiene | 1 | 1 |
| #4 | V1–V2 reference data, identity, RLS | 2–3 | 2 |
| #5 | V3–V4 accounts and FX rates | 4–5 | 3 |
| #6 | FastAPI, `require_owner`, `resolve_owner`, `connect` | 6 | 4 |
| #7 | FX source, peg derivation, carry-forward, backfill; three review rounds reported | 7–8 | 5 |
| #8 | Reviewed Next.js foundation and cookie-aware typed API client | 9 | 6 |
| #9 | Revocable owner-session persistence and strict session verification | 10 partial | 7 partial |
| #10 | Protected logout, CSRF origin checks and household configuration | 10 partial | 7 partial |
| #11 | Google OIDC, state/nonce/PKCE and pinned subject binding | 10 | 7 partial |
| #12 | Exact money formatting and generated API types with drift enforcement | 11 partial | 7 partial |
| #14 | Bounded API requests and compatible caller cancellation | 9 follow-up | 6 follow-up |
| #13 | Authenticated shell, configured currency preference and recovery | 11 partial | 7 partial |
| #15 | Protected Settings FX inspector, exact rates and provenance | 11 | 7 partial |
| #16 | Fixed account chooser on sign-in retry | 10 follow-up | 7 follow-up |
| #17 | Explicit dual-compiler verification and current API boundary comments | Final review follow-up | Closeout |
| #18 | Sequential current-checkout workflow and generic PR template | Workflow | Documentation |

PR #14 merged before #13 as a prerequisite. Original plan PR ordinals are not GitHub PR numbers.
The original Tasks 1–11 are implemented. The whole-phase review found no blocking defects;
both minor follow-ups were addressed in #17 and passed scoped and Cloud re-review.

## Verified gates

- [x] API: 59 tests, lint and strict type checks.
- [x] Web: 33 tests, both TypeScript compilers, lint and generated API contract drift.
- [x] Dependencies: strict installation and zero reported npm audit vulnerabilities.
- [x] Database: all five migrations, reference seeds and five SQL suites from empty; RLS negative-control proof.
- [x] Public FX reference history: 980 days, 1,960 rows, complete peg pairs and zero invariant violations.
- [x] Production images, cached offline startup and synthetic-session browser checks, including exact
  FX rates/provenance, keyboard/mobile use, currency persistence and logout/replay behavior.
- [x] Local and current-head Cloud review of each implementation delivery; final whole-phase review.

Evidence: [dated verification and dependency report](reports/2026-09-06-phase-0-verification.md).
These results describe the verified integration, not a new test run whenever this file is edited.

## Remaining exit gates and next action

- [ ] Privately configure the Google client credentials and real permitted email, matching the seeded owner.
- [ ] Verify real permitted-account sign-in and protected application access.
- [ ] Verify rejection of a real second Google account and successful account-choice recovery.
- [ ] Close Phase 0 only after those live checks are recorded here.

The last configuration check found missing Google client credentials and a placeholder permitted
email. The owner was asked to configure ignored `.env` privately; no credential values belong here.
Synthetic protocol/session/browser tests do not substitute for live Google identity checks.
Do not begin Phase 1 on the assumption these gates passed. Integration-to-main requires a separate
owner request.

## Later phases

| Phase | Status |
| --- | --- |
| Phase 1 — trusted financial position | Not started |
| Phase 2A — next month | Not started |
| Phase 2B — resilience | Not started |
| Phase 2C — deeper analysis | Not started |
| V2 | Not started |

Scope and sequencing remain in the [master plan](plans/implementation-plan.md) and
[product roadmap](../product/roadmap.html). Loan work remains conditional on the product decision;
this file tracks implementation, not scope decisions.
