# Slice 1.8 — the import asks: design

> **Historical numbering:** This delivered design is now mapped to Phase 1.5. Use the
> [canonical crosswalk](../../product/master-implementation-plan.html#historical-numbering-crosswalk)
> for the current sequence.

The decisions behind the [slice 1.8 plan](../plans/2026-09-10-slice-1-8-the-import-asks.md), taken
on 10 September 2026 against `develop` at `a3cf13a`, for two owner requests made that day: **an import
can create the account it needs**, and **an unclassified row should ask how to mark it**. The plan
is the execution path; this file is what is built and why, and wins where the two differ. The
design documents it changes are edited by the tasks that implement it (the plan's "Document edits
the tasks carry"); after that this file is the record of the reasoning.

## Why a new slice number

One owner request, one plan, one tracker row; neither part belongs to
a single existing slice (account resolution is 1.1's, classification 1.3's), and 1.1 is accepted —
reopening it would blur its acceptance record. The number is an identity, not a queue position:
1.8 ships next, ahead of 1.5–1.7, by owner request.

## What the survey found

| Capability | State | Where |
|---|---|---|
| Statement → account by salted hash | **Exists** | `routes/ingest._resolver` hashes the `account_number` capture through `accounts._account_number_identity` (sha256 of `ACCOUNT_HASH_SALT` + digits) and matches `account.account_number_hash`; the capture is `repr=False` and never echoed |
| An unresolved account | **A 422 dead end** | `pipeline._resolved` raises `AccountNotResolved` — "choose the account … or add it first" |
| `account_number_masked` / `account_number_hash`, unique per household | **Exists** | V3, `idx_account_number_hash` |
| Creating an account with a transient full number | **Exists** | `POST /accounts` `accountNumber` → mask + hash; the browser has to type the number |
| **A layout that captures `account_number`** | **Missing** | none of the four `db/seeds/*-layout.json` has the key, so every real import so far used the account picker and resolution-by-hash has never run on a real statement |
| A layout's product type | **Missing** | `statement_layout` has `institution_id` and nothing saying what kind of account it serves |
| A statement's currency | **Not captured** | no reserved `currency` key; normalization stamps the account's currency |
| The opening balance, in the ledger's frame | **Exists** | `reconcile.Verdict.declared_opening`, framed by `inverts_sign` (ADR-0010), flagged `opening_is_derived` when walked from the first row |
| A card's credit limit from its statement | **Exists** | 4a2's `normalize.card_facts`, reserved key `credit_limit` |
| Rule CRUD, preview, the unclassified pass | **Exists** | `routes/rules.py`: `POST /rules`, `POST /rules/preview` (count + 20-row sample), `POST /classify` (fills `unclassified` rows only, never overwrites) |
| A one-off category | **Exists** | `PATCH /transactions/{id}` stamps `user_manual` at 1.000 |
| A view of the rows with no category | **Missing** | `GET /transactions` has no `classified_by` filter; the review screen shows the proposal read-only, by 3a5's design |

## Part 1 — an import can create the account it needs

### Where the full number exists

In memory only, inside `POST /parse` and the new `POST /parse/account`, between extraction and
hashing — the frame it already lives in. It is never persisted, logged, put in an error or sent to
the browser; the browser only ever sees `****` plus the last four digits. So the browser **cannot**
create the account through `POST /accounts`, which needs the number typed: the service creates it
from the statement it re-reads. That is the whole reason a new route exists.

### The match key

Unchanged: the salted hash, compared server-side. Three additions, each gated on the owner's
confirmation — nothing resolves automatically on anything weaker than the hash
(`statement-ingestion.html` §3: "it never guesses which account a statement belongs to"):

1. **Last four.** The capture's last four ASCII digits. A capture with fewer than four is "no
   number stated" and keeps today's 422.
2. **Full number or mask.** A capture that passes `accounts._account_digits` and has more than
   four digits is a full number. One of exactly four digits, or one carrying mask characters (a
   card statement printing `XXXX XXXX XXXX 1234`), yields only its last four — a mask, never a
   hash, so two accounts ending alike never collide on `idx_account_number_hash`.
3. **Suggestions.** On no hash match, the proposal lists this household's accounts **with no stored
   hash** whose mask ends in the same four digits. An account with a hash is a different identity
   and is never suggested.

**Accounts made by hand without a hash are exactly the suggestions.** Confirming one *adopts* the
statement's identity onto it — backfill on confirmation — so its next statement resolves by hash.
There is no bulk backfill: nothing holds a full number to backfill from except a statement, and
`PATCH /accounts` with `accountNumber` stays the manual path.

**A card stores no hash.** `security-and-privacy.html` §1 allows a bank account "masked + salted
hash" but a card number "last 4 only". An account of type `credit_card` created here carries its
mask alone, so its statements are matched by suggestion and one click each time — today's
behaviour (the owner picks the card from the list) made shorter, not a regression. Adopting a
statement onto a card therefore writes nothing and answers 200: a hash is stored only when the
account's own type — the create body's, or the adopted account's — is not `credit_card` and
the capture is a full number.

### Flow and API shape

- `POST /parse`, dry or staging, with no `accountId`, a captured number and no hash match → **200**
  with `staged: false`, `accountId: null`, `accountProposal` set and `rows: []`; counts, period and
  reconciliation as usual. Nothing is written, whatever `stage` said. "No number stated" is still
  422.
- The analysis behind a proposal runs against a sentinel account (`UUID(int=0)`, the precedent
  `POST /rules/preview` sets), no committed rows, and the household's base currency as a stamp.
  The preview rows are withheld because their currency and dedupe hashes are provisional; counts,
  period and verdict depend on neither.
- `POST /parse/account` (multipart: `file`, `password?`, `layoutId?`, and exactly one of `account`
  or `accountId`) re-reads the statement, identifies its layout, extracts the capture, and either
  **creates** an account from `account` — an `AccountCreate` body with no identity fields — or
  **adopts** the statement's identity onto `accountId`. It stages nothing.
- The import continues by **re-checking the file with that `accountId`** through the existing,
  unchanged `POST /parse`. Creating and staging stay two acts, so a blocked staging never leaves a
  half-made account and a dry run still writes nothing.

`pipeline.parse` returns `account=None, staged=False` only for "a captured number, no hash match,
no `accountId`", and takes no new callback — the route builds the proposal from the analysis. The
upload-to-pages block and `create_account`'s INSERT become helpers both routes share; neither is
forked.

Rejected: creating the account inside `POST /parse` — it breaks "a dry run writes nothing"
(`api-contracts.html` §2), and on the staging path a blocked batch would leave an account behind.
Rejected: the browser carrying the hash to `POST /accounts` — the salted hash becomes a
client-supplied value, and a client could attach any identity to any account.

### Prefill — where each field comes from

| Field | Source | When the document lacks it |
|---|---|---|
| `accountNumberMasked` | `****` + last four | — (no proposal without it) |
| `accountType` | the new `statement_layout.account_type` | null |
| `institutionId`, `country` | the layout's `institution_id`; that institution's `country` | null |
| `currency` | the new reserved metadata key `currency`, upper-cased, **only** when it names a `currency` row | null |
| `openingBalance` | `Verdict.declared_opening`, framed — a card owed is negative | null |
| `openingBalanceDate` | the earlier of the period start and the oldest row's date | — |
| `openingBalanceNote` | "Declared opening balance of the statement for ‹start› to ‹end›", or "Derived from the first row's running balance of …" when `opening_is_derived` (I7) | — |
| `creditLimit` | the card fact `credit_limit` | null |
| `name` | the institution's short name, else the layout's name, then the mask — a suggestion | — |

A null is shown empty and a required one must be typed; nothing is defaulted silently. Every
currency, institution and country value comes from a row, never from code (ADR-0012).

**The opening balance date** is the earlier of period start and oldest row because V14/V18 refuse
any row dated before `opening_balance_date`, and a card statement lists installment rows under
their original purchase dates (`progress.md`, slice 1.1 run). The value stays the balance at
period start: `opening + Σ rows = declared closing` is the statement's own I8 identity, so
`assert_account_reconciles` holds at its first commit by construction.

**When the statement is not the account's earliest.** For a new account it is the earliest *held*.
Within one selection the screen offers creation from the file with the earliest period among files
sharing a mask. An older statement imported later is refused at commit by the existing anchor rule,
in its own words, and the owner re-anchors on Accounts — the existing path, unchanged here.

### Several statements of one new account in one selection

"Batch" in the request means the import screen's multi-file selection; `import_batch` is one per
document. The account is created once:

- **Server.** A create whose hash already exists is 409 naming that account, with
  `idx_account_number_hash` as the backstop for a race. A create that would store no hash (a card,
  a masked capture) is 409 when an account of the same type already carries the same mask.
- **Screen.** After a create or an adopt, every other file in proposal state with the same mask
  takes that account's id and is re-checked. Each file's summary names its account before its
  Stage button, so the owner sees the mapping before anything stages.

## Part 2 — an unclassified row asks

### Where the question lives: the transactions screen

A **"Needs a category"** view — `GET /transactions?classifiedBy=unclassified` — is the queue, and
choosing a category on an unclassified row asks the question inline. Chosen over:

- **The review screen.** Staged rows are not ledger rows: a rule created there cannot reach them
  (`POST /classify` reads `transaction`), the review vocabulary is exactly `accepted`/`rejected`,
  and 3a5 made the proposal read-only there on purpose. Carrying the question would take a
  staged-row reclassify route and a second categorisation control. The review screen instead
  **says** how many rows have no category and where they will be asked.
- **A dedicated queue.** A page, store or table holding what one filter already expresses. The
  filtered view reaches the 4,184 existing rows, not only rows imported from now on — ADR-0017's
  reasoning for transfers, unchanged.

### Rule or one-off — the owner chooses; the rule is the default

- **"Always mark rows like this"** (primary): a proposed, editable pattern, kind `contains`, the
  chosen category. `POST /rules/preview` shows how many rows it matches and how many of those have
  no category **before** anything is written. Confirming runs `POST /rules`, then
  `POST /classify?ruleId=‹new›`, which fills this row and every other **unclassified** row the rule
  decides — `user_rule` at 0.980 with the rule as provenance.
- **"Just this one"**: the existing `PATCH /transactions/{id}` — `user_manual` at 1.000.

Neither touches a row that already has a classification: `POST /classify` cannot overwrite one, and
the question appears only on unclassified rows. Re-categorising a classified row stays the existing
one-off; offering a rule there is `classification.html` §4's learn-from-correction, which the phase
plan puts in Phase 2.

**An earlier rule can claim a row first.** The apply runs the cascade as it stands, and user rules
are tried before aliases and priors, in `(priority, id)` order (`classify._first_matching`). So an
unclassified row the new rule matches is written by it only when no earlier-priority user rule
matches too. The preview's `unclassified` can exceed the apply's `changed` by exactly those rows:
the prompt reports `changed` after the write, and if the chosen row itself is claimed by an
earlier rule it says so rather than dropping the owner's choice silently.

**The proposed pattern** (one row; §4's longest-common-substring over many rows is Phase 2): the
longest contiguous run of the row's description (clean, else raw) that contains no digit and none
of `/ * # :`, trimmed, at least three characters long. It therefore always matches its own row and
**never carries a digit run**, which keeps an account or card number out of a stored pattern. No
such run → the owner types one.

### What is reused, and what is added

Reused unchanged: `POST /rules`, `PATCH /transactions/{id}`, the cascade, the transactions screen's
category select. Added, each a few lines: a `classifiedBy` filter on `GET /transactions`; an
`unclassified` count on `POST /rules/preview`; a `ruleId` scope on `POST /classify`, so the click
writes what it previewed rather than every other rule's backlog too. **No schema change**, no new
table and no new endpoint for part 2.

## Where the documents disagree

1. **`ui-information-architecture.html` §3 and §4, `classification.html` §1 and `prd.html`
   §5.5's cascade list (step 7)** send unclassified rows to "the review queue", and §3's mockup
   categorises inline during import review. Delivered code already differs (3a5); this plan puts
   the question on the transactions screen. No ADR decides the location — ADR-0003 mentions
   unclassified rows landing "in the review
   queue" as its *cost*, not its decision — so this is a documentation fix, not a superseding ADR.
   Open decision 2.
2. **`security-and-privacy.html` §1 against `POST /accounts`.** A card number is "last 4 only",
   yet `POST` and `PATCH /accounts` hash any `accountNumber` whatever the `accountType`. The new
   path never hashes a card; the existing one is raised, not folded in. Open decision 3.
3. **`api-contracts.html` §2 lists `PATCH /transactions/{id}` twice** (already in `progress.md`'s
   raised list); the second entry describes a rule-creating PATCH that does not exist and is not
   added here. 8b rewrites it.
4. **`statement-ingestion.html` §3** describes resolution by hash as the normal path; no seeded
   layout captures a number, so it has never run on a real statement. Intent is unchanged — 8a3
   makes it true.
