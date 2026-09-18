# Phase 4.7 Nova control closure

**Goal:** Finish the general-purpose shadcn control conversion required by the
[Phase 4.7 specification](../specs/2026-09-18-phase-4-7-workspace-experience.md)
before owner visual acceptance. This follows the merged 4.7.10 audit: source inspection found
legacy buttons, fields and selects in major owner workflows even though their screens were
styled. It repairs the existing contract; it adds no new financial feature.

**Workspace for every task:** `/Users/sandjaieravi/dev/OneLedger`, active checkout only, one
implementation writer. **Base and PR target:** current clean `develop` after the preceding task
merges; PR to `develop`. The first base is `f843ac94e7084f31978e47fff8037dbeddf644fc`.

Use the installed Base UI Nova source components and Inter/green tokens. Keep native HTML where
it carries domain semantics, but replace general-purpose custom controls and feedback with the
matching shadcn source. Keep authenticated requests, exact financial strings, form state,
confirmation, focus, and existing owner actions unchanged. Never inspect or commit real ledger
contents. Run focused tests and synthetic three-width checks, one read-only review and repair,
then `scripts/verify-web.sh` on the clean exact head before the first push. Update
`docs/superpowers/progress.md` in each PR.

## 4.7.11 — Accounts and transaction controls

**Branch:** `fix/p4-7-nova-control-coverage`.

Convert account entry/valuation, transaction entry/filters/row actions, transfer matching and
classification prompts to Nova Button, Input, NativeSelect, Checkbox and Field as appropriate.
Preserve identifier-reveal privacy and transfer/classification request contracts. Keep compact
ledger tables semantic and horizontally contained. Tests prove the shadcn control surface, exact
existing interactions, checkbox behavior, confirmation boundaries and heading hierarchy. Use
synthetic Account form visual checks; remove the temporary preview route before commit.

## 4.7.12 — Import and investment controls

**Branch:** `fix/p4-7-nova-import-investments`.

Convert statement-import proposal/mapper/review and investment entry/holding/funding/correction,
analysis and investment-import controls. Preserve staged import commits, evidence selection,
unsupported-state refusals and every exact monetary string. Test active, empty, error and
confirmation paths; inspect synthetic forms and detail views at three widths.

## 4.7.13 — Planning and remaining controls

**Branch:** `fix/p4-7-nova-planning-controls`.

Convert remaining general-purpose controls in Plans, Salary, Spending budgets, Overview, Wealth
and remaining detail routes. Recheck every sidebar destination for consistent Nova form,
feedback and action treatment at desktop, 588px and 360px, including keyboard focus, reduced
motion and page overflow. Keep charts' exact-value alternatives and all financial semantics.
After the verified PR merges, invite the owner to assess the actual application; only owner
acceptance closes Phase 4.7.
