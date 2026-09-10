# OneLedger

`docs/` is the source of truth and is more precise than anything restated here. Read it.

| Need | Open |
|---|---|
| What is complete and what to do next | `docs/superpowers/progress.md` — the single maintained implementation tracker |
| What ships in which phase | the master phase plan — `docs/superpowers/progress.md` names the current one at its top; one term, "phase", with the old phase/milestone names mapped |
| What to ship, and when | `docs/git-workflow.md` — the delivery cycle, the diff budget and the review gates |
| How Phase 0 was decomposed | `docs/superpowers/phase-0-pull-requests.md` — its PR breakdown, kept as the record |
| Global constraints | `docs/superpowers/plans/implementation-plan.md` — its sequencing is superseded by the phase plan; its constraints stand |
| How branches, phases and releases flow | `docs/git-workflow.md` — the single branching and release reference |
| **When to run which gate, and what it may do** | `docs/development-cycle.md` — the ordered cycle; the gates report and do not modify code |
| Product-review requirements | `docs/product/finance-os.html` — financial meaning, planning, resilience and actions |
| The eight invariants | `README.md`; each one's test, `docs/operations/testing-and-data-quality.html` §1 |
| Tables by migration | `docs/superpowers/schema.md`; the model behind them, `docs/design/data-model.html` |
| Decisions in force | `docs/adr/index.html`; replaced ones kept apart in `superseded.html` |

**When documents disagree**, and they do: ADRs decide, `docs/design/` and `docs/operations/`
specify, `docs/superpowers/` executes. A plan contradicting a design document is a bug in one of
them —
say which, in the PR, and fix it there rather than working around it.

`docs/superpowers/schema.md` is the table reference. It carries no trigger, function, view or RLS
policy,
so a migration having *more* than it shows is expected; a migration *disagreeing* with it is not.

## Planning location and progress ownership

All new implementation plans go in `docs/superpowers/plans/`; use dated filenames per feature.
All new specifications go in `docs/superpowers/specs/`. The current master plan is whichever
one `docs/superpowers/progress.md` names at its top — the tracker owns that pointer, so a
re-plan edits the tracker, not this file. The existing Phase 0 plans and references live
directly in `docs/superpowers/`.

### Plans are execution maps (every plan and spec written after Phase 1's)

An implementation plan is an execution map, not a pre-written patch. Do not include complete
production implementations or complete test implementations unless the task is a trivial
deterministic repair. This owner rule overrides skill defaults that require full embedded code
in plan steps; Phase 1's existing plan and spec predate it and stay as delivered.

For feature work, a task specifies: outcome; affected files/modules; dependencies;
interfaces/contracts; invariants; security requirements; required test behaviours; validation
commands; acceptance criteria; and `Workspace`, `Branch`, `Base and PR target`.

**A task block runs to about 40 lines, and a plan's non-task sections to about 400.** The budget is
per block, not per document: a milestone sliced into twenty task-sized branches needs twenty
blocks, and capping the total would only push the slicing back the way it came. Past those limits
the plan is carrying something that belongs elsewhere: architecture decisions and table semantics go
to
`docs/superpowers/specs/`, `docs/design/` or an ADR, which are reviewed once rather than on every
task. **Each task must also fit the diff budget** ([docs/git-workflow.md](docs/git-workflow.md))
— a task that cannot is split before dispatch, or ships as more than one PR under the same slice
number. `/oneledger-plan-review` checks both and does the splitting.

Use code snippets only when they define:

1. an exact cross-task interface,
2. a schema/API contract,
3. a non-obvious invariant or algorithm where prose is ambiguous, or
4. a tiny deterministic change.

The implementation agent must inspect the current repository before deciding the exact
implementation. The specification is authoritative; the plan is the intended execution path;
repository reality determines the exact implementation. An implementation agent may deviate
from plan implementation details when necessary to fit existing code, provided the
specification, contracts and acceptance criteria remain satisfied and the deviation is
recorded — in the PR and `docs/superpowers/progress.md`, not by editing the plan after
dispatch.

**Maintain implementation progress in one file: `docs/superpowers/progress.md`.** The orchestrator
updates it during Superpowers execution with completed work, remaining gates and evidence links.
Read it before dispatch and update it at task boundaries; do not maintain parallel status tables
or completion checkboxes in plans. This owner preference overrides skill defaults that would
publish task state elsewhere. Private Superpowers recovery notes may support resuming work but
are not another published tracker.

The documentation index is a navigation page. The README introduces the project; the roadmap
defines scope, sequence and acceptance criteria; plans/specs define work and contracts. Keep PR
merge lists, current-phase banners, test-run summaries and pending-work recaps out of those files.
Use a neutral link to the progress file where needed. Dated verification reports and PR reviews
are historical evidence, not live trackers; retain their results without refreshing them as status.

