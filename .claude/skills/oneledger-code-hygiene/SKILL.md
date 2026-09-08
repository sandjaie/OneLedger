---
name: oneledger-code-hygiene
description: Read-only audit of OneLedger for accumulated cruft — dead or unused code, duplication that should be shared, code rot, leftover migration and seed cruft, stale TODOs, orphaned files, and documentation that has drifted from the tree. It reports and deletes nothing; the cleanup ships as its own chore branch. Use at milestone boundaries, or when asked for a hygiene audit, to find dead code, find rot, find unwanted code, or clean up leftovers.
---

# oneledger-code-hygiene

Rot accumulates between milestones, not inside one. Run this **at a milestone boundary or on
request** — never as part of a feature branch. Hygiene findings that ride along in a feature PR
widen the diff, spend the size budget, and buy exactly the extra review rounds the budget exists
to prevent.

**This skill reports. It does not edit.** It reads the tree and runs read-only commands, and
deletes nothing — not an unused import, not a stale marker, not an orphaned file. The report goes
back to the owner or the implementing agent, and the cleanup ships as its own `chore/<name>`
branch through the normal cycle. A sweep that removes what it finds gives nobody the chance to
say "that one is load-bearing".

It is gate 8 of [docs/development-cycle.md](../../../docs/development-cycle.md); its trigger is a
milestone closing, or an explicit request.

## 1. Sweep

Run what the tooling already knows, then look for what it cannot see.

```bash
# Python: unused imports and names, unused arguments, commented-out code, stale noqa.
# A one-off audit selection — do not change api/pyproject.toml to make it permanent.
cd api && uv run ruff check --select F401,F811,F841,ARG,ERA,RUF100 --no-fix . ; cd ..

# TypeScript: unreferenced exports. Take each exported symbol and count its uses.
grep -rn '^export ' web/app web/components web/lib 2>/dev/null | head -100

# Markers that were meant to be temporary.
grep -rn 'TODO\|FIXME\|XXX\|HACK\|@ts-ignore\|@ts-expect-error\|eslint-disable\|# type: ignore\|noqa' \
  api/oneledger web/app web/components web/lib db scripts 2>/dev/null

# Files nothing imports or references.
git ls-files api/oneledger web/app web/components web/lib scripts
```

For each candidate, confirm it is genuinely unreferenced — including from tests, seeds,
migrations, `docker-compose.yml`, hooks, CI and the documentation — before calling it dead.
Something referenced only by its own test is dead code with a test attached; say so.

## 2. Look for the rot the tools miss

**Duplication that should be shared.** The same masking, hashing, money formatting, date parsing,
error shape or fixture builder written twice in different modules. Name both sites and where the
shared version belongs. Two similar-looking blocks that encode *different* rules are not
duplication — leave them.

**Migration and seed cruft.** A column, table, trigger, function, view, index or enum member no
longer referenced by any query, model or test. A seed row for something since removed. A
`db/tests/*.sql` asserting a shape that no longer exists.

> **Never edit an applied migration** to remove cruft — Flyway checksums them. Removal is a new
> forward migration, and it edits `docs/superpowers/schema.md` and `docs/design/data-model.html`
> in the same commit.

**Documentation drift.** `schema.md` against the migrations; `api-contracts.html` against the
routes; `web/types/api/` against the OpenAPI output; links and file references in `docs/` against
what exists (`python3 scripts/check-docs.py`). A plan or spec describing a shape the tree has
since moved past.

**Orphans.** Fixtures, scripts, test helpers, screenshots, mockups and dated reports nothing
points at. Superseded ADRs are **not** orphans — they are the record, and they stay.

**Rot in live code.** A compatibility shim for a case that no longer occurs; a flag with one
value; a branch no input can reach; a parameter every caller passes the same way; a wrapper that
only forwards.

## 3. What is never cleaned up

- **A test.** Never delete, skip or weaken one to tidy the tree. A test asserting removed
  behaviour is a finding to discuss, not a deletion.
- **An invariant, an assertion, or a `require_owner()` / `assert_is_owner()` call.** Redundant
  authorization is depth, not duplication.
- **An applied migration.**
- **An ADR body**, superseded or in force.
- **Anything a document still references** — fix the document in the same change, or leave both.
- **`pdf-statements/`**, `.env`, or anything else git-ignored. Do not touch it, do not add it.

## 4. Report

A table: finding · location · evidence it is unused or duplicated · proposed action · risk. Group
by mechanical (safe to remove: unused import, unreachable branch, stale marker) and judgement
(needs a decision: shared helper, dropped column, superseded fixture).

Then propose one `chore/` branch, sized to the same budget every other branch obeys — if the
cleanup is over 400 production lines, propose several, in the order they should ship. Whoever
implements it goes through the same cycle as any other branch, `/oneledger-code-review` included:
a deletion is a change like any other, and this report is not a licence to skip the gate.
