"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import { useEffect, useRef, useState } from "react";

import { errorText, type apiFetch } from "@/lib/api/client";
import type {
  FundingAllocationInput,
  FundingConfirmationResponse,
  FundingProposalResponse,
  FundingUnlinkResponse,
  HoldingCashLink,
  TransactionRow,
  TransactionsResponse,
} from "@/types/api";

/** Every field this panel actually reads off an event — a strict subset of both
 * `InvestmentEventRow` (the acquisition route's own response, used fresh from entry-screen) and
 * the holding page's thinner `HoldingEventRow`, so either satisfies it without a cast. */
export type FundableEvent = {
  id: string;
  eventDate: string;
  eventKind: string;
  currency: string;
  netAmount: string;
};

/** Every field this panel reads off a confirmed allocation — carries its own currency (neither
 * `ConfirmedFundingAllocation` nor a seeded `HoldingCashLink` share one shape that has it in the
 * same place) rather than depending on the cash transaction being loaded to display one. */
type FundedAllocation = { id: string; cashTransactionId: string; allocatedAmount: string; currency: string };

type Props = {
  events: Array<FundableEvent>;
  request: typeof apiFetch;
  // Cash already linked to these events before this panel opened (the holding page's own
  // `HoldingDetail.cashLinks`) — seeded so the owner can review and unlink prior funding, not
  // only funding confirmed in this session.
  existingLinks?: Array<HoldingCashLink>;
  // False (the default) matches the original single-shot flow: once anything is confirmed, the
  // draft form is done and only the confirmed/seeded list shows. A caller that knows the event
  // is not yet fully funded — the holding page, from the server's own `fundingComplete` — passes
  // true so the draft form stays offered alongside seeded or just-confirmed links. The server
  // remains the authority on whether a further proposal is actually accepted.
  allowAdditionalFunding?: boolean;
  // Called after a confirmation or an unlink succeeds, so a caller holding the source-of-truth
  // record (the holding page's own `HoldingDetail`) can reload it — this panel never re-fetches
  // its own seed.
  onChanged?: () => void;
};

type LoadState = "idle" | "loading" | "ready" | "error";
type AllocationDraft = FundingAllocationInput & { key: number };
type UnverifiedOutcome = { kind: "confirmation" | "removal"; detail: string };
type RemovalResult = { label: string; response: FundingUnlinkResponse };
type Confirmed = { allocations: Array<FundedAllocation> } | undefined;

const TRANSACTION_QUERY = "/transactions?amountMax=0&sort=bookedDate&direction=desc&limit=50&offset=";
const MAX_AUTO_PAGES = 4;
const JSON_HEADERS = { "Content-Type": "application/json" };

function purchaseLabel(event: FundableEvent) {
  return `${event.eventDate} · ${event.eventKind.replaceAll("_", " ")} · ${event.currency} ${event.netAmount}`;
}

function cashLabel(transaction: TransactionRow) {
  return `${transaction.bookedDate} · ${transaction.accountName} · ${transaction.descriptionRaw} · ${transaction.currency} ${transaction.amount}`;
}

function isEligible(transaction: TransactionRow) {
  return transaction.amount.startsWith("-")
    && transaction.transferId === null
    && !transaction.isSplitParent
    && !transaction.txnType.startsWith("investment_");
}

function newDraft(key: number, events: Array<FundableEvent>): AllocationDraft {
  return {
    key,
    investmentEventId: events.length === 1 ? events[0].id : "",
    cashTransactionId: "",
    allocatedAmount: "",
  };
}

