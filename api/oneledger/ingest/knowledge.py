"""Load one household's classification knowledge for every cascade caller."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from .classify import Alias, Knowledge, Prior, Rule

PRIORS_PER_ACCOUNT = 500
_TXN_TYPES = {
    "income",
    "expense",
    "fee",
    "interest",
    "tax",
    "refund",
    "transfer_in",
    "transfer_out",
    "investment_buy",
    "investment_sell",
    "investment_income",
    "investment_fee",
    "loan_disbursement",
    "loan_repayment",
    "adjustment",
    "opening_balance",
}
_PAYMENT_METHODS = {
    "card",
    "contactless",
    "atm",
    "cash",
    "cheque",
    "upi",
    "neft",
    "imps",
    "rtgs",
    "wire",
    "direct_debit",
    "standing_instruction",
    "wallet",
    "online",
    "other",
}


def _rule(row: tuple[Any, ...]) -> Rule:
    return Rule(
        id=row[0],
        priority=row[1],
        origin=row[2],
        match_description_pattern=row[3],
        match_pattern_kind=row[4],
        match_account_id=row[5],
        match_currency=row[6],
        match_amount_min=row[7],
        match_amount_max=row[8],
        match_amount_exact=row[9],
        match_direction=row[10],
        match_country=row[11],
        match_reference_pattern=row[12],
        set_category_id=row[13],
        set_merchant_id=row[14],
        set_txn_type=row[15],
        set_payment_method=row[16],
        set_country=row[17],
        set_tags=tuple(row[18] or []),
        set_is_transfer_candidate=row[19],
        set_counterparty_account_id=row[20],
    )


def load_knowledge(
    cur: psycopg.Cursor[Any], household_id: UUID, *, prior_limit: int = PRIORS_PER_ACCOUNT
) -> Knowledge:
    """Read rules, legacy aliases, evidenced aliases and trusted priors once per run."""
    cur.execute(
        """SELECT id, priority, origin, match_description_pattern, match_pattern_kind::text,
                  match_account_id, match_currency, match_amount_min, match_amount_max,
                  match_amount_exact, match_direction, match_country, match_reference_pattern,
                  set_category_id, set_merchant_id, set_txn_type::text,
                  set_payment_method::text, set_country, set_tags,
                  set_is_transfer_candidate, set_counterparty_account_id
             FROM classification_rule
            WHERE household_id = %s AND is_active
            ORDER BY priority, id""",
        (household_id,),
    )
    saved = [_rule(row) for row in cur.fetchall()]

    cur.execute(
        """SELECT a.merchant_id, a.pattern, a.pattern_kind::text, a.priority,
                  m.default_category_id
             FROM merchant_alias a
             JOIN merchant m ON m.id = a.merchant_id
            WHERE a.is_active
              AND (a.household_id IS NULL OR a.household_id = %s)
              AND (m.household_id IS NULL OR m.household_id = %s)""",
        (household_id, household_id),
    )
    aliases = [
        Alias(
            merchant_id=row[0],
            pattern=row[1],
            pattern_kind=row[2],
            priority=row[3],
            category_id=row[4],
        )
        for row in cur.fetchall()
    ]

    cur.execute(
        """SELECT a.pattern, a.pattern_kind::text, a.priority, a.role,
                  a.counterparty_id, a.knowledge_version_id, c.kind, c.merchant_id,
                  v.scope, category.id, scoped_account.id
             FROM counterparty_alias a
             JOIN counterparty c
               ON c.id = a.counterparty_id AND c.household_id = a.household_id
             JOIN classification_knowledge_version v
               ON v.id = a.knowledge_version_id AND v.household_id = a.household_id
        LEFT JOIN category
               ON category.id::text = v.scope->>'categoryId'
              AND (category.household_id IS NULL OR category.household_id = a.household_id)
        LEFT JOIN account scoped_account
               ON scoped_account.id::text = v.scope->>'accountId'
              AND scoped_account.household_id = a.household_id
            WHERE a.household_id = %s AND a.is_active AND c.is_active AND v.state = 'active'
              AND (NOT (v.scope ? 'categoryId') OR category.id IS NOT NULL)
              AND (NOT (v.scope ? 'accountId') OR scoped_account.id IS NOT NULL)""",
        (household_id,),
    )
    for row in cur.fetchall():
        scope = row[8]
        direction = scope.get("direction", "any")
        txn_type = scope.get("txnType")
        payment_method = scope.get("paymentMethod")
        if direction not in {"any", "debit", "credit"}:
            continue
        if txn_type is not None and txn_type not in _TXN_TYPES:
            continue
        if payment_method is not None and payment_method not in _PAYMENT_METHODS:
            continue
        aliases.append(
            Alias(
                merchant_id=row[7] if row[6] == "merchant" else None,
                pattern=row[0],
                pattern_kind=row[1],
                priority=row[2],
                role=row[3],
                counterparty_id=row[4],
                knowledge_version_id=row[5],
                direction=direction,
                category_id=row[9],
                account_id=row[10],
                txn_type=txn_type,
                payment_method=payment_method,
            )
        )

    # A derived prior or alias never becomes evidence for another prior. Alias decisions cannot
    # prove that the knowledge which produced them is still active because transaction predates
    # that evidence link. User-rule priors retain a checkable rule id and must still be active.
    cur.execute(
        """SELECT account_id, COALESCE(description_clean, description_raw), category_id,
                  merchant_id, txn_type::text, payment_method::text, amount
             FROM (SELECT *, row_number() OVER (
                       PARTITION BY account_id ORDER BY booked_date DESC, id) AS seen
                     FROM transaction
                    WHERE household_id = %s
                      AND (classified_by = 'user_manual'
                           OR (classified_by = 'user_rule' AND EXISTS (
                               SELECT 1 FROM classification_rule r
                                WHERE r.id = classification_rule_id
                                  AND r.household_id = transaction.household_id
                                  AND r.is_active)))) ranked
            WHERE seen <= %s""",
        (household_id, prior_limit),
    )
    priors = [
        Prior(
            account_id=row[0],
            description_clean=row[1],
            category_id=row[2],
            merchant_id=row[3],
            txn_type=row[4],
            payment_method=row[5],
            direction="debit" if row[6] < 0 else "credit",
        )
        for row in cur.fetchall()
    ]
    return Knowledge(
        user_rules=[rule for rule in saved if rule.origin != "seed"],
        seed_rules=[rule for rule in saved if rule.origin == "seed"],
        aliases=aliases,
        priors=priors,
    )
