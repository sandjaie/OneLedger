# Phase 4.7 owner workspace experience implementation plan

**Goal:** Deliver the consolidated decision-first visual and interaction system across every
existing owner-workspace destination, then invite owner acceptance of the finished experience.

**Architecture:** Task 1's page grammar and accessible `SectionNav`/`RowActions` are implemented
on `feature/ui-page-grammar`; merge that branch before Task 2. Tasks 2–7 adapt screen families
one branch at a time, using existing authenticated data and selective shadcn/ui controls. Task 8
audits and repairs cross-screen consistency. This plan supersedes the remaining-task execution
instructions of the [earlier decision-first plan](2026-09-17-decision-first-workspace.md); its
exact endpoint/call-order and test inventories remain reference material where not overridden
here. The [earlier refresh plan](2026-09-17-workspace-experience-refresh.md) is historical.

**Tech stack:** Next.js 16, React 19, TypeScript, Tailwind CSS 4, existing CSS tokens, and
selectively added shadcn/ui source components.

**Spec:**
[Phase 4.7 owner workspace experience](../specs/2026-09-18-phase-4-7-workspace-experience.md).

**Workspace:** `/Users/sandjaieravi/dev/OneLedger` — active checkout only, no worktree.

**Base and PR target:** each remaining task branch starts from current `develop` after its
dependencies merge and targets `develop`. Verify `origin/develop` before branching. The first
branch is Task 2 `feature/ui-settings-sections`; Tasks 3–7 may follow in any order, one writer at
a time. Task 8 follows all seven earlier tasks.

## Global contracts

- Web-only. No `api/`, `db/`, generated API type, financial contract, or owner/RLS change. A
  concrete backend gap stops its UI task for a separate contract and test-floor decision.
- Exact monetary values arrive as strings. TypeScript formats them without arithmetic or
  inferred values; unknown prices, dates, returns, and totals remain visibly unknown.
- Every screen calls Workspace's authenticated `request` wrapper. Preserve pinned fetch order,
  request shapes, and existing confirmation semantics unless this plan explicitly changes the
  presentation of a confirmation.
- Preserve Task 1's shell, page grammar, `SectionNav`, and `RowActions`. Do not redo the CSS
  foundation or apply a shadcn preset that overwrites OneLedger's colors or fonts.
- Add only the shadcn components a task actually consumes. Inspect generated config, CSS,
  dependencies, and source before commit; map semantic tokens to Task 1's palette.
- Use synthetic tests and structural live observations only. No real financial values, document
  names, full identifiers, or screenshots in code, fixtures, commits, logs, or agent messages.
- Keep keyboard focus, labels, reduced motion, a stable loading frame, and no page-wide
  horizontal overflow at 360px, 588px, and desktop widths. Test any shadcn control inside its
  actual screen, including cancel and focus behavior.
- Run affected tests during implementation. Each branch gets one read-only review, one
  consolidated repair, then `scripts/verify-web.sh` on the clean intended commit. Its exact-head
  receipt and diff-size result precede first push and a PR to `develop`; no API/database suite is
  repeated for this web-only phase.

## Task 1 — shared page grammar (already implemented)

Branch `feature/ui-page-grammar` contains the palette/grid grammar, `SectionNav`, `RowActions`,
and Coverage/Wealth/Spending adoption. It passed one read-only review with a local repair and
`scripts/verify-web.sh` at `ac410fe`, then was pushed. The Phase 4.7 specification and tracking
update follow on the same branch. Open/merge its PR before Task 2; do not reimplement Task 1.

## Task 2 — Settings architecture and shadcn foundation

**Branch:** `feature/ui-settings-sections`. **Dependency:** Task 1 merged.

