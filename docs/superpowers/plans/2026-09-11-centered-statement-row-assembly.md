# Centered statement row assembly

Date: 11 September 2026

## Context

A locally held passbook statement proved a gap that balance reconciliation cannot detect. Its
date and money cells are vertically centred beside a two-line description. PDF word extraction
therefore emits the description's first line immediately before the physical line containing the
date, amount and second description line. The current continuation rule always appends a
description-only line to the preceding transaction. Amounts and running balances remain correct,
so reconciliation matches while descriptions shift across transaction boundaries.

The generic, data-driven layout decision in ADR-0012 still stands. This producer needs an explicit
row-assembly strategy selected by layout data, not an institution-specific parser or identifier.
Existing committed source rows are immutable; repairing them is a separate guarded operation after
the parser can reproduce the correct rows.

## Task 1 — a layout can assign wrapped descriptions to the nearest dated row

**Outcome:** a profile quirk named `row_assembly:nearest_dated_row` groups qualifying
description-only physical lines with the closest row start on the same page. A tie stays with the
earlier row. Profiles without the quirk retain the current preceding-row behaviour exactly.

| Workspace | Branch | Base and PR target | Forecast |
| --- | --- | --- | --- |
| `/Users/sandjaieravi/dev/OneLedger` | `fix/p1-2-centered-statement-rows` | `develop` | about 60 production + 120 test lines; within budget |

**Affected modules:** `api/oneledger/ingest/engine.py`,
`api/oneledger/routes/layouts.py`, `api/tests/test_ingest_engine.py`,
`api/tests/test_layouts.py`, and `docs/superpowers/progress.md`.

**Dependencies:** ADR-0012's data-driven layout profile, the existing
`blank_date_and_money_columns` continuation rule, and the immutable source-field boundary. The
existing bounded `statement_layout.quirks` column means no migration or wire-shape change.

**Contract:** only description-bearing lines that already satisfy the selected continuation rule
are candidates. Row-start recognition, column bands, amounts, dates, references, running balances,
metadata captures, page provenance and row numbering stay unchanged. Candidate lines are assigned
within their page; furniture and unparseable prose remain unparsed. Raw text and description
fragments preserve physical top-to-bottom order.

**Invariants and security:** no institution, country or currency name enters application
identifiers. No real statement content enters the repository. The fix changes no money, frozen FX,
dedupe, authorization or reporting boundary. Unknown quirks remain rejected.

**Required test behaviours:** first write a synthetic regression in which a description-only line
is closer to the following dated row and show that it currently lands on the preceding row. Cover
the opt-in fix, unchanged legacy behaviour without the quirk, a tie assigned to the preceding row,
multiple fragments in physical order, and API validation accepting the new vocabulary while still
rejecting unknown values.

**Validation:** run focused engine/layout tests, then `/verify` and one local diff review against
`develop`; fix blocking findings and re-run only affected verification. Push hooks run the full
suite and diff budget.

**Local acceptance:** parse the git-ignored source statement in memory with the saved profile plus
the new quirk. Report only structural booleans/counts: row count is unchanged, the known pension
description no longer absorbs the following merchant, that merchant is assigned to its own dated
row, and running-balance reconciliation remains matched. Do not commit or print real rows or
figures.

**Acceptance criteria:** the synthetic regression is green; non-opted-in layouts are unchanged;
the local structural acceptance passes; `progress.md` records the parser fix and states honestly
that existing committed rows still need a separately designed guarded reparse before their source
descriptions can be repaired.
