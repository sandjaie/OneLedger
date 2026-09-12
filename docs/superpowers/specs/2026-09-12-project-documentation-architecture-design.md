# Project Documentation Architecture Design

**Date:** 12 September 2026  
**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `docs/progress-tracker-structure`  
**Base and PR target:** `develop` / PR #164

## Purpose

OneLedger currently describes the same delivery sequence through several incompatible naming
systems: Phase 0 and Phase 1, milestones M1-M10, M9a/M9b, Phase 2A-2C, V2, and Phase 1 slices.
The competing master plans also split authority between a product roadmap, an old implementation
plan, a dated phase-delivery plan, and the progress tracker. A reader cannot reliably determine
the canonical order or the next unimplemented outcome without translating between them.

This change establishes one numeric hierarchy, one master implementation plan, one live status
tracker, and one controlled intake register. It reorganizes documentation only; it does not change
product scope, implementation behavior, or the evidence behind completed work.

## Canonical document model

Each maintained document has one responsibility:

| Document | Responsibility |
| --- | --- |
| `docs/product/roadmap.html` | Product intent: approved outcomes, scope boundaries, and success criteria |
| `docs/product/master-implementation-plan.html` | Delivery architecture: the complete ordered Phase N.M sequence, dependencies, and acceptance boundaries |
| `docs/superpowers/progress.md` | Execution truth: current position, accepted work, next action, active gates, and evidence links |
| `docs/superpowers/project-register.md` | Controlled intake: deferred items, discoveries, unresolved decisions, risks, and candidates awaiting approval |
| Dated specifications and plans | Detailed contracts and execution maps for one approved outcome |
| Dated reports | Historical delivery and verification evidence |

The README introduces these roles without repeating phase status. The documentation index and the
published navigation link to the roadmap and then the master implementation plan. `CLAUDE.md` and
`docs/development-cycle.md` state these ownership rules for future agents.

No maintained roadmap, master plan, README, index, specification, or report may become a second
live progress tracker. Only `progress.md` states whether implementation is accepted, active,
blocked, ready, or planned.

## Information flow

New information moves through one controlled path:

```text
discovery
  -> project register
  -> owner approval
  -> product roadmap
  -> master implementation plan
  -> dated specification and execution plan
  -> progress tracker
  -> dated historical evidence
```

A discovery or improvement is not roadmap scope merely because it was recorded. It stays in the
project register until the owner approves it. Approval adds the product outcome to the roadmap and
its delivery position to the master plan. Implementation status then appears only in
`progress.md`. Completed run detail belongs in a dated report, with a concise evidence link from
the tracker.

Promoted entries leave the active project register; Git history preserves their intake record.
Rejected entries are removed rather than maintained as a second backlog. An unresolved decision
stays in the register until it is resolved or becomes an explicit prerequisite of an approved
phase.

## Canonical numbering

The only persistent delivery identifier is `Phase N.M`:

- `N` is the capability group.
- `M` is the ordered product outcome within that group.
- The words `milestone` and `slice`, M-numbers, lettered phases, and V2 are not used as current
  delivery identifiers.
- Dated execution plans may contain ordinary numbered tasks, but task numbers are local to that
  plan and are not project identifiers.
- Branches use the canonical phase number and a descriptive suffix, for example
  `feature/phase-1-6-investment-schema`.

The capability groups are:

| Group | Capability boundary |
| --- | --- |
| Phase 0.x | Foundations and manual ledger |
| Phase 1.x | Daily-use financial core |
| Phase 2.x | Verified history and data quality |
| Phase 3.x | Planning and resilience |
| Phase 4.x | Analysis and optimisation |
| Phase 5.x | Optional private intelligence |

This documentation migration is the one coordinated renumbering of already delivered work.
Afterward, an accepted Phase N.M identifier is immutable. Approved scope is appended at the next
available position in its capability group. Work follows numeric order. Moving unstarted work
ahead requires an owner-approved rebaseline of the affected unstarted identifiers before a branch
is dispatched; accepted identifiers never move.

Conditional scope receives a numeric position but is clearly marked with its activation condition
and does not block the next phase. Emergency fixes and operational repairs use descriptive branches
and remain linked from the current progress entry; they do not create a competing phase taxonomy.

## Phase 1 migration

The current delivery order is rebaselined so the accepted statement-guided onboarding work no
longer appears after future work:

| Canonical identifier | Outcome | Migration note |
| --- | --- | --- |
| Phase 1.1 | Statements land in the ledger | Existing identifier retained |
| Phase 1.2 | All supported statement layouts | Existing identifier retained |
| Phase 1.3 | Classification and transfers | Existing identifier retained |
| Phase 1.4 | Cards, net worth, and trends | Existing identifier retained |
| Phase 1.5 | Statement-guided onboarding and owner decisions | Previously delivered under Phase 1.8 |
| Phase 1.6 | Investments | Previously planned as Phase 1.5 |
| Phase 1.7 | Salary and a thin forecast | Previously planned as Phase 1.6 |
| Phase 1.8 | Layout mapper and documents | Previously planned as Phase 1.7 |

