"use client";

import { useEffect, useState } from "react";

import { ApiError, type apiFetch } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type {
  CurrencyRow,
  PayslipCreate,
  PayslipImportResponse,
  PayslipLineIn,
  PayslipRow,
  PayslipsResponse,
} from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type LineDraft = { kind: "earning" | "deduction"; label: string; amount: string };

type FormState = {
  payMonth: string;
  currency: string;
  grossEarnings: string;
  totalDeductions: string;
  netPay: string;
  lines: Array<LineDraft>;
};

const REASON_TEXT: Record<string, string> = {
  incomplete_month: "This month is missing its table, total, net pay or currency.",
  unreadable_line: "A line in this month's table could not be read.",
  too_many_lines: "This month has more lines than can be stored.",
  unknown_currency: "This month's currency is not one OneLedger recognises.",
  payments_total_disagrees: "The payment lines do not sum to the stated total.",
  deductions_total_disagrees: "The deduction lines do not sum to the stated total.",
  net_pay_disagrees: "Gross less deductions does not equal the stated net pay.",
};

const IMPORT_LOCKED = "PDF is encrypted or the password is incorrect";

function currencyOf(currencies: Array<CurrencyRow>, code: string): CurrencyRow {
  return (
    currencies.find((row) => row.code === code) ?? {
      code,
      name: code,
      symbol: code,
      decimals: 2,
      numberFormat: "international",
    }
  );
}

function detail(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.detail : fallback;
}

function emptyForm(currencies: Array<CurrencyRow>): FormState {
  return {
    payMonth: "",
    currency: currencies[0]?.code ?? "",
    grossEarnings: "",
    totalDeductions: "",
    netPay: "",
    lines: [{ kind: "earning", label: "", amount: "" }],
  };
}

function formFromPayslip(payslip: PayslipRow): FormState {
  return {
    payMonth: payslip.payMonth,
    currency: payslip.currency,
    grossEarnings: payslip.grossEarnings,
    totalDeductions: payslip.totalDeductions,
    netPay: payslip.netPay,
    lines: payslip.lines.map((line) => ({ kind: line.kind, label: line.label, amount: line.amount })),
  };
}

function depositState(payslip: PayslipRow) {
  if (payslip.supersededByPayslipId) return "Superseded";
  if (!payslip.deposit) return "Not linked";
  return payslip.deposit.eligible
    ? `Linked · ${payslip.deposit.bookedDate}`
    : "Linked, but no longer eligible";
}