export function FundingPanel({ events, request, existingLinks = [], allowAdditionalFunding = false, onChanged }: Props) {
  const nextKey = useRef(2);
  const inFlight = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  const reloadButton = useRef<HTMLButtonElement>(null);
  const removalOrigin = useRef<HTMLButtonElement>(null);
  const restoreRemovalFocus = useRef(false);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [transactions, setTransactions] = useState<Array<TransactionRow>>([]);
  const [nextOffset, setNextOffset] = useState(0);
  const [transactionTotal, setTransactionTotal] = useState(0);
  const [drafts, setDrafts] = useState<Array<AllocationDraft>>([newDraft(1, events)]);
  const [proposal, setProposal] = useState<FundingProposalResponse>();
  // Seeded once, from the links this panel opened with — a purchase funded before this session
  // shows the same "review removal" action as one confirmed just now.
  const [confirmed, setConfirmed] = useState<Confirmed>(() => {
    const eventIds = new Set(events.map((event) => event.id));
    const seeded = existingLinks
      .filter((link) => link.role === "funding" && eventIds.has(link.investmentEventId))
      .map((link) => ({
        id: `${link.investmentEventId}:${link.cashTransactionId}`,
        cashTransactionId: link.cashTransactionId,
        allocatedAmount: link.allocatedAmount,
        currency: link.currency,
      }));
    return seeded.length > 0 ? { allocations: seeded } : undefined;
  });
  const [removingCashId, setRemovingCashId] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [stale, setStale] = useState(false);
  const [unverified, setUnverified] = useState<UnverifiedOutcome>();
  const [removalResult, setRemovalResult] = useState<RemovalResult>();
  const [feedback, setFeedback] = useState("");

  useEffect(() => {
    if (proposal) confirmButton.current?.focus();
  }, [proposal]);

  useEffect(() => {
    if (removingCashId) removeButton.current?.focus();
  }, [removingCashId]);

  useEffect(() => {
    if (unverified) reloadButton.current?.focus();
  }, [unverified]);

  useEffect(() => {
    if (removingCashId || !restoreRemovalFocus.current) return;
    restoreRemovalFocus.current = false;
    if (removalOrigin.current?.isConnected) removalOrigin.current.focus();
    else heading.current?.focus();
  }, [removingCashId, confirmed]);

  const eligible = transactions.filter(isEligible);
  const hasMoreTransactions = nextOffset < transactionTotal;
  const draftsComplete = drafts.length > 0 && drafts.every((draft) => (
    draft.investmentEventId && draft.cashTransactionId && draft.allocatedAmount
  ));

  function begin() {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    setProblem("");
    setFeedback("");
    return true;
  }

  function end() {
    inFlight.current = false;
    setBusy(false);
  }

  async function readCandidatePages(offset: number, existing: Array<TransactionRow>) {
    const collected = [...existing];
    let cursor = offset;
    let total = transactionTotal;
    for (let page = 0; page < MAX_AUTO_PAGES; page += 1) {
      const response = await request<TransactionsResponse>(`${TRANSACTION_QUERY}${cursor}`);
      collected.push(...response.transactions);
      total = response.total;
      cursor += response.transactions.length;
      if (response.transactions.length === 0 || cursor >= total || collected.some(isEligible)) break;
    }
    return { collected, cursor, total };
  }

  async function loadTransactions({ append = false, recovery = false } = {}) {
    if (!begin()) return;
    setLoadState("loading");
    try {
      const result = await readCandidatePages(append ? nextOffset : 0, append ? transactions : []);
      setTransactions(result.collected);
      setNextOffset(result.cursor);
      setTransactionTotal(result.total);
      setLoadState("ready");
      if (recovery) {
        setUnverified(undefined);
        setProposal(undefined);
        setConfirmed(undefined);
        setRemovingCashId("");
        setDrafts([newDraft(1, events)]);
        setFeedback("Cash evidence reloaded. Review the current rows before another funding action.");
      }
    } catch (error) {
      const detail = errorText(error, "Cash entries could not be loaded. Retry.");
      if (recovery && unverified) setUnverified({ ...unverified, detail });
      else setProblem(detail);
      setLoadState("error");
    } finally {
      end();
    }
  }

  function invalidateProposal() {
    setProposal(undefined);
    setStale(false);
    setProblem("");
  }

  function changeDraft(key: number, field: keyof FundingAllocationInput, value: string) {
    setDrafts((current) => current.map((draft) => (
      draft.key === key ? { ...draft, [field]: value } : draft
    )));
    invalidateProposal();
  }

  function addDraft() {
    const key = nextKey.current;
    nextKey.current += 1;
    setDrafts((current) => [...current, newDraft(key, events)]);
    invalidateProposal();
  }

  function removeDraft(key: number) {
    setDrafts((current) => current.filter((draft) => draft.key !== key));
    invalidateProposal();
  }

  function allocationBody() {
    return drafts.map(({ investmentEventId, cashTransactionId, allocatedAmount }) => ({
      investmentEventId,
      cashTransactionId,
      allocatedAmount,
    }));
  }

  async function propose() {
    if (unverified || !draftsComplete || !begin()) return;
    try {
      const response = await request<FundingProposalResponse>("/investment-funding/proposals", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ allocations: allocationBody() }),
      });
      setProposal(response);
      setStale(false);
    } catch (error) {
      setProblem(errorText(error, "The funding proposal was not accepted. Review the exact rows."));
    } finally {
      end();
    }
  }

  async function confirm() {
    if (unverified || !proposal || !begin()) return;
    try {
      const response = await request<FundingConfirmationResponse>("/investment-funding/confirmations", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({
          allocations: allocationBody(),
          expectedState: proposal.expectedState,
        }),
      });
      // The confirmation response carries no currency; the event it funds always shares its
      // own (the proposal route refuses a currency mismatch), so that is the honest source.
      const justConfirmed = response.allocations.map((allocation) => ({
        id: allocation.id,
        cashTransactionId: allocation.cashTransactionId,
        allocatedAmount: allocation.allocatedAmount,
        currency: eventFor(allocation.investmentEventId)?.currency ?? "",
      }));
      setConfirmed((current) => ({
        allocations: [...(current?.allocations ?? []), ...justConfirmed],
      }));
      setProposal(undefined);
      setDrafts([newDraft(nextKey.current, events)]);
      nextKey.current += 1;
      setFeedback("Funding confirmed from the reviewed server proposal.");
      onChanged?.();
    } catch (error) {
      setProposal(undefined);
      const detail = errorText(error, "The funding confirmation response was not received.");
      if ((error as { status?: unknown } | null)?.status === 409
        && detail === "funding proposal state changed") {
        setProblem(detail);
        setStale(true);
      } else {
        setProblem("");
        setUnverified({ kind: "confirmation", detail });
      }
    } finally {
      end();
    }
  }

  function reviewRemoval(cashTransactionId: string, origin: HTMLButtonElement) {
    removalOrigin.current = origin;
    setRemovingCashId(cashTransactionId);
    setProblem("");
    setFeedback("");
    setRemovalResult(undefined);
  }

  function closeRemoval() {
    restoreRemovalFocus.current = true;
    setRemovingCashId("");
  }

  async function unlink() {
    if (unverified || !removingCashId || !begin()) return;
    const cashId = removingCashId;
    const transaction = transactionFor(cashId);
    try {
      const response = await request<FundingUnlinkResponse>(
        `/investment-funding/cash-transactions/${cashId}`,
        { method: "DELETE" },
      );
      setConfirmed((current) => {
        if (!current) return undefined;
        const allocations = current.allocations.filter((allocation) => allocation.cashTransactionId !== cashId);
        return allocations.length > 0 ? { ...current, allocations } : undefined;
      });
      // A seeded link's cash transaction was likely never loaded (loading it needs "Find cash
      // entries", which a review-and-unlink of an already-funded purchase has no reason to
      // press first) — the removal still happened and still gets a result, just without the
      // transaction's own description.
      setRemovalResult({ label: transaction ? cashLabel(transaction) : "This cash entry", response });
      closeRemoval();
      onChanged?.();
    } catch (error) {
      setProblem("");
      setRemovingCashId("");
      setUnverified({
        kind: "removal",
        detail: errorText(error, "The funding removal response was not received."),
      });
    } finally {
      end();
    }
  }

  function transactionFor(id: string) {
    return transactions.find((transaction) => transaction.id === id);
  }

  function eventFor(id: string) {
    return events.find((event) => event.id === id);
  }

  const confirmedCashIds = Array.from(new Set(
    confirmed?.allocations.map((allocation) => allocation.cashTransactionId) ?? [],
  ));
  const removingTransaction = transactionFor(removingCashId);

  return (
    <section aria-labelledby="funding-title" className="investment-funding-panel">
      <div className="investment-docket-head">
        <div>
          <p className="section-label">Cash evidence</p>
          <h2 id="funding-title" ref={heading} tabIndex={-1}>Purchase funding</h2>
        </div>
        <small>A proposal checks exact ledger rows. Only your confirmation creates links.</small>
      </div>

      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {feedback && <p className="investment-feedback" role="status">{feedback}</p>}
      {removalResult && (
        <p className="investment-feedback investment-removal-result" role="status">
          <strong>{removalResult.label}</strong>
          <span>{removalResult.response.removedAllocationCount} funding allocation{removalResult.response.removedAllocationCount === 1 ? "" : "s"} removed. Cash entry restored to {removalResult.response.restoredTransactionType}.</span>
        </p>
      )}

      {unverified && (
        <div className="investment-funding-stale">
          <p className="inline-alert" role="alert">
            {unverified.kind === "confirmation" ? "Confirmation" : "Removal"} outcome not verified. {unverified.detail}
          </p>
          <p>No claim is made about whether the funding state changed.</p>
          <Button variant="outline" disabled={busy} onClick={() => void loadTransactions({ recovery: true })} ref={reloadButton} type="button">
            Reload cash evidence
          </Button>
        </div>
      )}

      {!unverified && loadState === "idle" && (
        <Button variant="outline" onClick={() => void loadTransactions()} type="button">
          Find cash entries
        </Button>
      )}
      {loadState === "loading" && <p className="investment-state" role="status">Loading cash entries…</p>}
      {!unverified && loadState === "error" && (
        <Button variant="outline" onClick={() => void loadTransactions()} type="button">
          Try cash entries again
        </Button>
      )}

      {!unverified && loadState === "ready" && eligible.length === 0 && !hasMoreTransactions && (
        <p className="investment-state">No eligible cash debits are recorded.</p>
      )}
      {!unverified && loadState === "ready" && eligible.length === 0 && hasMoreTransactions && (
        <Button variant="outline" disabled={busy} onClick={() => void loadTransactions({ append: true })} type="button">
          Continue searching cash entries
        </Button>
      )}

      {!unverified && loadState === "ready" && (
        <aside className="investment-transfer-chain" aria-label="Funding and transfer paths">
          <p>Confirmed transfers remain separate ledger paths. Select only the final same-currency cash debit. This screen does not infer a funding path from date or amount.</p>
        </aside>
      )}

      {!unverified && loadState === "ready" && eligible.length > 0 && (!confirmed || allowAdditionalFunding) && !proposal && !stale && (
        <div className="investment-funding-drafts">
          {drafts.map((draft, index) => {
            const event = eventFor(draft.investmentEventId);
            const candidates = eligible.filter((transaction) => !event || transaction.currency === event.currency);
            return (
              <fieldset aria-label={`Funding allocation ${index + 1}`} key={draft.key}>
                <legend>Allocation {index + 1}</legend>
                <label className="select-field">
                  <span>Purchase for allocation {index + 1}</span>
                  <NativeSelect
                    disabled={busy}
                    onChange={(change) => changeDraft(draft.key, "investmentEventId", change.target.value)}
                    value={draft.investmentEventId}
                  >
                    <option value="">Choose a recorded purchase</option>
                    {events.map((item) => <option key={item.id} value={item.id}>{purchaseLabel(item)}</option>)}
                  </NativeSelect>
                </label>
                <label className="select-field">
                  <span>Cash entry for allocation {index + 1}</span>
                  <NativeSelect
                    disabled={busy}
                    onChange={(change) => changeDraft(draft.key, "cashTransactionId", change.target.value)}
                    value={draft.cashTransactionId}
                  >
                    <option value="">Choose an exact cash debit</option>
                    {candidates.map((transaction) => (
                      <option key={transaction.id} value={transaction.id}>{cashLabel(transaction)}</option>
                    ))}
                  </NativeSelect>
                </label>
                <label className="text-field">
                  <span>Allocated amount for allocation {index + 1}</span>
                  <Input
                    disabled={busy}
                    inputMode="decimal"
                    onChange={(change) => changeDraft(draft.key, "allocatedAmount", change.target.value)}
                    value={draft.allocatedAmount}
                  />
                </label>
                {drafts.length > 1 && (
                  <Button variant="outline" disabled={busy} onClick={() => removeDraft(draft.key)} type="button">
                    Remove row {index + 1}
                  </Button>
                )}
              </fieldset>
            );
          })}
          <div className="investment-funding-actions">
            {hasMoreTransactions && (
              <Button variant="outline" disabled={busy} onClick={() => void loadTransactions({ append: true })} type="button">Load more cash entries</Button>
            )}
            <Button variant="outline" disabled={busy} onClick={addDraft} type="button">Add allocation row</Button>
            <Button variant="default" disabled={busy || !draftsComplete} onClick={() => void propose()} type="button">
              {busy ? "Checking exact rows…" : "Review funding proposal"}
            </Button>
          </div>
        </div>
      )}

      {!unverified && stale && (
        <div className="investment-funding-stale">
          <p>The reviewed state is no longer current. No funding link was created.</p>
          <Button variant="outline" disabled={busy} onClick={() => void propose()} type="button">
            Review current state
          </Button>
        </div>
      )}

      {!unverified && proposal && (
        <fieldset aria-label="Confirm funding" className="investment-funding-proposal">
          <legend>Server-checked proposal</legend>
          <p>Server accepted every selected cash leaf as fully allocated.</p>
          <dl>
            {proposal.allocations.map((allocation, index) => (
              <div key={`${allocation.investmentEventId}:${allocation.cashTransactionId}`}>
                <dt>{transactionFor(allocation.cashTransactionId)?.descriptionRaw ?? `Allocation ${index + 1}`}</dt>
                <dd>{allocation.currency} {allocation.allocatedAmount} · cash leaf {allocation.currency} {allocation.cashAmount}</dd>
                <dd>Purchase remaining before proposal {allocation.currency} {allocation.eventRemainingAmount}</dd>
              </div>
            ))}
          </dl>
          <div className="investment-confirm-actions">
            <Button variant="default" disabled={busy} onClick={() => void confirm()} ref={confirmButton} type="button">
              {busy ? "Confirming funding…" : "Confirm funding"}
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => setProposal(undefined)} type="button">Edit allocation</Button>
          </div>
        </fieldset>
      )}

      {!unverified && confirmed && confirmed.allocations.length > 0 && (
        <div className="investment-funding-confirmed" aria-label="Confirmed funding links">
          {confirmedCashIds.map((cashId) => {
            const transaction = transactionFor(cashId);
            const allocations = confirmed.allocations.filter((allocation) => allocation.cashTransactionId === cashId);
            return (
              <article aria-label={`Confirmed funding for ${transaction?.descriptionRaw ?? "cash entry"}`} key={cashId}>
                <p className="section-label">Confirmed cash leaf</p>
                <h3>{transaction?.descriptionRaw ?? "Recorded cash entry"}</h3>
                {allocations.map((allocation) => (
                  <p className="investment-exact" key={allocation.id}>
                    {allocation.currency} {allocation.allocatedAmount}
                  </p>
                ))}
                <p>{allocations.length} recorded allocation{allocations.length === 1 ? "" : "s"}. The purchase and any confirmed transfer chain remain separate ledger records.</p>
                <p>Unlink this funding before correcting the purchase it funds.</p>
                <Button
                  aria-label={`Review removal for ${transaction?.descriptionRaw ?? "cash entry"}`}
                  variant="outline"
                  disabled={busy}
                  onClick={(event) => reviewRemoval(cashId, event.currentTarget)}
                  type="button"
                >Review removal</Button>
              </article>
            );
          })}
        </div>
      )}

      {!unverified && removingCashId && (
        <fieldset aria-label="Confirm funding removal" className="investment-funding-removal">
          <legend>Remove cash-leaf allocations</legend>
          {removingTransaction && <strong>{cashLabel(removingTransaction)}</strong>}
          {/* This panel only ever seeds or confirms the allocations for its own events, so a
              count drawn from its own state could undercount a cash leaf that also funds an
              event outside that scope — the unlink route removes every allocation on the leaf
              regardless, so the sentence never claims a number it cannot vouch for. */}
          <p>All funding allocations on this cash entry will be removed.</p>
          <p>The cash entry returns to its recorded prior type. The purchase and transfer chain remain unchanged.</p>
          <div className="investment-confirm-actions">
            <Button variant="default" disabled={busy} onClick={() => void unlink()} ref={removeButton} type="button">
              {busy ? "Removing funding link…" : "Remove funding link"}
            </Button>
            <Button variant="outline" disabled={busy} onClick={closeRemoval} type="button">Keep funding link</Button>
          </div>
        </fieldset>
      )}
    </section>
  );
}