**Files:** Create `web/app/settings/settings-screen.tsx` and its test. Move the inlined Settings
screen and FX controls from `web/app/workspace.tsx` into it. Adapt the Settings section modules,
three incoming links, affected Workspace tests, and Settings CSS named in the
[earlier Task 2](2026-09-17-decision-first-workspace.md#task-2--settings-section-architecture).
Initialize
shadcn in `web/` only after previewing config/CSS changes; add the grouped Field/Input controls
consumed by General and an Alert consumed by genuine Data health findings. Review the generated
`components.json`, component source, package files, and `globals.css` changes before commit.

**Contract:** General opens first with one Save. `SectionNav` selects General, Data health,
Categories, Rules, and Exchange rates; all remain mounted, but only one long section is visible.
Existing hash targets select and focus the correct section; direct links and Back/Forward work.
No pending form edit disappears on a section change. Loading/unavailable Data health never says
clean. Shadcn changes fields and the finding callout, not URL state or financial data.

**Proof:** Settings and Workspace tests cover initial section, each deep link, Back/Forward,
keyboard/focus, preserved draft, validation, fixed reporting pair, FX actions, and true/unknown
health. Run affected Vitest suites, lint/typecheck, then live desktop/588px/360px checks; review,
repair, commit, and run the web-only final verifier.

## Task 3 — Subscriptions and Recurring

**Branch:** `feature/ui-recurring-records`. **Dependency:** Task 1 merged.

**Files:** Adapt `web/app/subscriptions/subscriptions-screen.tsx`, its detail screen,
`web/app/recurring/recurring-screen.tsx`, affected tests, and screen CSS. Add only the shadcn
AlertDialog source consumed by cancellation/removal confirmations. The
[earlier Task 3](2026-09-17-decision-first-workspace.md#task-3--subscriptions-and-recurring-records)
owns the
existing fields, endpoint bodies, call counts, and exact confirmation sentences.

**Contract:** Lead with the next real dated item or truthful absence; show Current/Upcoming/Past
and Active/Ended views; make rows scannable with provenance in detail. Keep every lifecycle and
correction action in `RowActions` or existing detail. An ended row never reads Active. Replace
browser `window.confirm` with a labelled AlertDialog for destructive actions, retaining the
exact confirmation copy and sending no request on cancel. Update tests that spy `window.confirm`
to assert the dialog's visible text, keyboard cancel/focus return, and no POST on cancellation;
all endpoint and request-body assertions remain unchanged.

**Proof:** Affected Subscriptions/Recurring/Transactions suites, lint/typecheck, live current,
ended, empty, cancel, and remove states at three widths; one review and web-only final verifier.

## Task 4 — Documents and Classify

**Branch:** `feature/ui-documents-classify`. **Dependency:** Task 1 merged.

**Files:** Adapt `web/app/documents/documents-screen.tsx`,
`web/app/review/classification-review-screen.tsx`, their tests, and related CSS. The
[earlier Task 4](2026-09-17-decision-first-workspace.md#task-4--documents-and-classify) records exact
filter query order, actions, and review/evidence invariants.

**Contract:** Documents rows show type, account, period, import state, and next action before
provenance. Humanize enums; Type/Account filters remain visible and advanced filters collapse at
narrow widths. All Original/Transactions/Review/Re-read actions retain their existing conditions.
Classify keeps evidence, confidence, reversibility, and recovery, presented in the shared
decision-first hierarchy. Use a shadcn control only if a native one cannot meet an identified
interaction need; do not add a component for decoration.

**Proof:** Affected Documents/Classify tests, exact query and action assertions, lint/typecheck,
live dense/empty/error states at three widths, one review and web-only final verifier.

## Task 5 — Investments and investment import

**Branch:** `feature/ui-investments-focus`. **Dependency:** Task 1 merged.

**Files:** Adapt portfolio, investment-analysis, and investment-import screens and their tests;
extract shared return-reason labels only if the
[earlier Task 5](2026-09-17-decision-first-workspace.md#task-5--investments-focus-and-analysis-depth)
still
needs them against current code. Keep changes to the relevant CSS family.

**Contract:** Show real entry routes for no holdings; separate holdings, confidence, reviews,
and analysis when populated. Preserve coverage exclusions, methodology, and import review/commit
steps without leading with technical prose. Unknown value or return stays unknown.

**Proof:** Affected investment suites, lint/typecheck, live empty/valued/unvalued/import-review
states at three widths, one review and web-only final verifier.

## Task 6 — ledger destinations

**Branch:** `feature/ui-ledger-destinations`. **Dependency:** Task 1 merged.

**Files:** Adapt Overview, Accounts, Transactions, and Import screens, their tests, and local
CSS as bounded by the
[earlier Task 6](2026-09-17-decision-first-workspace.md#task-6--ledger-destinations).

**Contract:** Retain strong existing summaries and owner workflows while making the first real
state and next action clear. Filters and dense data stay contained; no endpoint or fetch order
changes. Preserve import review and reconciliation semantics.

**Proof:** Affected screen suites, lint/typecheck, live four-screen checks at three widths, one
review and web-only final verifier.

## Task 7 — analysis and planning destinations

**Branch:** `feature/ui-analysis-destinations`. **Dependency:** Task 1 merged.

**Files:** Adapt Spending, Salary, Plans, Coverage, and Wealth screens, their tests, and local
CSS as bounded by the
[earlier Task 7](2026-09-17-decision-first-workspace.md#task-7--analysis-destinations).

**Contract:** A real next task leads; methodology/history moves behind clear headings or
disclosures. Preserve forecast, goal, budget, salary, coverage, and wealth meanings, existing
links, confirmations, and uncertainty. Task 1's three adoption screens receive deeper IA only,
not a second page-grammar layer.

**Proof:** Affected screen suites, lint/typecheck, live positive/empty/incomplete states at
three widths, one review and web-only final verifier.

## Task 8 — cross-screen visual and UX audit

**Branch:** `fix/ui-decision-first-audit`. **Dependency:** Tasks 1–7 merged.

**Files:** Only screens, tests, and shared CSS with evidenced findings. The
[earlier Task 8](2026-09-17-decision-first-workspace.md#task-8--cross-screen-audit-and-repair)
supplies the
destination inventory and audit boundaries.

**Contract:** Inspect every sidebar destination and relevant detail route at desktop, 588px,
and 360px for first-viewport clarity, visual coherence, visible interaction states, rail/menu
behavior, keyboard/focus, loading/empty/error recovery, contained overflow, and preserved
financial caveats. Record only structural observations, never real ledger contents. Fix
evidenced defects in one coherent branch; do not expand the audit into new financial features.

**Proof:** Affected tests plus web lint/types and production build through
`scripts/verify-web.sh`, one read-only review and repair, then first push/PR. After merge, invite
the owner to assess the completed Phase 4.7 UI/UX in the running application. Owner acceptance is
separate from engineering verification and remains open until the owner confirms it.
