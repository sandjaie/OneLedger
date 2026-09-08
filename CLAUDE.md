# OneLedger

`docs/` is the source of truth and is more precise than anything restated here. Read it.

| Need | Open |
|---|---|
| What is complete and what to do next | `docs/superpowers/progress.md` — the single maintained implementation tracker |
| How to execute the work | `docs/superpowers/plans/2026-09-06-phase-0-completion.md` — task requirements and verification steps |
| What to ship, and when | `docs/git-workflow.md` — the delivery cycle, the diff budget and the review gates |
| How Phase 0 was decomposed | `docs/superpowers/phase-0-pull-requests.md` — its PR breakdown, kept as the record |
| Build order, global constraints | `docs/superpowers/plans/implementation-plan.md` |
| How branches, phases and releases flow | `docs/git-workflow.md` — the single branching and release reference |
| The three review skills | `.claude/skills/oneledger-{plan-review,code-review,code-hygiene}/SKILL.md` |
| Product-review requirements | `docs/product/finance-os.html` — financial meaning, planning, resilience and actions |
| The eight invariants | `README.md`; each one's test, `docs/operations/testing-and-data-quality.html` §1 |
| Tables by migration | `docs/superpowers/schema.md`; the model behind them, `docs/design/data-model.html` |
| Decisions in force | `docs/adr/index.html` — 16, plus four replaced ones kept apart in `superseded.html` |

**When documents disagree**, and they do: ADRs decide, `docs/design/` and `docs/operations/`
specify, `docs/superpowers/` executes. A plan contradicting a design document is a bug in one of them —
say which, in the PR, and fix it there rather than working around it.

`docs/superpowers/schema.md` is the table reference. It carries no trigger, function, view or RLS policy,
so a migration having *more* than it shows is expected; a migration *disagreeing* with it is not.

## Planning location and progress ownership

All new implementation plans go in `docs/superpowers/plans/`; use dated filenames per feature.
All new specifications go in `docs/superpowers/specs/`. The current master is
`docs/superpowers/plans/implementation-plan.md`; the existing Phase 0 plans and references live
directly in `docs/superpowers/`.

### Plans are execution maps (every plan and spec written after Phase 1's)

An implementation plan is an execution map, not a pre-written patch. Do not include complete
production implementations or complete test implementations unless the task is a trivial
deterministic repair. This owner rule overrides skill defaults that require full embedded code
in plan steps; Phase 1's existing plan and spec predate it and stay as delivered.

For feature work, a task specifies: outcome; affected files/modules; dependencies;
interfaces/contracts; invariants; security requirements; required test behaviours; validation
commands; acceptance criteria; and `Workspace`, `Branch`, `Base and PR target`.

**A task block runs to about 40 lines and a milestone plan to about 400.** Past that, the plan is
carrying something that belongs elsewhere: architecture decisions and table semantics go to
`docs/superpowers/specs/`, `docs/design/` or an ADR, which are reviewed once rather than on every
task. **Each task must also fit the diff budget** (400 production / 800 total added lines) — a
task that cannot is split before dispatch, not discovered mid-implementation.
`/oneledger-plan-review` checks both and does the splitting.

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

## Rules you will otherwise break by reflex

**Money.** `Decimal` in Python, `NUMERIC(20,4)` in Postgres, a **string** on the wire
(`"-310.0000"`). Never `float`, never a JSON number, never `parseFloat`/`Number()` on a money
field. TypeScript formats money and does no arithmetic on it; aggregation happens in Postgres.
Percentages are human-readable — `15.2500` is 15.25%, not `0.1525`.

**No currency, institution or country name is ever an identifier.** Not a column, a type, an enum
member, a function name, a locale constant or a required seed row. `amount_base` /
`amount_secondary`, never `amount_inr`. `formatMoney` takes a `currency` row and reads its
`symbol`, `decimals` and `number_format` — never a `"INR" | "AED"` union. The reporting pair is
`household.base_currency` / `secondary_currency`. Every bank and currency in the docs is an
example (ADR-0012). A static check fails the build on this; do not silence it.

**Reports never read the base table and never multiply by a rate.** Cash-flow, budget, spending
and savings-rate queries read `v_transaction_reportable` and the frozen `amount_base` /
`amount_secondary` columns. Both are static checks, not conventions.

**The authorization boundary is `require_owner()` in the API service** (ADR-0013, refined by
ADR-0016). Every financial route depends on it. `/health` and the `/auth/login` and
`/auth/callback` bootstrap routes are the complete public set. RLS is depth, not the wall — the
service connects privileged and bypasses it. Every `SECURITY DEFINER` function calls
`assert_is_owner()` first. The UI is never the boundary.

**Migrations are forward-only and run by Flyway** (ADR-0015). `db/migrations/V<n>__<name>.sql`,
applied with `docker compose run --rm flyway migrate`. **Never edit an applied migration** —
Flyway checksums them and will refuse to run. A table change edits the migration, `schema.md` and
`data-model.html` in one commit.

**Nothing about money is invented silently.** A carried-forward rate, an interpolated valuation
and an unexplained residual are stored as labelled facts and shown as such (Invariant 7).

**Recurring user-facing work belongs in the app, not a terminal.** `GET /doctor` backs
Settings › Data Quality. There is no `oneledger` command.

**No LLM calls in Phase 1 or Phase 2** (ADR-0003).

