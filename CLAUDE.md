# OneLedger

Read the document that owns the area you are changing. Do not copy its rules into another file.

| Need | Source of truth |
|---|---|
| Current implementation state and next task | `docs/superpowers/progress.md` |
| Product sequence and dependencies | `docs/product/master-implementation-plan.html` |
| Approved outcomes and acceptance | `docs/product/roadmap.html` |
| Deferred or unapproved work | `docs/superpowers/project-register.md` |
| Task plans and specifications | `docs/superpowers/plans/`, `docs/superpowers/specs/` |
| Branches, commits, PRs and releases | `docs/git-workflow.md` |
| Delivery gates | `docs/development-cycle.md` |
| Financial and API contracts | `docs/design/` |
| Security, privacy and testing rules | `docs/operations/` |
| Schema reference | `docs/superpowers/schema.md` |
| Decisions in force | `docs/adr/index.html` |

When sources disagree, ADRs decide; design and operations documents specify; the roadmap owns
product scope; the master plan owns delivery order; dated plans execute; `progress.md` alone owns
live status. Fix the contradiction instead of working around it. `schema.md` lists relations and
columns, not every trigger, function, view or RLS policy.

## Planning

A plan is an execution map, not a pre-written patch. Each task states:

- outcome, dependencies and affected files or modules;
- interfaces, contracts, invariants and security requirements;
- required test behaviours, validation commands and acceptance criteria;
- exact `Workspace`, `Branch`, and `Base and PR target`.

Use code only for an exact interface or schema contract, a non-obvious invariant, or a tiny
deterministic change. Put durable design rationale in a spec, design document or ADR. Size and
group branches to the diff budget in `docs/git-workflow.md`. `/oneledger-plan-review` checks a new
phase group's plans once.

Repository reality may change implementation details, but not approved contracts. Record a
necessary deviation in the PR and `docs/superpowers/progress.md`; do not rewrite a dispatched
plan or create another status tracker. README, indexes, roadmap pages and plans remain free of
current-phase banners, completion tables and test summaries.

## Non-negotiable engineering rules

| Rule | Owning document |
|---|---|
| Money: Postgres `NUMERIC`, Python `Decimal`, strings on the wire; no TypeScript money arithmetic | `docs/design/api-contracts.html`, `docs/design/data-model.html` |
| Frozen dual-currency amounts | `docs/adr/0002-frozen-dual-currency-amounts.html` |
| No currency, institution or country name as an identifier | `docs/adr/0012-statement-layouts-as-data.html` |
| Reports read approved reporting views | `docs/operations/testing-and-data-quality.html` |
| Owner authorization, household RLS and public-route boundary | `docs/adr/0013-python-backend-service.html`, `docs/adr/0016-revocable-owner-sessions.html`, `docs/operations/security-and-privacy.html` |
| Forward-only Flyway migrations; never edit one already applied | `docs/adr/0015-flyway-for-schema-migrations.html` |
| Never-store data and identifier handling | `docs/operations/security-and-privacy.html` |
| Eight financial invariants and their proofs | `README.md`, `docs/operations/testing-and-data-quality.html` |
| No AI calls before the approved phase | `docs/adr/0003-defer-ai-to-v2.html` |

Never fabricate a price, transaction, cost basis, residual, valuation or corporate action. Never
put real financial values, filenames, complete identifiers, credentials or statement content in
code, fixtures, commits, logs, errors or agent messages. Unsupported cases fail closed.

## Working rules

- Test major behavior (money totals, authorization, household isolation, database writes); obvious
  code needs no dedicated test and test-first is optional. Never weaken a test or invariant to
  reach green.
- Use the active checkout returned by `git rev-parse --show-toplevel`. Do not create worktrees.
- One implementation writer works at a time. Reviewers are read-only; the orchestrator owns
  branch switching, pushes, PRs and merges.
- Preserve unrelated changes and local data. Never point tests at the working ledger.
- For database work, start the branch's one test server with
  `eval "$(scripts/test-db.sh up)"`; reuse it for affected tests. The final verifier reuses it and
  tears it down with `scripts/test-db.sh down`.
- Follow the cycle in `docs/development-cycle.md`: implement and run affected tests, commit
  locally, review once, repair once, run the scope-appropriate verifier once, then push and open
  the PR.

Install the fast commit guard once:

```bash
pre-commit install --install-hooks -t pre-commit
```

Commit hooks check staged files. Run `scripts/verify.sh` after review, or
`scripts/verify-web.sh` when all changed paths are web, documentation or verifier/workflow files.
Both record the exact passing head and check diff size. There is no pre-push hook.
Do not use `--no-verify` for commits.
