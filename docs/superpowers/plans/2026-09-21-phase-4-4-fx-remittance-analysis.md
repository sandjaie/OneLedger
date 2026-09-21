# Phase 4.4 implementation plan — FX and remittance analysis

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Deliver the
[Phase 4.4 specification](../specs/2026-09-21-phase-4-4-fx-remittance-analysis.md): owner-recorded
provider and explicit fee on a confirmed remittance, and per-corridor, per-year, per-provider cost,
effective-rate and cheapest-channel comparison read from the `transfer` row's existing FX fields.

**Architecture:** Task 1 adds a pure aggregation module, one owner-correction route and one
read-only analysis route. Task 2 adds the correction control to the existing transfer panel and a
Remittances screen for the analysis. **No migration:** `transfer` already carries `provider`,
`explicit_fee_amount`, `explicit_fee_currency`, `implied_rate`, `market_rate`, `fx_spread_pct`,
`fx_spread_cost_source` and `total_cost_source` (schema `0010`), and `FX_COST` in
`routes/transfers.py` already computes the per-transfer figures at confirmation. This phase writes
the two fields nothing writes today and reads the rest.

**Stack:** Existing Python/FastAPI, PostgreSQL/Flyway and Next.js/React; no new dependency, no new
table, no new enum value. Phase 4.4 is planned as one phase and reviewed once.

## Global execution contract

- Workspace: the active checkout (`git rev-parse --show-toplevel`); one writer, no worktrees. Each
  branch starts from current `develop` after the previous one merges and opens one PR to `develop`.
- Money stays `NUMERIC`/`Decimal`/strings on the wire. TypeScript formats only;
  `scripts/check-identifiers.py` refuses `Number`/`parseFloat` on money and any direct read of the
  `transaction` table.
- Every route uses `require_owner` and an explicit `owner.household_id` filter. The analysis GET
  opens `SET TRANSACTION READ ONLY` and writes nothing. The correction PATCH takes the household
  lock (`SELECT 1 FROM household WHERE id = %s FOR UPDATE`) first, exactly as
  `routes/portfolio.py::put_allocation_targets` and `routes/transactions.py::update_transaction` do.
- Only `status = 'confirmed'` and `transfer_kind = 'remittance'` rows are read by the analysis or
  accepted by the correction. A `rejected` row is invisible to both.
- No currency, institution or country name becomes an identifier (ADR-0012). `provider` stays the
  owner's free text on the transfer row; corridors are labelled by the currency codes already
  stored on the transfer.
- `implied_rate`, `market_rate`, `fx_spread_pct` and `fx_spread_cost_source` stay owned by
  confirmation-time `FX_COST`. Nothing in this phase rewrites them.
