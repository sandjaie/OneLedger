"use client";

import { Button } from "@/components/ui/button";

import { useEffect, useState } from "react";

import { ForecastEvidenceChart } from "@/app/plans/forecast-evidence-chart";
import { NativeSelect } from "@/components/ui/native-select";
import { type apiFetch } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type { AccountRow, CurrencyRow, ForecastResponse, UnplannedCardDue } from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
  // Not projected (Task 1) reuses the same settlement panel PlansScreen renders per commitment.
  onSettle: (commitmentId: string) => void;
  // Plan payment hands the card due to PlansScreen, which pre-fills the commitment form — the
  // same flow Needs attention's card items use, from a differently-shaped server row.
  onPlanCardPayment: (due: UnplannedCardDue) => void;
  // Bumped by PlansScreen after any commitment write (Plan payment, Settle, edit, archive) so
  // the forecast refetches with it.
  refreshKey: number;
};

const EXCLUDED_ACCOUNT_TEXT: Record<string, string> = {
  not_ledger_valued: "This account is valued from holdings or a snapshot, not the ledger.",
  no_balance_date: "This account has no ledger balance date.",
  balance_after_as_of: "This account's balance date is after the forecast date.",
};

const EXCLUDED_COMMITMENT_TEXT: Record<string, string> = {
  account_closed: "Its account is closed.",
  account_excluded: "Its account is excluded from the forecast.",
  currency_mismatch: "Its currency no longer matches its account's currency.",
  card_settlement_not_projected: "Its card settlement is not projected onto the card.",
};

function accountName(accounts: Array<AccountRow>, id: string): string {
  return accounts.find((account) => account.id === id)?.name ?? id;
}

