# Project Documentation Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Task completion is recorded only in `../progress.md`.

**Goal:** Replace OneLedger's competing delivery taxonomies and master plans with one Phase N.M
sequence, one product-level master implementation plan, one live progress tracker, and one project
register.

**Architecture:** The roadmap owns approved product outcomes, the product-level master plan owns
delivery order and dependencies, `progress.md` owns live execution state, and the project register
owns unapproved or deferred information. A focused documentation check prevents retired current
terminology from returning while leaving dated historical evidence intact.

**Tech Stack:** HTML · Markdown · Python 3 documentation checks · pytest

**Spec:**
[`docs/superpowers/specs/2026-09-12-project-documentation-architecture-design.md`](../specs/2026-09-12-project-documentation-architecture-design.md)

## Global Constraints

- **Workspace:** `/Users/sandjaieravi/dev/OneLedger`.
- **Branch:** `docs/progress-tracker-structure`.
- **Base and PR target:** `develop` / PR #164.
- Use only `Phase N.M` as the persistent current delivery identifier.
- Do not change product scope, application behavior, database schema, historical results, branch
  names, PR titles, or commit messages.
- Keep live implementation state only in `docs/superpowers/progress.md`.
- Keep real financial figures, source filenames, complete account identifiers, and inferred
  institution identities out of every edited document and test fixture.
- Preserve dated plans, specifications, and reports as historical evidence; change only stale live
  links and add a historical-numbering notice where current authority would otherwise be unclear.
- Remove both obsolete master plans only after their still-valid content and every inbound link have
  a new owner.
- Use the active checkout only. Do not create a worktree.
- Run the documentation checks after each documentation task. Because Task 1 changes a Python
  checker, run the repository's verify and local code-review gates before the first push.

---

### Task 1: Enforce the canonical terminology boundary

**Files:**
- Modify: `api/tests/test_check_docs.py`
- Modify: `scripts/check-docs.py`

**Interfaces:**
- Consumes: the canonical-file and historical-crosswalk boundaries in the specification.
- Produces: `check_delivery_terminology()` and a named `CHECKS` entry used by the docs command.

- [ ] Add a failing pytest case with a temporary canonical document containing an M-number,
  lettered Phase 2 identifier, V2 phase label, and “slice”; assert all forms are reported.
- [ ] Add a passing case proving ordinary prose and a bounded historical crosswalk are permitted,
  and that dated plans and reports are outside the guard.
- [ ] Run `uv run --project api pytest api/tests/test_check_docs.py -q`; expect the new cases to fail
  because the checker does not exist.
- [ ] Implement the smallest checker over the explicit canonical path set. Strip the delimited
  historical crosswalk from the master-plan input before matching retired terms.
- [ ] Add the checker to `CHECKS` with a user-facing label that identifies terminology drift.
- [ ] Re-run `uv run --project api pytest api/tests/test_check_docs.py -q`; expect all tests to pass.
- [ ] Commit with `test(docs): guard canonical phase terminology`.

### Task 2: Publish the roadmap and master Phase N.M sequence

**Files:**
- Create: `docs/product/master-implementation-plan.html`
- Modify: `docs/product/roadmap.html`

**Interfaces:**
- Consumes: all approved scope and acceptance criteria in the existing roadmap and both obsolete
  master plans.
- Produces: the canonical product-outcome catalogue and complete delivery sequence used by every
  current document.

- [ ] Inventory each approved outcome and constraint from the three source documents into a private
  working checklist; confirm each has exactly one destination before rewriting.
- [ ] Rewrite the roadmap around Phase 0.x-5.x outcomes. Preserve product meaning and acceptance
  criteria, remove status narration, and link to the master plan for delivery details.
- [ ] Create the streamlined product-level master plan with authority rules, global sequencing
  constraints, the complete Phase N.M order, dependencies, acceptance boundaries, conditional
  activation, and a delimited historical-numbering crosswalk.
- [ ] Apply the approved Phase 1 mapping: 1.1-1.4 unchanged, delivered 1.8 becomes 1.5,
  investments becomes 1.6, salary/forecast becomes 1.7, and mapper/documents becomes 1.8.
- [ ] Verify that every former roadmap outcome appears once and that current sections contain no
  retired delivery terminology outside the crosswalk.