- A figure that is not known is null with the bucket's counts beside it, never zero and never a
  percentage of an unknown whole (Invariant I7, and the specification's "Evidence, not inference of
  money").
- Tests cover major behaviour: money totals, authorization, household isolation, database writes,
  and one web test per new flow.
- After affected tests: one `/oneledger-code-review`, one repair, confirmation for money,
  authorization or migration findings, the scope-appropriate verifier once
  (`scripts/verify.sh` for Task 1, `scripts/verify-web.sh` for the web-only Task 2), push, PR, merge.

## Branch table

| Task | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | corrections and the analysis API | `feature/p4-4-t1-remittance-analysis-api` | 600 | 600 | 4.3 |
| 2 | corrections and analysis on screen | `feature/p4-4-t2-remittance-analysis-screen` | 550 | 300 | 1 |

Both tasks together (1,150 production, 2,050 with tests) would fit one branch inside the budget.
They stay split so Task 2 is web-only and runs the cheaper `scripts/verify-web.sh` rather than
dragging the API, migration and SQL-invariant suites through a branch that changes no Python —
the same split Phases 4.1–4.3 used for their API-then-screen pairs.

## Files

Task 1 creates `api/oneledger/remittance_analysis.py`,
`api/tests/test_remittance_analysis.py`, `api/tests/test_remittance_analysis_live.py`; updates
`api/oneledger/routes/transfers.py`, generated types, `docs/design/api-contracts.html`,
`docs/superpowers/project-register.md`, `docs/superpowers/progress.md`. Task 2 creates
`web/app/remittances/remittances-screen.tsx`, `web/app/remittances/page.tsx` and their tests;
updates `web/app/transactions/transfer-panel.tsx` and its test, `web/app/ui/app-shell.tsx` (one nav
entry) and `docs/superpowers/progress.md`. No migration and no `db/tests/` file: this phase adds no
schema.

## Phase 4.4 Task 1 — corrections and the analysis API

**Workspace/branch/base/target:** the active checkout /
`feature/p4-4-t1-remittance-analysis-api` / `develop` / `develop`. **Depends:** Phase 4.3 Task 2
merged. **Contract:**

- **Pure `api/oneledger/remittance_analysis.py`** (no database), the shape
  `investments/returns.py` uses: the route reads rows, this decides every figure. Input: one
  sequence of transfer facts `(source_currency, destination_currency, transfer_date,
  source_amount, destination_amount, implied_rate | None, fx_spread_cost_source | None,
  total_cost_source | None, provider | None, cost_is_approximate)`. No fee field is passed and
  nothing is translated here: a stored fee's effect is already inside `total_cost_source`, frozen
  at correction time (ADR-0002), and the fee line is recovered by subtraction below. Output: the
  corridor list the GET returns. Rules:
  - **Corridors.** Group by `(source_currency, destination_currency)`. Order by `sentSource`
    descending over the rows supplied — with `?year=` set that is that year's figure, not a
    lifetime one — then source then destination code. No corridor's figures ever mix another's.
  - **Years.** Within a corridor, group by `transfer_date` calendar year, newest first.
  - **Groups.** Within a corridor-year, one row per distinct non-null `provider` string (exact
    match, no case-folding or fuzzy merge), ordered by `effectiveCostPct` ascending with nulls
    last, then provider name; plus at most one `unattributed` row for `provider IS NULL`.
  - **Per group:** `transferCount` counts every transfer; `sentSource` sums `source_amount` over
    all of them; the **costed subset** is those whose `total_cost_source` is not null, and
    `costedSentSource`, `spreadSource`, `totalCostSource` and `feesSource` sum only over it:
    `spreadSource` from `fx_spread_cost_source` (null treated as zero within a costed transfer),
    `totalCostSource` from `total_cost_source`, and
    `feesSource = Σ (total_cost_source − COALESCE(fx_spread_cost_source, 0))`. Deriving the fee by
    subtraction rather than re-reading and re-translating `explicit_fee_amount` is what makes
    `feesSource + spreadSource = totalCostSource` hold by construction, so no later `fx_rate`
    correction can produce a row that does not add up. `costedSentSource = 0` nulls `feesSource`,
    `spreadSource`, `totalCostSource` and `effectiveCostPct`.
    `effectiveCostPct = totalCostSource / costedSentSource * 100` at 4 dp half-even.
    `avgRate = sum(destination_amount) / sum(source_amount)` over transfers whose `implied_rate`
    is not null, 10 dp **half-up** (`money.py::_fixed`'s convention for the rates this figure sits
    beside), null when that subset is empty — a weighted average, never a mean of rates.
    `approximateCount` counts the costed subset's `cost_is_approximate` rows. A negative stored
    cost (a spread favourable to the owner, which `FX_COST` does not floor) stays negative in
    every figure of its own group: the evidence is reported as it stands.
  - **Cheapest channel.** Over the corridor-year's named provider rows with
    `costedSentSource > 0`: fewer than two leaves `cheapestProvider` and
    `potentialSavingsSource` null. Two or more sets `cheapestProvider` to the lowest
    `effectiveCostPct` (tie broken by provider name), then, with both sums over those named rows
    only, `potentialSavingsSource = min(Σ totalCostSource, max(0, Σ totalCostSource −
    Σ costedSentSource × max(0, cheapest.effectiveCostPct) / 100))` at 4 dp half-even. The inner
    `max(0, …)` on the cheapest rate refuses to project a favourable spread onto other transfers
    as if it were repeatable; the outer `min` refuses to state a saving larger than the cost
    actually incurred. The unattributed row never enters either figure.
  - **Corridor-year totals:** `totalSentSource` over every transfer of that corridor-year (named
    and unattributed); `totalCostedSentSource`, `totalFeesSource`, `totalSpreadSource` and
    `totalCostSource` over its whole costed subset by the same rules as a group; and
    `totalEffectiveCostPct = totalCostSource / totalCostedSentSource * 100` at 4 dp half-even,
    null when nothing is costed. The screen formats these; it never adds money
    (`scripts/check-identifiers.py` Rule 2).
- **`PATCH /transfers/{transferId}`** — the owner's correction. Body
  `{ provider?: str | null, explicitFeeAmount?: str | null, explicitFeeCurrency?: str | null,
  notes?: str | null }`; an omitted key leaves that field unchanged, an explicit `null` clears it.
  `notes` is bounded at 500 characters, as `TransferCreate.notes` already is. The route is
  **last-write-wins** and exposes no `expectedState`: the same body sent twice stores the same
  figures, because `total_cost_source` is recomputed from the stored spread and the submitted fee
  rather than adjusted by it, so the write floor's stale-state clause does not apply.
  - 404 — and one indistinguishable answer — for an unknown id, another household's row, a
    `rejected` row, and a `transfer_kind <> 'remittance'` row, matching
    `_read_transfer`'s existing "transfer not found".
  - `provider` trimmed, 1–80 characters after trimming, else 422; an empty or whitespace-only
    string is a 422, not a silent clear (clearing is an explicit `null`).
  - `explicitFeeAmount` and `explicitFeeCurrency` are paired: exactly one present is 422, mirroring
    the ledger's own `original_pair` rule. The amount is a decimal string `>= 0` at 4 dp or fewer
    (more places 422, never rounded); the currency is a 3-letter code that must exist in
    `currency`, else 422.
  - **Fee translation happens here and only here.** When the fee currency differs from
    `source_currency`, translate with `pair_rate` over `fx_rate` rows for the transfer's own
    `transfer_date`, the same lookup `_Scorer.market_rate` uses; no rate for that date and pair is
    422 ("no exchange rate is held for that date"), never a guessed translation (Invariant I7).
    Only the translated effect on `total_cost_source` is stored, so nothing downstream re-converts
    a frozen amount on read (ADR-0002) and a later `fx_rate` backfill cannot make a stored cost
    disagree with the fee and spread it is the sum of.
  - **`total_cost_source` recompute**, in the same statement and transaction as the field update:
    null when `fx_spread_cost_source` is null and the fee is null; otherwise
    `COALESCE(fx_spread_cost_source, 0) + COALESCE(fee translated to source currency, 0)` at 4 dp.
    Clearing the fee returns it to the FX-spread-only figure. `implied_rate`, `market_rate`,
    `fx_spread_pct` and `fx_spread_cost_source` are never written here.
  - Returns the updated `TransferRow` through the existing `_read_transfer`, so `costIsApproximate`
    stays derived on read.
- **`GET /transfers/analysis`** — read-only (`SET TRANSACTION READ ONLY`), `year` optional query
  filter on `transfer_date`'s calendar year (a non-integer or out-of-range year is 422). Reads the
  household's confirmed remittance transfers and hands them to `remittance_analysis` unchanged. It
  translates nothing and reads no `fx_rate` row: every cost figure it reports is one already frozen
  on the transfer. Response (every money figure, ratio and percent a string):

```text
{
  corridors: [{
    sourceCurrency, destinationCurrency,
    years: [{
      year, totalSentSource, totalCostedSentSource, totalFeesSource | null,
      totalSpreadSource | null, totalCostSource | null, totalEffectiveCostPct | null,
      cheapestProvider | null, potentialSavingsSource | null,
      providers: [ProviderRow], unattributed: ProviderRow | null
    }]
  }]
}
ProviderRow = { provider | null, transferCount, sentSource, costedSentSource,
  feesSource | null, spreadSource | null, totalCostSource | null,
  effectiveCostPct | null, avgRate | null, approximateCount }
```

- **Project register:** record what this phase deliberately leaves open — a provider catalogue to
  choose from rather than free text, cross-currency `investment_funding` cost tracking, and a
  corridor-level "switch and save" recommendation beyond the one stated opportunity-cost figure.

**Tests:** Pure — corridors never mix currency pairs; the documented per-group sums over a
two-transfer fixture including one carried-forward rate; an uncosted transfer inside `sentSource`
but outside `costedSentSource` and every costed sum; `effectiveCostPct` null when nothing is costed;
`avgRate` weighted, proven against a fixture whose simple mean differs; provider ordering with a
null `effectiveCostPct` last; one costed named provider yields no comparison; two yield the
documented `cheapestProvider` and `potentialSavingsSource`, and a corridor already entirely on the
cheapest channel yields exactly zero rather than a negative saving; a negative stored cost keeps
its sign in its own group yet yields neither a saving above `Σ totalCostSource` nor one projected
from a negative rate (the reviewed fixture: two providers, AED 10,000 each, costs −100 and +200);
`feesSource + spreadSource = totalCostSource` on every group of a mixed fixture; the corridor-year
total row reconciles to its groups; the unattributed row excluded from the comparison but inside
corridor-year totals. Live — `GET` writes nothing and reads no `fx_rate` row; another household's
transfers never appear in either route; `year` filters and refuses a bad value; a rejected,
non-remittance, unknown and foreign transfer each 404 on `PATCH`; provider trim and length
refusals; unpaired fee amount/currency 422; unknown fee currency 422; a third-currency fee
translated at the transfer's own date; a fee date with no rate 422; `total_cost_source` recomputed
on set and reverted on clear; `implied_rate`/`market_rate`/`fx_spread_pct`/`fx_spread_cost_source`
unchanged by a correction; an `fx_rate` row changed after a correction leaves the stored cost and
the response's reconciliation untouched.
**Financial write floor for `PATCH`** (`docs/development-cycle.md`): owner success; refusal for a
session that is not the household's owner (the `require_owner` dependency override
`test_investment_analysis_live.py::_member` already uses) and for another household's transfer;
refusal of a fee with excess precision before storage, and an exact round trip of a fee stored at
full `NUMERIC(20,4)` precision; an identical retry returning the original result with
`total_cost_source` recomputed rather than accumulated; no partially updated row after a late
refusal (a bad fee currency leaves provider, fee and cost as they were); a corrected remittance's
two legs still absent from spending, read through `v_transaction_reportable` (the roadmap's second
Phase 4.4 criterion).
**Commands:** `eval "$(scripts/test-db.sh up)"`; `(cd api && uv run pytest -q
tests/test_remittance_analysis.py tests/test_remittance_analysis_live.py tests/test_transfers_live.py
tests/test_ingest_transfers.py)` then `uv run ruff check . && uv run ruff format --check . &&
uv run mypy .`; shared types command; `(cd web && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** every figure the Remittances screen needs is an exact string that reconciles to the
confirmed transfers behind it, with each corridor-year's unattributed money and approximate-rate
count stated.

## Phase 4.4 Task 2 — corrections and analysis on screen

**Workspace/branch/base/target:** the active checkout /
`feature/p4-4-t2-remittance-analysis-screen` / `develop` / `develop`. **Depends:** Task 1.
**Contract:**

- **Transfer panel** (`web/app/transactions/transfer-panel.tsx`, which already renders the FX cost
  line). For a confirmed remittance it gains a correction control: provider text, explicit fee
  amount and its currency, and notes, saved with `PATCH /transfers/{id}` and reloaded from the
  response. A refusal shows the server's message; nothing is computed in the browser. A
  non-remittance transfer's panel is unchanged.
- **Remittances screen** (`/remittances`, one nav entry in the "Build & plan" group of
  `web/app/ui/app-shell.tsx`, following the existing compact-console page grammar the Phase 4.8
  screens use — `console-page-header`, `section-nav`, `data-table-region`, `metric-strip`).
  - One section per corridor, labelled by its currency codes, newest year first, with the
    per-provider table: provider, transfers, sent, fees, spread, total, effective %, average rate,
    closing on the corridor-year total row from the server's own `total*` fields.
  - The unattributed row is labelled as such and visibly outside the comparison, with the count of
    transfers still to label.
  - The cheapest-channel line states the provider and the potential saving when present, and states
    that a comparison needs two recorded providers when absent.
  - `approximateCount` is surfaced wherever non-zero, so a total resting partly on a
    carried-forward rate is never read as exact.
  - Loading, empty (no confirmed remittances), error and retry states. Wide tables scroll on
    phones. Every amount, percent and rate is a server string.

**Tests:** a corridor's provider table and its total row from server strings; the unattributed row;
the cheapest-channel line present and absent; an approximate-count notice; a provider-and-fee
correction from the transfer panel that PATCHes and reloads; a refusal message shown.
**Commands:** `(cd web && npx vitest run && npm run typecheck && npm run lint)`;
`scripts/verify-web.sh`. **Acceptance:** the owner reads, on one screen, what each provider has
cost them per corridor and year, sees which is cheapest once two are recorded, and can label a
remittance's provider or enter its fee without leaving the transaction it belongs to.
