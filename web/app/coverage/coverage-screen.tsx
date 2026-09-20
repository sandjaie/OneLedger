"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

import { AttentionRow } from "@/app/ui/attention-row";
import { ConsolePageHeader } from "@/app/ui/console-page-header";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { MetricStrip } from "@/app/ui/metric-strip";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
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

function reviewSummary(coverage: CoverageResponse) {
  let needsReview = 0;
  let acknowledged = 0;
  let reconciled = 0;
  let firstGap: Selection | undefined;
  for (const [index, month] of coverage.months.entries()) {
    for (const row of coverage.rows) {
      const cell = row.months[index];
      if (!cell) continue;
      if (cell.status === "missing" || cell.status === "partial") {
        needsReview += 1;
        firstGap ??= { row, month, cell };
      } else if (cell.status === "acknowledged") acknowledged += 1;
      else if (cell.status === "reconciled") reconciled += 1;
    }
  }
  return { needsReview, acknowledged, reconciled, firstGap };
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
  const selectedRef = useRef<HTMLElement>(null);
  const summary = coverage ? reviewSummary(coverage) : undefined;
  const firstGap = summary?.firstGap;

  useEffect(() => {
    if (selected) selectedRef.current?.focus();
  }, [selected]);

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
    <section aria-label="Coverage" className="workspace-content coverage-screen">
      <div id="coverage-title">
        <ConsolePageHeader
          title="Coverage"
          description="Month-by-month evidence, acknowledged gaps and the next decision that needs attention."
          actions={<Link className={buttonVariants({ variant: "outline" })} href="/import">Import evidence</Link>}
        />
        {coverage && (
          <details className="experience-disclosure"><summary>Coverage scope and anchor</summary>
            <p>History anchor <strong>{coverage.historyAnchor}</strong> (fixed; ADR-0011). Every statement-sourced account and payslips are shown month by month from the anchor.</p>
          </details>
        )}
      </div>

      {state === "loading" && <p className="loading-frame" role="status">Loading the coverage grid…</p>}
      {state === "error" && (
        <div className="page-status" role="alert">
          <p>The coverage grid could not be loaded.</p>
          <Button
            variant="outline"
            onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}
            type="button"
          >Try again</Button>
        </div>
      )}

      {state === "ready" && coverage && (
        <>
          {summary && <MetricStrip
            label="Coverage status"
            items={[
              { label: "Needs review", value: String(summary.needsReview), detail: "Missing or partial month cells" },
              { label: "Acknowledged", value: String(summary.acknowledged), detail: "Gaps explained by the owner" },
              { label: "Reconciled", value: String(summary.reconciled), detail: "Months with recorded evidence" },
            ]}
          />}
          {firstGap ? (
            <AttentionRow
              action={<Button onClick={() => selectCell(firstGap.row, firstGap.month, firstGap.cell)} type="button">Review earliest gap</Button>}
              detail={`${rowLabel(firstGap.row)} · ${firstGap.month}`}
              title={`${summary?.needsReview ?? 0} month ${summary?.needsReview === 1 ? "cell needs" : "cells need"} review`}
              tone="attention"
            />
          ) : (
            <AttentionRow title="No missing or partial month needs review." tone="success" />
          )}
          <ul className="coverage-legend" aria-label="Status legend">
            {(Object.keys(STATUS_LABEL) as Array<MonthCell["status"]>).map((status) => (
              <li key={status}><Badge variant={status === "missing" || status === "partial" ? "destructive" : "outline"}>{STATUS_LABEL[status]}</Badge></li>
            ))}
          </ul>

          {feedback && <p role="status">{feedback}</p>}

          <DataTableRegion label="Coverage by account and month">
            <table className="coverage-table console-table">
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
                {coverage.rows.length === 0 && <tr><td colSpan={coverage.months.length + 1}>No account or payslip evidence source is available for this period.</td></tr>}
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
                          <Button
                            aria-label={`${rowLabel(row)} ${month}: ${STATUS_LABEL[cell.status]}`}
                            className="coverage-cell"
                            disabled={!clickable}
                            onClick={() => selectCell(row, month, cell)}
                            type="button"
                          >
                            {STATUS_LABEL[cell.status]}
                          </Button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </DataTableRegion>

          {selected && (
            <section aria-labelledby="coverage-dialog-title" className="coverage-dialog" ref={selectedRef} tabIndex={-1}>
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
                  <Button
                    disabled={busy}
                    onClick={() => void removeAcknowledgement()}
                    type="button"
                  >{busy ? "Removing…" : "Remove acknowledgement"}</Button>
                </>
              ) : (
                <>
                  <Link className={buttonVariants({ variant: "outline" })} href="/import">Import statements</Link>
                  <label className="select-field">
                    <span>Reason</span>
                    <NativeSelect
                      onChange={(e) => setReason(e.target.value as Reason)}
                      value={reason}
                    >
                      {(Object.keys(REASON_LABEL) as Array<Reason>).map((value) => (
                        <option key={value} value={value}>{REASON_LABEL[value]}</option>
                      ))}
                    </NativeSelect>
                  </label>
                  <label className="text-field">
                    <span>Note (optional)</span>
                    <Input maxLength={200} onChange={(e) => setNote(e.target.value)} value={note} />
                  </label>
                  <Button
                    disabled={busy}
                    onClick={() => void acknowledge()}
                    type="button"
                  >{busy ? "Saving…" : "Acknowledge gap"}</Button>
                </>
              )}
              <Button onClick={closeDialog} type="button" variant="outline">Close</Button>
            </section>
          )}
        </>
      )}
    </section>
  );
}
