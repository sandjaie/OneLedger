# Slice 1.2 — all four bank layouts

> **Historical numbering:** This delivered plan retains the identifier used at review time. Use
> the [canonical crosswalk](../../product/master-implementation-plan.html#historical-numbering-crosswalk)
> for current Phase N.M identifiers.

Execution map for Phase 1 slice 1.2, written against the tree at `8435fb1` on 10 September 2026.
The phase delivery plan ([current
crosswalk](../../product/master-implementation-plan.html#historical-numbering-crosswalk)) owns the
historical outcome mapping and acceptance; this file owns how it is executed and is what
implementers are dispatched from.

## What the survey found, and why the branches differ from the phase plan

The phase plan names the outcome as four engine capabilities — password-protected PDFs,
multi-line descriptions as a profile rule, no-space extraction via column bands, running-balance
reconciliation — and two branches, `feature/p1-2a-engine-hardening` and
`feature/p1-2b-remaining-layouts`.

**Three of the four already exist and are tested**, delivered with M2 and exercised by slice 1.1:

| Capability | Where it lives | Evidence |
|---|---|---|
| Multi-line descriptions | `engine.py` — `rowStartRule` `has_date` / `has_date_and_amount`, `continuationRule` `no_date_and_no_amount` | `test_ingest_engine.py`; spec §4.4 rule 5 |
| No-space extraction | Column bands are the extraction mechanism throughout — `columns[].x_min/x_max` | the card layout is a space-less producer already (`db/seeds/README.md`) |
| Running-balance reconciliation | `reconcile.py` — `running_balance_break`, the row-to-row walk | `test_ingest_reconcile.py`; spec §4.6 |
| Encrypted PDFs | `words.extract_pages(path, password=…)`, `POST /parse`'s `password` form field | `test_ingest_words.py` |

The gap is not the engine. It is that **the browser has no way to supply a password** — `/import`
contains no password handling at all — and that the three remaining layouts have never met the
engine, because the statements they describe exist only on the owner's machine.

So the slice is one UI gap, one defect the 1.1 acceptance run surfaced, and a validation task that
cannot run in a remote checkout. `2a` is not "engine hardening" because there is no evidence any
is needed; hardening that the real files demand belongs to whichever layout branch proves it.

**Deviation recorded**: five branches rather than two, renamed for what they carry. The phase
plan's outcome and acceptance are unchanged. `/oneledger-plan-review` split the layout work one
branch per bank on 10 September 2026 — one layout's frame test alone is the size of
`test_ingest_card_frame.py`, and the engine changes are unbounded until a real file proves them.

## Sequence

`2a` and `2b` are independent and may ship in either order. The `2c` branches depend on both —
they need the password prompt to import the encrypted layout, and cancellation to recover from a
mis-staged batch without a manual `UPDATE`, which the acceptance criterion's "no manual
correction" forbids — and then on each other, one layout at a time.

## Where the statements are

`pdf-statements/` is git-ignored and holds the real files **on the owner's machine only**. A
remote checkout clones without them. `2c` is therefore a local task; `2a` and `2b` are not, and
are provable against synthetic fixtures here.

No real statement, extract, figure or filename enters this repository on any branch — including
in a commit message, a PR body or a test fixture. A fixture reproduces the *arithmetic* and the
*geometry*, never the amounts (`db/seeds/README.md` sets the house style).

---

## Task 2a — a password reaches the parse without touching disk

**Workspace:** `/home/user/OneLedger`
**Branch:** `feature/p1-2a-encrypted-statements`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** nothing.
**Forecast:** ~100 production, ~200 test.

**Outcome.** A statement the producer encrypted imports from `/import`: the file reports that it
needs a password, the owner supplies one for that file, and the parse proceeds. No other file is
affected by it.

**Files.** Modify `web/app/import/import-screen.tsx` and its test.

**Contracts.** `POST /parse` already accepts `password` as a form field and already answers an
encrypted file it cannot open with `ExtractionError` → 422 "PDF is encrypted or the password is
incorrect". The screen sends `password` only for the file it was entered for, and only when
non-empty. A wrong password is that file's failure and leaves every other file's outcome intact,
exactly as any other per-file failure does today.

**Invariants / security.** **The password is on the never-store list**
(`security-and-privacy.html` §1). It lives in component state for the duration of the request and
goes nowhere else: not `localStorage`, not a URL, not a log, not a retained field on the
candidate, not the parse summary. It is cleared once the file is **staged**, and deliberately
kept through a refusal: a wrong password answers with the same sentence as no password, so
clearing there would give the owner one attempt and no way to make a second.
`document.is_password_protected` is the only record that a file was encrypted, and the pipeline
already sets it from `password is not None`. Money is formatted, never computed.

**Required test behaviours (failing first).**
- An encrypted file reports that it needs a password and offers a field; no other file does.
- The password is sent for that file only — a second file in the same batch posts none.
- A wrong password shows the service's own refusal and leaves the other files' summaries intact.
- The password is not in `localStorage`, not in any later request, and is gone from state once
  the file is staged.
- Staging re-sends it: `POST /parse` is called twice for an encrypted file and both carry it.

**Validation.** `npm run lint`, `typecheck`, `typecheck:compat`, `vitest run`, `npm run build`,
the bundle secret scan, `scripts/check-identifiers.py`.

**Acceptance.** An encrypted synthetic PDF imports end to end from the screen, and no test can
find the password anywhere but the request that used it.

---

## Task 2b — a staged batch can be cancelled

**Workspace:** `/home/user/OneLedger`
**Branch:** `feature/p1-2b-batch-cancellation`
**Base and PR target:** `develop`
**Migration:** none — `import_status` already carries `cancelled` (V12).
**Depends on:** nothing.
**Forecast:** ~180 production, ~260 test.

**Outcome.** An owner can abandon a staged batch from the review screen, which frees its account
and period for re-staging.

**Files.** Modify `api/oneledger/routes/ingest.py`, `api/tests/test_import_review_live.py`,
`web/app/import/review-screen.tsx` and its test, `docs/design/api-contracts.html`; regenerate
`web/types/api/`.

**Contracts.** A new `POST /import/{batch_id}/cancel` behind `require_owner()`, filtered by the
owner's household before anything else, answering 404 for a batch of another household exactly as
its siblings do. It moves an `awaiting_review` batch to `cancelled` and no other status — a
`completed` batch is a record, not a working set, and is 409. `pipeline._refuse_second_staging`
already treats `cancelled` as finished, so cancelling is what frees the period; that is the whole
point and the test says so.

**Invariants / security.** **Cancelling deletes the batch's staged rows.** Not a tidiness
choice: `reporting_pair_is_locked()` discovers every table carrying `amount_base` beside
`household_id` and counts rows, and `staged_transaction` has both — V13 says so in its own
comment. Rows left behind by a batch that can never commit would keep the household's reporting
pair locked forever. It bites during onboarding, where the pair is still changeable: stage a
batch, notice the pair is wrong, cancel, and still be unable to change it. Nothing in the ledger
is touched — cancellation is not a reversal, and a committed batch has no cancel path. Money is
untouched throughout.

**Required test behaviours (failing first).**
- Cancelling an `awaiting_review` batch sets `cancelled` and imports nothing.
- The same account and period can then be staged again — the case that motivated this, found in
  the 1.1 acceptance run, where an abandoned batch blocked its period permanently.
- A `completed` batch is refused; a batch of another household is 404.
- A cancelled batch cannot be committed and cannot be decided.
- Its staged rows are gone, and a household with nothing committed can change its reporting pair
  again afterwards — the onboarding case the deletion exists for.
- The screen offers cancel only while the batch is `awaiting_review`, and says what it will do.

**Validation.** The API gate (`ruff`, `mypy`, `pytest -q`), the web gate, `check-docs.py`,
`scripts/check-identifiers.py`.

**Acceptance.** A mis-staged batch is recoverable from the app, with no `UPDATE` by hand — which
is what "no manual correction" requires of the 71-file run.

---

## Tasks 2c1–2c3 — the remaining layouts, one branch each (local)

**Workspace:** the owner's machine, where `pdf-statements/` exists.
**Branches:** `feature/p1-2c1-layout-two`, `feature/p1-2c2-layout-three`,
`feature/p1-2c3-layout-four`, in that order off `develop`.
**Base and PR target:** `develop`.
**Migration:** none expected; a quirk a file demands may need one and is called out if so.
**Depends on:** 2a and 2b merged; then each on the one before it.
**Forecast:** each ~0–80 production, ~300 test.

One branch per layout, because one layout's frame test alone is the size of
`test_ingest_card_frame.py` — **288 lines** — so three together are ~900 test lines before golden
output, and the engine changes are unbounded until a real file proves what is needed. A layout
that turns out to need engine work then blocks only itself. The profiles are free against the
budget: `check-diff-size.sh` skips `.json`, and `card-layout.json` is 23 lines.

**Outcome, per branch.** That bank's statements import: a saved profile, a redacted fixture, a
golden-output test, and every account of that layout reconciling across its period. The last
branch also carries the 71-file run and records it.

**Files, per branch.** Create `db/seeds/<layout>.json` — a `POST /layouts` body as
`card-layout.json` is; read `db/seeds/README.md` first, it explains why these are not `psql`
seeds and what its own three non-obvious values cost to measure. Create that layout's redacted
fixture and golden-output test under `api/tests/`. Modify `api/oneledger/ingest/engine.py` or
`normalize.py` **only where that layout's real file proves a gap** — the engine already
implements every rule M2 spec §4.4–§4.6 names, so a change needs the failing file behind it and
ships with the fixture that failed.

**Contracts.** A profile is data (ADR-0012): no bank, currency or country name becomes an
identifier, and `scripts/check-identifiers.py` fails the build on one. The layout vocabulary is
what grows, not the code — a new behaviour is a `row_start_rule`, `continuation_rule` or `quirks`
entry, and a genuinely new vocabulary member is recorded in the M2 spec §7 before it is used.

**Invariants / security.** I8: every account reconciles across its period, and a statement that
does not is blocked rather than imported. I6: every committed row names its document, page, row,
batch and layout version. Spec §5.6: no digit run of seven or more from a document reaches any
response, log or message. **No real statement, figure or filename enters the repository** — a
fixture reproduces the arithmetic and the geometry, never the amounts.

**Required test behaviours (failing first).**
- A golden-output test for this layout: its redacted fixture in, the normalized rows and the
  reconciliation verdict out, asserted exactly.
- ~~Its profile validates through `LayoutCreate`~~ — **already covered**, generically, by
  `api/tests/test_seeded_layouts.py`: every file in `db/seeds/` is validated on every run, so
  dropping the profile in is enough and no branch writes this test again.
- Where this layout's narration wraps: one row per transaction, not one per physical line.
- Where this layout is encrypted: it parses with its password and refuses without one.
- Any engine change carries the fixture that failed before it.

**Validation.** `/verify` in full, including migrations from empty and every `db/tests/*.sql`.

**Acceptance.** Per branch: that layout's statements import and its accounts reconcile. On the
last: all 71 gathered files import, every account reconciles across its period, and an encrypted
statement imports without its password touching disk — recorded in `docs/superpowers/progress.md`
with the count, as slice 1.1's run was.

## What is not in this slice

Classification and transfers are 1.3. The layout mapper UI is 1.7 — until it arrives a profile is
seeded through `POST /layouts` from a JSON body, which is the same body that UI will post. The
review screen still renders no period; that is recorded in `progress.md` and belongs to whichever
branch next touches that screen.
