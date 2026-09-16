# Phase 4.2 — salary analysis

Roadmap outcome ([Phase 4.2](../../product/roadmap.html)): compensation, cash received, bonuses and
increment history reconcile to payslip and ledger evidence without inventing missing periods. The
[master plan](../../product/master-implementation-plan.html#phase-4-delivery) lists compensation
versus cash received, bonuses, increments and deeper payslip history. This builds on the Phase 1.7
payslips of the [salary and forecast specification](2026-09-14-phase-1-7-salary-forecast.md) and
adds no second payslip model. Plan: [Phase 4.2
plan](../plans/2026-09-16-phase-4-2-salary-analysis.md).

## Outcome

The Salary screen answers, per currency and per year: what the owner was paid (compensation), how
much of it was fixed pay and how much was variable pay such as bonuses, what was deducted, what
arrived in the bank, when fixed pay changed and by how much, and where the evidence is missing — a
month with no payslip, a payslip whose credit is not linked, or a salary credit with no payslip.

## Evidence, not inference of money

- Every figure is a sum of recorded **active** payslip lines (a payslip no correction supersedes) or
  of linked ledger credits read through `v_transaction_reportable`. Nothing is projected,
  annualised, interpolated or averaged across a missing month.
- A month between a currency's first and last active payslip with no active payslip is a **gap**. It
  is listed, never filled, and is counted beside every yearly total.
- Payslip amounts stay in the payslip's own currency. There is no conversion and no cross-currency
  total (ADR-0002: payslips carry no frozen base amount).

## Earning components

Phase 1.7 records an earning line only as a free-text label. Analysis needs to know what kind of pay
a line is, so every earning label has one **component**:

- **fixed** — base pay and regular allowances;
- **variable** — bonuses, overtime, arrears and other irregular pay;
- **reimbursement** — money repaid to the owner, which is cash received but not compensation.

Following the owner's opt-out direction of 16 September 2026, components are inferred and the owner
corrects them. The inference is deterministic and computed on read (no AI before Phase 5, ADR-0003):
within one currency, a label is **fixed** when it appears in at least three payslip months and in at
least three quarters of the payslip months from its first to its last month; otherwise it is
**variable**. Reimbursement is never inferred. The owner's choice for a label is stored as an
earning rule, applies to every month carrying that label, and always wins over the inference.
Removing the rule returns the label to the inference.

## Figures

A currency can have more than one active payslip in a month (a separate bonus payslip), so the
active payslips sharing a month form one **payslip month** whose figures are their sums. For each
payslip month: **compensation** is fixed plus variable earnings; **reimbursement** is the
reimbursement earnings; **deductions** and **net pay** are the payslip's own totals, so compensation
plus reimbursement less deductions equals net pay. **Cash received** is the linked credit's amount
when the link is eligible (Phase 1.7 requires it to equal net pay); a payslip is otherwise
**unlinked** (no link) or **ineligible** (a link whose credit is no longer eligible) and has no cash
received.

Each calendar year sums those figures over its active payslips and reports its payslip months, its
gap months and its months with cash received.

## Increments

An **increment** is a change in a payslip month's fixed pay from the previous payslip month in the
same currency that was not a one-off. A change that the next payslip month reverses is a one-off (a
partial month, an arrears adjustment) and is not an increment. A change in the latest payslip month
is shown as **unconfirmed** until a later payslip month exists. An increment across a gap says so.
The percentage change is `(new − previous) / previous × 100` to two places half-even, null when the
previous fixed pay is zero.

## Variable pay

Every variable earning line is listed by month with its label and amount, and each year reports its
variable total and its share of compensation to two places.

## Salary credits without a payslip

A reportable income credit whose effective category is Salary or one of its children, which no
payslip links and which is not the income leg of a deduction counted as spending (V44), is listed by
booked date. It is evidence of pay with no payslip, never matched to a month automatically.

## Deeper history

Every earning label lists its component, whether that came from the owner or the inference, the
months it appears on, its first and last month and its latest amount; each month lists its lines
with their components, so a label's history reads straight from the payslips.

## Non-goals

Employer contributions (payslips carry no such line; project register); scanned or other payslip
layouts (project register); pay dates; tax-year views; matching an unlinked credit to a payslip
automatically; projected or annualised salary; conversion between payslip currencies; forecast use
of payslips; charts beyond the existing table and summary patterns.

## Required test evidence

- Live: components inferred for a monthly label and an irregular one; an owner rule overriding both
  ways and its removal; reimbursement leaving compensation but not net pay; yearly totals exact and
  reconciling to net pay; a gap listed and counted, never filled; a superseded payslip excluded;
  cash received for a linked eligible credit and none for unlinked and ineligible; an increment, a
  reversed one-off that is not one, an unconfirmed latest change and one across a gap; a separate
  bonus payslip in the same month adding no month and no increment; the salary credit without a
  payslip listed while a linked credit and a V44 income leg are not; another household's payslips,
  rules and credits never appear; a GET writes nothing.
- Web: the yearly summary, months with gaps, increments, variable pay, labels with a component
  change, and credits without a payslip render from server strings.

## Acceptance

Owner-operated on real payslips: each year's compensation and net pay agree with the payslips, the
fixed and variable split is right after at most a few label corrections, increments match the
owner's pay history, and every missing payslip month or unlinked credit is visible. Record
structural counts only.
