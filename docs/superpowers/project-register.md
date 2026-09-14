# OneLedger project register

The controlled intake for work that must not be forgotten but is not part of the current delivery
action. The [roadmap](../product/roadmap.html) owns approved product outcomes, the
[master implementation plan](../product/master-implementation-plan.html) owns delivery order, and
[progress](progress.md) owns live implementation state.

Updated: 14 September 2026.

## How entries move

1. Record a discovery, proposal, decision, risk, or deferred concern here without treating it as
   committed product scope.
2. State the evidence and the condition that should cause another review.
3. Obtain owner approval before adding a product outcome to the roadmap or assigning a Phase N.M
   identifier.
4. Remove promoted or rejected entries from this active register. Git history retains the intake
   record.

No entry may contain real financial figures, statement filenames, complete account identifiers, or
an inferred institution identity.

## Deferred approved work

| Added | Item | Reason or evidence | Review condition |
| --- | --- | --- | --- |
| 14 Sep 2026 | Drag band edges over a rendered page in the layout mapper | Phase 1.8 edits bands as numbers beside a live parsed preview; a rendered page widens the private-data surface | When numeric band entry proves tedious for a real new layout |
| 14 Sep 2026 | Link investment events to their source document, page and row | Investment evidence retains document, page and row, but no owner read exposes them; Phase 1.8 links ledger rows | When an investment figure needs source inspection, or Phase 4.3 planning |
| 12 Sep 2026 | Show whether an import period was derived | The review screen does not expose this existing parser fact | Include in the next focused review-screen delivery |
| 12 Sep 2026 | Remove a harmless repeated-header word from one encrypted layout's narration | Acceptance reconciles, but the retained description can be cleaner | Address with Phase 1.8 mapping diagnostics |
| 12 Sep 2026 | Surface card metadata-read failures on the import screen | The backend reports the condition but the import screen does not | Include in the next focused import-screen delivery |
| 12 Sep 2026 | Capture transfer fees when a trustworthy source supplies them | Current statement evidence does not expose a reliable fee value | Reconsider when a source document or owner-entered path exists |
| 14 Sep 2026 | Per-lot lineage across splits, switches and platform transfers | Owner re-scoped Phase 1.6 to holding-level corporate actions; V40's `predecessor_lot_id` is present but unused | Before Phase 5.6 planning, or when the owner needs lot cost after a corporate action |
| 14 Sep 2026 | Lot-level realised gain after a corporate action, and a realised-gain view | Phase 1.6 withholds basis and realised claims from a corporate action's date; the disposal lot-allocation API remains | Phase 4.3 planning |
| 14 Sep 2026 | Account- and asset-class XIRR; platform and currency allocation groupings | Phase 1.6 reports holding and portfolio XIRR, and allocation by asset class and account | Phase 4.3 planning |
| 14 Sep 2026 | Resolve a blocked import group inside the import | Blocked groups stay uncommitted; the owner cancels or uses owner entry | When blocked groups recur in real monthly imports |
| 14 Sep 2026 | Switch charges (exit load, stamp duty) | The event matrix records no switch charge, so such a switch fails closed | When real evidence contains one |
| 14 Sep 2026 | Backdated unit events before a platform transfer | V40's order guard refuses them although holding-level transfers carry explicit units | Phase 2.1 planning |
| 14 Sep 2026 | Persist the price-source check time | Refresh throttling lives in process memory, so a restart refetches once | If refetches become noticeable |
| 14 Sep 2026 | Scanned payslips and payslip layouts other than the supported monthly pay advice | Phase 1.7 imports one content-selected text-PDF layout; other files are reported unsupported | When the owner receives a payslip in another layout |
| 14 Sep 2026 | Withdraw a payslip entered in error without a replacement | Phase 1.7 corrections only supersede; an exact duplicate month is refused at entry | When a mistaken payslip must be removed |
| 14 Sep 2026 | Employer-contribution and reimbursement payslip lines | Phase 1.7 records payment and deduction lines only | Phase 4.2 planning |
| 14 Sep 2026 | Keep a linked payslip credit eligible when its ledger row is reclassified | Phase 1.7 checks eligibility when linking and reports it on read | Phase 4.2 planning |
| 14 Sep 2026 | Search document contents | Phase 1.8 filters documents by type, account, period and import status; indexing statement text widens the private-data surface | Only through a separate privacy decision |

## Unapproved discoveries and improvement candidates

No unapproved product candidate is currently recorded. New candidates enter here before they can
change the roadmap.

## Unresolved owner decisions and external prerequisites

| Added | Decision or prerequisite | Why it matters | Review condition |
| --- | --- | --- | --- |
| 6 Sep 2026 | Complete the permitted-account Google sign-in and protected-route check | Automated checks cannot prove the real identity-provider journey | Owner performs the real browser check |
| 6 Sep 2026 | Complete second-account rejection and account-chooser recovery | The rejection boundary needs real provider evidence | Owner performs the real browser check with a second account |
| 6 Sep 2026 | Gather missing history back to the approved anchor | Phase 2.1 can reconcile only evidence that exists | Before Phase 2.1 planning |
| 6 Sep 2026 | Obtain complete investment history where available | Phase 1.6 and later return analysis need transaction-level continuity | During Phase 1.6 design and import planning |
| 6 Sep 2026 | Confirm whether a real loan exists | Loan support is conditional and must not be speculative | Before activating Phase 4.6 |
| 6 Sep 2026 | Confirm whether real property evidence exists | Property uses dated valuation snapshots, but no asset should be created without owner evidence | Before property enters Phase 2.1 coverage |
| 6 Sep 2026 | Decide whether emergency handover ever permits another person access | This changes the single-owner privacy boundary | Only through a separate product and privacy decision |

The detailed scoping-question record remains in [questions](questions.html). A question is linked
from an implementation specification when it becomes a prerequisite; this register does not mirror
the full questionnaire.

## Known risks and operational gaps

| Added | Risk or gap | Current boundary | Review condition |
| --- | --- | --- | --- |
| 12 Sep 2026 | The documents volume has no independent backup copy | Database backup does not preserve imported originals | Plan a focused operations delivery before originals exist only on one device |
| 12 Sep 2026 | Current restore evidence does not include a fresh row-count and structural comparison | Existing restore helpers are protected, but recovery has not been recently demonstrated end to end | Phase 2.4 recovery acceptance |
| 12 Sep 2026 | Paid or hosted infrastructure can expand the privacy and operations surface | The current deployment remains local and isolated | Reassess only when an approved outcome requires hosting |

## Outside approved scope

Mobile applications, multi-user collaboration, stored bank credentials, automatic payments or
trades, receipt OCR, advisor access, business accounting, and unsupported complex instruments are
not approved roadmap outcomes. A future proposal starts in the candidate section above and follows
the normal owner-approval path.
