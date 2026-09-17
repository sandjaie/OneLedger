# Workspace experience refresh implementation plan

**Goal:** Make every owner-workspace destination readable, navigable, and actionable across desktop
and narrow screens without changing financial semantics.

**Architecture:** Correct the shared shell and CSS first. Apply a small number of reusable page,
panel, record, metadata, and action patterns to existing screens. Preserve API calls and data
transformations; use browser inspection and existing component tests to catch regressions.

**Spec:** [Workspace experience refresh](../specs/2026-09-17-workspace-experience-refresh-design.md)

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/ui-experience-refresh`

**Base and PR target:** `develop` at `29e6c1f2547a3a12e98ddcae8fb6d6a39005e62b`;
PR target `develop`. The remote refresh attempted on 17 September failed authentication, so verify
the remote head before first push.

## Work sequence

### 1. Shared navigation and responsive frame

Modify `web/app/workspace.tsx`, `web/app/globals.css`, and
`web/app/__tests__/workspace.test.tsx`. Keep desktop navigation in view while main content scrolls;
add a labelled compact-menu control with expanded state, close behavior, and current-page state.
Remove the global 880px main minimum. Check no page-wide overflow at 360px and 588px.

### 2. Scan-first records and decisions

Modify the Subscriptions, Recurring, Documents, Classify, Investments, and investment-import
screens plus shared CSS. Present critical facts and primary actions before secondary metadata;
keep every existing financial warning, lifecycle operation, and review step available. Run their
affected component suites after editing each family.

### 3. Consistent remaining destinations

Apply the same panel/header/contained-table patterns to Spending, Salary, Plans, Coverage, Wealth,
Settings, and related detail screens. Retain Overview, Accounts, Transactions, and Import flows.
Run affected component suites and inspect all destinations in the live development app.

### 4. Delivery gate

Commit locally, conduct one read-only code review, consolidate any blocking repair, run the
affected checks again, then run `scripts/verify.sh` on the clean committed head. First push and PR
to `develop` follow only if that proof and remote-base check pass. Record actual evidence in the
PR and update `docs/superpowers/progress.md` only if live implementation status changes.
