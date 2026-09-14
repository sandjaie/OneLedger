# Superpowers plans and references

The [product roadmap](../product/roadmap.html) owns approved outcomes and acceptance criteria. The
[master implementation plan](../product/master-implementation-plan.html) owns their canonical
Phase N.M order and dependencies. [Implementation progress](progress.md) is the only live status
tracker, while the [project register](project-register.md) holds deferred or unapproved information.

All **new implementation plans** live in `docs/superpowers/plans/`. Name them
`YYYY-MM-DD-<feature>.md` and write them immediately before execution, using the current code and
accepted product requirements. Each plan implements one Phase N.M outcome and does not maintain
status.

All **new specifications** live in `docs/superpowers/specs/`.

Plans and specifications for delivered phases (through Phase 2.1) are history and are not
rewritten. Many were written and reviewed under the process of their time (Codex review, older diff
budgets, test-first evidence). [development-cycle.md](../development-cycle.md) and
[git-workflow.md](../git-workflow.md) govern current work.

The existing [Phase 0 task plan](phase-0-foundations.md) and
[PR breakdown](phase-0-pull-requests.md) remain as historical evidence. The
[schema](schema.md), [test suite](test-suite.md), and [question record](questions.html) are
supporting references. Keep new detailed plans in `plans/` and new specifications in `specs/`.

Product requirements remain in `docs/product/`; technical specifications in `docs/design/`;
operational requirements in `docs/operations/`; decisions in `docs/adr/`. A plan links to those
sources rather than becoming a competing specification. Never rewrite an accepted ADR body.
