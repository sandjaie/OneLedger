# OneLedger

`docs/` is the source of truth and is more precise than anything restated here. Read it.

| Need | Open |
|---|---|
| What to type next | `docs/superpowers/plans/2026-09-06-phase-0-completion.md` — remaining Phase 0 work; original Tasks 1–9 delivered by GitHub PRs #3–#8 |
| What to ship, and when | `docs/superpowers/phase-0-pull-requests.md` — seven PRs, the review gate, the 500-line cap |
| Build order, global constraints | `docs/superpowers/plans/implementation-plan.md` |
| Product-review requirements | `docs/product/finance-os.html` — financial meaning, planning, resilience and actions |
| The eight invariants | `README.md`; each one's test, `docs/operations/testing-and-data-quality.html` §1 |
| Tables by migration | `docs/superpowers/schema.md`; the model behind them, `docs/design/data-model.html` |
| Decisions in force | `docs/adr/index.html` — 16, plus four replaced ones kept apart in `superseded.html` |

**When documents disagree**, and they do: ADRs decide, `docs/design/` and `docs/operations/`
specify, `docs/superpowers/` executes. A plan contradicting a design document is a bug in one of them —
say which, in the PR, and fix it there rather than working around it.

`docs/superpowers/schema.md` is the table reference. It carries no trigger, function, view or RLS policy,
so a migration having *more* than it shows is expected; a migration *disagreeing* with it is not.

## Planning location and current phase

All new implementation plans go in `docs/superpowers/plans/`; use dated filenames per feature.
All new specifications go in `docs/superpowers/specs/`. The current master is
`docs/superpowers/plans/implementation-plan.md`; the existing Phase 0 plans and references live
directly in `docs/superpowers/`.

Phase 0 is in progress. GitHub PRs #3–#8 are merged into `feature/phase-0-foundations`, covering
original Tasks 1–9 through the reviewed web foundation and typed client. Next are Task 10
(Google OIDC and revocable sessions), Task 11 (money and navigation), then phase-exit checks.
Product-review additions do not expand Phase 0.

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
ADR-0016). Every financial route depends on it. `/health` and the future `/auth/login` and
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

**Branches** carry a type prefix and say what is inside them: `feature/`, `fix/`, `docs/`,
`chore/`. Phase 0 work branches from `feature/phase-0-foundations` and merges back into it.

**The hooks are the gate, not the pull request.** Install once:

```bash
pre-commit install --install-hooks -t pre-commit -t pre-push
```

Commit runs lint, types, secrets and the docs check. Push runs pytest, vitest and every
`db/tests/*.sql` against a live database. A failing push is not a reason to reach for
`--no-verify`; fix it and push again.

**Every PR waits for Codex review on GitHub** and merges only on approval. No PR exceeds 500 lines
of implementation and test code — documentation, configuration and generated files do not count,
because nobody reads a lockfile.

**Commits**: `type(scope): a lowercase sentence stating what is now true`. Types `feat fix docs ci
chore refactor test`; scopes `db api web ci docs plan`. Subjects are declarative, not imperative —
"the ADR folder now lists only decisions in force", not "update ADR folder". The body says what
was wrong and why this is right. `git log` is the style guide.
