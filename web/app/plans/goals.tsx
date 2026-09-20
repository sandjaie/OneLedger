"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import { accountValueReasonText, reasonsText } from "@/lib/plans/account-value-reasons";
import type {
  AccountRow,
  CurrencyRow,
  GoalRow,
  GoalsResponse,
  ReservationRow,
} from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  // A goal's currency is the household base or secondary currency only (spec 3.3 "Goal
  // progress"), so the picker offers just these two rather than the whole catalogue.
  householdCurrencies: { base: string; secondary: string };
  request: typeof apiFetch;
};

const VALUATION_MODE_TEXT: Record<string, string> = {
  ledger_balance: "ledger balance",
  holdings: "holdings",
  snapshot: "snapshot",
};

type GoalFormState = {
  name: string;
  targetAmount: string;
  targetCurrency: string;
  targetDate: string;
  priority: number;
  plannedMonthly: string;
};

// A fixed option list, not a parsed string: `Number`/`parseInt` are banned in this codebase
// (money is a string on the wire and never becomes a JS number), so the priority picker below
// looks the chosen option up by its index instead of converting the select's string value.
const PRIORITIES = [1, 2, 3, 4, 5] as const;

type ReserveKind = "amount" | "whole_account";

type ReserveFormState = {
  accountId: string;
  kind: ReserveKind;
  amount: string;
};

// Server sends short reason codes so the web owns only the sentence, per the codebase's
// server-strings convention (see plans-screen.tsx's STATUS_TEXT).
const PROGRESS_REASON_TEXT: Record<string, string> = {
  missing_value_or_rate: "a value or exchange rate is unavailable",
  over_reserved: "this account is reserved for more than it is currently worth",
};

function reasonText(reasons: Array<string>): string {
  return reasonsText(reasons, PROGRESS_REASON_TEXT);
}

function emptyGoalForm(base: string): GoalFormState {
  return {
    name: "",
    targetAmount: "",
    targetCurrency: base,
    targetDate: "",
    priority: 3,
    plannedMonthly: "",
  };
}

function goalFormFrom(goal: GoalRow): GoalFormState {
  return {
    name: goal.name,
    targetAmount: goal.targetAmount,
    targetCurrency: goal.targetCurrency,
    targetDate: goal.targetDate,
    priority: goal.priority,
    plannedMonthly: goal.plannedMonthly ?? "",
  };
}