- [ ] Run `python3 scripts/check-docs.py` and `git diff --check`; expect both to pass.
- [ ] Commit with `docs(product): establish canonical phase roadmap`.

### Task 3: Separate live progress from the project register

**Files:**
- Create: `docs/superpowers/project-register.md`
- Modify: `docs/superpowers/progress.md`
- Modify: `CLAUDE.md`
- Modify: `docs/development-cycle.md`
- Modify: `README.md`
- Modify: `docs/superpowers/README.md`

**Interfaces:**
- Consumes: the canonical phase catalogue from Task 2 and every open gate or deferred item in the
  current tracker and obsolete plans.
- Produces: one live tracker, one controlled intake register, and repository instructions that
  route future information correctly.

- [ ] Create the register with sections for deferred approved work, unapproved discoveries,
  unresolved decisions/prerequisites, and known risks/operational gaps. Give every current item its
  date, evidence or reason, and review condition without assigning an unapproved phase number.
- [ ] Rewrite the tracker with the current Phase N.M, one next action, a concise full status table,
  active gates, and historical evidence links. Move all non-blocking deferred material to the
  register.
- [ ] Update repository instructions so the roadmap, master plan, tracker, register, and dated plans
  each have one stated owner. Replace the “tracker chooses a master” rule.
- [ ] Update both README files with the four-document map and the new product-level plan link,
  without copying live status or the full phase table.
- [ ] Run `python3 scripts/check-docs.py` and `git diff --check`; expect both to pass.
- [ ] Commit with `docs: separate delivery status from project intake`.

### Task 4: Migrate navigation and retire the competing plans

**Files:**
- Modify: `docs/index.html`
- Modify: every tracked `docs/**/*.html` file carrying the shared product/Superpowers navigation
- Modify: current and historical Markdown/HTML documents linking to either obsolete master plan
- Delete: `docs/superpowers/plans/implementation-plan.md`
- Delete: `docs/superpowers/plans/2026-09-09-phase-delivery-plan.md`

**Interfaces:**
- Consumes: the stable product-plan URL created in Task 2.
- Produces: one reachable navigation path and no inbound reference to a deleted plan.

- [ ] In each shared navigation block, add Master implementation plan immediately after Roadmap
  under Product and remove the old Implementation plan entry under Superpowers.
- [ ] Update the index's reading order and document-responsibility table to the four-document model.
- [ ] Redirect every link to either obsolete plan to the new master plan, a dated execution plan, or
  the historical crosswalk according to the surrounding claim.
- [ ] Add the concise historical-numbering notice to dated plans/specifications that otherwise read
  as current global sequencing; do not rewrite their original delivery identifiers.
- [ ] Delete the two obsolete master plans.
- [ ] Search HTML and Markdown link targets for
  `plans/implementation-plan.md` and `2026-09-09-phase-delivery-plan.md`; expect no live link to
  either deleted file. Plain historical discussion in this spec and plan may retain the paths.
- [ ] Run `python3 scripts/check-docs.py` and `git diff --check`; expect both to pass.
- [ ] Commit with `docs: retire competing implementation plans`.

### Task 5: Verify the migration and update its delivery record

**Files:**
- Modify: `docs/superpowers/progress.md` only if verification changes its active gates or evidence.
- Modify: PR #164 title/body through GitHub after local verification; no repository file is used as
  a PR-status mirror.

**Interfaces:**
- Consumes: Tasks 1-4 as one base-to-head documentation architecture migration.
- Produces: current verification evidence and a PR accurately describing the expanded scope.

- [ ] Run the focused checker test and record its exact result:
  `uv run --project api pytest api/tests/test_check_docs.py -q`.
- [ ] Run `python3 scripts/check-docs.py`, `git diff --check`, and the repository `/verify`
  equivalent required by `docs/development-cycle.md`.
- [ ] Review the complete diff against `develop` for lost scope, duplicate status, broken historical
  meaning, terminology leakage, private-data exposure, and unintended application changes.
- [ ] Fix any blocking finding, rerun the affected checks, and re-review only the fix range.
- [ ] Confirm `git status --short` is clean and the branch contains only the intended migration.
- [ ] Push only after verification and review are green, then update PR #164 using the repository PR
  template with actual command results and limitations.
