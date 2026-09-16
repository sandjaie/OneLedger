"""Phase 4.2 Task 1: `GET /salary/analysis` and the earning-rule routes (spec `docs/superpowers/
specs/2026-09-16-phase-4-2-salary-analysis.md`, plan `docs/superpowers/plans/2026-09-16-phase-4-2-
salary-analysis.md` "Phase 4.2 Task 1"). This module reads active payslips and their earning
lines, each payslip's receipt status, the household's `payslip_earning_rule` rows and salary
credits with no payslip, then hands the payslip rows to the pure `salary_analysis.analyze_salary`
— no figure is computed here. `GET /salary/analysis` and `GET /salary/earning-rules` open
`SET TRANSACTION READ ONLY`; the earning-rule writes take the household lock first, matching
`routes/recurring.py`'s convention.

Reimbursement is a component now (not a payslip line kind); "reused" from Phase 1.7 is exactly
`routes/payslips.py::_normalize_label_param`/`_pay_month_str`/`_salary_category_id` — no second
copy of label normalization, month formatting or the Salary category lookup.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from ..auth import OwnerSession, require_owner
from ..categories_sql import EFFECTIVE_CATEGORY
from ..db import connect
from ..salary_analysis import (
    Component,
    CurrencyAnalysis,
    EarningLineIn,
    PayslipIn,
    ReceiptStatus,
    analyze_salary,
)
from .payslips import _normalize_label_param, _pay_month_str, _salary_category_id

router = APIRouter(tags=["salary"])

_CREDIT_LIMIT = 200


class EarningLineOut(BaseModel):
    lineNo: int
    label: str
    amount: str
    component: Component
    source: Literal["rule", "inferred"]


class SalaryPayslipOut(BaseModel):
    payslipId: UUID
    payMonth: str
    compensation: str
    fixed: str
    variable: str
    reimbursement: str
    deductions: str
    netPay: str
    cashReceived: str | None
    receiptStatus: ReceiptStatus
    lines: list[EarningLineOut]


class SalaryYearOut(BaseModel):
    year: int
    compensation: str
    fixed: str
    variable: str
    variableShare: str | None
    reimbursement: str
    deductions: str
    netPay: str
    cashReceived: str
    payslipMonths: int
    gapMonths: int
    receivedMonths: int


class SalaryIncrementOut(BaseModel):
    effectiveMonth: str
    previousFixed: str
    newFixed: str
    change: str
    changePercent: str | None
    confirmed: bool
    acrossGap: bool


class SalaryVariablePayOut(BaseModel):
    payslipId: UUID
    payMonth: str
    label: str
    amount: str


class SalaryLabelOut(BaseModel):
    label: str
    component: Component
    source: Literal["rule", "inferred"]
    monthCount: int
    firstMonth: str
    lastMonth: str
    latestAmount: str


class SalaryCurrencyOut(BaseModel):
    currency: str
    years: list[SalaryYearOut]
    payslips: list[SalaryPayslipOut]
    gaps: list[str]
    increments: list[SalaryIncrementOut]
    variablePay: list[SalaryVariablePayOut]
    labels: list[SalaryLabelOut]


class SalaryCreditOut(BaseModel):
    transactionId: UUID
    bookedDate: date
    amount: str
    currency: str
    accountName: str


class SalaryAnalysisResponse(BaseModel):
    currencies: list[SalaryCurrencyOut]
    salaryCreditsWithoutPayslip: list[SalaryCreditOut]


class EarningRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: Component


class EarningRuleRow(BaseModel):
    label: str
    component: Component


class EarningRulesResponse(BaseModel):
    rules: list[EarningRuleRow]


def _load_active_payslips(cur: psycopg.Cursor[Any], household_id: UUID) -> list[PayslipIn]:
    cur.execute(
        """SELECT p.id, p.pay_month, p.currency, p.gross_earnings, p.total_deductions, p.net_pay,
                  CASE
                    WHEN p.deposit_transaction_id IS NULL THEN 'unlinked'
                    WHEN EXISTS (
                      SELECT FROM v_transaction_reportable r
                       WHERE r.id = p.deposit_transaction_id AND r.household_id = p.household_id
                         AND r.txn_type = 'income' AND rtrim(r.currency) = rtrim(p.currency)
                         AND r.amount = p.net_pay
                    ) THEN 'received'
                    ELSE 'ineligible'
                  END
             FROM payslip p
            WHERE p.household_id = %s
              AND NOT EXISTS (SELECT FROM payslip s WHERE s.supersedes_payslip_id = p.id)""",
        (household_id,),
    )
    headers = cur.fetchall()
    payslip_ids = [UUID(str(row[0])) for row in headers]
    lines_by_id: dict[UUID, list[EarningLineIn]] = {pid: [] for pid in payslip_ids}
    if payslip_ids:
        cur.execute(
            """SELECT payslip_id, line_no, label, amount FROM payslip_line
                WHERE household_id = %s AND payslip_id = ANY(%s) AND kind = 'earning'
                ORDER BY payslip_id, line_no""",
            (household_id, payslip_ids),
        )
        for payslip_id, line_no, label, amount in cur.fetchall():
            lines_by_id[UUID(str(payslip_id))].append(
                EarningLineIn(line_no=line_no, label=str(label), amount=Decimal(amount))
            )
    return [
        PayslipIn(
            payslip_id=UUID(str(row[0])),
            pay_month=row[1],
            currency=str(row[2]).strip(),
            gross=Decimal(row[3]),
            deductions=Decimal(row[4]),
            net=Decimal(row[5]),
            lines=tuple(lines_by_id[UUID(str(row[0]))]),
            receipt_status=row[6],
            cash_received=Decimal(row[5]) if row[6] == "received" else None,
        )
        for row in headers
    ]


def _load_earning_rules(cur: psycopg.Cursor[Any], household_id: UUID) -> dict[str, Component]:
    cur.execute(
        "SELECT label, component FROM payslip_earning_rule WHERE household_id = %s",
        (household_id,),
    )
    return {str(label): component for label, component in cur.fetchall()}


def _load_salary_credits_without_payslip(
    cur: psycopg.Cursor[Any], household_id: UUID
) -> list[SalaryCreditOut]:
    salary_id = _salary_category_id(cur)
    cur.execute(
        f"""SELECT t.id, t.booked_date, t.amount, t.currency, a.name
              FROM v_transaction_reportable t
              {EFFECTIVE_CATEGORY}
              JOIN account a ON a.household_id = t.household_id AND a.id = t.account_id
             WHERE t.household_id = %(household)s AND t.txn_type = 'income'
               AND (c.id = %(salary)s OR c.parent_id = %(salary)s)
               AND NOT EXISTS (
                     SELECT FROM payslip p
                      WHERE p.household_id = t.household_id AND p.deposit_transaction_id = t.id)
               AND NOT EXISTS (
                     SELECT FROM payslip_line_posting pp
                      WHERE pp.household_id = t.household_id AND pp.income_transaction_id = t.id)
             ORDER BY t.booked_date DESC, t.id DESC
             LIMIT %(limit)s""",
        {"household": household_id, "salary": salary_id, "limit": _CREDIT_LIMIT},
    )
    return [
        SalaryCreditOut(
            transactionId=row[0],
            bookedDate=row[1],
            amount=str(row[2]),
            currency=str(row[3]).strip(),
            accountName=row[4],
        )
        for row in cur.fetchall()
    ]


def _out_currency(analysis: CurrencyAnalysis) -> SalaryCurrencyOut:
    return SalaryCurrencyOut(
        currency=analysis.currency,
        years=[
            SalaryYearOut(
                year=y.year,
                compensation=str(y.compensation),
                fixed=str(y.fixed),
                variable=str(y.variable),
                variableShare=None if y.variable_share is None else str(y.variable_share),
                reimbursement=str(y.reimbursement),
                deductions=str(y.deductions),
                netPay=str(y.net_pay),
                cashReceived=str(y.cash_received),
                payslipMonths=y.payslip_months,
                gapMonths=y.gap_months,
                receivedMonths=y.received_months,
            )
            for y in analysis.years
        ],
        payslips=[
            SalaryPayslipOut(
                payslipId=p.payslip_id,
                payMonth=_pay_month_str(p.pay_month),
                compensation=str(p.compensation),
                fixed=str(p.fixed),
                variable=str(p.variable),
                reimbursement=str(p.reimbursement),
                deductions=str(p.deductions),
                netPay=str(p.net_pay),
                cashReceived=None if p.cash_received is None else str(p.cash_received),
                receiptStatus=p.receipt_status,
                lines=[
                    EarningLineOut(
                        lineNo=line.line_no,
                        label=line.label,
                        amount=str(line.amount),
                        component=line.component,
                        source=line.source,
                    )
                    for line in p.lines
                ],
            )
            for p in analysis.payslips
        ],
        gaps=[_pay_month_str(g) for g in analysis.gaps],
        increments=[
            SalaryIncrementOut(
                effectiveMonth=_pay_month_str(i.effective_month),
                previousFixed=str(i.previous_fixed),
                newFixed=str(i.new_fixed),
                change=str(i.change),
                changePercent=None if i.change_percent is None else str(i.change_percent),
                confirmed=i.confirmed,
                acrossGap=i.across_gap,
            )
            for i in analysis.increments
        ],
        variablePay=[
            SalaryVariablePayOut(
                payslipId=v.payslip_id,
                payMonth=_pay_month_str(v.pay_month),
                label=v.label,
                amount=str(v.amount),
            )
            for v in analysis.variable_pay
        ],
        labels=[
            SalaryLabelOut(
                label=label.label,
                component=label.component,
                source=label.source,
                monthCount=label.month_count,
                firstMonth=_pay_month_str(label.first_month),
                lastMonth=_pay_month_str(label.last_month),
                latestAmount=str(label.latest_amount),
            )
            for label in analysis.labels
        ],
    )


@router.get("/salary/analysis", response_model=SalaryAnalysisResponse)
def read_salary_analysis(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> SalaryAnalysisResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        payslips = _load_active_payslips(cur, owner.household_id)
        rules = _load_earning_rules(cur, owner.household_id)
        credits_without_payslip = _load_salary_credits_without_payslip(cur, owner.household_id)
    currencies = [_out_currency(analysis) for analysis in analyze_salary(payslips, rules)]
    return SalaryAnalysisResponse(
        currencies=currencies, salaryCreditsWithoutPayslip=credits_without_payslip
    )


@router.get("/salary/earning-rules", response_model=EarningRulesResponse)
def list_earning_rules(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> EarningRulesResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(
            """SELECT label, component FROM payslip_earning_rule
                WHERE household_id = %s ORDER BY label""",
            (owner.household_id,),
        )
        rules = [EarningRuleRow(label=str(r[0]), component=r[1]) for r in cur.fetchall()]
    return EarningRulesResponse(rules=rules)


@router.put("/salary/earning-rules/{label:path}", response_model=EarningRuleRow)
def save_earning_rule(
    label: str,
    body: EarningRuleIn,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> EarningRuleRow:
    normalized = _normalize_label_param(label)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            """INSERT INTO payslip_earning_rule (household_id, label, component)
               VALUES (%s, %s, %s)
               ON CONFLICT (household_id, label)
               DO UPDATE SET component = EXCLUDED.component, updated_at = now()""",
            (owner.household_id, normalized, body.component),
        )
    return EarningRuleRow(label=normalized, component=body.component)


@router.delete("/salary/earning-rules/{label:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_earning_rule(
    label: str, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> Response:
    normalized = _normalize_label_param(label)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            "DELETE FROM payslip_earning_rule WHERE household_id = %s AND label = %s",
            (owner.household_id, normalized),
        )
        removed = cur.rowcount
    if removed == 0:
        raise HTTPException(status_code=404, detail="earning rule not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