export function CashForecast({
  accounts,
  currencies,
  request,
  onSettle,
  onPlanCardPayment,
  refreshKey,
}: Props) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [forecast, setForecast] = useState<ForecastResponse>();
  const [attempt, setAttempt] = useState(0);
  const [selectedContext, setSelectedContext] = useState("");

  useEffect(() => {
    let current = true;
    request<ForecastResponse>("/forecast")
      .then((result) => {
        if (!current) return;
        setForecast(result);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, refreshKey, request]);

  const contexts = forecast ? [
    ...forecast.currencies.map((row) => ({ id: `currency:${row.currency}`, title: `${row.currency} · eligible accounts`, currency: row.currency, points: row.points })),
    ...forecast.accounts.map((row) => ({ id: `account:${row.accountId}`, title: `${row.accountName} · ${row.currency}`, currency: row.currency, points: row.points })),
  ] : [];
  const context = contexts.find((item) => item.id === selectedContext) ?? contexts[0];

  return (
    <section aria-labelledby="plans-forecast-title" id="forecast">
      <h2 id="plans-forecast-title">Forecast{forecast ? ` as of ${forecast.asOf}` : ""}</h2>
      {state === "loading" && <p role="status">Loading the forecast…</p>}
      {state === "error" && (
        <div role="alert">
          <p>The forecast could not be loaded.</p>
          <Button
            variant="outline"
            onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}
            type="button"
          >Try again</Button>
        </div>
      )}
      {state === "ready" && forecast && (
        <>
          {context && <div className="forecast-chart-panel">
            <label className="select-field">
              <span>Chart context</span>
              <NativeSelect onChange={(event) => setSelectedContext(event.target.value)} value={context.id}>
                <optgroup label="Separate currency totals">
                  {forecast.currencies.map((row) => <option key={row.currency} value={`currency:${row.currency}`}>{row.currency} · eligible accounts</option>)}
                </optgroup>
                <optgroup label="Individual accounts">
                  {forecast.accounts.map((row) => <option key={row.accountId} value={`account:${row.accountId}`}>{row.accountName} · {row.currency}</option>)}
                </optgroup>
              </NativeSelect>
            </label>
            <ForecastEvidenceChart title={`${context.title} · 30 / 60 / 90-day cash forecast`}
              currency={context.currency} currencies={currencies} points={context.points} />
          </div>}
          <h3>By currency</h3>
          {forecast.currencies.length === 0 && <p>No currency can be forecast yet.</p>}
          {forecast.currencies.length > 0 && (
            <ul className="plans-currency-list">
              {forecast.currencies.map((currency) => (
                <li key={currency.currency}>
                  <strong>{currency.currency}</strong>
                  <p>
                    {currency.accountIds.length} account{currency.accountIds.length === 1 ? "" : "s"}
                  </p>
                  <ul className="plans-points">
                    {currency.points.map((point) => (
                      <li key={point.days}>
                        {point.days}d ({point.date}): {moneyText(point.balance, currency.currency, currencies)}
                        {" · confirmed "}
                        {moneyText(point.confirmedBalance, currency.currency, currencies)}
                      </li>
                    ))}
                  </ul>
                  <p>
                    Lowest {moneyText(currency.lowest.balance, currency.currency, currencies)} on{" "}
                    {currency.lowest.date}
                    {" · confirmed lowest "}
                    {moneyText(currency.lowestConfirmed.balance, currency.currency, currencies)} on{" "}
                    {currency.lowestConfirmed.date}
                  </p>
                  {currency.excludedAccountIds.length > 0 && (
                    <p>
                      Excluded: {currency.excludedAccountIds.map((id) => accountName(accounts, id)).join(", ")}
                    </p>
                  )}
                  {currency.staleAccountIds.length > 0 && (
                    <p>Stale: {currency.staleAccountIds.map((id) => accountName(accounts, id)).join(", ")}</p>
                  )}
                </li>
              ))}
            </ul>
          )}

          <h3>By account</h3>
          {forecast.accounts.length === 0 && <p>No account can be forecast yet.</p>}
          <ul className="plans-account-list">
            {forecast.accounts.map((account) => (
              <li key={account.accountId}>
                <strong>{account.accountName}</strong>
                <p>
                  Start {moneyText(account.startBalance, account.currency, currencies)} as of {account.balanceDate}
                </p>
                <ul className="plans-points">
                  {account.points.map((point) => (
                    <li key={point.days}>
                      {point.days}d ({point.date}): {moneyText(point.balance, account.currency, currencies)}
                      {" · confirmed "}
                      {moneyText(point.confirmedBalance, account.currency, currencies)}
                    </li>
                  ))}
                </ul>
                <p>
                  Lowest {moneyText(account.lowest.balance, account.currency, currencies)} on {account.lowest.date}
                  {" · confirmed lowest "}
                  {moneyText(account.lowestConfirmed.balance, account.currency, currencies)} on{" "}
                  {account.lowestConfirmed.date}
                </p>
                <p className="plans-badges">
                  {account.balanceAgeDays > 35 && (
                    <small className="plans-badge">Stale balance · {account.balanceAgeDays} days old</small>
                  )}
                  {account.coverageGapMonths !== null && account.coverageGapMonths > 0 && (
                    <small className="plans-badge">
                      {account.coverageGapMonths} coverage gap month{account.coverageGapMonths === 1 ? "" : "s"}
                    </small>
                  )}
                </p>
                {account.shortfall && (
                  <div className="plans-shortfall" role="alert">
                    <p>
                      Shortfall of {moneyText(account.shortfall.amount, account.currency, currencies)} on{" "}
                      {account.shortfall.date}
                    </p>
                    {account.shortfall.fundingAccounts.length > 0 ? (
                      <p>
                        Could be funded from{" "}
                        {account.shortfall.fundingAccounts
                          .map(
                            (funding) =>
                              `${funding.accountName} (${moneyText(funding.lowestBalance, account.currency, currencies)})`,
                          )
                          .join(", ")}
                        . Nothing is moved automatically.
                      </p>
                    ) : (
                      <p>No other account in this currency could cover it on its own.</p>
                    )}
                  </div>
                )}
                {account.occurrences.length > 0 && (
                  <ul className="plans-occurrences">
                    {account.occurrences.map((occurrence, index) => (
                      <li key={index}>
                        {occurrence.date} {occurrence.label}{" "}
                        {moneyText(occurrence.amount, account.currency, currencies)}
                        {occurrence.kind === "card_settlement" && <small> · card settlement</small>}
                        {occurrence.certainty === "estimated" && <small> · estimated</small>}
                        {occurrence.dueByAsOf && <small> · not yet in the ledger</small>}
                      </li>
                    ))}
                  </ul>
                )}
                {account.unappliedOccurrences.length > 0 && (
                  <div>
                    <h4>Not projected</h4>
                    <ul className="plans-occurrences">
                      {account.unappliedOccurrences.map((occurrence, index) => (
                        <li key={index}>
                          {occurrence.date} {occurrence.label}{" "}
                          {moneyText(occurrence.amount, account.currency, currencies)}
                          <Button
                            variant="outline"
                            onClick={() => onSettle(occurrence.commitmentId)}
                            type="button"
                          >Settle</Button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </li>
            ))}
          </ul>

          {forecast.excludedAccounts.length > 0 && (
            <div>
              <h3>Accounts not forecast</h3>
              <ul className="plans-exclusions">
                {forecast.excludedAccounts.map((excluded) => (
                  <li key={excluded.accountId}>
                    {excluded.accountName}: {EXCLUDED_ACCOUNT_TEXT[excluded.reason] ?? excluded.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {forecast.excludedCommitments.length > 0 && (
            <div>
              <h3>Commitments not applied</h3>
              <ul className="plans-exclusions">
                {forecast.excludedCommitments.map((excluded) => (
                  <li key={excluded.commitmentId}>
                    {excluded.label}: {EXCLUDED_COMMITMENT_TEXT[excluded.reason] ?? excluded.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {forecast.unplannedCardDues.length > 0 && (
            <div>
              <h3>Card statements with no planned payment</h3>
              <ul className="plans-unplanned-dues">
                {forecast.unplannedCardDues.map((due) => (
                  <li key={due.cardStatementId}>
                    {due.accountName} · {moneyText(due.statementBalance, due.currency, currencies)} · due{" "}
                    {due.paymentDueDate}
                    <Button
                      variant="outline"
                      onClick={() => onPlanCardPayment(due)}
                      type="button"
                    >Plan payment</Button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <details className="plans-assumptions-disclosure">
        <summary id="plans-assumptions-title">Forecast assumptions and exclusions</summary>
        <ul className="plans-assumptions">
          <li>Balances start from each account&rsquo;s latest ledger balance and its date.</li>
          <li>
            Every active commitment occurs in full on its scheduled date and is unposted when
            dated after that balance date.
          </li>
          <li>
            Nothing else changes: no other income or spending, interest, prices or exchange
            rates.
          </li>
          <li>Balances stay in each account&rsquo;s own currency.</li>
          <li>A forecast posts nothing.</li>
          <li>A resolved occurrence is not projected.</li>
          <li>A part-paid occurrence projects its remainder, not its full amount.</li>
          <li>Each figure states whether it comes from confirmed or estimated inputs.</li>
          <li>A planned card payment reduces the card&rsquo;s balance on the same date.</li>
          <li>
            An unplanned card due, an unsettled occurrence inside the balance date, and an
            excluded account are not projected.
          </li>
          <li>Funding accounts are suggestions; nothing here moves money.</li>
          <li>Currency totals cover liquid asset accounts and credit cards, with no conversion.</li>
        </ul>
      </details>
    </section>
  );
}
