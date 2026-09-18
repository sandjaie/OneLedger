"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { type apiFetch, errorText } from "@/lib/api/client";
import type { AcknowledgementIn, CoverageResponse, CoverageRow, MonthCell } from "@/types/api";

type Props = {
  request: typeof apiFetch;
};

type Reason = AcknowledgementIn["reason"];

type Selection = {
  row: CoverageRow;
  month: string;
  cell: MonthCell;
};

// Text labels, not colour, carry the status (spec "one legend; colour is never the only signal").
const STATUS_LABEL: Record<MonthCell["status"], string> = {
  outside_account_life: "Outside account life",
  reconciled: "Reconciled",
  acknowledged: "Acknowledged",
  partial: "Partial",
  in_progress: "In progress",
  missing: "Missing",
};

const REASON_LABEL: Record<Reason, string> = {
  not_issued: "Not issued",
  unobtainable: "Unobtainable",
  unsupported_format: "Unsupported format",
  account_not_in_use: "Account not in use",
};

function rowLabel(row: CoverageRow): string {
  return row.accountName ?? "Payslips";
}

function rowKey(row: CoverageRow): string {
  return `${row.subject}:${row.accountId ?? ""}`;
}

export function CoverageScreen({ request }: Props) {
  const [coverage, setCoverage] = useState<CoverageResponse>();
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState<Selection>();
  const [reason, setReason] = useState<Reason>("not_issued");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [feedback, setFeedback] = useState<string>();

  useEffect(() => {
    let current = true;
    request<CoverageResponse>("/coverage")
      .then((loaded) => {
        if (!current) return;
        setCoverage(loaded);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  function selectCell(row: CoverageRow, month: string, cell: MonthCell) {
    if (cell.status === "outside_account_life" || cell.status === "reconciled" || cell.status === "in_progress") {
      return;
    }
    setSelected({ row, month, cell });
    setReason(cell.reason ?? "not_issued");
    setNote(cell.note ?? "");
    setProblem(undefined);
    setFeedback(undefined);
  }

  function closeDialog() {
    setSelected(undefined);
    setProblem(undefined);
  }

  async function acknowledge() {
    if (!selected) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const body: AcknowledgementIn = {
        subject: selected.row.subject,
        accountId: selected.row.accountId,
        month: selected.month,
        reason,
        note: note.trim() || null,
      };
      await request("/coverage/acknowledgements", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setFeedback("Gap acknowledged.");
      setSelected(undefined);
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This gap could not be acknowledged."));
    } finally {
      setBusy(false);
    }
  }

  async function removeAcknowledgement() {
    if (!selected) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const params = new URLSearchParams({ subject: selected.row.subject, month: selected.month });
      if (selected.row.accountId) params.set("accountId", selected.row.accountId);
      await request(`/coverage/acknowledgements?${params.toString()}`, { method: "DELETE" });
      setFeedback("Acknowledgement removed.");
      setSelected(undefined);
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This acknowledgement could not be removed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="workspace-content coverage-screen" aria-labelledby="coverage-title">
      <header className="page-head">
        <h1 className="workspace-title" id="coverage-title">Coverage</h1>
        {coverage && (
          <p>
            History anchor <strong>{coverage.historyAnchor}</strong> (fixed; ADR-0011). Every
            statement-sourced account and payslips are shown month by month from the anchor.
          </p>
        )}
      </header>

      {state === "loading" && <p className="loading-frame" role="status">Loading the coverage grid…</p>}
      {state === "error" && (
        <div className="page-status" role="alert">
          <p>The coverage grid could not be loaded.</p>
          <button
            className="text-action"
            onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}
            type="button"
          >Try again</button>
        </div>
      )}

      {state === "ready" && coverage && (
        <>
          <ul className="coverage-legend" aria-label="Status legend">
            {(Object.keys(STATUS_LABEL) as Array<MonthCell["status"]>).map((status) => (
              <li key={status}>{STATUS_LABEL[status]}</li>
            ))}
          </ul>

          {feedback && <p role="status">{feedback}</p>}

          <div className="coverage-table-wrap">
            <table className="coverage-table">
              <caption>History coverage, one row per account and payslips</caption>
              <thead>
                <tr>
                  <th scope="col">Row</th>
                  {coverage.months.map((month) => (
                    <th key={month} scope="col">{month}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {coverage.rows.map((row) => (
                  <tr key={rowKey(row)}>
                    <th scope="row">{rowLabel(row)}</th>
                    {row.months.map((cell, index) => {
                      const month = coverage.months[index];
                      const clickable =
                        cell.status !== "outside_account_life"
                        && cell.status !== "reconciled"
                        && cell.status !== "in_progress";
                      return (
                        <td key={month}>
                          <button
                            aria-label={`${rowLabel(row)} ${month}: ${STATUS_LABEL[cell.status]}`}
                            className="coverage-cell"
                            disabled={!clickable}
                            onClick={() => selectCell(row, month, cell)}
                            type="button"
                          >
                            {STATUS_LABEL[cell.status]}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selected && (
            <section aria-labelledby="coverage-dialog-title" className="coverage-dialog">
              <h2 id="coverage-dialog-title">
                {rowLabel(selected.row)} · {selected.month}
              </h2>
              <p>Status: {STATUS_LABEL[selected.cell.status]}</p>

              {problem && <p className="inline-alert" role="alert">{problem}</p>}

              {selected.cell.status === "acknowledged" ? (
                <>
                  <p>
                    Reason: {selected.cell.reason ? REASON_LABEL[selected.cell.reason] : ""}
                    {selected.cell.note ? ` — ${selected.cell.note}` : ""}
                  </p>
                  <button
                    className="primary-action"
                    disabled={busy}
                    onClick={() => void removeAcknowledgement()}
                    type="button"
                  >{busy ? "Removing…" : "Remove acknowledgement"}</button>
                </>
              ) : (
                <>
                  <Link className="text-action" href="/import">Import statements</Link>
                  <label className="select-field">
                    <span>Reason</span>
                    <select
                      onChange={(e) => setReason(e.target.value as Reason)}
                      value={reason}
                    >
                      {(Object.keys(REASON_LABEL) as Array<Reason>).map((value) => (
                        <option key={value} value={value}>{REASON_LABEL[value]}</option>
                      ))}
                    </select>
                  </label>
                  <label className="text-field">
                    <span>Note (optional)</span>
                    <input maxLength={200} onChange={(e) => setNote(e.target.value)} value={note} />
                  </label>
                  <button
                    className="primary-action"
                    disabled={busy}
                    onClick={() => void acknowledge()}
                    type="button"
                  >{busy ? "Saving…" : "Acknowledge gap"}</button>
                </>
              )}
              <button className="text-action" onClick={closeDialog} type="button">Close</button>
            </section>
          )}
        </>
      )}
    </section>
  );
}
