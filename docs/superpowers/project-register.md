# OneLedger project register

The controlled intake for work that must not be forgotten but is not part of the current delivery
action. The [roadmap](../product/roadmap.html) owns approved product outcomes, the
[master implementation plan](../product/master-implementation-plan.html) owns delivery order, and
[progress](progress.md) owns live implementation state.

Updated: 17 September 2026.

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
| 12 Sep 2026 | Show whether an import period was derived on the batch review screen | The import check screen and document library already show this parser fact; the batch review screen does not | Include in the next focused review-screen delivery |
| 12 Sep 2026 | Surface card metadata-read failures on the import screen | The backend reports the condition but the import screen does not | Include in the next focused import-screen delivery |
| 12 Sep 2026 | Read a transfer fee from a statement rather than from the owner | Phase 4.4 Task 1 closed the owner-entered half: `PATCH /transfers/{id}` records a fee and folds it into `total_cost_source`. No layout this ledger reads itemises a remittance fee, so extraction stays unevidenced | Reconsider when a source document exposes a reliable fee value |
| 21 Sep 2026 | A remittance provider catalogue to choose from rather than free text | Phase 4.4 keeps `transfer.provider` as the owner's own text, so two spellings are two rows until they correct one. A `payment_channel`-style seeded table would group them, and neither design document asks for one | When correcting spellings by hand becomes the friction rather than the labelling itself |
| 21 Sep 2026 | FX cost for cross-currency `investment_funding` and other non-remittance kinds | `docs/design/transfers-and-reconciliation.html` §2 tracks FX cost for a cross-currency funding leg, but its §5 cost analysis and Phase 4.4 cover `remittance` only | When cross-currency funding legs carry enough evidence to cost |
| 21 Sep 2026 | A corridor-level "switch provider and save" recommendation | Phase 4.4 states one bounded opportunity-cost figure per corridor-year and stops there; advice would need per-provider limits, speed and reliability the ledger does not hold | Only as an approved product outcome |
| 14 Sep 2026 | Per-lot lineage across splits, switches and platform transfers | Owner re-scoped Phase 1.6 to holding-level corporate actions; V40's `predecessor_lot_id` is present but unused | Before Phase 5.6 planning, or when the owner needs lot cost after a corporate action |
| 14 Sep 2026 | Lot-level realised gain after a corporate action, and a realised-gain view | Phase 1.6 withholds basis and realised claims from a corporate action's date; the disposal lot-allocation API remains | Phase 4.3 planning |
| 14 Sep 2026 | Platform and currency allocation groupings | Phase 1.6 reports holding and portfolio XIRR, and allocation by asset class and account; Phase 4.3 Task 1 closed the account- and asset-class XIRR half on `GET /portfolio/analysis` | Phase 4.3 planning or when platform/currency groupings are needed |
| 14 Sep 2026 | Resolve a blocked import group inside the import | Blocked groups stay uncommitted; the owner cancels or uses owner entry | When blocked groups recur in real monthly imports |
| 14 Sep 2026 | Switch charges (exit load, stamp duty) | The event matrix records no switch charge, so such a switch fails closed | When real evidence contains one |
| 14 Sep 2026 | Backdated unit events before a platform transfer | V40's order guard refuses them although holding-level transfers carry explicit units | Phase 2.2–2.4 planning — elapsed 16 Sep 2026 (Phases 2.2–2.4 planned and delivered) without this review; awaits an owner decision |
| 14 Sep 2026 | Persist the price-source check time | Refresh throttling lives in process memory, so a restart refetches once | If refetches become noticeable |
| 14 Sep 2026 | Scanned payslips and payslip layouts other than the supported monthly pay advice | Phase 1.7 imports one content-selected text-PDF layout; other files are reported unsupported | When the owner receives a payslip in another layout |
| 14 Sep 2026 | Withdraw a payslip entered in error without a replacement | Phase 1.7 corrections only supersede; an exact duplicate month is refused at entry | When a mistaken payslip must be removed |
| 14 Sep 2026 | Employer-contribution payslip lines | Phase 1.7 records payment and deduction lines only; Phase 4.2 Task 1 closed the reimbursement half of this item by adding `reimbursement` as an earning component, distinct from compensation | When employer-paid contributions (pension, insurance) need their own evidenced figure |
| 14 Sep 2026 | Keep a linked payslip credit eligible when its ledger row is reclassified | Phase 1.7 checks eligibility when linking and reports it on read; Phase 4.2 Task 1 keeps this read-time-only and now surfaces the stale link as `receiptStatus: ineligible` in `GET /salary/analysis`, rather than silently dropping its cash-received figure | When the owner needs a route to re-link or acknowledge a reclassified credit, rather than only seeing it flagged |
| 14 Sep 2026 | Search document contents | Phase 1.8 filters documents by type, account, period and import status; indexing statement text widens the private-data surface | Only through a separate privacy decision |
| 15 Sep 2026 | Same-date valuation snapshot correction | UI completion Task 2a's `POST /accounts/{accountId}/valuation-snapshots` is append-only (`preserve_valuation_snapshot`): a mistyped same-date valuation has no correction route and needs a migration to add one | When a real mistyped same-date valuation occurs, or Phase 4.3 planning |
| 15 Sep 2026 | Per-holding links after an investment import | UI gaps Task 3a links each committed import group to the portfolio: `CommittedGroupOutcome` holds no holding id and one group can touch several holdings | When import follow-up (funding a just-imported purchase) becomes a frequent owner task |
| 16 Sep 2026 | A new service read as a price change across imports | Recurring refresh reads a charge more than 20% away from the only series on a billing descriptor as that series' price change when it lands one cadence after the old price's last charge. If a genuinely new service's first charge arrives in an import on its own, before the old service's next charge, it is indistinguishable within one refresh and joins as a price change; the old service's later charges then cluster with its old members and are never proposed. Monthly statements usually bring both charges in one import, which the timing rule separates | When a real descriptor carries two services at different prices, or when Phase 4.1 subscriptions Task 2 adds billing periods (a second on-cadence new-price charge could then be required before accepting a price change) |
| 15 Sep 2026 | Record a switch or systematic transfer into a not-yet-held security, or a platform transfer into a new account, from the app | UI completion Task 2b's `investment-event-form.tsx` picks a switch/systematic-transfer's "in" security from securities the household already holds in the same account (via `/portfolio/positions`), and a platform-transfer's "in" account from the existing accounts list; neither offers the catalogue-resolve lookup `entry-screen.tsx` uses for a first acquisition, so the destination must already exist | When the owner needs to switch or transfer into an instrument or account not yet recorded |
| 15 Sep 2026 | Planned remittances with funding and receipt dates, fees and an estimated rate (Finance OS F07) | Phase 3.2 models a cross-currency remittance as two ordinary commitments; a single record naming both legs, a fee and an estimated rate is not built | Phase 3.3 planning — elapsed 16 Sep 2026 (Phase 3.3 planned and delivered) without this review; awaits an owner decision, or when a real remittance's two-commitment workaround proves confusing |
| 15 Sep 2026 | Data-quality and review-queue items inside the action list (Finance OS F08) | Phase 3.2 Task 2's monthly review surfaces coverage gaps, transfer candidates and unclassified postings only inside the review itself, each linking to where it is resolved; they are not also gathered as their own `GET /actions` item kind the way a commitment or card due is | When the owner wants those items actionable from Needs attention between monthly reviews |
| 16 Sep 2026 | Consolidate the four copies of the effective-category merge (`COALESCE(o.bucket, c.bucket)` / `COALESCE(o.is_active, c.is_active)`) | Phase 4.1 Task 1 added `api/oneledger/categories_sql.py::EFFECTIVE_CATEGORY` for new reads and left `routes/categories.py::MERGED`/`MERGED_FROM`, `routes/transactions.py::_assert_category_allowed` and `resources.py::_SPENDING` as they were, per the task's own contract | Before a fifth copy would otherwise be written, or in a dedicated hygiene pass |
| 16 Sep 2026 | A catalogue of known services to choose from for a billing descriptor | The [subscriptions spec](specs/2026-09-16-phase-4-1-subscriptions.md)'s own non-goal: a detected or owner-added series takes its label from the charge's own description or the owner's free text; no seeded or fetched list of known service names exists to pick from | When the owner finds free-text labelling for a subscription tedious enough to want autocomplete |
| 16 Sep 2026 | Merging two recurring series | The same non-goal: two series on one descriptor (spec "one descriptor, several subscriptions") stay apart even when the owner learns they are in fact the same subscription; there is no action that folds one series' periods and members into another's | When a real duplicate series (a double-detected subscription, or a payment channel switch split into two series) needs combining |

## Unapproved discoveries and improvement candidates

No unapproved product candidate is currently recorded. New candidates enter here before they can
change the roadmap.

## Unresolved owner decisions and external prerequisites

| Added | Decision or prerequisite | Why it matters | Review condition |
| --- | --- | --- | --- |
| 6 Sep 2026 | Complete the permitted-account Google sign-in and protected-route check | Automated checks cannot prove the real identity-provider journey | Tracked as an open gate in [progress](progress.md#active-delivery-gates) |
| 6 Sep 2026 | Complete second-account rejection and account-chooser recovery | The rejection boundary needs real provider evidence | Tracked as an open gate in [progress](progress.md#active-delivery-gates) |
| 6 Sep 2026 | Gather missing history back to the approved anchor | Phase 2.1 can reconcile only evidence that exists | Before Phase 2.1 owner acceptance |
| 6 Sep 2026 | Obtain complete investment history where available | Phase 1.6 and later return analysis need transaction-level continuity; Phase 4.3 Task 1 does not infer missing history | Before Phase 4.3 owner acceptance or when a gap blocks analysis |
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
