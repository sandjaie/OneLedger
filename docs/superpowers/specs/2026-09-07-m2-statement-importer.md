# M2 — the statement importer: specification

The contracts, table semantics and architecture decisions M2 is built against. The execution map is
[the M2 plan](../plans/2026-09-07-m2-statement-importer.md), which points here rather than
restating any of it.

Per `CLAUDE.md`, this file **specifies** and the plan **executes**: where the two disagree, this
one wins and the plan is corrected. It is reviewed once, not re-read on every task.

**Section numbers are preserved from the plan they were extracted from** — §4.6 is still §4.6, so
every existing cross-reference in the plan, the PRs and `progress.md` still resolves. They are
deliberately not renumbered.

---

## 4. Architecture decisions

Grounded in the current tree, which the implementer should read before starting each task.

### 4.1 New module layout

```text
api/oneledger/
  money.py                    # Task 5: freeze helpers extracted from routes/transactions.py
  ingest/
    __init__.py
    words.py                  # Task 3: pdfplumber words -> banded rows; scanned detection
    engine.py                 # Task 3: matches() / extract() / propose(); quirk handlers
    normalize.py              # Task 4: dates, signs, currency, description cleanup
    dedupe.py                 # Task 4: fingerprint + near-duplicate
    reconcile.py              # Task 4: the four verdicts
    storage.py                # Task 5: blob write, filename masking
    pipeline.py               # Task 5: orchestration + the staging transaction
  schemas/
    __init__.py
    staging.py                # Task 4: StagedTransaction (api-contracts.html §4 names this path)
  routes/
    layouts.py                # Tasks 2 and 3
    ingest.py                 # Tasks 5 and 6
```

`api/oneledger/ingest/` **may not import from `api/oneledger/routes/`** and may not open a
database connection except in `pipeline.py`. Extraction, normalization, dedupe and reconciliation
are pure functions over values, unit-testable with no database (`testing-and-data-quality.html`
§2, "Parsers may not import from lib/queries").

Web:

```text
web/app/import/
  page.tsx                    # Task 7
  import-screen.tsx           # Task 7
  layout-mapper.tsx           # Task 8
  review-screen.tsx           # Task 9
  diagnostics-panel.tsx       # Task 9
  __tests__/*.test.tsx
```

`web/app/workspace.tsx` gains an `"import"` section: the `Props["section"]` union,
`sectionFromPath()` and the nav — a `next/link` entry labelled **Import** pointing at the
`/import` route, placed between Accounts and Transactions, following the existing `Link` entries
exactly. The disabled "Plans" nav button stays as it is.

### 4.2 Route surface added by M2

All under `require_owner()`, all money as decimal strings, all field names **camelCase** on the
wire (matching `routes/transactions.py` and `routes/accounts.py`).

| Route | Task | Does |
|---|---|---|
| `GET /layouts` | 2 | The household's saved layout profiles, newest version per name |
| `POST /layouts` | 2 | Saves a profile. Re-saving an existing `name` creates `version + 1` |
| `POST /layouts/propose` | 3 | Multipart PDF → a draft profile: header row, column bands, sample rows. With an optional `draft` field, previews that edited draft against the file instead of auto-proposing. Persists nothing in either mode |
| `POST /parse` | 5 | Multipart PDF + `stage` flag. Identify, extract, normalize, dedupe, reconcile; stage only when `stage=true` and the verdict allows it |
| `GET /import/{batch_id}` | 6 | The batch, its verdict and its staged rows grouped for review |
| `PATCH /import/{batch_id}/rows` | 6 | Bulk row decisions: accept, reject, categorize |
| `POST /import/{batch_id}/commit` | 6 | One database transaction: staged accepted rows become `transaction` rows, then the batch re-verifies and rolls back whole on mismatch |

`GET /documents/{id}/original` is **out of scope** — the document library is M10. The review
screen shows `description_raw`, page and row number, which is what M2's traceability gate needs.

### 4.3 The parse contract: dry run then stage

`POST /parse` takes the file, an optional `layoutId`, an optional `accountId`, and a boolean
`stage` (default **false**).

- `stage=false` writes **nothing** and returns the full summary: identification result,
  reconciliation verdict, row counts by proposed status, and the first 50 normalized rows for
  preview. This is the roadmap's "nothing written until Stage batch".
- `stage=true` performs the staging transaction described in §5.4 and returns the same summary
  plus `batchId`.

The file crosses localhost twice. That is acceptable at statement sizes and avoids a
server-side scratch store with its own lifetime, ownership and cleanup problems; revisit only if
measured latency makes it unacceptable.

### 4.4 The extraction engine

One engine, driven entirely by a profile row. `matches()`, `extract()`, `propose()` per
`statement-ingestion.html` §4. Extraction rules:

1. `page.extract_words()` gives words with `x0`, `x1`, `top`, `bottom`, `text`. Group words into
   **physical lines** by `top` within a tolerance (the profile's default; propose from the modal
   line height). Never sort by reading order across columns.
2. Assign each word to the column whose `[x_min, x_max)` band contains the word's **horizontal
   midpoint** `(x0 + x1) / 2`. Midpoint, not `x0`: a right-aligned amount that overhangs its band
   on the left otherwise lands in the description column. A word whose midpoint falls in no band
   is dropped from `fields` but stays in `raw_text`.