## Never

- **Real financial data in this repository** — no statement, no unredacted fixture, no `.env`, on
  any branch. `pdf-statements/` sits in the working tree, git-ignored, holding real statements
  with card numbers in the filenames. Never `git add -f` it.
- Anything on the never-store list (`security-and-privacy.html` §1): full account or card number,
  CVV, PIN, OTP, password, full tax or national ID. Masked value plus salted hash only.
- A secret behind `NEXT_PUBLIC_`. `NEXT_PUBLIC_API_URL` is the only such variable, and it holds
  a URL.
- String-interpolated SQL.
- Weakening an invariant, loosening an assertion or deleting a test to reach green. If an
  invariant is genuinely wrong, that is a superseding ADR, not an edit.
- Editing an ADR body. One that gets rewritten stops being a record — supersede it.
- Committing straight to `main`.

## Working

**TDD, in the order the plans give.** Write the failing test, run it, watch it fail *for the right
reason*, implement, watch it pass, commit. A test that has never failed has never been shown to
test anything. Every step states its expected output — run the command and compare.

**Branches and releases follow [docs/git-workflow.md](docs/git-workflow.md)** — the single
branching and release reference; do not restate its model elsewhere. In short: `develop` is the
standing integration branch; a milestone is planned once and delivered as several task-sized
branches `<type>/<name>` (types `feature/`, `fix/`, `docs/`, `chore/`) created from `develop`,
each its own PR into `develop`; the plan lands with the first. The delivery cycle per PR is:
plan and implement →
Codex review → patch the findings → Codex thumbs-up → merge. A `release/*` branch carries
changelog, release notes and the tag into `main`; release and any integration toward `main`
need a separate owner request.

**Sequential agents use the active repository checkout.** Resolve its root from the current
repository with `git rev-parse --show-toplevel` and record that path in the task plan/spec before
dispatch. Never substitute a path from another machine. Do not create Git worktrees; this owner
preference overrides skill defaults that request one. Do not remove existing worktrees as part of this preference change.
Only one implementation agent may work at a time. Wait for any reviewer using this checkout to
finish before changing its branch or files; do not run builds and type checks concurrently against
the same generated output.

**Put the branch in the plan or spec before dispatch.** Every task records `Workspace`, `Branch`
and `Base and PR target`, using exact values and the prefixes above. Each split delivery has its
own declared branch. Pass that file to the implementer; do not leave the branch name only in a
chat message or let the agent invent another one. The orchestrator checks local changes, switches
branches and dispatches the agent. Preserve unrelated changes; never reset or overwrite them.

**Use the repository PR template.** After implementation and local review, the orchestrator creates
the PR from `.github/PULL_REQUEST_TEMPLATE.md`, filled with the actual change, implementation and
verification evidence. Record failures, skipped checks and reasons honestly; do not pre-check
success. Verify Codex findings against the tree, patch them on the same branch, push for
re-review, and merge to `develop` only on Codex's thumbs-up; then delete the branch and start
the next milestone. Integration-to-main still requires a separate owner request.

**The hooks are the gate, not the pull request.** Install once:

```bash
pre-commit install --install-hooks -t pre-commit -t pre-push
```

Commit runs lint, types, secrets and the docs check. Push runs pytest, vitest, every
`db/tests/*.sql` against a live database, and the diff budget. A failing push is not a reason to
reach for `--no-verify`; fix it and push again.

**Review here, not on GitHub.** A Codex round is the audit of a review that should already have
happened locally. Three skills own that, and they run in this order:

- `/oneledger-plan-review` — on every plan Superpowers produces, **before dispatching any task**.
  It forecasts each task against the diff budget and splits what does not fit. Splitting on paper
  costs a paragraph; splitting after implementation costs a day.
- `/oneledger-code-review` — after implementation, **before every push**. Fresh-context subagents
  read the diff against this repository's own rules: identifier leakage, money handling, the
  authorization boundary, reporting views, migrations, invariants, TDD evidence. Nothing is pushed
  with a blocking finding open.
- `/oneledger-code-hygiene` — at milestone boundaries. Its findings ship as their own `chore/`
  branch, never folded into a feature PR.

**The diff budget**: **400 added production lines, 800 added production + test lines**, hard stop
500 / 1000, measured against `develop` by `scripts/check-diff-size.sh` on push. Documentation,
configuration and generated files do not count. Past the hard stop a branch is split, not argued
with; `ONELEDGER_SIZE_OVERRIDE="reason"` exists for the indivisible migration and the reason goes
in the PR. One module plus its tests already spends the whole budget in this tree — slice below
the module.

**Every PR waits for Codex review on GitHub** and merges when no blocking finding is open. A PR
carries one task-sized slice of a milestone. Codex findings are triaged: correctness, security,
invariant and contract findings are patched on the branch; hardening, style and out-of-scope
suggestions are answered and recorded in the PR's Notes, not patched — each one widens the diff
and buys another round. A documentation-only PR merges on owner review alone.

**Commits**: `type(scope): a lowercase sentence stating what is now true`. Types `feat fix docs ci
chore refactor test`; scopes `db api web ci docs plan`. Subjects are declarative, not imperative —
"the ADR folder now lists only decisions in force", not "update ADR folder". The body says what
was wrong and why this is right. `git log` is the style guide.