## The rules are in the documents, not here

This file used to restate the application's rules — money representation, the identifier rule,
the authorization boundary, the never-store list. Every one of them already lives in a document
that owns it, and a rule written twice is a rule that drifts: the copy here claimed two static
checks existed that the tree had never had. So the rules stay where they are decided, and this
file says where to look and what is mechanically enforced.

**Read the owning document before working in its area.** These are not optional background.

| The rule | Who owns it |
|---|---|
| How money is represented, in Python, in Postgres and on the wire | [api-contracts.html](docs/design/api-contracts.html) §2, [data-model.html](docs/design/data-model.html) |
| Frozen dual-currency amounts, and why they are frozen | [ADR-0002](docs/adr/0002-frozen-dual-currency-amounts.html) |
| No currency, institution or country name as an identifier | [ADR-0012](docs/adr/0012-statement-layouts-as-data.html) |
| Which relations a report may read | [testing-and-data-quality.html](docs/operations/testing-and-data-quality.html) §1–2 |
| The authorization boundary, and what is public | [ADR-0013](docs/adr/0013-python-backend-service.html), [ADR-0016](docs/adr/0016-revocable-owner-sessions.html), [security-and-privacy.html](docs/operations/security-and-privacy.html) |
| Migrations: forward-only, Flyway, never edited once applied | [ADR-0015](docs/adr/0015-flyway-for-schema-migrations.html), [schema.md](docs/superpowers/schema.md) |
| The never-store list, and what may be kept instead | [security-and-privacy.html](docs/operations/security-and-privacy.html) §1 |
| The eight invariants, and the test that proves each | [README.md](README.md), [testing-and-data-quality.html](docs/operations/testing-and-data-quality.html) §1 |
| When AI may be called at all | [ADR-0003](docs/adr/0003-defer-ai-to-v2.html) |
| Branches, releases, commit style, the diff budget | [git-workflow.md](docs/git-workflow.md) |
| The gates, when each runs, and what it may do | [development-cycle.md](docs/development-cycle.md) |
| That nothing is weakened to reach green | [development-cycle.md](docs/development-cycle.md) §3 |
| That an ADR is superseded, never rewritten | [docs/adr/index.html](docs/adr/index.html) |

**What is enforced mechanically, so you will be told rather than trusted:**
`scripts/check-identifiers.py` fails the build on a currency, institution or country name used as an
identifier, on money arithmetic in TypeScript, on a report reading the base `transaction` table
instead of `v_transaction_reportable`, and on a flow multiplied by a rate outside the freeze.
`scripts/check-docs.py` fails on a broken link, malformed HTML, and on real financial data reaching
git. `scripts/check-diff-size.sh` refuses an oversized push. The pre-commit and pre-push hooks run
lint, types, secrets, pytest, vitest and every `db/tests/*.sql` against a live database. None of
these may be silenced.

## Working

**TDD, in the order the plans give.** Write the failing test, run it, watch it fail *for the right
reason*, implement, watch it pass, commit. A test that has never failed has never been shown to
test anything. Every step states its expected output — run the command and compare.

**Process is not restated here.** Branches, the delivery cycle, releases, PR titles, commit
style and the diff budget belong to [docs/git-workflow.md](docs/git-workflow.md); the gates —
`/verify`, `/oneledger-plan-review`, `/oneledger-code-review`, `/oneledger-code-hygiene` — when
each runs, and that they report findings without modifying code, belong to
[docs/development-cycle.md](docs/development-cycle.md). Read both; run the gates unprompted at
their triggers; nothing reaches `origin` unreviewed.

**Sequential agents use the active repository checkout.** Resolve its root from the current
repository with `git rev-parse --show-toplevel` and record that path in the task plan/spec before
dispatch. Never substitute a path from another machine. Do not create Git worktrees; this owner
preference overrides skill defaults that request one. Do not remove existing worktrees as part of
this preference change.
Only one implementation agent may work at a time. Wait for any reviewer using this checkout to
finish before changing its branch or files; do not run builds and type checks concurrently against
the same generated output.

**Put the branch in the plan or spec before dispatch.** Pass that file to the implementer; do
not leave the branch name only in a chat message or let the agent invent another one. The
orchestrator checks local changes, switches branches and dispatches the agent. Preserve
unrelated changes; never reset or overwrite them.

**The hooks are the gate, not the pull request.** Install once:

```bash
pre-commit install --install-hooks -t pre-commit -t pre-push
```

Commit runs lint, types, secrets and the docs check. Push runs pytest, vitest, every
`db/tests/*.sql` against a live database, and the diff budget. A failing push is not a reason to
reach for `--no-verify`; fix it and push again.
