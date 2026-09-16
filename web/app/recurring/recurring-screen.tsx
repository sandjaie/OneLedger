"use client";

import { useEffect, useState } from "react";

import { errorText, type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type { CurrencyRow, RecurringResponse, RecurringSeriesRow } from "@/types/api";

type Props = {
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type State = "loading" | "ready" | "error";

const CADENCE_TEXT: Record<string, string> = {
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
  semiannual: "Semiannual",
  annual: "Annual",
};

const STATUS_TEXT: Record<string, string> = {
  active: "Active",
  overdue: "Overdue",
};

export function RecurringScreen({ currencies, request }: Props) {
  const [series, setSeries] = useState<Array<RecurringSeriesRow>>();
  const [state, setState] = useState<State>("loading");
  const [problem, setProblem] = useState<string>();

  const [renamingId, setRenamingId] = useState<string>();
  const [renameValue, setRenameValue] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameProblem, setRenameProblem] = useState<string>();

  const [flipBusy, setFlipBusy] = useState<string>();
  const [flipProblem, setFlipProblem] = useState<string>();

  const [removeBusy, setRemoveBusy] = useState<string>();
  const [removeProblem, setRemoveProblem] = useState<string>();

  // Refresh runs exactly once, when the screen opens — never on every reload after a local
  // action, and never in a loop. `request` is Workspace's own stable `useCallback`, so this
  // effect's dependency array never re-fires on an unrelated re-render.
  useEffect(() => {
    let current = true;
    request("/recurring/refresh", { method: "POST" })
      .catch(() => undefined) // a refresh hiccup should not hide what is already stored
      .then(() => request<RecurringResponse>("/recurring"))
      .then((result) => {
        if (!current) return;
        setSeries(result.series);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        setProblem(errorText(error, "Recurring payments could not be loaded."));
        setState("error");
      });
    return () => {
      current = false;
    };
  }, [request]);

  async function reload() {
    try {
      const result = await request<RecurringResponse>("/recurring");
      setSeries(result.series);
      setProblem(undefined);
      setState("ready");
    } catch (error) {
      setProblem(errorText(error, "Recurring payments could not be reloaded."));
    }
  }

  async function flip(row: RecurringSeriesRow) {
    setFlipBusy(row.id);
    setFlipProblem(undefined);
    try {
      await request(`/recurring/${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ isSubscription: !row.isSubscription }),
      });
      await reload();
    } catch (error) {
      setFlipProblem(errorText(error, "This could not be saved."));
    } finally {
      setFlipBusy(undefined);
    }
  }

  function startRename(row: RecurringSeriesRow) {
    setRenamingId(row.id);
    setRenameValue(row.label);
    setRenameProblem(undefined);
  }

  async function submitRename(seriesId: string) {
    setRenameBusy(true);
    setRenameProblem(undefined);
    try {
      await request(`/recurring/${seriesId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: renameValue }),
      });
      setRenamingId(undefined);
      await reload();
    } catch (error) {
      setRenameProblem(errorText(error, "This name could not be saved."));
    } finally {
      setRenameBusy(false);
    }
  }

  async function remove(row: RecurringSeriesRow) {
    if (
      !window.confirm(
        `Remove ${row.label}? This is not "not a subscription" — it stops tracking this payment, and it will not come back on its own.`,
      )
    ) {
      return;
    }
    setRemoveBusy(row.id);
    setRemoveProblem(undefined);
    try {
      await request(`/recurring/${row.id}/remove`, { method: "POST" });
      await reload();
    } catch (error) {
      setRemoveProblem(errorText(error, "This could not be removed."));
    } finally {
      setRemoveBusy(undefined);
    }
  }

  return (
    <section className="workspace-content" aria-labelledby="recurring-title">
      <p className="eyebrow">Recurring</p>
      <h1 className="workspace-title" id="recurring-title">Recurring payments</h1>
      <p className="lede">
        Found automatically from the ledger — nothing here needs confirming. A payment is shown as
        a subscription or not; the one correction is that flip. Remove takes a payment off this
        list entirely and it will not return on its own.
      </p>

      {state === "loading" && <p role="status">Checking for recurring payments…</p>}
      {state === "error" && (
        <div role="alert">
          <p>{problem}</p>
          <button className="text-action" onClick={() => void reload()} type="button">
            Try again
          </button>
        </div>
      )}

      {state === "ready" && series && (
        <>
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          {flipProblem && <p className="inline-alert" role="alert">{flipProblem}</p>}
          {removeProblem && <p className="inline-alert" role="alert">{removeProblem}</p>}

          {series.length === 0 && <p>No recurring payment has been found yet.</p>}
          {series.length > 0 && (
            <ul className="recurring-confirmed-list">
              {series.map((row) => (
                <li key={row.id}>
                  <span>
                    {row.label}
                    {row.needsName && <span className="plans-badge">Needs a name</span>}
                    {" "}· {CADENCE_TEXT[row.cadence] ?? row.cadence}
                    {row.categoryName ? ` · ${row.categoryName}` : ""}
                  </span>
                  <div className="plans-badges">
                    <span className="plans-badge">{STATUS_TEXT[row.status] ?? row.status}</span>
                    {row.isSubscription && <span className="plans-badge">Subscription</span>}
                  </div>
                  <span>
                    {moneyText(row.nativeAmount, row.nativeCurrency, currencies)} · last seen{" "}
                    {row.lastSeenOn}
                    {row.priceChangedFrom && row.priceChangedOn && (
                      <>
                        {" "}· price changed from{" "}
                        {moneyText(row.priceChangedFrom, row.nativeCurrency, currencies)} to{" "}
                        {moneyText(row.nativeAmount, row.nativeCurrency, currencies)} on{" "}
                        {row.priceChangedOn}
                      </>
                    )}
                  </span>

                  <div className="plans-settlement-actions">
                    <button
                      className="text-action"
                      disabled={flipBusy === row.id}
                      onClick={() => void flip(row)}
                      type="button"
                    >
                      {flipBusy === row.id
                        ? "Saving…"
                        : row.isSubscription
                          ? "Not a subscription"
                          : "A subscription"}
                    </button>
                    {renamingId !== row.id && (
                      <button className="text-action" onClick={() => startRename(row)} type="button">
                        Rename
                      </button>
                    )}
                    <button
                      className="text-action"
                      disabled={removeBusy === row.id}
                      onClick={() => void remove(row)}
                      type="button"
                    >
                      {removeBusy === row.id ? "Removing…" : "Remove"}
                    </button>
                  </div>

                  {renamingId === row.id && (
                    <div className="recurring-dismiss-form">
                      <label className="text-field">
                        <span>Label</span>
                        <input onChange={(e) => setRenameValue(e.target.value)} value={renameValue} />
                      </label>
                      {renameProblem && <p className="inline-alert" role="alert">{renameProblem}</p>}
                      <button
                        className="primary-action"
                        disabled={renameBusy || !renameValue.trim()}
                        onClick={() => void submitRename(row.id)}
                        type="button"
                      >
                        {renameBusy ? "Saving…" : "Save name"}
                      </button>
                      <button className="text-action" onClick={() => setRenamingId(undefined)} type="button">
                        Cancel
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
