"use client";

import { useEffect, useState } from "react";

import { ApiError, errorText, type apiFetch } from "@/lib/api/client";
import { dateLabel } from "@/lib/format/date";
import type {
  HoldingLotRow,
  LotAllocationInput,
  LotAllocationProposalResponse,
  LotAllocationResponse,
} from "@/types/api";

type Props = {
  holdingId: string;
  disposalEventId: string;
  disposalDate: string;
  request: typeof apiFetch;
  // Called once a confirmed allocation returns, so the holding page (the source-of-truth
  // record) can reload — this panel never re-fetches the holding itself.
  onAllocated: () => void;
};

type LoadState = "loading" | "ready" | "error";
// Lot id -> the owner's exact typed units; an empty string means "not allocating this lot".
type Draft = Record<string, string>;
const JSON_HEADERS = { "Content-Type": "application/json" };

function lotLabel(lot: HoldingLotRow) {
  return `Acquired ${dateLabel(lot.acquiredOn)} · ${lot.remainingUnits} of ${lot.acquiredUnits} units remaining · cost basis ${lot.costBasisNative}`;
}

export function LotAllocationPanel({ holdingId, disposalEventId, disposalDate, request, onAllocated }: Props) {
  const [state, setState] = useState<LoadState>("loading");
  const [lots, setLots] = useState<Array<HoldingLotRow>>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [proposal, setProposal] = useState<LotAllocationProposalResponse>();
  const [result, setResult] = useState<LotAllocationResponse>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [stale, setStale] = useState(false);

  useEffect(() => {
    let current = true;
    request<Array<HoldingLotRow>>(`/portfolio/holdings/${holdingId}/lots`)
      .then((rows) => {
        if (!current) return;
        setLots(rows);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [holdingId, request]);

  // Eligible lots dated on or before the disposal — the server itself refuses proposing more
  // than a lot's own remaining units, so this list is not further filtered by that string here.
  const eligibleLots = lots.filter((lot) => lot.eligible && lot.acquiredOn <= disposalDate);

  function invalidate() {
    setProposal(undefined);
    setIdempotencyKey("");
    setStale(false);
    setProblem("");
  }

  function changeDraft(lotId: string, value: string) {
    setDraft((current) => ({ ...current, [lotId]: value }));
    invalidate();
  }

  function allocationBody(): Array<LotAllocationInput> {
    return Object.entries(draft)
      .filter(([, units]) => units.trim() !== "")
      .map(([acquisitionLotId, allocatedUnits]) => ({ acquisitionLotId, allocatedUnits }));
  }

  const allocations = allocationBody();

  async function propose() {
    if (allocations.length === 0 || busy) return;
    setBusy(true);
    setProblem("");
    try {
      const response = await request<LotAllocationProposalResponse>(
        "/investment-funding/lot-allocation-proposals",
        {
          method: "POST",
          headers: JSON_HEADERS,
          body: JSON.stringify({ disposalEventId, allocations }),
        },
      );
      setProposal(response);
      setStale(false);
      if (!idempotencyKey) setIdempotencyKey(crypto.randomUUID());
    } catch (error) {
      setProblem(errorText(error, "The lot allocation proposal was not accepted. Review the exact units."));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!proposal || busy) return;
    // Reused on a same-body retry after a failed confirmation; a changed draft or a stale
    // proposal (handled below) clears it so the next proposal gets its own key.
    const key = idempotencyKey || crypto.randomUUID();
    if (!idempotencyKey) setIdempotencyKey(key);
    setBusy(true);
    setProblem("");
    try {
      const response = await request<LotAllocationResponse>("/investment-funding/lot-allocations", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({
          disposalEventId,
          allocations,
          expectedState: proposal.expectedState,
          idempotencyKey: key,
        }),
      });
      setResult(response);
      onAllocated();
    } catch (error) {
      setProblem(errorText(error, "The lot allocation was not confirmed."));
      if (error instanceof ApiError && error.status === 409) {
        setProposal(undefined);
        setIdempotencyKey("");
        setStale(true);
      }
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <div className="investment-lot-allocation-result" aria-label="Confirmed lot allocation">
        <p className="investment-feedback" role="status">Lot allocation confirmed.</p>
        <dl>
          <div><dt>Realised cost</dt><dd>{result.realisedCostNative ?? "Not yet determinable"}</dd></div>
          <div><dt>Realised gain</dt><dd>{result.realisedGainNative ?? "Not yet determinable"}</dd></div>
          <div><dt>Remaining basis</dt><dd>{result.remainingBasisNative ?? "Not yet determinable"}</dd></div>
        </dl>
        {result.reviewReasons.length > 0 && <p>This disposal still has units to allocate.</p>}
      </div>
    );
  }

  return (
    <section aria-labelledby="lot-allocation-title" className="investment-lot-allocation-panel">
      <p className="section-label">Lot allocation</p>
      <h3 id="lot-allocation-title">Allocate lots</h3>

      {state === "loading" && <p className="investment-state" role="status">Loading lots…</p>}
      {state === "error" && <p className="inline-alert" role="alert">Lots could not be loaded.</p>}
      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      {state === "ready" && eligibleLots.length === 0 && (
        <p className="investment-state">No eligible lots are dated on or before this disposal.</p>
      )}

      {state === "ready" && eligibleLots.length > 0 && !proposal && (
        <div className="investment-lot-allocation-drafts">
          {eligibleLots.map((lot) => (
            <label className="text-field" key={lot.lotId}>
              <span>{lotLabel(lot)}</span>
              <input
                disabled={busy}
                inputMode="decimal"
                onChange={(event) => changeDraft(lot.lotId, event.target.value)}
                value={draft[lot.lotId] ?? ""}
              />
            </label>
          ))}
          {stale && <p>The reviewed state is no longer current. No allocation was created.</p>}
          <button className="primary-action" disabled={busy || allocations.length === 0} onClick={() => void propose()} type="button">
            {busy ? "Checking exact units…" : "Review allocation proposal"}
          </button>
        </div>
      )}

      {proposal && (
        <fieldset aria-label="Confirm lot allocation" className="investment-lot-allocation-proposal">
          <legend>Server-checked proposal</legend>
          <dl>
            {proposal.allocations.map((allocation) => (
              <div key={allocation.acquisitionLotId}>
                <dt>{eligibleLots.find((lot) => lot.lotId === allocation.acquisitionLotId)?.acquiredOn ?? "Lot"}</dt>
                <dd>{allocation.allocatedUnits} units</dd>
              </div>
            ))}
          </dl>
          <div className="investment-confirm-actions">
            <button className="primary-action" disabled={busy} onClick={() => void confirm()} type="button">
              {busy ? "Confirming allocation…" : "Confirm allocation"}
            </button>
            <button className="text-action" disabled={busy} onClick={() => setProposal(undefined)} type="button">Edit allocation</button>
          </div>
        </fieldset>
      )}
    </section>
  );
}
