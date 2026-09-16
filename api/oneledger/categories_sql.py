# api/oneledger/categories_sql.py
"""One shared SQL fragment for a category's effective bucket and active flag (ADR-0008): a
household may re-bucket a seed category through `category_override`, and every category-aware
read must resolve `COALESCE(o.bucket, c.bucket)` / `COALESCE(o.is_active, c.is_active)`. Three
copies of that merge already exist — `routes/categories.py::MERGED`/`MERGED_FROM`,
`routes/transactions.py::_assert_category_allowed`, `resources.py::_SPENDING` — and stay as they
are; consolidating them is recorded in `docs/superpowers/project-register.md` rather than done
here. New reads (starting with `routes/spending.py`) use this fragment instead of a fourth copy.

`EFFECTIVE_CATEGORY` joins the override on the reading row's own `household_id` column, exactly
as `resources.py::_SPENDING` does, so it takes no positional parameter of its own and a caller
cannot mis-order it against the query's other placeholders. Embed it right after the base row is
aliased `t` (e.g. `FROM v_transaction_reportable t {EFFECTIVE_CATEGORY}`); `c.id`, `c.name`,
`c.parent_id`, `COALESCE(o.bucket, c.bucket)` and `COALESCE(o.is_active, c.is_active)` are then
selectable as the category's effective id, name, parent id, bucket and active flag."""

from __future__ import annotations

EFFECTIVE_CATEGORY = """LEFT JOIN category c ON c.id = t.category_id
                    LEFT JOIN category_override o
                           ON o.category_id = t.category_id AND o.household_id = t.household_id"""