3. Within a column, join words by ascending `x0` with a single space. **This is what makes the
   space-less producer work** (M3's layout D) and it costs nothing here.
4. A line matching any `skip_line_patterns` entry is dropped before row assembly.
5. Row assembly follows `row_start_rule`. `has_date_and_amount`: a line starts a row when its
   date column parses under `date_format` **and** its amount column parses as a number.
   `has_date`: the date alone starts a row. Any other line, when `continuation_rule` is
   `no_date_and_no_amount`, appends its description column to the previous row's description
   with a single space; with no continuation rule it is dropped and counted as unparsed.
6. `raw_text` is the whole physical line, kept verbatim, for the diagnostics panel.

**Quirk `filter_script:arabic`.** Before line grouping, drop any word whose text contains a
character in `U+0600–U+06FF`, `U+0750–U+077F`, `U+08A0–U+08FF`, `U+FB50–U+FDFF` or
`U+FE70–U+FEFF`. Dropped words are counted and the count is reported in the parse summary
(Invariant 7 — a filter that silently removes content is indistinguishable from a parser bug).

**Quirk `continuation:no_date_and_no_amount`.** As rule 5 above. Recorded as a profile flag so
that the vocabulary, not the layout, is what grew.

The quirk vocabulary is **exactly these two** in M2. A third is a considered addition with the
owner's agreement, not a reflex (ADR-0012).

**Reserved metadata keys.** `metadata_patterns` is a generic capture map, with three reserved
keys the pipeline understands: `account_number` (sensitive and transient — Task 3),
`period_start` and `period_end` (the statement period, parsed under the profile's
`date_format`). When the period keys are captured they are authoritative: they gate row dates
(a parsed date outside the period fails its row) and window two-digit years. When absent, the
period is **derived** from the parsed row dates and labelled derived (I7) in the parse summary
and on `import_batch`. Because windowing two-digit years against a derived period is circular,
**a profile whose `date_format` has a two-digit year must capture both period keys — enforced
at save (422)**. Every reserved capture takes exactly one capture group, enforced at save.

**Scanned detection.** A page whose `extract_words()` returns zero words is a scanned page.
`extract()` reports `has_text=false` and the parse is refused with a message saying scanned PDFs
are not supported, never garbage rows. The full M3 refusal UX is out of scope here.

### 4.5 `propose()` — auto-proposal

Given the pages of an unrecognised PDF:

1. Find the **header row**: the physical line whose words match the most entries in a synonym
   table, requiring at least a date synonym and one amount synonym. The synonym table is
   **module-level data in `engine.py`**, grouped by field, case-insensitive, whitespace- and
   punctuation-insensitive: `booked_date` ← date, tran date, transaction date, txn date, value
   date, posting date; `description` ← description, narration, particulars, details, remarks;
   `reference` ← ref, ref no, reference, chq no, cheque no, chq./ref.no.; `amount` ← amount;
   `debit` ← debit, withdrawal, withdrawal amt, dr, debits; `credit` ← credit, deposit, deposit
   amt, cr, credits; `running_balance` ← balance, closing balance, running balance. **No entry
   in this table is a bank name.**
2. Propose a band per matched header word: `x_min` is the midpoint between this word's `x0` and
   the previous word's `x1`; `x_max` the midpoint between this word's `x1` and the next word's
   `x0`. The first band extends to the page's left text margin, the last to the right margin.
3. Infer `sign_convention`: `debit_credit_columns` when both a debit and a credit header matched;
   otherwise `cr_suffix` when any sampled amount cell ends in `CR`/`DR` (case-insensitive);
   otherwise `signed`.
4. Infer `date_format` by trying `dd/MM/yyyy`, `dd-MM-yyyy`, `dd MMM yyyy`, `dd/MM/yy`,
   `yyyy-MM-dd` against the sampled date cells and keeping the one that parses every sample
   unambiguously; propose nothing rather than guess when two formats both parse every sample.
5. Infer `chronological_order` from the sampled dates.
6. Propose `fingerprints` as up to three distinctive lines from page 1 that are not the header
   row. The digit screen runs on a **normalized** copy of the line — every separator that
   formats an identifier (whitespace, `-`, `–`, `.`, `/`) removed between digits first — and
   rejects any line whose normalized form contains a digit run of length ≥ 4. `123-456-789`
   normalizes to a 9-digit run and is rejected; checking each raw 3-digit group independently
   would wave it through, and a formatted full account number must never become a stored
   fingerprint (the never-store list is absolute).
7. Return the draft **plus the first 20 rows extracted with it**, so the UI can show the parse
   live. `propose()` persists nothing and never invents a balance anchor: the draft's balance
   patterns are empty and the owner supplies them, which the `has_a_balance_anchor` CHECK then
   enforces at save.

### 4.6 Reconciliation (Invariant I8)

`reconcile.py` returns exactly one verdict, computed on **native signed ledger amounts** —
after `inverts_sign` has been applied to the rows *and* to the declared balances, so the identity
holds in one frame. A balance that stated its own direction is the one exception, added by Task
4d: `100.00 Dr` is an overdraft whatever the amount column does, so the suffix is read and
`inverts_sign` is then withheld from that figure. Applying both would reverse the statement rather
than reframe it — the same reason `normalize.py` leaves a row alone whose amount stated its
direction. All balance arithmetic runs in **chronological order**: when the
profile's `chronological_order` is `descending`, the rows are reversed first, because the
running-balance identity `balanceₙ = balanceₙ₋₁ + amountₙ` reads the *previous chronological*
row, and applying it in display order on a descending statement flags every valid row as a
break. Reported row numbers always reference document row numbers regardless of the
evaluation order:

```text
opening_ledger + Σ(amount_ledger) == closing_ledger
```

For a card layout with `inverts_sign = true`: a declared "previous balance 4,500.00" (owed) is
`-4500.0000` in the ledger, a purchase presented as `310.00` is `-310.0000`, a declared "new
balance 4,810.00" is `-4810.0000`, and `-4500 + -310 = -4810` reconciles. Getting this frame
wrong produces a batch that is internally consistent and entirely wrong, which is why it is
stated here rather than left to the implementer.

Verdicts:

- `matched` — the identity holds exactly (both sides are `Decimal`; there is no tolerance).
  For a running-balance-only layout (no closing pattern), the **chronologically last row's
  stated running balance is the terminal balance**: every row-to-row step must hold, and that
  terminal value plays the declared-closing role everywhere downstream — it is what staging
  records on `import_batch` and what the commit verifies against (§4.9), so the verification
  never receives a null.
- `mismatched` — it does not. Carries `declared_closing`, `computed_closing`, `delta`,
  `first_divergent_row` and `candidate_rows`. `first_divergent_row` is the first row at which
  the running total departs from the stated running balance — it exists **only when the layout
  has a running-balance column**; with a closing anchor alone no algorithm can honestly name
  *the* divergent row, because any single row could absorb the delta (I7 forbids inventing one).
  In that case `first_divergent_row` is `None` and `candidate_rows` lists the rows whose amount
  equals the delta exactly (the dropped-row case; empty for a corrupted amount), and the
  message says row-level evidence is unavailable. **Blocks either way.**
- `running_balance_break` — the layout has `has_running_balance` and no declared closing;
  `balanceₙ = balanceₙ₋₁ + amountₙ` fails at a row. Carries that row number, the previous
  balance, the amount and both the expected and stated balances. **Blocks.**
- `unavailable` — the layout's configured balance evidence did not extract. **Blocks in M2.**
  The `has_a_balance_anchor` CHECK means every saved layout declares a closing pattern or a
  running-balance column, so `unavailable` at parse time is never "this statement has no
  balances" — it is a shifted band, a changed label or a parse regression, and letting it
  through would commit rows that bypassed reconciliation entirely. The message says the
  configured evidence failed to extract and points at remapping the layout. A genuinely
  anchorless statement source, if one ever exists, is a separate explicit case for the
  milestone that meets it — not this value. (The **draft-preview** in the mapper is the one
  place `unavailable` renders without blocking anything: an auto-proposed draft has no anchors
  yet, and nothing is being staged.)

**Reconciliation runs immediately after normalization, before FX enrichment.** Running the cheap,
purely local, blocking check first means a doomed batch costs no network round trip and no rate
rows, and the verdict recorded on `import_batch` is identical either way — the identity is
evaluated on native signed ledger amounts, which enrichment does not touch.
`statement-ingestion.html`
§1 and `architecture.html` §5 now both document this order, so it is no longer a deviation and
needs no note in a PR.

### 4.7 Dedupe

Three layers, per `statement-ingestion.html` §6.

- **Layer 1 — document checksum.** `sha256` of the file bytes, uniquely indexed on
  `(household_id, sha256)`. Checked before parsing; an identical file is rejected with the date
  of the earlier import and its batch id named. Free, and catches the common case.
- **Layer 2 — transaction fingerprint.**
<!-- markdownlint-disable-next-line MD013 -->
  `sha256("|".join([account_id, booked_date.isoformat(), f"{amount:.4f}", normalise_for_hash(description_raw), disambiguator]))`
  where `normalise_for_hash` uppercases, collapses whitespace and strips punctuation, and
  `disambiguator` is `reference_raw` when the profile maps a reference column and the value is
  non-empty, **otherwise the occurrence index within the day, within this document** — the nth
  identical `(date, amount, normalised description)` among this statement's own rows, counted
  from 0 in statement order. Never counted against rows already committed: a statement is a
  stable, self-contained ordering of its day, so the same transaction carries the same index in a
  Jul–Aug and an Aug–Sep statement and the hashes collide — which is exactly how the overlap is
  recognised. An offset by committed rows would hand the overlapping row index 1 while the
  committed copy holds index 0, and August would import twice. And never a raw row ordinal within
  the document either — an ordinal differs between two statements covering the same day even when
  the day's identical rows are the same.
  A row whose hash already exists in `transaction` is staged with `status='duplicate'` and
  `duplicate_of_transaction_id` set, and is excluded from import by default. **Accepting a
  duplicate is a confirmed-distinct override**: V10's unique
  `(household_id, account_id, dedupe_hash)` index means the colliding hash cannot commit
  unchanged, so the accept endpoint recomputes and stores the row's hash with the occurrence
  index advanced to the next free index among committed collisions — deterministic, stable, and
  it keeps future overlap detection intact (a later statement carrying both occurrences hashes
  them 0 and 1 and recognises both as duplicates). The unique index remains the final guard;
  a commit-time collision still fails loudly rather than silently skipping.
- **Layer 3 — near-duplicate warning.** Same account, same amount, `booked_date` within ±3 days,
  description token-set similarity ≥ 0.85 (a pure-Python token-set ratio over the normalised
  descriptions; no new dependency). Staged as `status='near_duplicate'`. **Warns, never blocks** —
  it catches a genuinely double-charged subscription as readily as a re-import, and only the owner
  can tell those apart.

Manual rows created by M1b use their own occurrence-index formula and are not expected to collide
with imported rows; layer 3 is what surfaces that overlap. Do not change the M1b formula.

### 4.8 FX enrichment with fetch-on-missing

Today `routes/transactions.py::_rate_leg` refuses a save when a rate is missing, and
`POST /fx/backfill` is the only writer of `fx_rate`. M2 adds fetch-on-missing without a second
implementation of either:

1. Extract `POST /fx/backfill`'s body — `reporting_config` → `terminal_anchor` → `fetch_range` →
   `to_rows` → `upsert_rates` — into a plain function in `api/oneledger/routes/fx.py` (or a new
   `api/oneledger/fxrange.py` if the import graph reads better), called by both the route and the
   pipeline. The route's future-date and ordering guards stay on the route.
2. **Only on the `stage=true` path.** A dry run (`stage=false`) skips the FX step entirely — no
   fetch, no upsert, no `fx_rate` row: the preview shows native amounts only, and "a dry run
   writes nothing" includes reference data. When staging, the pipeline computes the batch's
   distinct booked dates, checks which lack a stored rate for the pair it needs, and fetches
   **one contiguous range** covering the gaps before the staging transaction opens.
3. If the fetch fails or leaves a date uncovered, **the import is refused** with the uncovered
   dates named. Never a fallback to `1.0`, never today's rate, never an interpolation
   (`multi-currency-and-fx.html` §3).
4. A carried-forward rate stamps `fx_rate_is_carried = true` and the earlier `fx_rate_date`
   (Invariant I7 — and the `carried_rate_dates` CHECK on `transaction` enforces it at commit).

### 4.9 The commit

`POST /import/{batch}/commit` calls a single `SECURITY DEFINER` Postgres function
`commit_import_batch(p_batch_id UUID)` created in **V14** (api-contracts.html §3: "if a failure
halfway through would leave the ledger inconsistent, it belongs in one function in the database").
The function, in order:

1. `PERFORM assert_is_owner();`
2. Locks the household row `FOR UPDATE` — it is a writer of frozen amounts (spec B3.5).
3. Refuses unless the batch belongs to the current household and its `reconciliation_status` is
   `matched` — the only status a staged batch can carry in M2, since every other verdict blocks
   staging (§4.6, §5.5); the function still checks rather than assumes.
4. `INSERT INTO transaction (...) SELECT ... FROM staged_transaction WHERE import_batch_id = $1
   AND status = 'accepted'`, carrying `source='statement_import'`, `source_document_id`,
   `source_page_number`, `source_row_number`, `import_batch_id`, `statement_layout_id`,
   `layout_version` and `dedupe_hash` through unchanged (Invariant I6), and `txn_type` from the
   staged row's `proposed_txn_type` — non-null on every accepted row by V13's CHECK (§5.2).
5. Calls `assert_account_reconciles(p_account_id, p_declared_closing, p_period_end)` — also
   created in V14, also `SECURITY DEFINER`, also calling `assert_is_owner()` first — which
   **`RAISE`s immediately when either argument is null** (a null comparison must never silently
   pass as verified), then computes the account's balance **as of the statement close** in
   Postgres (the account's opening balance plus the sum of its posted transactions with
   `booked_date <= p_period_end`) and `RAISE`s on any difference from the declared closing.
   `p_declared_closing` is the batch's recorded closing target: the closing-pattern capture
   when the layout declares one, else the terminal running balance §4.6 defines — staged on
   `import_batch`, so the value always exists for a batch that reached staging. Not the maintained
   `current_balance`: the multi-file screen imposes no chronological commit order, and a valid
   older statement committed after a newer one would compare its closing against a balance that
   already contains the newer postings and wrongly roll back. This is the post-commit
   verification, inside the same transaction, so a raise rolls the whole batch back.
6. Updates `import_batch` to `status='completed'` with `rows_imported` and `completed_at`.

M2's commit inserts `transaction` rows only. `transfer` (M5) and `card_statement` (M6) inserts
named in `statement-ingestion.html` §11 belong to those milestones.

---

## 5. Table and storage contracts

### 5.1 V12 — `document`, `import_batch`, `statement_layout`

`schema.md` `0008` is the shape, verbatim, with these M2 additions and one correction:

- All three tables get `ENABLE ROW LEVEL SECURITY` and a `household_isolation` policy
  (`schema.md` `0008` shows the policy for `statement_layout` only; `document` and `import_batch`
  need it too — `schema.md` carries no policies by design, so this is an addition, not a
  disagreement).
- `document.storage_bucket` is the constant `'documents'` and `storage_path` is relative to
  `DOCUMENTS_DIR` (already in `.env.example`, already a compose volume).
- `import_batch.reconciliation_status` stays `TEXT` with a `CHECK` restricting it to
  `matched | mismatched | running_balance_break | unavailable`. The design doc's `switch` uses
  `reconciled`; the column comment in `schema.md` uses `matched`. **`matched` wins** — it is the
  column's documented value set. Task 4 records the alignment.
- `statement_layout` keeps `has_a_balance_anchor`. `UNIQUE (household_id, name, version)` makes
  version bumping a plain insert.
- `document.original_filename` is written **masked** — see §5.6.
- **V12 completes the deferred foreign keys whose targets it creates** (the `0018` rule: an FK
  clause lands with its target table, not earlier and not later): `ALTER TABLE transaction` adds
  the constraints for `source_document_id → document`, `receipt_document_id → document`,
  `import_batch_id → import_batch` and `statement_layout_id → statement_layout`. All four columns
  are entirely NULL today, so validation is trivial. `classification_rule_id` stays a bare UUID —
  its target arrives with M4, which adds only that FK. `schema.md`'s V10 note currently says "the
  M4 migration that completes the target set adds their foreign keys"; Task 2 corrects that
  sentence to the per-target rule in the same commit (§7.8).

### 5.2 V13 — `staged_transaction`

`statement-ingestion.html` §9 is the shape. It is not currently in `schema.md`, so Task 4 adds it
as a new subsection under the `V13 (applied)` heading in the same commit. M2 deltas:

- `proposed_transfer_peer_txn_id`, `duplicate_of_transaction_id` and `near_duplicate_of_id`
  reference `transaction(id)`, which exists — declare them as real foreign keys.
- `proposed_transfer_kind` is created as `TEXT` with a `CHECK` (the `transfer_kind` enum arrives
  with M5) and `classification_rule_id` as a bare `UUID` (the table arrives with M4) — the same
  deferred-reference pattern V10 already uses and documents.
- A `CHECK`: `status <> 'accepted' OR proposed_txn_type IS NOT NULL` — an accepted row always
  carries a committable type, because `transaction.txn_type` is `NOT NULL` and a row that can be
  accepted but never committed is a dead end the database refuses to represent.
- `ENABLE ROW LEVEL SECURITY` + `household_isolation`.
- **Consequence to test, not to work around:** `reporting_pair_is_locked()` (V7) discovers every
  table carrying both `amount_base` and `household_id`. Creating `staged_transaction` therefore
  makes a *staged, uncommitted* batch lock the household's reporting pair. That is correct — those
  rows are already frozen against the current pair — and Task 4's DB test asserts it, so nobody
  later reads it as an accident.

### 5.3 Statuses

`staged_transaction.status ∈ {new, duplicate, near_duplicate, needs_review, accepted, rejected}`.
M2 stages `new` (clean), `duplicate` (layer 2 hit) or `near_duplicate` (layer 3 hit); `needs_review`
is set by M4's classifier. Review moves rows to `accepted` or `rejected`. Only `accepted` commits.
Every staged row carries a `proposed_txn_type` (sign-derived at normalization, overridable in
review — Task 4's Type contract), so acceptance always leaves a row that can commit into
`transaction.txn_type NOT NULL`.

### 5.4 The staging transaction

Per api-contracts.html §4: the service writes the original into the documents volume **first**,
then writes `document`, `import_batch` and every staged row **inside one database transaction**.
Original first, because a staged batch referencing a blob that failed to write is worse than an
orphaned file. The blob is written to a **uniquely named temporary file** in the same directory
and atomically renamed to its final `<sha256>.pdf` path before the transaction opens, so a
partial write never sits at the final name (two identical concurrent uploads renaming to the
same path is harmless — the bytes are identical by construction of the name). On any failure the
database transaction rolls back and cleanup removes **only the request's own uniquely named
temporary file — never the final-path blob**. A committed-rows ownership check cannot close the
race: a concurrent identical upload's `document` row may be inserted but not yet committed, so
under MVCC the failing request would see no owner and delete a blob the winner is about to
reference. A final-path orphan is the safe residue instead — it is inert, content-addressed
(identical bytes by construction), and self-healing: the next identical upload renames over it,
and `GET /doctor` may report unreferenced blobs when the document library arrives (M10).

### 5.5 What a blocked batch leaves behind

**Nothing.** A reconciliation mismatch, a running-balance break, an `unavailable` verdict
(configured evidence that failed to extract — §4.6), an unidentified layout, an
unresolved account, a missing FX rate or a parse failure leaves the database **unchanged** —
no `document`, no `import_batch`, no staged row (api-contracts.html §4's error table; Invariant
I8's "stages zero rows"). The verdict travels in the `POST /parse` response body so the
diagnostics panel can render it.

This deviates from `data-model.html` §12's sentence "a blocked import records why it blocked".
The reason is functional: `document.sha256` is uniquely indexed per household, so a persisted
blocked document would make its own checksum reject the retry after the owner re-maps the layout —
the exact recovery path ADR-0012 exists to provide. **Task 4 corrects that sentence in
`data-model.html`** to say the batch records its verdict for batches that stage, and records the
correction in its PR. Do not work around it in code.

### 5.6 Filenames, logs and the never-store list

`pdf-statements/` holds real statements **with card numbers in the filenames**. Two consequences:

1. `document.original_filename` is stored **masked**: the mask detects identifier-length digit
   sequences on a **normalized** view — separators that format an identifier (whitespace, `-`,
   `–`, `.`, `_`, `/`) are ignored between digit groups — and replaces each whole formatted
   sequence totalling 7 or more digits with `…` plus its last four digits, before the value
   reaches the database. `statement-1234-5678-9012-3456.pdf` masks to `statement-…3456.pdf`;
   checking only contiguous runs would store that full card number. The unmasked filename exists
   only as a local variable during the request.
2. **No log line, error message, exception detail or HTTP response may contain the raw filename,
   the file bytes, a password, or any digit sequence of 7 or more digits — contiguous or
   separator-formatted — from the document.** Parse errors name page and row numbers and the
   *masked* filename.

   **`POST /parse`'s preview rows are screened, not exempt.** Statement narration is untyped text
   out of the document and is where a card number actually appears — `PAYMENT RECEIVED CARD 4111
   1111 1111 1111` — so `description_raw`, `description_clean` and `reference_raw` go out through
   the same redactor `POST /layouts/propose` already applies to its own preview rows. Dates,
   amounts and balances are typed and pass through intact: the preview exists to show that the
   columns landed where they were mapped, and redacting the figures would defeat it while
   protecting nothing.

   The *stored* `staged_transaction.description_raw` is deliberately unscreened — it is the audit
   record, and `api-contracts.html` §4 makes it what the review screen shows for traceability.
   Whether the review payload screens it on the way out is **1.1b's call, not this rule's**: it is
   the same tension, one route later, and it is named here so it is decided rather than inherited.

Task 5 owns both, and Task 5's tests assert them.

---

## 7. Contradictions between the documents and the tree

Named here because CLAUDE.md requires the disagreement to be stated and fixed at its source, not
worked around.

1. **The PR line cap.** `docs/git-workflow.md` and `CLAUDE.md` say "no hard line cap — keep each
   PR reviewable at one sitting". `docs/superpowers/plans/implementation-plan.md` line 53 still
   says "at most 500 lines of implementation and test code per PR", and
   `docs/superpowers/phase-0-pull-requests.md` §"The 500-line cap" still states the cap.
   **`docs/git-workflow.md` is the authority.** Task 1 fixes `implementation-plan.md` line 53 to
   point at the git-workflow rule. `phase-0-pull-requests.md` is a Phase 0 historical document
   and is left alone; **CLAUDE.md's index row still describes it as "branch/review workflow and
   the 500-line cap" — that is the owner's file, so Task 1 flags it in the PR rather than
   editing it.**
2. **`pdfplumber` is not a new dependency.** `api/pyproject.toml` already declares
   `pdfplumber>=0.11.10` and `api/uv.lock` pins `0.11.10`; nothing imports it yet. M2 adds no
   dependency at all. Any planning note describing pdfplumber as a new addition is out of date.
3. **`reconciled` versus `matched`.** `statement-ingestion.html` §8's TypeScript sketch returns
   `reconciled`; `schema.md` `0008` documents the column's values as
   `matched|mismatched|unavailable`.
   Resolved in §5.1: the column value set wins, extended with `running_balance_break`, which §8
   describes but the column comment omits.
4. **A blocked import's durability.** `api-contracts.html` §4 says the database is unchanged;
   `data-model.html` §12 says a blocked import records why it blocked. Resolved in §5.5 in favour
   of api-contracts, with `data-model.html` corrected by Task 4.
5. **The error envelope.** `api-contracts.html` §6 specifies
   `{"error": {"code": ..., "message": ..., "detail": {...}}}`; every implemented route returns
   FastAPI's `{"detail": "..."}`. M2 does **not** introduce a second error shape for a handful of
   routes. Blocking conditions are not HTTP errors at all — `POST /parse` returns **200** with a
   `reconciliation` object carrying `status`, `declaredClosing`, `computedClosing`, `delta` and
   `firstDivergentRow`, which is what the diagnostics panel needs and what a `detail` string could
   never carry. Genuine client errors keep the established `detail` string. Task 5 records this;
   converging the whole surface on §6's envelope is a separate, repo-wide change and is out of
   M2's scope.
6. **The static checks do not exist.** `testing-and-data-quality.html` §2 and CLAUDE.md describe
   build-failing static checks (reports never read the base table; no `amount × rate`; no
   `parseFloat`/`Number()` on money; no currency/institution/country identifier). None is in the
   tree — there is no such script and no such ESLint rule. Roadmap **M5** owns the reporting
   check; M2 does not build any of them. Recorded here so the gap is known rather than assumed
   covered. M2's own compliance is enforced by review and by the tests each task declares.

   **Settled, 10 September 2026.** Slice 1.4 writes the first reporting reader in this
   repository, so the reporting checks are built there — task 4b1, before the dashboard
   query they guard — rather than waiting for M5. Owner decision; this item stands as the
   record of how long the claim outran the tree.
7. **`FxRateResponse` uses snake_case** (`carried_from`) while every later route uses camelCase.
   M2's new models use camelCase; the existing response is not renamed here (it would break the
   Settings FX inspector for no M2 benefit).
8. **Who adds V10's deferred foreign keys.** `schema.md`'s V10 note says "the M4 migration that
   completes the target set adds their foreign keys" — all five at once, in M4. The `0018` rule
   is per target: an FK clause lands with its target table. Resolved per target (§5.1): V12 adds
   the four FKs whose targets it creates, M4 adds `classification_rule`'s. Waiting for M4 would
   leave M2's own commit path writing `import_batch_id`, `statement_layout_id` and
   `source_document_id` into `transaction` for two milestones with no referential check. Task 2
   corrects the `schema.md` sentence in the migration's commit.
9. **The dedupe index in `statement-ingestion.html` §6.** The specification's own worked example
   establishes that any statement covering a day carries the same set for that day — the
   within-document property — yet one sentence then said the index is computed against the union
   of committed rows and earlier batch rows. Under the union rule a committed overlap shifts
   every incoming index and the recognition defeats itself. Corrected at source in Task 1's PR:
   the index counts within the current document only; the split-boundary edge stages as a
   visible `duplicate` for review instead of silently doubling a month.
10. **The roadmap's "names the divergent row" gate.** With a closing-anchor-only layout (no
    running-balance column) there is no per-row balance evidence, so no algorithm can honestly
    name *the* divergent row — any single row could absorb the delta. §4.6 defines the honest
    diagnostic: the row is named when running-balance evidence exists; otherwise the verdict
    carries the exact delta and the candidate rows whose amount equals it (the dropped-row
    case), and says that row-level evidence is unavailable. The roadmap gate is met in that
    form; inventing a row number would violate I7.
11. **"Detect duplicates against staged and committed rows."** `statement-ingestion.html` §1
    words the pipeline stage that way, but V13 gives both `duplicate_of_transaction_id` and
    `near_duplicate_of_id` a foreign key to `transaction(id)`, so neither verdict can name a
    staged row. **The verdict columns win**: layer 2 and layer 3 compare against committed rows,
    which is what `dedupe.classify` takes. The gap this leaves is real and belongs to Task 5d:
    two unreviewed batches of the same statement, differing by a byte so layer 1 misses them,
    both verdict `new` and collide at commit on V10's unique index. The pipeline is where that
    is caught — by refusing a second staging for an account and period that already has an
    unreviewed batch — not by a verdict column that cannot reference one.
12. **§4.7's disambiguator is namespaced in the tree.** The formula gives the disambiguator as
    `reference_raw`, otherwise the occurrence index — two values from different worlds sharing one
    namespace. A bank reference of `1` and an occurrence index of `1` then produce one fingerprint
    for two distinct rows, which V10's `idx_txn_dedupe` rejects at commit or which marks one row a
    false duplicate of the other. The implementation emits **`occ:<n>`** or
    **`ref:<reference>#<seen>`**, with the repeat counter appended to every referenced row so that
    a literal reference `ABC#1` cannot collide with the `ABC` that repeated once — splitting on
    the last `#` always separates the two. **Task 6c's confirmed-distinct override must recompute
    in this form**, advancing the `occ:` index to the next free one among committed collisions.
13. **§4.4 rule 5's continuation test cannot separate wrapped narration from a trailing
    paragraph.** The rule appends any line with no parsable date and no amount, and a statement's
    closing legal block is exactly that: forty-odd lines of prose that fail both tests and are
    appended, one after another, to the last transaction of the statement. Measured on slice
    1.2's third layout, 246 such lines reach 6 of 1,532 rows and take their `description_raw`
    past 1,400 characters. Nothing about the money is wrong — every statement still reconciles
    identically — which is why it is invisible without looking.

    The distinction the rule is missing is **geometric, not textual**: wrapped narration sits
    inside the description column and leaves the date and money columns empty, while a paragraph
    set across the page fills them with prose that merely fails to parse. "No date" and "the date
    column is blank" are different questions, and only the second separates the two.

    **`continuation_rule` therefore gains a second member, `blank_date_and_money_columns`**, which
    appends a line only when its `booked_date`, `debit`, `credit` and `running_balance` cells are
    *empty* and its description is not. The vocabulary grows by one considered member rather than
    the engine learning a bank's footer (ADR-0012); `no_date_and_no_amount` is unchanged and stays
    the right rule for a producer whose trailing lines carry no description column at all. On the
    layout that raised it the stricter rule rejects all 246 prose lines and drops 48 lines that
    the looser one appended — every one of them page furniture (the address block, the scheme
    line, the period line, the repeated column header and the `OPENING BALANCE` line), and none of
    them narration. Dropped lines are counted in `lines_unparsed` and reported, as rule 5 requires
    (Invariant I7).

    Owner decision, 10 September 2026, taken against the alternatives: accepting the six corrupted
    descriptions, or switching the layout to no continuation at all, which costs roughly a
    thousand rows their second narration line.
14. **§4.4's period gate is stricter than a real statement.** The section says a captured period
    "gates row dates (a parsed date outside the period fails its row)". Slice 1.2's fourth layout
    disproves it: its statements book year-end interest and the tax deducted on it on **1 January**
    — one day after the period they declare — and carry both in their own running balance. Six such
    rows across four of its six statements failed, and because they sit at the end, dropping them
    left a consistent prefix that still reconciled `matched`. A statement short two money-bearing
    rows that reports itself reconciled is the outcome §4.6 exists to prevent, and only
    `blocks_staging` stopped it.

    The layout cannot express its way out: its rows carry a two-digit year, and §4.4 requires a
    profile with one to capture both period bounds, so the period cannot simply be left derived
    the way the card layout leaves it.

    `_windowed` already states the principle the gate contradicts — "the period anchors the
    century — it does not confine the date: a settlement date days after the statement closes is
    ordinary". **The gate is corrected to agree with it: a row whose `booked_date` falls outside
    the captured period is the statement's own when its `value_date` falls inside.** The statement
    itself is the evidence, so nothing is invented and no tolerance is guessed — a row with
    neither date inside the period still fails, exactly as before, and a layout that maps no value
    date is unaffected. Every one of the six rows is valued 31 December.
15. **V14 verifies a batch as of the wrong date, for the same reason.** Item 14 lets the late
    rows normalize; the commit path then refuses the whole statement anyway.
    `commit_import_batch` calls `assert_account_reconciles(account, declared_closing,
    period_end)`, and with rows booked past `period_end` those rows are outside the sum while
    the declared closing includes them. Two of the six statements failed at commit, each short
    by exactly its late rows' total.

    **V18 verifies as of the last booked date the batch itself carries, never earlier than
    `period_end`.** `GREATEST` is what keeps it never earlier: shrinking the window would
    exclude rows the statement lists, which is this defect arriving from the other side. For
    every layout whose rows fall inside their period — the three before this one — the as-of
    date is `period_end` and nothing changes, which `db/tests/0016` asserts directly.

    The window widens by the days the producer is late by, so an older statement committed
    *after* a newer one can now see a day or two of the newer one's rows. That hazard is V14's
    own and unchanged in kind: it is why the comparison is against the batch's
    `declared_closing_balance` and never the maintained `current_balance` (§4.9).
16. **`statement_layout.times_used` and `last_used_at` had no writer.** V12 created both, and
    `GET /layouts` and `POST /layouts` returned them as `timesUsed` and `lastUsedAt`. But no
    document said who maintains them (`statement-ingestion.html` and this spec are both silent),
    and no code did: after 67 real imports across four layouts, all four reported
    `timesUsed: 0`.

    **V22 drops them rather than maintaining them.** No code reads them. The only layout picker
    is the import screen's (`api-contracts.html` §4), and it ranks by fingerprint score. Slice
    1.7 builds the mapper, the document library and the diagnostics panel, and none of those
    ranks layouts.

    One specified screen does want the two facts. `ui-information-architecture.html` §6's
    Settings › Statement layouts shows, for each saved layout, "how many documents it has
    parsed, and when it was last used". **Both are derived, never stored.** The first is a count
    of `document` rows by `statement_layout_id`; the second is the latest
    `import_batch.started_at` by the same key. Both are grouped by layout name across versions.
    That grouping is why a counter is the wrong shape as well as a stale one: re-mapping a
    layout inserts a new versioned `statement_layout` row (the same §6), so a counter on the row
    restarts at every version, while the screen describes the layout. The derived figures are
    also right for all 67 existing imports, where a counter maintained from here on would start
    67 short and need a backfill before it stopped being wrong.

    `LayoutProfile` loses both fields and `web/types/api/` is regenerated. No screen in the tree
    read either one.

    Decided 10 September 2026 under the owner's delegation. The alternative was incrementing the
    counter in `ingest/pipeline.py` when a batch stages.

---

---

## 8. Decisions and deviations recorded during delivery

Each of these was taken while building a task and would otherwise be invisible: a refinement §4.6
does not cover, a deviation from the plan, or a document this work found to be wrong. They live
here rather than in the tracker because they are contract, not status — the tracker says what is
done, this says what was decided. Ordered by the task that raised them.

Task 4b records three deviations. **A repeated reference no longer collapses distinct rows**: §4.7
makes `reference_raw` the disambiguator, which separates identical-looking transactions only while
it is unique, and a bank that fills the column with a constant would have staged three distinct
rows under one fingerprint for V10's unique index to reject wholesale at commit — the occurrence
index is appended when a reference repeats. **The occurrence counter advances for every row**,
including one a reference disambiguated, so a reference present in one statement and blank in the
next no longer shifts every later index and marks each row a duplicate of the wrong transaction.
**`normalise_for_hash` normalises, uppercases and normalises again**, because extraction yields
precomposed or decomposed text depending on the font, and uppercasing expands some letters into a
base plus a combining mark — either way the two forms otherwise hash differently and score 0.0
against each other. **The disambiguator is namespaced** `occ:<n>` or `ref:<reference>#<seen>`
(spec §7 item 12), because a bank reference of `1` and an occurrence index of `1` otherwise share
a fingerprint; Task 6c's override recomputes in that form. Spec §7 gains item 11: the pipeline's
"staged and committed rows" against
V13's foreign keys, which reference `transaction(id)` alone — the verdict columns win, and the
cross-batch gap is Task 5d's to close in the pipeline.

Task 2 delivers V12 (`document`, `import_batch`, `statement_layout`, RLS on all three, and the
four foreign keys V12 completes on `transaction` per schema.md's per-target `0018` rule) and the
layouts API (`GET /layouts`, `POST /layouts`) behind `require_owner`, with every 422 validation
rule Task 2's contract names (band ordering, sign-convention/column agreement, the quirks
vocabulary, regex compilation, reserved metadata capture groups, the two-digit-year period-
capture requirement, and the institution predicate). No parsing exists yet — that is Task 3.

Task 3 review clarifications: ambiguous numeric day/month dates are refused for both slash and
dash separators. Since the profile contract has no line-tolerance field, the PDF boundary infers
tolerance from modal character height for both automatic proposal and saved-profile extraction;
the fixed default is only a fallback when no reliable mode exists. Turning continuation off with
`has_date` leaves the transaction count unchanged and instead drops description fragments while
increasing `lines_unparsed`; this corrects the plan test's wording, not the binding row predicate.
Automatic proposals may be incomplete unsaved drafts (including two-digit dates before period
mapping); submitted drafts and saved profiles still enforce period captures. Raw diagnostics
retain original PDF characters, including whitespace and filtered script text. Public previews
redact identifiers in both modes, and account validation responses omit sensitive request input.

**Task 4 is split into two deliveries**, each its own branch and PR: 4a
(`feature/m2-staged-rows-and-normalization`) carries V13, `db/tests/0011`, the schema and
data-model corrections, `schemas/staging.py` and `ingest/normalize.py`. Dedupe and reconciliation
are now **4b (`feature/m2-t4b-dedupe`) and 4c (`feature/m2-t4c-reconcile`)**, separated by the
plan re-split because they are the subtlest logic in M2 and PR #34 showed how slowly a large
surface converges. Both depend on 4a's normalized row and on nothing else, and are independent of
each other. The combined `feature/m2-dedupe-and-reconcile` branch is superseded and must not be
used.

Task 4c records two decisions beyond §4.6 and one budget exception. **A running-balance divergence
blocks even where the totals agree**, because two amounts misread in opposite directions sum to
the right closing balance while putting both wrong figures in the ledger, and the walk departing
is the only evidence of it; §4.6 does not cover the case. **A configured running-balance column
with a gap in it is `unavailable`**, not a column to walk past: §4.6 asks every row-to-row step to
hold, `engine.extract_pages` merges a physical continuation line into the row above rather than
emitting one of its own, and a walk that checks only where a balance was stated reports `matched`
for a pair of amounts misread in opposite directions that cancel before the next anchor. The
branch is **982 counted lines against the 800 budget**, under the 1000 hard stop: the production
code is 363 of 400, the excess is test lines five review rounds added, and a reconciliation
returning three of four verdicts is a wrong slice rather than a smaller one. On a
`running_balance_break` the verdict's three closing fields carry row-level figures, per §4.6's own
wording — Task 9b's panel must read the status before the fields.

Two corrections ride with the task. **`statement-ingestion.html` §8 said `unavailable` warns and
proceeds**, and its switch still named the verdict `reconciled`; §4.6 says `unavailable` blocks in
M2 and V12 already recorded that `matched` won the name. The design document is the one that was
wrong, so it was corrected there rather than worked around, and its illustrative field names now
match the `Verdict` this task delivers. **The verdict carries the divergent row's `amount`**,
which §4.6 names among the five figures a break carries and which no consumer can derive:
TypeScript formats money and does no arithmetic on it.

Task 4a deviations, recorded rather than worked around. The plan's date test pairs `dd/MM/yyyy`
with `MM/dd/yyyy`; Task 3's save-time validation has since restricted `dateFormat` to the
engine's supported set, so month-first cannot be declared at all and the no-inference rule is
proved in that stronger form. `original_amount` and `original_currency` join the mappable column
vocabulary, without which the currency contract's foreign amount could never be captured.
`api/tests/test_schemas_staging.py` is an added test file the plan does not list, because
`schemas/staging.py` is a new module. A statement whose dates never parse is refused whole rather
than row by row: without one parsed date there is no period to place the others in. And a Dr/Cr
suffix appearing under a convention that does not read one fails its row, because dropping it
silently is exactly the invisible sign error Invariant I2 exists to prevent.

Two review findings about the statement period were answered differently from the way they were
raised. A `dateFormat` carrying a two-digit year with no period capture now refuses the whole
statement: `LayoutCreate` already refuses to save that shape, but normalization is a pure
function over a profile-shaped value and does not lean on a route-layer validator, and a derived
period is made of the very dates it would have to correct — Python reads `99` as 1999 and the
derived period then agrees, so a century-early preview looks internally consistent. The second
finding asked that a captured period be read only in the profile's declared `dateFormat`; that
is **partially declined**, because a statement's header commonly writes its period differently
from its transaction lines and the rule would reject those layouts outright. Both bounds must
instead parse under one shared four-digit-year format, which blocks the mis-captured fragment
pairing with a genuine date — the risk the finding names — without the product cost.

**Owner decision, 2026-09-08 — statement text is stored as the statement wrote it.** Codex's
fifth review round found that a full account or card number appearing inside a transaction
description is kept verbatim in `description_raw` and `raw_row`, which Task 5 will insert into
`staged_transaction`. The owner ruled that this is correct for this product: the number is the
owner's own data, in a single-user system, and the ledger is where they read it, so it is stored
and shown rather than masked. Normalization therefore applies no identifier mask, and Task 5's
insert does not need one for these two fields.

This decision **contradicts `security-and-privacy.html` §1**, which introduces the never-store
list with "Absolute. No feature justifies an exception, and no future integration changes it"
and lists full card and bank account numbers. Nothing in the tree or the documents reconciles
that yet, and the repository's own rule is that an invariant which is genuinely wrong changes by
a superseding ADR rather than by an edit or by code quietly diverging. **An ADR is owed before
the documents and the code agree**, and until it exists this paragraph is the only record.

The ruling is read narrowly, pending that ADR. It covers identifiers the *statement itself*
prints in a transaction line. It does not touch: the account number the owner enters for an
account, which Task 3 stores as a mask plus a salted hash and never whole; the §5.6 rule keeping
document-derived digit sequences out of log lines, error messages and responses, which Task 4a
implements; the layout preview's redaction, delivered and reviewed in Task 3; or credentials —
CVV, PIN, OTP and passwords — which no part of this decision admits into storage.

Header recognition claims each word once, across all fields at once, longest label first: a
directional label such as `Withdrawal Amount` is one debit column, never a debit column beside an
amount column. The plan's synonym table is extended with those directional compounds (`withdrawal
amount`, `debit amount`, `deposit amount`, `credit amount`, their `amt` spellings, and
`transaction amount`) so the compound outranks the generic word it contains; the plan is not
edited after dispatch. An automatic proposal that would still hold an amount column beside debit
and credit is refused, because no saved layout may hold all three. Identifier screening treats any
non-alphanumeric run as a grouping separator in previews, captures and fingerprints, so a
non-breaking hyphen or an Arabic thousands separator groups an identifier exactly as a plain
hyphen does.

The preview privacy boundary preserves a validated date or monetary value only in its known
typed field. Raw diagnostics and description/reference text redact every identifier-length digit
run, including separator variants, even when it resembles a date or amount. A deliberately
captured account identifier overrides all field protections. This reduces detail in raw previews
and incorrectly mapped fields; header/band semantics are the accepted boundary for auto-proposed
financial cells. Internal extraction still retains the verbatim source text for transient parsing.

**Balances carrying a `Dr`/`Cr` suffix are Task 4d**, on `fix/m2-balance-direction`, split out of
4c. Codex raised it on [PR #42](https://github.com/sandjaie/OneLedger/pull/42): `parse_amount`
strips the suffix without reading it — right for an amount, where it means something only under
`cr_suffix`, and wrong for a balance, where `100.00 Dr` is an overdraft whatever the amount column
does. `parse_balance` reads it and returns the value beside `direction_is_stated`; `frame_balance`
then carries a balance into the ledger's frame, reframing only one that stated no direction of its
own, because honouring a suffix and then applying `inverts_sign` reverses the statement instead of
reframing it. **This refines §4.6**, which says `inverts_sign` is applied to the declared balances
flatly; the section above now states the narrower rule, and `statement-ingestion.html` carries it
beside its sign table. Both modules read `frame_balance`, and `_balance_deltas` reads it *before*
subtracting: the pre-push review found that subtracting first and framing the difference is wrong
for any chain mixing suffixed and bare balances, and got 18 of 54 combinations wrong. Such a chain
needs no exotic layout — an owner's anchor pattern capturing `([0-9,.-]+)` takes the number without
its suffix while the running-balance column keeps it, and nothing prevents that. Carrying
both halves in 4c would have put that branch at 1,009 counted lines, past the hard stop.

**A balance that contradicts itself — `-100.00 Cr` — stops the statement.** Refusing the cell is
required by I2: resolving a sign and a suffix that disagree is the invisible sign error the
convention machinery exists to prevent. Refusing it by *failing the row* is not, and the pre-push
review caught that: `reconcile` receives the surviving rows and cannot tell one was dropped, so a
statement whose last line was dropped reconciled `matched` against the line before it — I8
reporting satisfaction over a truncated statement. The cause is a band and not a row, so the
refusal is statement-level and the remedy is the one both balance guards already give. **It runs
ahead of the row loop**, over every extracted row: evaluated inside that loop it was skipped
whenever the same row failed an earlier check, and the check that dropped the row took the
contradiction with it. A declared opening refuses the same way but names its own remedy — that
anchor is a regex over the document text, so no band is at fault and no column sits beside it.

**A wider hole this made visible, and did not open.** `reconcile` cannot tell that *any* row was
dropped, whatever the reason — a row failing for "the row states no description" produces the
same false `matched` on `develop` today. Task 4d only stopped routing the contradicting-balance
case into it. Closing it properly needs the row count, or the failures, to reach the verdict; that
is its own task.

**One question 4d does not settle**, and 4d narrows it. `layouts.py` forbids `inverts_sign` on
`debit_credit_columns` and `cr_suffix`, while `statement-ingestion.html` names a card source
pairing a `CR` suffix with `inverts_sign = True`, so a card whose amounts state their own
direction has no frame switch for its balances. Where those balances carry their own suffix it
now needs none, and such a card reconciles. What still blocks is the card that prints its
balances **bare**: nothing then says the figures are owed, and the walk accuses row 1 for what is
a frame error. One of those two documents is wrong; deciding which is the owner's, and the fix is
its own task once decided.

**Owner decision, 2026-09-09 — `inverts_sign` reframes whatever stated no direction.** The
question 4d left open above is settled, and settled the way 4d's own rule already pointed. The
flag is not a property of the row convention; it is the statement's frame, and it applies to
every figure the statement wrote without a direction of its own — an amount under `signed` or
`balance_delta`, and any declared or running balance carrying no `Dr`/`Cr` suffix. A figure that
stated its direction has nothing left to reframe, so the flag is withheld from rows under
`cr_suffix` and `debit_credit_columns`. That is the exact symmetry of the balance rule 4d
delivered, now stated once for both halves rather than twice with different scope.

`statement-ingestion.html` is the document that was right: a card pairing a `CR` suffix with
`inverts_sign = True` is a valid layout, and it is the layout in hand. `layouts.py` was the one
that was wrong — its `inversion_matches_its_convention` validator refused exactly that pairing,
so the card had no frame switch for its bare anchors and every statement mismatched. The
validator is removed rather than narrowed: with `has_a_balance_anchor` guaranteeing every saved
layout declares an anchor, there is no convention under which the flag has nothing to act on.
`DIRECTIONLESS_CONVENTIONS` goes with it, and the semantics now live on the field itself beside
`normalize._signed_amount` and `normalize.frame_balance`, which is where they are applied.

Measured on the layout in hand before the change, and the reason the decision is not a matter of
taste: the statement declares a previous balance and a total payment due, both written bare and
both money owed, and the identity closes only once both anchors are negative. Read in the
issuer's frame the computed closing overshoots the declared one by twice the opening balance,
which is what blocked all 38 files. The arithmetic is reproduced with invented figures in
`api/tests/test_ingest_card_frame.py`; no real balance is recorded here.

Two further findings from that measurement, recorded so they are not rediscovered as bugs:

- **The card's transaction table legitimately carries rows dated outside its statement period.**
  Its installment block prints each plan's original purchase date, and those instalment charges
  are part of the declared purchase total the anchors reconcile against. The layout therefore
  **derives** its period and must not capture it: capturing the printed period would fail those
  rows and break the identity. Its `date_format` carries a four-digit year, so deriving is
  permitted (§4.4's two-digit-year rule does not bind here).
- **`continuation:no_date_and_no_amount` runs past the end of the transaction table**, appending
  trailing page furniture to the last row's description — 2,232 characters of it against 46
  with the rule off. The card writes each transaction on one physical line, so the profile
  simply does not set the rule; the dropped lines are counted in `lines_unparsed` and reported
  in the parse summary, which keeps the loss visible rather than silent (I7). Data, not code,
  and it moves no amount and no verdict.
- **The balance anchors cannot be regexes over a label.** This producer emits no space
  characters between cells, so the statement summary extracts as one run —
  one line of six figures with no separator. It is the
  no-space case `statement-ingestion.html` §4 attributes to another layout, present here too.
  Every figure ends in exactly two decimal places, so `\d[\d,]*\.\d{2}` segments the run
  deterministically — the decimal points alone fix the six boundaries, so there is nothing for
  the greedy digit class to explore. The seeded patterns require exactly six figures on their
  own line, which makes a five- or seven-figure line yield no anchor at all rather than a wrong
  one, and they
  capture the first and the **fifth**. The fifth is the total payment due. The sixth is the
  current balance, which also carries the remaining installment principal and reconciles
  against nothing — anchoring on it would fail every statement that has an instalment plan.
- **One profile covers both card variants.** Their summary blocks differ (one wraps its column
  labels across two lines) but both print the same six figures in the same order on their own
  line, so anchoring on the figures rather than the labels covers both without a second layout.

Measured over the 38 card statements held locally, with the seeded profile: **every one of them
reconciles `matched`, with no row failing to normalize.** That is slice 1.1's acceptance
criterion at the parse and reconciliation level; staging, commit and the review screen are the
branches that follow. No figure, date or per-file count from those statements is recorded here —
they are git-ignored, and the committed evidence is the invented-figure suite.

**Task 5d records four decisions and one budget exception.** They were taken while building
`ingest/pipeline.py` and `POST /parse`, and none is covered by §4.3.

**Identification declines on a tie.** §4.4 says the engine is driven entirely by a profile row
and `statement-ingestion.html` §1 orders identification "text fingerprint → filename hint → ask",
without saying when to stop. The pipeline scores every saved layout and chooses one only when a
single layout scores highest and scores at least half its fingerprints; two layouts scoring alike
go to the picker. The reason the ambiguous case cannot be resolved by taking the higher half of a
coin toss is that column bands are geometry rather than meaning: the losing choice still parses
every row, into the wrong fields, with no error raised anywhere. The **filename hint is not
implemented** and `engine.matches` discards the argument on purpose — a statement's filename is
owner-chosen and routinely carries a card number (§5.6), so scoring on it would both mis-identify
and read a value the pipeline exists to keep out of everything it touches.

**The account lookup is inverted through a resolver the caller supplies.** `ingest/` may not
import `routes/` (§4.1), and resolving a statement's own account number needs the salted hash
`routes/accounts.py` defines and the `ACCOUNT_HASH_SALT` it owns. `pipeline.parse` therefore takes
an `AccountResolver` callable and the route passes a closure. The alternative — moving the hash
into `ingest/` — would put a second copy of the account identity in the tree, and the never-store
list makes one copy already delicate enough.

**A blocked batch is returned, not raised.** §7.5 already decided that `POST /parse` answers a
blocking condition with a 200 and a reconciliation object; this records that the *pipeline* does
the same, returning `ParseResult(staged=False)` with the full analysis rather than an exception.
A raise would have forced the route to re-parse to obtain the diagnostics the panel needs, and
would have made "asked to stage, and it did not" indistinguishable from "the upload failed".
`pipeline.blocks_staging` is the single predicate: reconciliation not `matched`, or any row that
failed to normalize.

**`document.document_type` comes from the account's own `account_type`.** An earlier draft of
this entry recorded the column as hardcoded `bank_statement`, on the reasoning that the layout
profile carries no document-type field and a heuristic over the fingerprints would be the silent
guess ADR-0012 forbids. The review found the premise false: the account is resolved before
staging and `account.account_type` already carries `credit_card` as a stored fact, so the card
distinction needs no heuristic and no new layout column. It matters because the branch merged
immediately before this one made card statements importable, `idx_document_period` indexes on
`document_type`, and migrations are forward-only — a wrong value written now is a backfill later.
Only the card distinction is drawn: a loan account's statements are M7's, and guessing ahead of
the milestone that will map them is the same mistake in the other direction.

**The review round added four fixes worth recording as contract.** Each was a defect the branch
shipped and the gate caught, and each changes behaviour a later branch will rely on.

*`money._frozen` returns both legs already fixed at 10 dp.* The secondary leg is a cross — a
division — and `1 / 3` is a 28-digit Decimal that `NUMERIC(20,10)` and the `Rate` annotation both
refuse outright, so staging raised a validation error for every reporting pair whose cross does
not terminate. The rate is now fixed inside `_frozen`, where both callers share it, because the
stored rate is the auditable fact and the stored amounts must derive from *it* rather than from an
unrounded intermediate. `routes/transactions.py` already quantized after the call; that step is
now redundant rather than load-bearing. The live fixture's rate moved from 4 to 3 for the same
reason the bug survived: 1/4 terminates, and a fixture that only ever divides by 4 proves nothing.

*Staging takes the household row lock before it freezes* (spec B3.5), making it the second writer
of frozen amounts to do so after `routes/transactions.py`. Without it a `PATCH /household` landing
during a parse sees no frozen rows — this batch's are uncommitted and so invisible under MVCC —
changes the reporting pair, and the batch then commits rows frozen against the pair that no longer
applies, which `reporting_pair_is_locked()` thereafter reports as locked by exactly those rows. A
pair that moved refuses the staging rather than re-freezing, because the FX range was already
fetched for the old secondary currency.

*The staging transaction locks the account row.* `_refuse_second_staging`'s in-transaction re-check
was documented as "actually atomic" and was not: a bare `SELECT` under READ COMMITTED takes no lock
and cannot see a sibling transaction's uncommitted `import_batch` row, and `import_batch` carries no
unique constraint to fall back on. The window is seconds wide — a rollback, a network rate fetch and
a blob write separate the two checks. `SELECT … FROM account … FOR UPDATE` is the rendezvous; a
partial unique index over `(household_id, account_id, period_start, period_end)` would be the
stronger answer and needs a migration, so it belongs to whichever branch next opens one.

*A derived opening is stored NULL rather than as a declared figure* (Invariant I7).
`import_batch.declared_opening_balance` has no companion flag, and the label the parse response
carries lives only as long as that response — the review screen reads the row. A computed number
must not be readable there as one the statement stated.

**Slice 1.1a ships as two PRs under the same `[P1.1]` prefix.** Written as one branch, it
measured 1,233 production and 2,545 counted lines — past the 1,200 / 2,400 hard stop, which
`git-workflow.md` makes a splitting instruction rather than an override case. The seam is the
one the module boundary already draws: `feature/p1-1a-parse-pipeline` carries `ingest/pipeline.py`
and the freeze helper it fixed, `feature/p1-1a-parse-route` carries `POST /parse` and the wire
contract. The route branch bases off `develop` once the pipeline has merged, so neither PR
reviews code the other has not landed.

The pipeline branch is still **over the 800 / 1,600 budget and under the hard stop**: one module
at 826 added lines, in the range `normalize.py` (705) and `engine.py` (607) already occupy, and
its staging transaction cannot be halved into a pipeline that parses and a pipeline that writes
without shipping one that no caller can use.

**Task 5d's route round records one more.** `original_currency` was unconstrained between the
extracted cell and the wire, and `staged_transaction.original_currency` is CHAR(3) with
`StagedTransaction` stating the same rule. A cell reading `EURO` — a mis-mapped band, or an
issuer spelling the word out — therefore passed the dry run and failed inside the staging
transaction, where the only thing able to refuse it was a pydantic validator whose message quotes
the cell back: a 500 the owner cannot act on, and a document value in a log line, which §5.6
forbids outright. `normalize` now refuses a non-three-letter code as a named `RowFailure`, so the
dry run reports it and `blocks_staging` stops the batch like any other unnormalizable row.

`POST /parse`'s preview screens `originalCurrency` alongside the other document-sourced strings.
After the rule above nothing redactable can reach that field, so the screen is a no-op in
practice; it is there so the redaction rule reads "every untyped field" with no exception a later
reader has to carry.

**Open for 1.1c, not decided here.** `ParseResponse.counts` is built from a `Counter`, so a status
with no rows is *absent* rather than zero and the generated TypeScript is an index signature —
`counts.duplicate` is `undefined`, not `0`. Whether the wire should carry all four statuses
explicitly is the import screen's call, and it is named here so it is made rather than discovered.
