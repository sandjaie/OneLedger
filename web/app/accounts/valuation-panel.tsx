"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { DataTableRegion } from "@/app/ui/data-table-region";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import type { apiFetch } from "@/lib/api/client";
import { errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  AccountValuationsResponse,
  CurrencyRow,
  ValuationModeCreate,
  ValuationModeResponse,
  ValuationSnapshotCreate,
  ValuationSnapshotPreview,
  ValuationSnapshotRow,
} from "@/types/api";

type Props = {
  accountId: string;
  accountName: string;
  currency: string;
  baseCurrency: string;
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
  onChanged: () => void;
  onClose: () => void;
};

type LoadState = "loading" | "ready" | "error";

const VALUATION_MODES = ["ledger_balance", "holdings", "snapshot"] as const;
const METHODS = ["owner_entered", "statement"] as const;
const CONFIDENCES = ["high", "medium", "low"] as const;

const METHOD_LABEL: Record<(typeof METHODS)[number], string> = {
  owner_entered: "Entered by owner",
  statement: "From a statement",
};

const CONFIDENCE_LABEL: Record<(typeof CONFIDENCES)[number], string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

const MODE_LABEL: Record<(typeof VALUATION_MODES)[number], string> = {
  ledger_balance: "Ledger balance",
  holdings: "Holdings",
  snapshot: "Dated valuation",
};

