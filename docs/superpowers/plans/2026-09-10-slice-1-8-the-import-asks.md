# Slice 1.8 — the import asks: accounts from statements, and unclassified rows

> **Historical numbering:** This delivered plan is now mapped to Phase 1.5. Use the
> [canonical crosswalk](../../product/master-implementation-plan.html#historical-numbering-crosswalk)
> for the current sequence.

Execution map, written against `develop` at `a3cf13a` on 10 September 2026, for two owner
requests made that day while the ledger was being rebuilt from statements (`progress.md`, "What
the second rebuild cost"): **an import can create the account it needs**, and **an unclassified
row should ask how to mark it**. The phase delivery plan gains a §1.8 entry carrying this slice's
outcome and acceptance when 8a1 is dispatched; this file owns how it is executed.

**The design is in [the slice 1.8 spec](../specs/2026-09-10-slice-1-8-the-import-asks.md)**: why
a new slice number, what the survey of the tree found, Part 1 (where the full number exists, the
match key, flow and API shape, prefill, create-once) and Part 2 (where the question lives, rule or
one-off, the proposed pattern). Task blocks cite its headings; the spec wins where they differ.

## Sequence and migrations

`8a1 → 8a2 → 8a3`; `8b` is independent and may ship before, between or after them. One
implementation agent at a time in the checkout: dispatch after whatever is in flight there has
merged, and rebase each branch onto the current `develop` before its push and again before its
merge. PR titles carry `[P1.8]`.

| Migration | Task | Does |
|---|---|---|
| `V22__statement_layout_account_type.sql` | 8a1 | `statement_layout.account_type account_type` (nullable) — the kind of account a layout's statements belong to |

V22 is the next free number at the time of writing; take the next free one if another lands first.
A table change edits the migration, `schema.md` and `data-model.html` in one commit.

## Document edits the tasks carry

| Task | Edits |
|---|---|
| 8a1 | `schema.md` (V22); `data-model.html` (the layout's account type; a card's identity is its mask alone); `api-contracts.html` (`POST /parse`'s proposal, `POST /parse/account`, `accountType` on layouts, the `currency` reserved key); `statement-ingestion.html` §3 ("stops and asks" gains "offering an account prefilled from the statement, or an existing one to adopt its identity") |
| 8a1, by the orchestrator at dispatch | the phase plan's §1.8 entry; `progress.md`'s slice row and task table |
| 8b | `api-contracts.html` (the three contracts, and the second `PATCH /transactions/{id}` entry rewritten to point at this flow); per open decision 2, `docs/product/prd.html` §5.5 step 7, `docs/product/ui-information-architecture.html` §3 and §4, and `docs/design/classification.html` §1 |

## Documents this plan disagrees with

The reasoning is in the spec's "Where the documents disagree".

1. `prd.html` §5.5, `ui-information-architecture.html` §3–§4 and `classification.html` §1 send
   unclassified rows to "the review queue" — a doc fix carried by 8b (open decision 2).
2. `POST`/`PATCH /accounts` hash a card number, which `security-and-privacy.html` §1 forbids —
   open decision 3.
3. `api-contracts.html` §2 lists `PATCH /transactions/{id}` twice — 8b rewrites the second.
4. `statement-ingestion.html` §3 assumes resolution by hash, which no seeded layout supports yet —
   8a3 makes it true.

## The slice's acceptance

- **Part 1.** A statement for an account OneLedger does not hold is offered a prefilled account;
  confirming creates it once, however many of its statements are selected; its statements stage
  and commit `matched`; a hand-made account without an identity is suggested and, once adopted,
  resolves by itself. No full number appears in any response, log line or column — only as a hash,
  and for a card not even that.
- **Part 2.** Every unclassified row is reachable from one view; answering "always" creates a
  `user_rule` that marks it and every unclassified row it matches, with the count shown first;
  "just this one" stamps `user_manual`; no row that already had a classification changes.

Both real-data runs are local, on the owner's machine (8a3 for part 1, 8b's acceptance for part 2).
No real figure, filename, pattern or number enters the repository on any branch.

## Open owner decisions

Each has a default, and dispatch proceeds on it unless the owner says otherwise.

1. **Slice number.** Default: new slice 1.8, shipping next. Alternative: follow-ups `1.1e`, `1.3c`.
2. **Where unclassified rows are asked.** Default: the transactions screen, with the documents in
   item 1 above fixed by 8b. Alternative: the review screen, which needs a staged-row reclassify
   route and roughly 400 more lines.
3. **Card hashes already written by `POST /accounts`.** Default: a separate `fix/` branch makes
   that path store the mask alone for `credit_card` and decides what happens to any card hash
   already stored. Alternative: fold it into 8a1.
4. **Acknowledging the opening balance.** Default: `opening_balance_confirmed_at` stays null on an
   account created from a statement, as on every created account; the owner acknowledges on
   Accounts. Alternative: the confirming click stamps it when the figure is the statement's own,
   unedited and not derived.
5. **The layout's account type.** Default: the V22 column, so the type is prefilled. Lazier
   alternative: no migration, and the owner picks the type on every proposal.

## Not in this plan

- Checking a captured statement currency against the resolved account's currency — a sound guard,
  its own task.
- Re-anchoring an account when an older statement arrives after its creation — the existing
  Accounts path.
- A card whose statement opens in credit: `AccountCreate` refuses a positive liability opening, so
  such a statement cannot seed its account until that rule is revisited.
- The rule prompt on re-categorising a classified row, and pattern suggestion across many rows —
  Phase 2's learn-from-correction.
- Card accounts storing a hash through `POST`/`PATCH /accounts` —
  `fix/card-accounts-store-mask-only` (open decision 3); the orchestrator adds it to
  `progress.md`'s raised list when 8a1 is dispatched.
- An Overview attention count linking to "Needs a category" — once 4b3 exists.
- Freeing a cancelled batch's file (`progress.md`, raised) — slice 1.7.

---

## Task 8a1 — a statement proposes its own account

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-8a1-account-from-statement-api`
**Base and PR target:** `develop`
**Migration:** V22. **Depends on:** nothing unmerged.
**Forecast:** ~330–400 production (V22 ~15, `layouts.py` ~25, `pipeline.py` ~30, `ingest.py`
~170–230, `accounts.py` ~30–60; comments count), ~600–750 test — worst case ~1,150 of 1,600.

**Outcome.** An unresolved statement answers with a prefilled account instead of a 422; one request
creates it, or adopts the statement's identity onto an existing account, from the statement itself.

**Files.** Create V22, `db/tests/0020_statement_layout_account_type.sql` and
`api/tests/test_account_from_statement_live.py`; modify `api/oneledger/routes/{layouts,ingest,accounts}.py`,
`api/oneledger/ingest/pipeline.py`, `api/tests/{test_layouts,test_ingest_pipeline}.py`; regenerate
`web/types/api/types.gen.ts`. Docs per the table above.

**Contracts.**

```text
POST/GET /layouts      + accountType: account_type | null     (the DB enum is the vocabulary)
RESERVED_METADATA_KEYS + "currency"                            (exactly one capture group)
ParseResponse          accountId: UUID | null — null exactly when accountProposal is set
                       accountProposal: { accountNumberMasked,
                         suggestedAccounts: [{ id, name, accountNumberMasked }],
                         name, accountType, institutionId, country, currency,
                         openingBalance, openingBalanceIsDerived, openingBalanceDate,
                         openingBalanceNote, creditLimit }       (money as strings)
POST /parse/account    multipart file, password?, layoutId?, and exactly one of
                         account   = JSON AccountCreate, no accountNumber/accountNumberMasked → 201 AccountRow
                         accountId = an account to adopt this statement's identity        → 200 AccountRow
```

**Invariants.** The spec's Part 1 rules, exactly: a hash is stored only when the account's own type
(the create body's, or the adopted account's) is not `credit_card` and the capture is a full number
(more than four digits); a proposal writes nothing on either path; `AccountCreate`'s rules hold.

**Security.** `require_owner()` and the Origin check on the new route; `accountId` and every
suggestion are this household's (404 otherwise). The capture stays `repr=False` and reaches no
response, error, log or column except as hash or mask. Unset `ACCOUNT_HASH_SALT` is 503, as today.

**Required test behaviours (failing first).**
- An unknown number, dry or `stage=true`: 200, a proposal, `rows` empty, and nothing written — no
  account, document, batch, staged row or `fx_rate`. The number is in no body, `detail` or `caplog`.
- Each prefill field from its source; absent sources give null; a derived opening says so; a
  card's opening is negative; suggestions are only hashless accounts with the same last four.
- Create: a bank account gets mask and hash and the same file then resolves by itself; a card gets
  its mask only; a second create is 409 naming the first; identity fields, or neither/both of
  `account` and `accountId`, are 422. Adopt fills a hashless account; a different last four is 409;
  adopt onto a card stores no hash and is 200; a four-digit capture yields a mask, never a hash.
- 401, 403 and a foreign Origin on the new route; `accountType` round-trips, an unknown one is 422.

**Validation.** `/verify` in full, and V22 applied onto a copy of the standing ledger.

**Acceptance.** Through the API alone, a synthetic statement for an unknown account is proposed,
created and re-checked into a stageable summary, its number visible nowhere but as a mask.

---

## Task 8a2 — the import screen offers the account

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-8a2-account-from-statement-screen`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 8a1 merged.
**Forecast:** ~290–370 production (`account-proposal.tsx` sized like `transfer-panel.tsx`, 351;
`import-screen.tsx` ~70), ~350–450 test — worst case ~820 of 1,600.

**Outcome.** A file whose statement names an account OneLedger does not hold shows that account,
prefilled; the owner confirms it, or picks the suggested existing one, and the file carries on to
its summary and Stage button with nothing retyped that the statement stated.

**Files.** Create `web/app/import/account-proposal.tsx` and its test; modify
`web/app/import/import-screen.tsx` and its test; export `ACCOUNT_TYPES` from
`web/app/accounts/accounts-screen.tsx` rather than copying it.

**Contracts.** `Candidate` gains its own `accountId`, sent instead of the screen-wide select when
set. A file answered with `accountProposal` shows no Stage button and a panel with every field
editable (null ones empty, required ones marked). "Create account and continue" (primary) posts
`account` to `POST /parse/account`; a suggestion ("This is ‹name› ‹mask›") posts `accountId`.
Success sets `accountId` on that file and every proposal-state file with the same mask, then
re-checks them in sequence — the form sits on the earliest-period file of a mask (spec, Part 1). The
panel loads `/institutions` itself, leaving the workspace's request sequence unchanged.

**Invariants / security.** TypeScript formats money and does no arithmetic on it — the opening
balance and credit limit are strings in and out, displayed through `formatMoney` with the
catalogue's currency row, never a code union (ADR-0012). The browser never holds more of the number
than the mask; nothing goes to `localStorage` or a URL. The UI hides nothing the API would allow and
allows nothing it would refuse.

**Required test behaviours (failing first).**
- A proposal renders its prefill; a null required field blocks "Create" until typed.
- Create posts `account` with money as strings and no identity field, then re-checks the file with
  the new `accountId` and shows the ordinary summary and Stage.
- Two selected files with one mask produce one create; the second re-checks against the account
  the first made.
- A suggestion posts `accountId`; a 409 shows the service's own sentence.
- The earliest-period file carries the form; the password is re-sent and still cleared on staging.

**Validation.** `/verify`, and `npm run build`.

**Acceptance.** From the import screen alone, a statement for an unknown account becomes an account
and a staged batch, with only the fields the statement did not state typed by hand.

---

## Task 8a3 — the layouts read the account's identity (local)

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-8a3-layout-identity-captures`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 8a1 and 8a2 merged. Shares `db/seeds/card-layout.json` with 4a3: whichever lands
second rebases, and neither removes the other's keys.
**Forecast:** ~0 production, ~40 test; the profiles are `.json` and uncounted.

**Outcome.** Each of the four seeded profiles declares its `accountType` and — where its
statements print them — captures `account_number` and `currency`, measured against the real
statements on the owner's machine exactly as 4a3 measures the card facts. The run then proves
part 1 against real files.

**Files.** Modify `db/seeds/{card,account,passbook,ledger}-layout.json`, `db/seeds/README.md`
(what each new capture reads, and that a card capture may be masked), and
`api/tests/test_seeded_layouts.py`.

**Contracts.** Each new pattern has exactly one capture group. A masked card capture is expected
and correct. Committed profiles still carry no `institutionId` (`db/seeds/README.md`); the owner
sets it on the local re-post so institution and country prefill.

**Invariants / security.** A pattern is generic statement vocabulary: never a literal account or
card number, the owner's name, or any figure from a real statement. No real file, extract or
figure enters the repository; the run is recorded in `progress.md` as slices 1.1 and 1.2 were.

**Required test behaviours (failing first).**
- Every seeded profile still validates through `LayoutCreate`.
- No seeded pattern contains a literal run of five or more digits.

**Validation.** `/verify`. Then, locally: re-post the four profiles on the working ledger
(version + 1 each; imported rows keep their version); import one new statement per existing
account — each resolves by hash, or is suggested once, adopted, and resolves by hash on the next.
Then, on a scratch stack with its own volumes (`development-cycle.md` §5, never the working
ledger), import one statement per account from empty.

**Acceptance.** On the scratch stack every account is proposed, created once from its earliest
statement, and its statements commit `matched`; on the working ledger no duplicate account is
created and no full number is stored.

---

## Task 8b — an unclassified row asks

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-8b-unclassified-asks`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** nothing unmerged; independent of 8a.
**Forecast:** ~260–320 production (API ~30; `classify-prompt.tsx` ~160–220, screens ~70),
~530–650 test — worst case ~970 of 1,600.

**Outcome.** The owner opens "Needs a category" on Transactions, picks a category for a row, and is
asked whether to mark every row like it — seeing how many first — or just this one.

**Files.** Modify `api/oneledger/routes/{transactions,rules}.py` and `api/tests/{test_transactions,
test_rules,test_rules_live}.py`; create `web/app/transactions/classify-prompt.tsx` and its test;
modify `web/app/transactions/transactions-screen.tsx`, `web/app/import/review-screen.tsx` and their
tests; regenerate `web/types/api/types.gen.ts`. Docs per the table above.

**Contracts.**

```text
GET  /transactions?classifiedBy=<classified_by>       filter; an unknown value is 422
POST /rules/preview   → { affected, unclassified, sample }
                        unclassified = matched rows whose classified_by is 'unclassified'
POST /classify?ruleId=<id>[&preview=true]
                      the cascade runs as today; only rows whose answer is that rule are
                      written and counted; a rule that is not this household's, or inactive, is 404
```

The screen behaves as the spec's Part 2 describes; the prompt fetches the preview when it opens.
`unclassified` can exceed the apply's `changed` by exactly the rows an earlier-priority user rule
claims; the prompt reports `changed` after the write, and says so when the chosen row is one. The review
screen states how many of its rows have no category and that Transactions will ask about them.

**Invariants.** Rules never silently change past data: the apply writes only `unclassified` rows,
only with the new rule's answer, and advances only that rule's counter. The pattern never holds a
digit. No money arithmetic in TypeScript. `origin` stays `user_created`.

**Security.** `require_owner()` on every route, unchanged; `ruleId` is this household's or 404.

**Required test behaviours (failing first).**
- The filter returns only unclassified rows (unknown value 422); the preview's `unclassified` is
  right and writes nothing; `ruleId` writes only rows that rule decides — not one an earlier-priority
  user rule claims, never a classified one — and a foreign rule is 404.
- A category on an unclassified row opens the prompt; on a classified row it PATCHes as today.
- The pattern is a digit-free substring of the row's description; "Create" is disabled once the
  edited pattern no longer occurs in it.
- Both counts show before any write; the default creates, applies and reloads; "Just this one"
  sends the existing PATCH; a refusal shows the service's sentence; review shows its count.

**Validation.** `/verify`, and `npm run build`.

**Acceptance.** Locally, on the working ledger: the unclassified count falls by exactly the
`changed` each apply reports, and no row that had a classification before the run has a different
one after it (compared by id and `classified_by`).
