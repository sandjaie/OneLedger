"""Starter classification-rule packs, shipped inside the API package rather than in
`db/seeds/rules/` — one source of truth for the same JSON `routes/rules.py` loads
(`db/seeds/README.md`). `importlib.resources` reads them so the path works the same whether the
package runs from a checkout or an installed wheel.
"""