export function PayslipsScreen({ currencies, request }: Props) {
  const [payslips, setPayslips] = useState<Array<PayslipRow>>();
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [correcting, setCorrecting] = useState<PayslipRow>();
  const [form, setForm] = useState<FormState>(() => emptyForm(currencies));
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [feedback, setFeedback] = useState<string>();

  const [file, setFile] = useState<File>();
  const [password, setPassword] = useState("");
  const [locked, setLocked] = useState(false);
  const [preview, setPreview] = useState<PayslipImportResponse>();
  const [importResult, setImportResult] = useState<PayslipImportResponse>();
  const [importBusy, setImportBusy] = useState(false);
  const [importProblem, setImportProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    request<PayslipsResponse>("/payslips")
      .then((result) => {
        if (!current) return;
        setPayslips(result.payslips);
        setLoadState("ready");
      })
      .catch(() => {
        if (current) setLoadState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  function changeForm<K extends keyof FormState>(field: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [field]: value }));
    setProblem(undefined);
  }

  function changeLine(index: number, change: Partial<LineDraft>) {
    setForm((current) => ({
      ...current,
      lines: current.lines.map((line, i) => (i === index ? { ...line, ...change } : line)),
    }));
    setProblem(undefined);
  }

  function addLine(kind: LineDraft["kind"]) {
    setForm((current) => ({ ...current, lines: [...current.lines, { kind, label: "", amount: "" }] }));
  }

  function removeLine(index: number) {
    setForm((current) => ({ ...current, lines: current.lines.filter((_, i) => i !== index) }));
  }

  function startCorrection(payslip: PayslipRow) {
    setCorrecting(payslip);
    setForm(formFromPayslip(payslip));
    setIdempotencyKey(crypto.randomUUID());
    setProblem(undefined);
    setFeedback(undefined);
  }

  function cancelCorrection() {
    setCorrecting(undefined);
    setForm(emptyForm(currencies));
    setIdempotencyKey(crypto.randomUUID());
    setProblem(undefined);
  }

  async function submit() {
    const body: PayslipCreate = {
      payMonth: form.payMonth,
      currency: form.currency,
      grossEarnings: form.grossEarnings,
      totalDeductions: form.totalDeductions,
      netPay: form.netPay,
      lines: form.lines.filter((line) => line.label && line.amount) as Array<PayslipLineIn>,
      idempotencyKey,
    };
    setBusy(true);
    setProblem(undefined);
    setFeedback(undefined);
    try {
      await request<PayslipRow>(
        correcting ? `/payslips/${correcting.id}/corrections` : "/payslips",
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
      );
      setFeedback(correcting ? "Correction recorded." : "Payslip recorded.");
      setCorrecting(undefined);
      setForm(emptyForm(currencies));
      setIdempotencyKey(crypto.randomUUID());
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(detail(error, "This payslip could not be recorded."));
    } finally {
      setBusy(false);
    }
  }

  function chooseFile(files: FileList | null) {
    setFile(files?.[0]);
    setPassword("");
    setLocked(false);
    setPreview(undefined);
    setImportResult(undefined);
    setImportProblem(undefined);
  }

  async function checkImport() {
    if (!file) return;
    setImportBusy(true);
    setImportProblem(undefined);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("commit", "false");
      if (password) form.append("password", password);
      const result = await request<PayslipImportResponse>("/payslips/import", {
        method: "POST",
        body: form,
      });
      setPreview(result);
      setLocked(false);
    } catch (error) {
      const message = detail(error, "This file could not be checked.");
      setImportProblem(message);
      if (message === IMPORT_LOCKED) setLocked(true);
    } finally {
      setImportBusy(false);
    }
  }

  async function confirmImport() {
    if (!file || !preview) return;
    setImportBusy(true);
    setImportProblem(undefined);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("commit", "true");
      if (password) form.append("password", password);
      const result = await request<PayslipImportResponse>("/payslips/import", {
        method: "POST",
        body: form,
      });
      setImportResult(result);
      setAttempt((value) => value + 1);
    } catch (error) {
      setImportProblem(detail(error, "This file could not be imported."));
    } finally {
      setImportBusy(false);
    }
  }

  const completeForm = Boolean(
    form.payMonth && form.currency && form.grossEarnings && form.totalDeductions && form.netPay
      && form.lines.some((line) => line.kind === "earning" && line.label && line.amount),
  );

  return (
    <section className="workspace-content salary-screen" aria-labelledby="salary-title">
      <header>
        <p className="eyebrow">Recorded evidence</p>
        <h1 className="workspace-title" id="salary-title">Salary</h1>
        <p>Payslips reconcile exactly or are refused; a correction supersedes rather than edits.</p>
      </header>

      <section aria-labelledby="salary-list-title">
        <h2 id="salary-list-title">Payslips</h2>
        {loadState === "loading" && <p role="status">Loading payslips…</p>}
        {loadState === "error" && (
          <div role="alert">
            <p>Payslips could not be loaded.</p>
            <button className="text-action" onClick={() => { setLoadState("loading"); setAttempt((v) => v + 1); }} type="button">Try again</button>
          </div>
        )}
        {loadState === "ready" && payslips?.length === 0 && <p>No payslips are recorded yet.</p>}
        {loadState === "ready" && payslips && payslips.length > 0 && (
          <ul className="salary-payslip-list">
            {payslips.map((payslip) => {
              const currency = currencyOf(currencies, payslip.currency);
              return (
                <li key={payslip.id}>
                  <div>
                    <strong>{payslip.payMonth}</strong>
                    <span>{formatMoney(payslip.netPay, currency)} net · {formatMoney(payslip.grossEarnings, currency)} gross</span>
                    <span>{depositState(payslip)}</span>
                    {payslip.imported && <small>Imported</small>}
                  </div>
                  <ul className="salary-payslip-lines">
                    {payslip.lines.map((line) => (
                      <li key={line.lineNo}>{line.kind}: {line.label} {formatMoney(line.amount, currency)}</li>
                    ))}
                  </ul>
                  <button
                    className="text-action"
                    disabled={Boolean(payslip.deposit) || Boolean(payslip.supersededByPayslipId)}
                    onClick={() => startCorrection(payslip)}
                    type="button"
                  >{payslip.deposit ? "Unlink before correcting" : "Correct"}</button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="salary-entry-title">
        <h2 id="salary-entry-title">{correcting ? `Correct ${correcting.payMonth}` : "Enter a payslip"}</h2>
        <label className="date-field">
          <span>Pay month</span>
          <input onChange={(e) => changeForm("payMonth", e.target.value)} type="month" value={form.payMonth} />
        </label>
        <label className="select-field">
          <span>Currency</span>
          <select onChange={(e) => changeForm("currency", e.target.value)} value={form.currency}>
            {currencies.map((row) => <option key={row.code} value={row.code}>{row.code}</option>)}
          </select>
        </label>
        <label className="text-field"><span>Gross earnings</span><input inputMode="decimal" onChange={(e) => changeForm("grossEarnings", e.target.value)} value={form.grossEarnings} /></label>
        <label className="text-field"><span>Total deductions</span><input inputMode="decimal" onChange={(e) => changeForm("totalDeductions", e.target.value)} value={form.totalDeductions} /></label>
        <label className="text-field"><span>Net pay</span><input inputMode="decimal" onChange={(e) => changeForm("netPay", e.target.value)} value={form.netPay} /></label>

        <fieldset>
          <legend>Lines</legend>
          {form.lines.map((line, index) => (
            <div key={index}>
              <select onChange={(e) => changeLine(index, { kind: e.target.value as LineDraft["kind"] })} value={line.kind}>
                <option value="earning">Earning</option>
                <option value="deduction">Deduction</option>
              </select>
              <input onChange={(e) => changeLine(index, { label: e.target.value })} placeholder="Label" value={line.label} />
              <input inputMode="decimal" onChange={(e) => changeLine(index, { amount: e.target.value })} placeholder="Amount" value={line.amount} />
              <button onClick={() => removeLine(index)} type="button">Remove</button>
            </div>
          ))}
          <button className="text-action" onClick={() => addLine("earning")} type="button">Add earning line</button>
          <button className="text-action" onClick={() => addLine("deduction")} type="button">Add deduction line</button>
        </fieldset>

        {problem && <p className="inline-alert" role="alert">{problem}</p>}
        {feedback && <p role="status">{feedback}</p>}
        <button className="primary-action" disabled={!completeForm || busy} onClick={() => void submit()} type="button">
          {busy ? "Recording…" : correcting ? "Record correction" : "Record payslip"}
        </button>
        {correcting && <button className="text-action" onClick={cancelCorrection} type="button">Cancel correction</button>}
      </section>

      <section aria-labelledby="salary-import-title">
        <h2 id="salary-import-title">Import from a PDF</h2>
        <label className="file-field">
          <span>Payslip file</span>
          <input accept="application/pdf,.pdf" disabled={importBusy} onChange={(e) => chooseFile(e.target.files)} type="file" />
        </label>
        {locked && (
          <label className="password-field">
            <span>Password for this file</span>
            <input autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} type="password" value={password} />
          </label>
        )}
        {importProblem && <p className="inline-alert" role="alert">{importProblem}</p>}
        <button className="text-action" disabled={!file || importBusy} onClick={() => void checkImport()} type="button">Check file</button>

        {preview && !importResult && (
          <div>
            {!preview.supported && <p>This file&rsquo;s layout is not supported.</p>}
            {preview.supported && (
              <>
                <ul className="salary-import-preview">
                  {preview.months.map((month, index) => (
                    <li key={index}>
                      <strong>{month.payMonth ?? "unknown month"}</strong>
                      <span>{month.reason ? REASON_TEXT[month.reason] ?? month.reason : "Reconciles"}</span>
                    </li>
                  ))}
                </ul>
                <button
                  className="primary-action"
                  disabled={importBusy || !preview.months.some((m) => m.reason === null)}
                  onClick={() => void confirmImport()}
                  type="button"
                >Import reconciling months</button>
              </>
            )}
          </div>
        )}

        {importResult && (
          <ul className="salary-import-result">
            {importResult.months.map((month, index) => (
              <li key={index}>
                <strong>{month.payMonth ?? "unknown month"}</strong>
                <span>{month.status ?? "not reconciled"}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