export function Goals({ accounts, currencies, householdCurrencies, request }: Props) {
  const goalCurrencies = currencies.filter(
    (currency) =>
      currency.code === householdCurrencies.base || currency.code === householdCurrencies.secondary,
  );
  const activeAssetAccounts = accounts.filter(
    (account) => account.isActive && account.accountClass === "asset",
  );
  const [data, setData] = useState<GoalsResponse>();
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState<GoalRow>();
  const [form, setForm] = useState<GoalFormState>(() => emptyGoalForm(householdCurrencies.base));
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [feedback, setFeedback] = useState<string>();

  const [reservingGoalId, setReservingGoalId] = useState<string>();
  const [reserveForm, setReserveForm] = useState<ReserveFormState>({
    accountId: "", kind: "amount", amount: "",
  });
  const [reserveKey, setReserveKey] = useState(() => crypto.randomUUID());
  const [reserveBusy, setReserveBusy] = useState(false);
  const [reserveProblem, setReserveProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    request<GoalsResponse>("/goals?includeClosed=true")
      .then((result) => {
        if (!current) return;
        setData(result);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  function changeForm<K extends keyof GoalFormState>(field: K, value: GoalFormState[K]) {
    setForm((current) => ({ ...current, [field]: value }));
    setProblem(undefined);
  }

  function startEdit(goal: GoalRow) {
    setEditing(goal);
    setForm(goalFormFrom(goal));
    setReservingGoalId(undefined);
    setProblem(undefined);
    setFeedback(undefined);
  }

  function cancelEdit() {
    setEditing(undefined);
    setForm(emptyGoalForm(householdCurrencies.base));
    setIdempotencyKey(crypto.randomUUID());
    setProblem(undefined);
  }

  async function submitGoal() {
    setBusy(true);
    setProblem(undefined);
    setFeedback(undefined);
    try {
      const fields = {
        name: form.name,
        targetAmount: form.targetAmount,
        targetCurrency: form.targetCurrency,
        targetDate: form.targetDate,
        priority: form.priority,
        plannedMonthly: form.plannedMonthly || null,
      };
      if (editing) {
        await request<GoalRow>(`/goals/${editing.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(fields),
        });
        setFeedback("Goal updated.");
      } else {
        await request<GoalRow>("/goals", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...fields, idempotencyKey }),
        });
        setFeedback("Goal added.");
      }
      setEditing(undefined);
      setForm(emptyGoalForm(householdCurrencies.base));
      setIdempotencyKey(crypto.randomUUID());
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This goal could not be saved."));
    } finally {
      setBusy(false);
    }
  }

  async function closeGoal(goal: GoalRow, outcome: "achieved" | "abandoned") {
    const question =
      outcome === "achieved"
        ? `Mark "${goal.name}" achieved? Its reservations will be released.`
        : `Abandon "${goal.name}"? Its reservations will be released.`;
    if (!window.confirm(question)) {
      return;
    }
    setBusy(true);
    setProblem(undefined);
    try {
      await request<GoalRow>(`/goals/${goal.id}/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: outcome }),
      });
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This goal could not be closed."));
    } finally {
      setBusy(false);
    }
  }

  function startReserve(goal: GoalRow) {
    setEditing(undefined);
    setReservingGoalId(goal.id);
    setReserveForm({ accountId: activeAssetAccounts[0]?.id ?? "", kind: "amount", amount: "" });
    setReserveKey(crypto.randomUUID());
    setReserveProblem(undefined);
  }

  async function submitReserve(goalId: string) {
    setReserveBusy(true);
    setReserveProblem(undefined);
    try {
      await request("/reservations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          purpose: "goal",
          goalId,
          accountId: reserveForm.accountId,
          kind: reserveForm.kind,
          amount: reserveForm.kind === "amount" ? reserveForm.amount : null,
          idempotencyKey: reserveKey,
        }),
      });
      setReservingGoalId(undefined);
      setReserveKey(crypto.randomUUID());
      setAttempt((value) => value + 1);
    } catch (error) {
      setReserveProblem(errorText(error, "This reservation could not be made."));
    } finally {
      setReserveBusy(false);
    }
  }

  async function release(reservation: ReservationRow) {
    setBusy(true);
    setProblem(undefined);
    try {
      await request(`/reservations/${reservation.id}/release`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      setAttempt((value) => value + 1);
    } catch (error) {
      setProblem(errorText(error, "This reservation could not be released."));
    } finally {
      setBusy(false);
    }
  }

  const completeGoalForm = Boolean(
    form.name && form.targetAmount && form.targetCurrency && form.targetDate,
  );
  const completeReserveForm = Boolean(
    reserveForm.accountId && (reserveForm.kind === "whole_account" || reserveForm.amount),
  );
  const reservingAccount = data?.accounts.find((row) => row.accountId === reserveForm.accountId);

  return (
    <section className="plans-goals-section" aria-labelledby="goals-title" id="goals">
      <div className="console-section-head"><h2 id="goals-title">Goals</h2></div>

      {state === "loading" && <p role="status">Loading goals…</p>}
      {state === "error" && (
        <div role="alert">
          <p>Goals could not be loaded.</p>
          <Button variant="outline" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</Button>
        </div>
      )}

      {state === "ready" && data && (
        <>
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          {feedback && <p role="status">{feedback}</p>}

          {data.goals.length === 0 && <p>No goals are recorded yet.</p>}
          {data.goals.length > 0 && (
            <ul className="plans-goal-list">
              {data.goals.map((goal) => (
                <li key={goal.id}>
                  <span>
                    {goal.name} · Target {moneyText(goal.targetAmount, goal.targetCurrency, currencies)}
                    {" by "}{goal.targetDate} · Priority {goal.priority}
                    {goal.state !== "active" && (
                      <> · {goal.state === "achieved" ? "Achieved" : "Abandoned"}</>
                    )}
                  </span>

                  {goal.progressStatus === "unavailable" ? (
                    <p>
                      Progress unavailable
                      {goal.reasons.length > 0 && <> — {reasonText(goal.reasons)}</>}
                    </p>
                  ) : (
                    <>
                      <p>
                        Current {moneyText(goal.current ?? "0", goal.targetCurrency, currencies)}
                        {goal.percent !== null && <> ({goal.percent}%)</>}
                        {" · Contributed "}
                        {moneyText(goal.contributed ?? "0", goal.targetCurrency, currencies)}
                        {" · Market "}
                        {moneyText(goal.market ?? "0", goal.targetCurrency, currencies)}
                        {" · FX "}
                        {moneyText(goal.fx ?? "0", goal.targetCurrency, currencies)}
                      </p>
                      {goal.plannedMonthly && (
                        <p>
                          Planned {moneyText(goal.plannedMonthly, goal.targetCurrency, currencies)}/month
                          {" · Projected "}
                          {moneyText(goal.projected ?? "0", goal.targetCurrency, currencies)}
                        </p>
                      )}
                      {/* Both server figures, never compared in TypeScript; at most one is non-zero. */}
                      <p>
                        Shortfall {moneyText(goal.shortfall ?? "0", goal.targetCurrency, currencies)}
                        {" · Surplus "}{moneyText(goal.surplus ?? "0", goal.targetCurrency, currencies)}
                      </p>
                      {goal.reasons.includes("over_reserved") && (
                        <p className="inline-alert" role="alert">
                          {reasonText(["over_reserved"])}
                        </p>
                      )}
                    </>
                  )}

                  {goal.reservations.length > 0 && (
                    <ul>
                      {goal.reservations.map((reservation) => (
                        <li key={reservation.id}>
                          {reservation.accountName} · Reserved {reservation.reservedOn}
                          {" · "}
                          {moneyText(reservation.nativeAmount, reservation.currency, currencies)}
                          {reservation.currentValue && (
                            <>
                              {" · Now "}
                              {moneyText(reservation.currentValue, goal.targetCurrency, currencies)}
                            </>
                          )}
                          {reservation.releasedAt ? (
                            <small>
                              {" · Released"}
                              {reservation.releaseReason && ` (${reservation.releaseReason})`}
                            </small>
                          ) : (
                            <Button
                              variant="outline"
                              disabled={busy}
                              onClick={() => void release(reservation)}
                              type="button"
                            >Release</Button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}

                  {goal.state === "active" && (
                    <>
                      <Button variant="outline" onClick={() => startEdit(goal)} type="button">Edit</Button>
                      <Button variant="outline" onClick={() => startReserve(goal)} type="button">Reserve</Button>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => void closeGoal(goal, "achieved")}
                        type="button"
                      >Mark achieved</Button>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => void closeGoal(goal, "abandoned")}
                        type="button"
                      >Abandon</Button>
                    </>
                  )}

                  {reservingGoalId === goal.id && (
                    <div className="plans-reserve-form">
                      <h4>Reserve for {goal.name}</h4>
                      <label className="select-field">
                        <span>Account</span>
                        <NativeSelect
                          onChange={(e) =>
                            setReserveForm((current) => ({ ...current, accountId: e.target.value }))
                          }
                          value={reserveForm.accountId}
                        >
                          {activeAssetAccounts.map((account) => (
                            <option key={account.id} value={account.id}>{account.name}</option>
                          ))}
                        </NativeSelect>
                      </label>
                      {reservingAccount && (
                        <p>
                          Value{" "}
                          {reservingAccount.value
                            ? moneyText(reservingAccount.value, reservingAccount.currency, currencies)
                            : `unavailable (${accountValueReasonText(reservingAccount.reason ?? "")})`}
                          {" · Reserved "}
                          {reservingAccount.reserved
                            ? moneyText(reservingAccount.reserved, reservingAccount.currency, currencies)
                            : "—"}
                          {" · Unreserved "}
                          {reservingAccount.unreserved
                            ? moneyText(reservingAccount.unreserved, reservingAccount.currency, currencies)
                            : "—"}
                          {reservingAccount.overReserved && " · Over-reserved"}
                        </p>
                      )}
                      <label className="select-field">
                        <span>Reserve</span>
                        <NativeSelect
                          onChange={(e) =>
                            setReserveForm((current) => ({
                              ...current, kind: e.target.value as ReserveKind,
                            }))
                          }
                          value={reserveForm.kind}
                        >
                          <option value="amount">An amount</option>
                          <option value="whole_account">The whole account</option>
                        </NativeSelect>
                      </label>
                      {reserveForm.kind === "amount" && (
                        <label className="text-field">
                          <span>Amount{reservingAccount ? ` (${reservingAccount.currency})` : ""}</span>
                          <Input
                            inputMode="decimal"
                            onChange={(e) =>
                              setReserveForm((current) => ({ ...current, amount: e.target.value }))
                            }
                            value={reserveForm.amount}
                          />
                        </label>
                      )}
                      {reserveProblem && <p className="inline-alert" role="alert">{reserveProblem}</p>}
                      <Button
                        variant="default"
                        disabled={!completeReserveForm || reserveBusy}
                        onClick={() => void submitReserve(goal.id)}
                        type="button"
                      >{reserveBusy ? "Reserving…" : "Reserve"}</Button>
                      <Button
                        variant="outline"
                        onClick={() => setReservingGoalId(undefined)}
                        type="button"
                      >Cancel</Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          <h3>{editing ? `Edit ${editing.name}` : "Add a goal"}</h3>
          <label className="text-field">
            <span>Name</span>
            <Input onChange={(e) => changeForm("name", e.target.value)} value={form.name} />
          </label>
          <label className="text-field">
            <span>Target amount</span>
            <Input
              inputMode="decimal"
              onChange={(e) => changeForm("targetAmount", e.target.value)}
              value={form.targetAmount}
            />
          </label>
          <label className="select-field">
            <span>Currency</span>
            <NativeSelect
              onChange={(e) => changeForm("targetCurrency", e.target.value)}
              value={form.targetCurrency}
            >
              {goalCurrencies.map((currency) => (
                <option key={currency.code} value={currency.code}>{currency.code}</option>
              ))}
            </NativeSelect>
          </label>
          <label className="date-field">
            <span>Target date</span>
            <Input
              onChange={(e) => changeForm("targetDate", e.target.value)}
              type="date"
              value={form.targetDate}
            />
          </label>
          <label className="select-field">
            <span>Priority</span>
            <NativeSelect
              onChange={(e) => changeForm("priority", PRIORITIES[e.target.selectedIndex])}
              value={form.priority}
            >
              {PRIORITIES.map((priority) => (
                <option key={priority} value={priority}>{priority}</option>
              ))}
            </NativeSelect>
          </label>
          <label className="text-field">
            <span>Planned monthly (optional)</span>
            <Input
              inputMode="decimal"
              onChange={(e) => changeForm("plannedMonthly", e.target.value)}
              value={form.plannedMonthly}
            />
          </label>

          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          <Button
            variant="default"
            disabled={!completeGoalForm || busy}
            onClick={() => void submitGoal()}
            type="button"
          >{busy ? "Saving…" : editing ? "Save changes" : "Add goal"}</Button>
          {editing && (
            <Button variant="outline" onClick={cancelEdit} type="button">Cancel</Button>
          )}

          <h3>Resources</h3>
          {data.accounts.length === 0 && <p>No reservable accounts.</p>}
          {data.accounts.length > 0 && (
            <>
              <table className="plans-resources-table">
                <thead>
                  <tr><th>Account</th><th>Valuation</th><th>Value</th><th>Reserved</th><th>Unreserved</th></tr>
                </thead>
                <tbody>
                  {data.accounts.map((row) => (
                    <tr key={row.accountId}>
                      <td>{row.accountName}</td>
                      <td>{VALUATION_MODE_TEXT[row.valuationMode] ?? row.valuationMode}</td>
                      <td>
                        {row.value
                          ? moneyText(row.value, row.currency, currencies)
                          : `Unavailable (${accountValueReasonText(row.reason ?? "")})`}
                      </td>
                      <td>{row.reserved ? moneyText(row.reserved, row.currency, currencies) : "—"}</td>
                      <td>
                        {row.unreserved ? moneyText(row.unreserved, row.currency, currencies) : "—"}
                        {row.overReserved && " · Over-reserved"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p>
                <small>
                  A snapshot account&rsquo;s change is treated as valuation movement, not a
                  contribution.
                </small>
              </p>
            </>
          )}
        </>
      )}
    </section>
  );
}
