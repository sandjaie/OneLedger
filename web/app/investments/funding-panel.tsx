"use client";

import { useEffect, useRef, useState } from "react";

import type { apiFetch } from "@/lib/api/client";
import type {
  FundingAllocationInput,
  FundingConfirmationResponse,
  FundingProposalResponse,
  FundingUnlinkResponse,
  InvestmentEventRow,
  TransactionRow,
  TransactionsResponse,
} from "@/types/api";

type Props = {
  events: Array<InvestmentEventRow>;
  request: typeof apiFetch;
};

type LoadState = "idle" | "loading" | "ready" | "error";
type AllocationDraft = FundingAllocationInput & { key: number };
type UnverifiedOutcome = { kind: "confirmation" | "removal"; detail: string };
type RemovalResult = { transaction: TransactionRow; response: FundingUnlinkResponse };

const TRANSACTION_QUERY = "/transactions?amountMax=0&sort=bookedDate&direction=desc&limit=50&offset=";
const MAX_AUTO_PAGES = 4;
const JSON_HEADERS = { "Content-Type": "application/json" };

function refusalText(error: unknown, fallback: string) {
  const detail = (error as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail ? detail : fallback;
}

function purchaseLabel(event: InvestmentEventRow) {
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

function newDraft(key: number, events: Array<InvestmentEventRow>): AllocationDraft {
  return {
    key,
    investmentEventId: events.length === 1 ? events[0].id : "",
    cashTransactionId: "",
    allocatedAmount: "",
  };
}

export function FundingPanel({ events, request }: Props) {
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
  const [confirmed, setConfirmed] = useState<FundingConfirmationResponse>();
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
      const detail = refusalText(error, "Cash entries could not be loaded. Retry.");
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
      setProblem(refusalText(error, "The funding proposal was not accepted. Review the exact rows."));
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
      setConfirmed(response);
      setProposal(undefined);
      setFeedback("Funding confirmed from the reviewed server proposal.");
    } catch (error) {
      setProposal(undefined);
      const detail = refusalText(error, "The funding confirmation response was not received.");
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
      if (transaction) setRemovalResult({ transaction, response });
      closeRemoval();
    } catch (error) {
      setProblem("");
      setRemovingCashId("");
      setUnverified({
        kind: "removal",
        detail: refusalText(error, "The funding removal response was not received."),
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
          <strong>{cashLabel(removalResult.transaction)}</strong>
          <span>{removalResult.response.removedAllocationCount} funding allocation{removalResult.response.removedAllocationCount === 1 ? "" : "s"} removed. Cash entry restored to {removalResult.response.restoredTransactionType}.</span>
        </p>
      )}

      {unverified && (
        <div className="investment-funding-stale">
          <p className="inline-alert" role="alert">
            {unverified.kind === "confirmation" ? "Confirmation" : "Removal"} outcome not verified. {unverified.detail}
          </p>
          <p>No claim is made about whether the funding state changed.</p>
          <button className="text-action" disabled={busy} onClick={() => void loadTransactions({ recovery: true })} ref={reloadButton} type="button">
            Reload cash evidence
          </button>
        </div>
      )}

      {!unverified && loadState === "idle" && (
        <button className="text-action" onClick={() => void loadTransactions()} type="button">
          Find cash entries
        </button>
      )}
      {loadState === "loading" && <p className="investment-state" role="status">Loading cash entries…</p>}
      {!unverified && loadState === "error" && (
        <button className="text-action" onClick={() => void loadTransactions()} type="button">
          Try cash entries again
        </button>
      )}

      {!unverified && loadState === "ready" && eligible.length === 0 && !hasMoreTransactions && (
        <p className="investment-state">No eligible cash debits are recorded.</p>
      )}
      {!unverified && loadState === "ready" && eligible.length === 0 && hasMoreTransactions && (
        <button className="text-action" disabled={busy} onClick={() => void loadTransactions({ append: true })} type="button">
          Continue searching cash entries
        </button>
      )}

      {!unverified && loadState === "ready" && (
        <aside className="investment-transfer-chain" aria-label="Funding and transfer paths">
          <p>Confirmed transfers remain separate ledger paths. Select only the final same-currency cash debit. This screen does not infer a funding path from date or amount.</p>
        </aside>
      )}

      {!unverified && loadState === "ready" && eligible.length > 0 && !confirmed && !proposal && !stale && (
        <div className="investment-funding-drafts">
          {drafts.map((draft, index) => {
            const event = eventFor(draft.investmentEventId);
            const candidates = eligible.filter((transaction) => !event || transaction.currency === event.currency);
            return (
              <fieldset aria-label={`Funding allocation ${index + 1}`} key={draft.key}>
                <legend>Allocation {index + 1}</legend>
                <label className="select-field">
                  <span>Purchase for allocation {index + 1}</span>
                  <select
                    disabled={busy}
                    onChange={(change) => changeDraft(draft.key, "investmentEventId", change.target.value)}
                    value={draft.investmentEventId}
                  >
                    <option value="">Choose a recorded purchase</option>
                    {events.map((item) => <option key={item.id} value={item.id}>{purchaseLabel(item)}</option>)}
                  </select>
                </label>
                <label className="select-field">
                  <span>Cash entry for allocation {index + 1}</span>
                  <select
                    disabled={busy}
                    onChange={(change) => changeDraft(draft.key, "cashTransactionId", change.target.value)}
                    value={draft.cashTransactionId}
                  >
                    <option value="">Choose an exact cash debit</option>
                    {candidates.map((transaction) => (
                      <option key={transaction.id} value={transaction.id}>{cashLabel(transaction)}</option>
                    ))}
                  </select>
                </label>
                <label className="text-field">
                  <span>Allocated amount for allocation {index + 1}</span>
                  <input
                    disabled={busy}
                    inputMode="decimal"
                    onChange={(change) => changeDraft(draft.key, "allocatedAmount", change.target.value)}
                    value={draft.allocatedAmount}
                  />
                </label>
                {drafts.length > 1 && (
                  <button className="text-action" disabled={busy} onClick={() => removeDraft(draft.key)} type="button">
                    Remove row {index + 1}
                  </button>
                )}
              </fieldset>
            );
          })}
          <div className="investment-funding-actions">
            {hasMoreTransactions && (
              <button className="text-action" disabled={busy} onClick={() => void loadTransactions({ append: true })} type="button">Load more cash entries</button>
            )}
            <button className="text-action" disabled={busy} onClick={addDraft} type="button">Add allocation row</button>
            <button className="primary-action" disabled={busy || !draftsComplete} onClick={() => void propose()} type="button">
              {busy ? "Checking exact rows…" : "Review funding proposal"}
            </button>
          </div>
        </div>
      )}

      {!unverified && stale && (
        <div className="investment-funding-stale">
          <p>The reviewed state is no longer current. No funding link was created.</p>
          <button className="text-action" disabled={busy} onClick={() => void propose()} type="button">
            Review current state
          </button>
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
            <button className="primary-action" disabled={busy} onClick={() => void confirm()} ref={confirmButton} type="button">
              {busy ? "Confirming funding…" : "Confirm funding"}
            </button>
            <button className="text-action" disabled={busy} onClick={() => setProposal(undefined)} type="button">Edit allocation</button>
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
                    {transaction?.currency} {allocation.allocatedAmount}
                  </p>
                ))}
                <p>{allocations.length} recorded allocation{allocations.length === 1 ? "" : "s"}. The purchase and any confirmed transfer chain remain separate ledger records.</p>
                <button
                  aria-label={`Review removal for ${transaction?.descriptionRaw ?? "cash entry"}`}
                  className="text-action"
                  disabled={busy}
                  onClick={(event) => reviewRemoval(cashId, event.currentTarget)}
                  type="button"
                >Review removal</button>
              </article>
            );
          })}
        </div>
      )}

      {!unverified && removingCashId && (
        <fieldset aria-label="Confirm funding removal" className="investment-funding-removal">
          <legend>Remove cash-leaf allocations</legend>
          {removingTransaction && <strong>{cashLabel(removingTransaction)}</strong>}
          <p>This removes every funding allocation on this cash leaf and restores its recorded prior type. It does not remove the purchase or transfer chain.</p>
          <div className="investment-confirm-actions">
            <button className="primary-action" disabled={busy} onClick={() => void unlink()} ref={removeButton} type="button">
              {busy ? "Removing funding link…" : "Remove funding link"}
            </button>
            <button className="text-action" disabled={busy} onClick={closeRemoval} type="button">Keep funding link</button>
          </div>
        </fieldset>
      )}
    </section>
  );
}
