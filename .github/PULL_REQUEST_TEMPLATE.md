## What is now true

<!-- One paragraph. What was wrong or missing, and what this changes. Not a list of files. -->

## Invariants

Delete every line this diff cannot violate. Leave the rest, checked.

- [ ] Money is `Decimal` / `NUMERIC` / a string on the wire — no float, no `parseFloat`, no JSON number
- [ ] No currency, institution or country name appears as an identifier, type, enum value or locale constant
- [ ] Reporting queries read `v_transaction_reportable` and the frozen `amount_base` / `amount_secondary`
- [ ] New routes depend on `require_owner()`; new `SECURITY DEFINER` functions call `assert_is_owner()`
- [ ] Migrations are forward-only, no applied migration was edited, and `schema.md` changed with them
- [ ] No real financial data, no secret, nothing behind `NEXT_PUBLIC_` that is not a URL

## Verification

<!--
The command you ran and what it printed — not "tests pass".
Generated files, if any, and their line count, so the 500-line cap can be read honestly.
-->
