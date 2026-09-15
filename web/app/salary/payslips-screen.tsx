"use client";

import { useEffect, useState } from "react";

import { LONG_REQUEST_TIMEOUT_MS, type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  CategoryRow,
  CurrencyRow,
  DeductionRuleRow,
  DeductionRulesResponse,
  DepositCandidate,
  PayslipCreate,
  PayslipImportResponse,
  PayslipLineIn,
  PayslipLineOut,
  PayslipRow,
  PayslipsResponse,
} from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  categories: Array<CategoryRow>;
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

export function PayslipsScreen({ currencies, categories, request }: Props) {
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

  const [candidatesFor, setCandidatesFor] = useState<string>();
  const [candidates, setCandidates] = useState<Array<DepositCandidate>>();
  const [candidatesBusy, setCandidatesBusy] = useState(false);
  const [candidatesProblem, setCandidatesProblem] = useState<string>();
  const [confirmingCandidate, setConfirmingCandidate] = useState<DepositCandidate>();
  const [linkBusy, setLinkBusy] = useState(false);
  const [unlinkFor, setUnlinkFor] = useState<string>();
  const [unlinkProblem, setUnlinkProblem] = useState<string>();

  const [spendingDraft, setSpendingDraft] = useState<Record<string, string>>({});
  const [spendingBusy, setSpendingBusy] = useState<string>();
  const [spendingProblem, setSpendingProblem] = useState<{ key: string; message: string }>();

  const [deductionRules, setDeductionRules] = useState<Array<DeductionRuleRow>>();
  const [deductionRulesState, setDeductionRulesState] = useState<"loading" | "ready" | "error">("loading");
  const [deductionRulesAttempt, setDeductionRulesAttempt] = useState(0);
  const [newRuleLabel, setNewRuleLabel] = useState("");
  const [newRuleCategoryId, setNewRuleCategoryId] = useState("");
  const [deductionRuleBusy, setDeductionRuleBusy] = useState<string>();
  const [deductionRuleProblem, setDeductionRuleProblem] = useState<string>();

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

  useEffect(() => {
    let current = true;
    request<DeductionRulesResponse>("/payslips/deduction-rules")
      .then((result) => {
        if (!current) return;
        setDeductionRules(result.rules);
        setDeductionRulesState("ready");
      })
      .catch(() => {
        if (current) setDeductionRulesState("error");
      });
    return () => {
      current = false;
    };
  }, [deductionRulesAttempt, request]);

  async function saveDeductionRule(label: string, categoryId: string) {
    if (!label.trim() || !categoryId) return;
    const key = label.trim();
    setDeductionRuleBusy(key);
    setDeductionRuleProblem(undefined);
    try {
      await request<DeductionRuleRow>(`/payslips/deduction-rules/${encodeURIComponent(key)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ categoryId }),
      });
      setNewRuleLabel("");
      setNewRuleCategoryId("");
      setDeductionRulesAttempt((value) => value + 1);
    } catch (error) {
      setDeductionRuleProblem(errorText(error, "This deduction rule was not saved."));
    } finally {
      setDeductionRuleBusy(undefined);
    }
  }

  async function removeDeductionRule(label: string) {
    setDeductionRuleBusy(label);
    setDeductionRuleProblem(undefined);
    try {
      await request<void>(`/payslips/deduction-rules/${encodeURIComponent(label)}`, { method: "DELETE" });
      setDeductionRulesAttempt((value) => value + 1);
    } catch (error) {
      setDeductionRuleProblem(errorText(error, "This deduction rule was not removed."));
    } finally {
      setDeductionRuleBusy(undefined);
    }
  }

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
      setProblem(errorText(error, "This payslip could not be recorded."));
    } finally {
      setBusy(false);
    }
  }

  async function findCandidates(payslip: PayslipRow) {
    setCandidatesFor(payslip.id);
    setCandidates(undefined);
    setConfirmingCandidate(undefined);
    setCandidatesBusy(true);
    setCandidatesProblem(undefined);
    try {
      const result = await request<{ candidates: Array<DepositCandidate> }>(
        `/payslips/${payslip.id}/deposit-candidates`,
      );
      setCandidates(result.candidates);
    } catch (error) {
      setCandidatesProblem(errorText(error, "Candidates could not be loaded."));
    } finally {
      setCandidatesBusy(false);
    }
  }

  function cancelCandidates() {
    setCandidatesFor(undefined);
    setCandidates(undefined);
    setConfirmingCandidate(undefined);
    setCandidatesProblem(undefined);
  }

  async function confirmLink(payslip: PayslipRow) {
    if (!confirmingCandidate) return;
    setLinkBusy(true);
    setCandidatesProblem(undefined);
    try {
      await request<PayslipRow>(`/payslips/${payslip.id}/deposit`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transactionId: confirmingCandidate.transactionId }),
      });
      cancelCandidates();
      setAttempt((value) => value + 1);
    } catch (error) {
      setCandidatesProblem(errorText(error, "This credit could not be linked."));
    } finally {
      setLinkBusy(false);
    }
  }

  async function unlink(payslip: PayslipRow) {
    setLinkBusy(true);
    setUnlinkFor(payslip.id);
    setUnlinkProblem(undefined);
    try {
      await request<PayslipRow>(`/payslips/${payslip.id}/deposit`, { method: "DELETE" });
      setUnlinkFor(undefined);
      setAttempt((value) => value + 1);
    } catch (error) {
      setUnlinkProblem(errorText(error, "This payslip could not be unlinked."));
    } finally {
      setLinkBusy(false);
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
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      setPreview(result);
      setLocked(false);
    } catch (error) {
      const message = errorText(error, "This file could not be checked.");
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
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      setImportResult(result);
      setAttempt((value) => value + 1);
    } catch (error) {
      setImportProblem(errorText(error, "This file could not be imported."));
    } finally {
      setImportBusy(false);
    }
  }

  const categoryName = new Map(categories.map((row) => [row.id, row.name]));
  function categoryLabel(row: CategoryRow) {
    if (!row.parentId) return row.name;
    const parent = categoryName.get(row.parentId);
    return parent ? `${parent} → ${row.name}` : row.name;
  }
  const expenseCategories = categories.filter((row) => row.kind === "expense" && row.isActive);
  // Spec: the category picker defaults to Travel › Flights when that category exists.
  const defaultSpendingCategoryId = categories.find((row) => row.slug === "travel-flights")?.id ?? "";

  function spendingCategoryFor(key: string, line: PayslipLineOut) {
    return spendingDraft[key] ?? line.categoryId ?? defaultSpendingCategoryId;
  }

  async function toggleSpending(payslip: PayslipRow, line: PayslipLineOut) {
    const key = `${payslip.id}:${line.lineNo}`;
    setSpendingBusy(key);
    setSpendingProblem(undefined);
    try {
      if (line.countedAsSpending) {
        await request<PayslipRow>(`/payslips/${payslip.id}/lines/${line.lineNo}/spending`, {
          method: "DELETE",
        });
      } else {
        const categoryId = spendingCategoryFor(key, line);
        if (!categoryId) return;
        await request<PayslipRow>(`/payslips/${payslip.id}/lines/${line.lineNo}/spending`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ categoryId }),
        });
      }
      setAttempt((value) => value + 1);
    } catch (error) {
      setSpendingProblem({ key, message: errorText(error, "This could not be saved.") });
    } finally {
      setSpendingBusy(undefined);
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
              return (
                <li key={payslip.id}>
                  <div>
                    <strong>{payslip.payMonth}</strong>
                    <span>{moneyText(payslip.netPay, payslip.currency, currencies)} net · {moneyText(payslip.grossEarnings, payslip.currency, currencies)} gross</span>
                    <span>{depositState(payslip)}</span>
                    {payslip.imported && <small>Imported</small>}
                  </div>
                  <ul className="salary-payslip-lines">
                    {payslip.lines.map((line) => {
                      const key = `${payslip.id}:${line.lineNo}`;
                      const busy = spendingBusy === key;
                      const chosenCategory = spendingCategoryFor(key, line);
                      return (
                        <li key={line.lineNo}>
                          <span>{line.kind}: {line.label} {moneyText(line.amount, payslip.currency, currencies)}</span>
                          {line.kind === "deduction" && (
                            <span className="salary-line-spending">
                              <label>
                                <input
                                  checked={line.countedAsSpending}
                                  disabled={busy || (!line.countedAsSpending && !chosenCategory)}
                                  onChange={() => void toggleSpending(payslip, line)}
                                  type="checkbox"
                                />
                                Count as spending
                              </label>
                              <select
                                disabled={line.countedAsSpending || busy}
                                onChange={(e) =>
                                  setSpendingDraft((current) => ({ ...current, [key]: e.target.value }))
                                }
                                value={chosenCategory}
                              >
                                <option value="">Choose a category</option>
                                {expenseCategories.map((row) => (
                                  <option key={row.id} value={row.id}>{categoryLabel(row)}</option>
                                ))}
                              </select>
                              {spendingProblem?.key === key && (
                                <span className="inline-alert" role="alert">{spendingProblem.message}</span>
                              )}
                            </span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                  <button
                    className="text-action"
                    disabled={Boolean(payslip.deposit) || Boolean(payslip.supersededByPayslipId)}
                    onClick={() => startCorrection(payslip)}
                    type="button"
                  >{payslip.deposit ? "Unlink before correcting" : "Correct"}</button>

                  {!payslip.supersededByPayslipId && (
                    payslip.deposit ? (
                      <>
                        <button
                          className="text-action"
                          disabled={linkBusy}
                          onClick={() => void unlink(payslip)}
                          type="button"
                        >Unlink</button>
                        {unlinkFor === payslip.id && unlinkProblem && (
                          <p className="inline-alert" role="alert">{unlinkProblem}</p>
                        )}
                      </>
                    ) : (
                      <button
                        className="text-action"
                        disabled={candidatesBusy}
                        onClick={() => void findCandidates(payslip)}
                        type="button"
                      >Find net-pay credit</button>
                    )
                  )}

                  {candidatesFor === payslip.id && (
                    <div className="salary-deposit-candidates">
                      {candidatesBusy && <p role="status">Looking for candidates…</p>}
                      {candidatesProblem && <p className="inline-alert" role="alert">{candidatesProblem}</p>}
                      {candidates && candidates.length === 0 && <p>No eligible credit was found.</p>}
                      {candidates && candidates.length > 0 && (
                        <ul>
                          {candidates.map((candidate) => (
                            <li key={candidate.transactionId}>
                              <span>{candidate.bookedDate} · {moneyText(candidate.amount, candidate.currency, currencies)} · {candidate.description}</span>
                              <button
                                className="text-action"
                                onClick={() => setConfirmingCandidate(candidate)}
                                type="button"
                              >Link</button>
                            </li>
                          ))}
                        </ul>
                      )}
                      {confirmingCandidate && (
                        <div role="alertdialog">
                          <p>Link the credit on {confirmingCandidate.bookedDate} to this payslip&rsquo;s net pay?</p>
                          <button className="primary-action" disabled={linkBusy} onClick={() => void confirmLink(payslip)} type="button">
                            {linkBusy ? "Linking…" : "Confirm link"}
                          </button>
                          <button className="text-action" onClick={() => setConfirmingCandidate(undefined)} type="button">Cancel</button>
                        </div>
                      )}
                      <button className="text-action" onClick={cancelCandidates} type="button">Close</button>
                    </div>
                  )}
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

      <section aria-labelledby="salary-deduction-rules-title">
        <h2 id="salary-deduction-rules-title">Deduction rules</h2>
        <p>
          A rule remembers a deduction label&rsquo;s spending category. It affects payslips linked
          afterwards, not deductions already counted as spending.
        </p>
        {deductionRulesState === "loading" && <p role="status">Loading deduction rules…</p>}
        {deductionRulesState === "error" && (
          <div role="alert">
            <p>Deduction rules could not be loaded.</p>
            <button
              className="text-action"
              onClick={() => { setDeductionRulesState("loading"); setDeductionRulesAttempt((v) => v + 1); }}
              type="button"
            >Try again</button>
          </div>
        )}
        {deductionRuleProblem && <p className="inline-alert" role="alert">{deductionRuleProblem}</p>}
        {deductionRulesState === "ready" && deductionRules && (
          <>
            {deductionRules.length === 0 && <p>No deduction rules are recorded yet.</p>}
            {deductionRules.length > 0 && (
              <ul className="salary-deduction-rules">
                {deductionRules.map((rule) => (
                  <li key={rule.label}>
                    <span>{rule.label}</span>
                    <select
                      disabled={deductionRuleBusy === rule.label}
                      onChange={(e) => void saveDeductionRule(rule.label, e.target.value)}
                      value={rule.categoryId}
                    >
                      {!expenseCategories.some((row) => row.id === rule.categoryId) && (
                        <option disabled value={rule.categoryId}>Category no longer active</option>
                      )}
                      {expenseCategories.map((row) => (
                        <option key={row.id} value={row.id}>{categoryLabel(row)}</option>
                      ))}
                    </select>
                    <button
                      className="text-action"
                      disabled={deductionRuleBusy === rule.label}
                      onClick={() => void removeDeductionRule(rule.label)}
                      type="button"
                    >Remove</button>
                  </li>
                ))}
              </ul>
            )}
            <div className="salary-deduction-rule-form">
              <label className="text-field">
                <span>Deduction label</span>
                <input onChange={(e) => setNewRuleLabel(e.target.value)} value={newRuleLabel} />
              </label>
              <label className="select-field">
                <span>Category</span>
                <select onChange={(e) => setNewRuleCategoryId(e.target.value)} value={newRuleCategoryId}>
                  <option value="">Choose a category</option>
                  {expenseCategories.map((row) => (
                    <option key={row.id} value={row.id}>{categoryLabel(row)}</option>
                  ))}
                </select>
              </label>
              <button
                className="primary-action"
                disabled={!newRuleLabel.trim() || !newRuleCategoryId || deductionRuleBusy === newRuleLabel.trim()}
                onClick={() => void saveDeductionRule(newRuleLabel, newRuleCategoryId)}
                type="button"
              >Add rule</button>
            </div>
          </>
        )}
      </section>
    </section>
  );
}
