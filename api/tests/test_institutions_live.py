"""Institution resolver visibility against the real schema and alias scope."""

from __future__ import annotations

from uuid import UUID

from oneledger.institutions import resolve_institution

from .live_database import connect_to_test_database


def test_shared_and_own_aliases_are_visible_but_another_households_are_not() -> None:
    conn = connect_to_test_database()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT h.id, h.base_currency, h.secondary_currency
                     FROM household h ORDER BY h.created_at LIMIT 1"""
            )
            owner = cur.fetchone()
            assert owner is not None
            owner_id, base_currency, secondary_currency = owner
            cur.execute(
                """INSERT INTO household (name, base_currency, secondary_currency)
                     VALUES ('resolver other household', %s, %s) RETURNING id""",
                (base_currency, secondary_currency),
            )
            other = cur.fetchone()
            assert other is not None
            other_id = other[0]
            institution_ids: list[UUID] = []
            for household_id, name in (
                (None, "Resolver Shared Institution"),
                (owner_id, "Resolver Owner Institution"),
                (other_id, "Resolver Other Institution"),
            ):
                cur.execute(
                    """INSERT INTO institution
                           (household_id, name, short_name, country, institution_type)
                         VALUES (%s, %s, NULL, 'XZ', 'bank') RETURNING id""",
                    (household_id, name),
                )
                inserted = cur.fetchone()
                assert inserted is not None
                institution_ids.append(inserted[0])
            for household_id, institution_id, provenance in (
                (None, institution_ids[0], "public_seed"),
                (owner_id, institution_ids[1], "owner_confirmed"),
                (other_id, institution_ids[2], "owner_confirmed"),
            ):
                cur.execute(
                    """INSERT INTO institution_alias
                           (household_id, institution_id, alias, alias_normalized,
                            country, provenance)
                         VALUES (%s, %s, 'Resolver Alias', 'resolver alias', 'XZ', %s)""",
                    (household_id, institution_id, provenance),
                )

            owner_result = resolve_institution(cur, owner_id, "Resolver Alias")
            other_result = resolve_institution(cur, other_id, "Resolver Alias")

        assert owner_result.status == other_result.status == "ambiguous"
        assert {candidate.id for candidate in owner_result.candidates} == {
            institution_ids[0],
            institution_ids[1],
        }
        assert {candidate.id for candidate in other_result.candidates} == {
            institution_ids[0],
            institution_ids[2],
        }
    finally:
        conn.rollback()
        conn.close()