function today() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function dayAfter(isoDate: string) {
  const next = new Date(`${isoDate}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString().slice(0, 10);
}

export function ValuationPanel({
  accountId,
  accountName,
  currency,
  baseCurrency,
  currencies,
  request,
  onChanged,
  onClose,
}: Props) {
  const [state, setState] = useState<LoadState>("loading");
  const [data, setData] = useState<AccountValuationsResponse>();
  const [attempt, setAttempt] = useState(0);

  const [asOfDate, setAsOfDate] = useState(today());
  const [valueNative, setValueNative] = useState("");
  const [method, setMethod] = useState<(typeof METHODS)[number]>("owner_entered");
  const [confidence, setConfidence] = useState<(typeof CONFIDENCES)[number]>("high");
  const [sourceNote, setSourceNote] = useState("");
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewProblem, setReviewProblem] = useState("");
  const [preview, setPreview] = useState<{ shown: ValuationSnapshotPreview; body: ValuationSnapshotCreate }>();
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [confirmProblem, setConfirmProblem] = useState("");
  const [recorded, setRecorded] = useState<ValuationSnapshotRow>();
  const draft = useRef(0);

  // Editing any field invalidates a shown or pending review: the owner must review again, and
  // confirm posts exactly the reviewed body, never the since-changed draft.
  function editField<T>(setter: (value: T) => void) {
    return (value: T) => {
      draft.current += 1;
      setPreview(undefined);
      setConfirmProblem("");
      setter(value);
    };
  }

  const [newMode, setNewMode] = useState<(typeof VALUATION_MODES)[number]>("snapshot");
  const [effectiveFrom, setEffectiveFrom] = useState(today());
  const [modeBusy, setModeBusy] = useState(false);
  const [modeProblem, setModeProblem] = useState("");
  const [modeNotice, setModeNotice] = useState(false);

  useEffect(() => {
    let current = true;
    request<AccountValuationsResponse>(`/accounts/${accountId}/valuations`)
      .then((next) => {
        if (!current) return;
        setData(next);
        setNewMode(next.currentMode);
        setState("ready");
      })
      .catch(() => {
        if (!current) return;
        setState("error");
      });
    return () => {
      current = false;
    };
  }, [accountId, request, attempt]);

  async function reviewSnapshot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (reviewBusy) return;
    setReviewBusy(true);
    setReviewProblem("");
    setPreview(undefined);
    setRecorded(undefined);
    const reviewedDraft = draft.current;
    try {
      const body: ValuationSnapshotCreate = {
        asOfDate,
        valueNative,
        method,
        confidence,
        sourceNote: sourceNote || null,
      };
      const shown = await request<ValuationSnapshotPreview>(
        `/accounts/${accountId}/valuation-snapshots`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, dryRun: true }),
        },
      );
      if (draft.current === reviewedDraft) setPreview({ shown, body });
    } catch (error) {
      if (draft.current === reviewedDraft) {
        setReviewProblem(errorText(error, "This valuation could not be reviewed. Retry."));
      }
    } finally {
      setReviewBusy(false);
    }
  }

  async function confirmSnapshot() {
    if (confirmBusy || !preview) return;
    setConfirmBusy(true);
    setConfirmProblem("");
    try {
      const row = await request<ValuationSnapshotRow>(`/accounts/${accountId}/valuation-snapshots`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(preview.body),
      });
      setRecorded(row);
      setPreview(undefined);
      setValueNative("");
      setSourceNote("");
      setAttempt((value) => value + 1);
      onChanged();
    } catch (error) {
      setConfirmProblem(errorText(error, "This valuation could not be recorded. Retry."));
    } finally {
      setConfirmBusy(false);
    }
  }

  async function submitMode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (modeBusy || !data) return;
    setModeBusy(true);
    setModeProblem("");
    setModeNotice(false);
    try {
      const body: ValuationModeCreate = { valuationMode: newMode, effectiveFrom };
      await request<ValuationModeResponse>(`/accounts/${accountId}/valuation-mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setModeNotice(effectiveFrom < today());
      setAttempt((value) => value + 1);
      onChanged();
    } catch (error) {
      setModeProblem(errorText(error, "The valuation mode could not be changed. Retry."));
    } finally {
      setModeBusy(false);
    }
  }

  const latest = data?.snapshots[0];

  return (
    <section aria-labelledby="valuation-panel-title" className="valuation-panel">
      <div className="console-section-head">
        <h2 id="valuation-panel-title">Valuation — {accountName}</h2>
        <Button onClick={onClose} size="sm" type="button" variant="outline">Close</Button>
      </div>
      {state === "loading" && <p className="investment-state" role="status">Loading valuation…</p>}
      {state === "error" && <p className="investment-state" role="alert">Valuation could not be loaded.</p>}
      {state === "ready" && data && (
        <>
          <dl className="valuation-facts">
            <div>
              <dt>Current mode</dt>
              <dd className="valuation-current-mode">
                <strong>{MODE_LABEL[data.currentMode]}</strong> since {data.currentEffectiveFrom}
              </dd>
            </div>
            <div><dt>Account currency</dt><dd>{currency}</dd></div>
            <div>
              <dt>Stored valuations</dt>
              <dd>{data.snapshots.length}</dd>
            </div>
          </dl>

          {data.currentMode === "snapshot" && (
            <p className="valuation-latest">
              {latest
                ? `Latest valuation: ${moneyText(latest.valueNative, currency, currencies)} as of ${latest.asOfDate}`
                : "No valuation recorded yet."}
            </p>
          )}

          {data.currentMode === "snapshot" && (
            <form className="valuation-form" onSubmit={(event) => void reviewSnapshot(event)}>
              <h3>Add valuation</h3>
              <label className="date-field">
                <span>As of date</span>
                <Input
                  max={today()}
                  onChange={(event) => editField(setAsOfDate)(event.target.value)}
                  required
                  type="date"
                  value={asOfDate}
                />
              </label>
              <label className="text-field">
                <span>Value ({currency})</span>
                <Input
                  inputMode="decimal"
                  onChange={(event) => editField(setValueNative)(event.target.value)}
                  required
                  type="text"
                  value={valueNative}
                />
              </label>
              <label className="select-field">
                <span>Method</span>
                <NativeSelect
                  onChange={(event) => editField(setMethod)(event.target.value as (typeof METHODS)[number])}
                  value={method}
                >
                  {METHODS.map((option) => (
                    <option key={option} value={option}>{METHOD_LABEL[option]}</option>
                  ))}
                </NativeSelect>
              </label>
              <label className="select-field">
                <span>Confidence</span>
                <NativeSelect
                  onChange={(event) => editField(setConfidence)(event.target.value as (typeof CONFIDENCES)[number])}
                  value={confidence}
                >
                  {CONFIDENCES.map((option) => (
                    <option key={option} value={option}>{CONFIDENCE_LABEL[option]}</option>
                  ))}
                </NativeSelect>
              </label>
              <label className="text-field">
                <span>Source note</span>
                <Input onChange={(event) => editField(setSourceNote)(event.target.value)} value={sourceNote} />
              </label>
              {reviewProblem && <p className="inline-alert" role="alert">{reviewProblem}</p>}
              <Button disabled={reviewBusy} type="submit">Review valuation</Button>
            </form>
          )}

          {preview && (
            <div className="valuation-preview">
              <p role="status">
                Review: {moneyText(preview.shown.valueNative, currency, currencies)} as of {preview.shown.asOfDate}
                {" "}— FX rate date {preview.shown.fxRateDate},{" "}
                {moneyText(preview.shown.valueBase, baseCurrency, currencies)} in the
                base currency. Nothing is recorded yet; a later valuation would supersede this one only from its
                own date.
              </p>
              {confirmProblem && <p className="inline-alert" role="alert">{confirmProblem}</p>}
              <Button
                disabled={confirmBusy}
                onClick={() => void confirmSnapshot()}
                type="button"
              >
                Confirm and record
              </Button>
            </div>
          )}

          {recorded && (
            <p className="valuation-confirmation" role="status">
              Recorded {moneyText(recorded.valueNative, currency, currencies)} as of {recorded.asOfDate} — FX rate
              date {recorded.fxRateDate}, {moneyText(recorded.valueBase, baseCurrency, currencies)} in the base
              currency. A later valuation supersedes this one only from its own date.
            </p>
          )}

          <section aria-label="Stored valuation evidence" className="console-section">
            <h3>Stored evidence</h3>
            {data.snapshots.length === 0 ? (
              <p>No valuation has been recorded for this account yet.</p>
            ) : (
              <>
                <p className="console-module-sub">
                  Newest first. Each row keeps the rate date it was frozen against; a later
                  valuation supersedes an earlier one only from its own date.
                </p>
                <DataTableRegion label="Stored valuation evidence table">
                  <table className="console-table">
                    <caption className="visually-hidden">Stored valuations, newest first</caption>
                    <thead>
                      <tr>
                        <th scope="col">As of</th>
                        <th className="console-number" scope="col">Value ({currency})</th>
                        <th className="console-number" scope="col">Value ({baseCurrency})</th>
                        <th scope="col">FX rate date</th>
                        <th scope="col">Method</th>
                        <th scope="col">Confidence</th>
                        <th scope="col">Source note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.snapshots.map((snapshot) => (
                        <tr key={snapshot.id}>
                          <th scope="row">{snapshot.asOfDate}</th>
                          <td className="console-number">
                            {moneyText(snapshot.valueNative, currency, currencies)}
                          </td>
                          <td className="console-number">
                            {moneyText(snapshot.valueBase, baseCurrency, currencies)}
                          </td>
                          <td>{snapshot.fxRateDate}</td>
                          <td>{METHOD_LABEL[snapshot.method as (typeof METHODS)[number]] ?? snapshot.method}</td>
                          <td>{CONFIDENCE_LABEL[snapshot.confidence]}</td>
                          <td>{snapshot.sourceNote ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </DataTableRegion>
              </>
            )}
          </section>

          <form className="valuation-mode-form" onSubmit={(event) => void submitMode(event)}>
            <h3>Change valuation mode</h3>
            <label className="select-field">
              <span>Mode</span>
              <NativeSelect
                onChange={(event) => setNewMode(event.target.value as (typeof VALUATION_MODES)[number])}
                value={newMode}
              >
                {VALUATION_MODES.map((mode) => (
                  <option key={mode} value={mode}>{MODE_LABEL[mode]}</option>
                ))}
              </NativeSelect>
            </label>
            <label className="date-field">
              <span>Effective from</span>
              <Input
                min={dayAfter(data.currentEffectiveFrom)}
                onChange={(event) => setEffectiveFrom(event.target.value)}
                required
                type="date"
                value={effectiveFrom}
              />
            </label>
            {modeProblem && <p className="inline-alert" role="alert">{modeProblem}</p>}
            <Button disabled={modeBusy} type="submit" variant="outline">Change mode</Button>
          </form>
          {modeNotice && (
            <p className="valuation-mode-notice" role="status">
              This backdated change affects past net worth. <Link href="/wealth">Open Wealth</Link> to refresh
              its history.
            </p>
          )}
        </>
      )}
    </section>
  );
}
