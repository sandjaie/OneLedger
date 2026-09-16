"use client";

import { useEffect, useState } from "react";

import { errorText, type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type { BudgetCopyResponse, BudgetLineRow, BudgetsResponse, CategoryRow, CurrencyRow } from "@/types/api";

type Props = {
  categories: Array<CategoryRow>;
  currencies: Array<CurrencyRow>;
  // Budgets are always in the household base currency (spec "Budgets are owner assumptions, not
  // postings"), never the toggled reporting currency `SpendingScreen` shows elsewhere — this is
  // `SpendingResponse.baseCurrency`, already fetched by the screen that mounts this panel.
  baseCurrency: string;
  month: string;
  request: typeof apiFetch;
};

type State = "loading" | "ready" | "error";
type ScopeKind = "category" | "bucket";

const BUCKETS = ["need", "want", "investment", "debt_service", "income", "ignore"] as const;

const BUCKET_TEXT: Record<string, string> = {
  need: "Need",
  want: "Want",
  investment: "Investment",
  debt_service: "Debt service",
  income: "Income",
  ignore: "Ignore",
};

// Server sends short reason codes so the web owns only the sentence (this codebase's
// server-strings convention — see plans-screen.tsx's STATUS_TEXT and goals.tsx's reasonText).
const CARRY_REASON_TEXT: Record<string, string> = {
  no_prior_line: "no earlier line to carry from",
  rollover_off: "the earlier line does not carry forward",
  bound_reached: "carry stops after twelve months",
};

type LineFormState = {
  scopeKind: ScopeKind;
  categoryId: string;
  bucket: string;
  amountBase: string;
  rolloverEnabled: boolean;
  note: string;
};

type EditFormState = {
  amountBase: string;
  rolloverEnabled: boolean;
  note: string;
};

function emptyLineForm(defaultCategoryId: string): LineFormState {
  return {
    scopeKind: "category",
    categoryId: defaultCategoryId,
    bucket: BUCKETS[0],
    amountBase: "",
    rolloverEnabled: false,
    note: "",
  };
}

/** The prior calendar month for `month` (`YYYY-MM-01`) — `Date` arithmetic only, matching
 * `spending-screen.tsx::monthRange`, never `Number`/`parseInt` on the pieces. */
function previousMonth(month: string): string {
  const first = new Date(`${month}T00:00:00Z`);
  const prior = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() - 1, 1));
  return prior.toISOString().slice(0, 10);
}

function categoryName(categories: Array<CategoryRow>, id: string): string {
  return categories.find((category) => category.id === id)?.name ?? id;
}

function scopeLabel(row: BudgetLineRow): string {
  if (row.categoryId) return row.categoryName ?? row.categoryId;
  return BUCKET_TEXT[row.bucket ?? ""] ?? row.bucket ?? "";
}

