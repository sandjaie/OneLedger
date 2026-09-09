# Seeds

Data a database needs that no migration writes. Applied by hand, once, against a database that
has already been migrated.

| File | What it is | How it is applied |
| --- | --- | --- |
| `institutions.sql` | The starter institution rows | `psql "$(scripts/dsn.sh)" -f db/seeds/institutions.sql` |
| `card-layout.json` | A `POST /layouts` body for the card statement layout | through the API, not `psql` — see below |

## `card-layout.json`

A `statement_layout` row is household-scoped and is validated by `LayoutCreate`, so it is seeded
through `POST /layouts` rather than by an `INSERT`. A raw insert would bypass every rule that
model enforces — band ordering, sign-convention agreement, the quirks vocabulary, regex
compilation — and could store a profile the application refuses to save and cannot use. The
mapping UI supersedes this file entirely once it arrives; until then this is the same body that
UI would post.

`api/tests/test_ingest_card_frame.py` holds it to that: the file is validated through
`LayoutCreate` on every test run, so it cannot drift out of the shape the endpoint accepts.

Three of its values are not obvious, and each is measured rather than assumed.

**The balance anchors match a line of run-together figures, not a label.** The producer emits no
space characters between cells, so the statement summary extracts as
`6,140.404,479.180.006,140.404,479.1813,728.34` — one line, six figures, no separator. A regex
anchored on the label cannot split them, which is the same reason
[statement ingestion](../../docs/design/statement-ingestion.html) calls word coordinates
mandatory for this producer. Each figure ends in exactly two decimal places, so
`\d[\d,]*\.\d{2}` segments the run deterministically; the patterns require exactly six figures on
their own line and capture the first and the fifth. The fifth is the total payment due. The sixth
is the current balance, which also carries any remaining installment principal and reconciles
against nothing.

**`continuationRule` is null.** With `no_date_and_no_amount` the last row absorbs every trailing
line of the document — 2,232 characters of page furniture in one `description_raw`. Off, the
longest description is 46 characters. The dropped lines are counted in `lines_unparsed` and
reported in the parse summary, so the loss is visible rather than silent (Invariant 7). This
card writes each transaction on one physical line; a layout whose narration genuinely wraps sets
the rule on.

**No statement period is captured.** The card's installment block prints each plan's original
purchase date, so the statement legitimately carries rows dated outside its own period — and
those instalment charges are part of the declared purchase total the anchors reconcile against.
Capturing the period would fail those rows and break the identity. `dateFormat` carries a
four-digit year, so the derived period is sound.

Measured across the 38 card statements held locally: 2,159 rows normalized, zero row failures,
38 of 38 reconciling `matched`. Those files are git-ignored and never enter this repository.