The master implementation plan carries one historical-numbering appendix so old PRs, branches,
dated plans, and reports remain interpretable. The main sequence uses only canonical identifiers.

## Historical-document policy

The two competing master plans are removed after their still-valid constraints, scope, and
acceptance requirements have moved to their owning documents:

- `docs/superpowers/plans/implementation-plan.md`
- `docs/superpowers/plans/2026-09-09-phase-delivery-plan.md`

All live links to either file are redirected to the new product-level master plan or to a more
specific owner. A deleted plan is never left as the target of a link.

Dated plans, specifications, reports, branch names, PR titles, and commit messages remain
historical evidence. Their original identifiers are not rewritten because doing so would falsify
the record of what was reviewed and delivered. When a historical document could be mistaken for
current sequencing, it receives a short notice that its numbering is historical and that the
current mapping lives in the master implementation plan.

## Master implementation plan structure

`docs/product/master-implementation-plan.html` is a stable, published product document. It contains:

1. its authority and relationship to the roadmap, tracker, register, and dated plans;
2. global sequencing rules and cross-phase constraints that are not already owned by an ADR,
   design document, operations document, or repository workflow document;
3. the complete Phase 0.x-5.x ordered outcome table;
4. one section per capability group with dependencies, scope, acceptance boundary, and links to
   relevant specifications;
5. conditional activation rules for scope such as loans;
6. an appendix mapping former identifiers to canonical Phase N.M identifiers.

It does not contain checkboxes, current statuses, merge lists, test-run results, or a current-phase
banner. Dated execution details remain in `docs/superpowers/plans/`.

## Progress tracker structure

`docs/superpowers/progress.md` remains compact and operational:

1. current Phase N.M and its state;
2. the single next action;
3. a complete but concise Phase N.M status table;
4. active acceptance or delivery gates;
5. links to historical evidence.

The canonical state vocabulary is `Accepted`, `Active`, `Ready`, `Planned`, `Conditional`, and
`Blocked`. A row may add a date or a short factual qualifier, but narrative histories and deferred
ideas move to their owning report or the project register.

The tracker links directly to the master implementation plan and the detailed plan for the active
outcome. It does not link to either retired master plan.

## Project register structure

`docs/superpowers/project-register.md` contains four active sections:

- deferred approved work;
- unapproved discoveries and improvement candidates;
- unresolved owner decisions and external prerequisites;
- known risks and operational gaps.

Each entry records its date, concise statement, reason or evidence, and the condition for its next
review. It does not assign a Phase N.M number before approval. It contains no private financial
figures, source filenames, complete account identifiers, or inferred institution identities.

Open gates for the currently active outcome remain in `progress.md`; longer-horizon or unscheduled
items belong in the register. This separates “what blocks the next action” from “what should not be
forgotten.”

## Navigation and README

The published documentation navigation treats the master implementation plan as a product-level
document and places it immediately after Roadmap. The former Superpowers navigation link to the
old implementation plan is removed. Relative links are correct from every published HTML page.

`README.md` includes a concise documentation map explaining:

- Roadmap: approved product outcomes;
- Master implementation plan: canonical Phase N.M order;
- Progress: live execution state and next action;
- Project register: deferred and unapproved information.

The README uses the canonical phase-group names but does not mirror current status or enumerate the
full phase table.

## Consistency guard and validation

The documentation check gains a focused guard for the maintained source-of-truth files. Outside the
master plan's explicitly bounded historical crosswalk, those files reject the retired identifier
forms: M-number milestones, lettered Phase 2 names, V2 as a delivery phase, and “slice” as a project
unit. Historical dated artifacts are excluded from this terminology guard.

The implementation is complete when:

- the roadmap and master implementation plan cover every previously approved product outcome;
- every canonical outcome has exactly one Phase N.M identifier;
- `progress.md` reports every accepted and remaining canonical outcome without conflicting order;
- the project register contains every still-relevant deferred item, decision, risk, and operational
  gap removed from the tracker or old master plans;
- the two obsolete master plans are deleted and no link targets them;
- published navigation places the master implementation plan immediately after the roadmap;
- the README explains the four-document model;
- historical evidence retains its original meaning and contains no private data;
- `python3 scripts/check-docs.py`, the focused documentation-check tests, and
  `git diff --check` pass.

## Scope boundaries

This change does not alter application code, database schema, financial behavior, product scope,
or the repository's branching and review policy. It does not rewrite historical branch names, PR
titles, commit messages, or delivery results. It does not convert dated Markdown execution plans to
HTML. It does not add a second backlog or a second progress tracker.