export function BudgetPanel({ categories, currencies, baseCurrency, month, request }: Props) {
  const activeCategories = categories.filter((category) => category.isActive);

  const [data, setData] = useState<BudgetsResponse>();
  const [state, setState] = useState<State>("loading");
  const [attempt, setAttempt] = useState(0);
  const [problem, setProblem] = useState<string>();

  const [form, setForm] = useState<LineFormState>(() => emptyLineForm(activeCategories[0]?.id ?? ""));
  const [formKey, setFormKey] = useState(() => crypto.randomUUID());
  const [formBusy, setFormBusy] = useState(false);
  const [formProblem, setFormProblem] = useState<string>();

  const [editingId, setEditingId] = useState<string>();
  const [editForm, setEditForm] = useState<EditFormState>({
    amountBase: "", rolloverEnabled: false, note: "",
  });
  const [editBusy, setEditBusy] = useState(false);
  const [editProblem, setEditProblem] = useState<string>();

  const [copyBusy, setCopyBusy] = useState(false);
  const [copyKey, setCopyKey] = useState(() => crypto.randomUUID());
  const [copyMessage, setCopyMessage] = useState<string>();
  const [copyProblem, setCopyProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    // No `setState("loading")` reset here: this panel lives inside `SpendingScreen`'s own
    // ready block, so a `month` change already unmounts and remounts it (the screen resets to
    // its own loading state first); an `attempt` bump after a write just refreshes quietly,
    // matching `coverage-screen.tsx` and `wealth-screen.tsx`'s own effects.
    request<BudgetsResponse>(`/budgets?month=${encodeURIComponent(month)}`)
      .then((result) => {
        if (!current) return;
        setData(result);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        setProblem(errorText(error, "The budget could not be loaded."));
        setState("error");
      });
    return () => {
      current = false;
    };
  }, [month, attempt, request]);

  async function submitLine() {
    setFormBusy(true);
    setFormProblem(undefined);
    try {
      await request("/budgets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotencyKey: formKey,
          periodMonth: month,
          categoryId: form.scopeKind === "category" ? form.categoryId : null,
          bucket: form.scopeKind === "bucket" ? form.bucket : null,
          amountBase: form.amountBase,
          rolloverEnabled: form.rolloverEnabled,
          note: form.note || null,
        }),
      });
      setForm(emptyLineForm(activeCategories[0]?.id ?? ""));
      setFormKey(crypto.randomUUID());
      setAttempt((value) => value + 1);
    } catch (error) {
      setFormProblem(errorText(error, "This budget line could not be saved."));
    } finally {
      setFormBusy(false);
    }
  }

  function startEdit(row: BudgetLineRow) {
    setEditingId(row.id);
    setEditForm({
      amountBase: row.amountBase, rolloverEnabled: row.rolloverEnabled, note: row.note ?? "",
    });
    setEditProblem(undefined);
  }

  async function submitEdit(lineId: string) {
    setEditBusy(true);
    setEditProblem(undefined);
    try {
      await request(`/budgets/${lineId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amountBase: editForm.amountBase,
          rolloverEnabled: editForm.rolloverEnabled,
          note: editForm.note || null,
        }),
      });
      setEditingId(undefined);
      setAttempt((value) => value + 1);
    } catch (error) {
      setEditProblem(errorText(error, "This budget line could not be updated."));
    } finally {
      setEditBusy(false);
    }
  }

  async function removeLine(row: BudgetLineRow) {
    if (!window.confirm(`Remove the budget for ${scopeLabel(row)}? This does not touch the ledger.`)) {
      return;
    }
    setProblem(undefined);
    try {
      await request(`/budgets/${row.id}`, { method: "DELETE" });
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This budget line could not be removed."));
    }
  }

  async function copyLastMonth() {
    setCopyBusy(true);
    setCopyMessage(undefined);
    setCopyProblem(undefined);
    try {
      const result = await request<BudgetCopyResponse>("/budgets/copy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fromMonth: previousMonth(month),
          toMonth: month,
          idempotencyKey: copyKey,
        }),
      });
      const copiedCount = result.created.length;
      setCopyMessage(
        `Copied ${copiedCount} line${copiedCount === 1 ? "" : "s"}` +
          (result.skippedCount > 0
            ? ` — ${result.skippedCount} already had a budget this month.`
            : "."),
      );
      setCopyKey(crypto.randomUUID());
      setAttempt((value) => value + 1);
    } catch (error) {
      setCopyProblem(errorText(error, "Last month could not be copied."));
    } finally {
      setCopyBusy(false);
    }
  }

  const editingLine = data?.lines?.find((line) => line.id === editingId);
  const completeLineForm = Boolean(
    form.amountBase && (form.scopeKind === "bucket" || form.categoryId),
  );

  return (
    <section aria-labelledby="budget-panel-title" className="budget-panel" id="budget-panel">
      <h2 id="budget-panel-title">Budget</h2>
      <p className="lede">
        A budget is an assumption, not a posting — setting, editing or removing one never changes
        the ledger. A bucket&rsquo;s actual excludes any category that carries its own budget this
        month, so nothing is counted twice.
      </p>

      {state === "loading" && <p role="status">Loading budget…</p>}
      {state === "error" && (
        <div role="alert">
          <p>{problem}</p>
          <button
            className="text-action"
            onClick={() => setAttempt((value) => value + 1)}
            type="button"
          >Try again</button>
        </div>
      )}

      {state === "ready" && data && (
        <>
          {problem && <p className="inline-alert" role="alert">{problem}</p>}

          {data.lines.length === 0 && <p>No budget lines are set for this month.</p>}
          {data.lines.length > 0 && (
            <table className="spending-table">
              <caption className="visually-hidden">Budget lines for {month}</caption>
              <thead>
                <tr>
                  <th scope="col">Scope</th>
                  <th scope="col">Budgeted</th>
                  <th scope="col">Actual</th>
                  <th scope="col">Available</th>
                  <th scope="col">Variance</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.lines.map((row) => (
                  <tr key={row.id}>
                    <th scope="row">
                      {scopeLabel(row)}
                      {row.excludedCategoryIds.length > 0 && (
                        <small>
                          {" "}— excludes{" "}
                          {row.excludedCategoryIds
                            .map((id) => categoryName(categories, id))
                            .join(", ")}
                        </small>
                      )}
                      {row.rolloverEnabled && (
                        <small>
                          {" "}— carried {moneyText(row.carriedBase, row.currency, currencies)} over{" "}
                          {row.carryMonths} month{row.carryMonths === 1 ? "" : "s"}
                          {" "}({CARRY_REASON_TEXT[row.carryStoppedReason] ?? row.carryStoppedReason})
                        </small>
                      )}
                    </th>
                    <td>{moneyText(row.amountBase, row.currency, currencies)}</td>
                    <td>{moneyText(row.actualBase, row.currency, currencies)}</td>
                    <td>{moneyText(row.availableBase, row.currency, currencies)}</td>
                    <td className={row.varianceBase.startsWith("-") ? undefined : "budget-variance positive"}>
                      {moneyText(row.varianceBase, row.currency, currencies)}
                    </td>
                    <td>
                      <button className="text-action" onClick={() => startEdit(row)} type="button">
                        Edit
                      </button>
                      <button className="text-action" onClick={() => void removeLine(row)} type="button">
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {editingId && editingLine && (
            <div className="budget-line-form">
              <h3>Edit {scopeLabel(editingLine)}</h3>
              <label className="text-field">
                <span>Amount ({editingLine.currency})</span>
                <input
                  inputMode="decimal"
                  onChange={(e) => setEditForm((current) => ({ ...current, amountBase: e.target.value }))}
                  value={editForm.amountBase}
                />
              </label>
              <label>
                <input
                  checked={editForm.rolloverEnabled}
                  onChange={(e) =>
                    setEditForm((current) => ({ ...current, rolloverEnabled: e.target.checked }))
                  }
                  type="checkbox"
                />
                {" "}Carry the unspent or overspent remainder into next month
              </label>
              <label className="text-field">
                <span>Note (optional)</span>
                <input
                  onChange={(e) => setEditForm((current) => ({ ...current, note: e.target.value }))}
                  value={editForm.note}
                />
              </label>
              {editProblem && <p className="inline-alert" role="alert">{editProblem}</p>}
              <button
                className="primary-action"
                disabled={editBusy || !editForm.amountBase}
                onClick={() => void submitEdit(editingId)}
                type="button"
              >{editBusy ? "Saving…" : "Save changes"}</button>
              <button className="text-action" onClick={() => setEditingId(undefined)} type="button">
                Cancel
              </button>
            </div>
          )}

          <div className="budget-panel-actions">
            <button
              className="text-action"
              disabled={copyBusy}
              onClick={() => void copyLastMonth()}
              type="button"
            >{copyBusy ? "Copying…" : "Copy last month"}</button>
          </div>
          {copyMessage && <p role="status">{copyMessage}</p>}
          {copyProblem && <p className="inline-alert" role="alert">{copyProblem}</p>}

          <h3>Set a budget</h3>
          <div aria-label="Scope" role="radiogroup">
            <label>
              <input
                checked={form.scopeKind === "category"}
                onChange={() => setForm((current) => ({ ...current, scopeKind: "category" }))}
                type="radio"
              />
              {" "}Category
            </label>
            <label>
              <input
                checked={form.scopeKind === "bucket"}
                onChange={() => setForm((current) => ({ ...current, scopeKind: "bucket" }))}
                type="radio"
              />
              {" "}Bucket
            </label>
          </div>
          {form.scopeKind === "category" ? (
            <label className="select-field">
              <span>Category</span>
              <select
                onChange={(e) => setForm((current) => ({ ...current, categoryId: e.target.value }))}
                value={form.categoryId}
              >
                {activeCategories.map((category) => (
                  <option key={category.id} value={category.id}>{category.name}</option>
                ))}
              </select>
            </label>
          ) : (
            <label className="select-field">
              <span>Bucket</span>
              <select
                onChange={(e) => setForm((current) => ({ ...current, bucket: e.target.value }))}
                value={form.bucket}
              >
                {BUCKETS.map((bucket) => (
                  <option key={bucket} value={bucket}>{BUCKET_TEXT[bucket]}</option>
                ))}
              </select>
            </label>
          )}
          <label className="text-field">
            <span>Amount ({baseCurrency})</span>
            <input
              inputMode="decimal"
              onChange={(e) => setForm((current) => ({ ...current, amountBase: e.target.value }))}
              value={form.amountBase}
            />
          </label>
          <label>
            <input
              checked={form.rolloverEnabled}
              onChange={(e) => setForm((current) => ({ ...current, rolloverEnabled: e.target.checked }))}
              type="checkbox"
            />
            {" "}Carry the unspent or overspent remainder into next month
          </label>
          <label className="text-field">
            <span>Note (optional)</span>
            <input
              onChange={(e) => setForm((current) => ({ ...current, note: e.target.value }))}
              value={form.note}
            />
          </label>
          {formProblem && <p className="inline-alert" role="alert">{formProblem}</p>}
          <button
            className="primary-action"
            disabled={formBusy || !completeLineForm}
            onClick={() => void submitLine()}
            type="button"
          >{formBusy ? "Saving…" : "Add budget"}</button>
        </>
      )}
    </section>
  );
}
