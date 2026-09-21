"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ConsolePageHeader } from "@/app/ui/console-page-header";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { MetricStrip } from "@/app/ui/metric-strip";
import { StatusLabel } from "@/app/ui/status-label";
import { Button } from "@/components/ui/button";
import type { apiFetch } from "@/lib/api/client";
import { formatPercent, moneyText } from "@/lib/money/format";
import type {
  CorridorOut,
  CorridorYearOut,
  CurrencyRow,
  ProviderRowOut,
  RemittanceAnalysisResponse,
} from "@/types/api";

// Phase 4.4 Task 2. Every amount, percent and rate here is a string the service computed —
// including each corridor-year's total row, because the browser formats money and adds none of
// it (`scripts/check-identifiers.py` Rule 2). A null figure means the evidence does not support
// one yet and prints as a word, never as zero.

type Props = {
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type State = "loading" | "ready" | "error";

const NOT_COSTED = "Not costed yet";

function corridorLabel(corridor: CorridorOut) {
  return `${corridor.sourceCurrency} → ${corridor.destinationCurrency}`;
}

function corridorKey(corridor: CorridorOut) {
  return `${corridor.sourceCurrency}-${corridor.destinationCurrency}`;
}

export function RemittancesScreen({ currencies, request }: Props) {
  const [analysis, setAnalysis] = useState<RemittanceAnalysisResponse>();
  const [state, setState] = useState<State>("loading");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    request<RemittanceAnalysisResponse>("/transfers/analysis")
      .then((loaded) => {
        if (!current) return;
        setAnalysis(loaded);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  const corridors = analysis?.corridors ?? [];

  function money(value: string | null, code: string) {
    return value === null ? NOT_COSTED : moneyText(value, code, currencies);
  }

  /** One provider row, or the unattributed bucket, as the table renders it. */
  function providerCells(row: ProviderRowOut, corridor: CorridorOut) {
    return (
      <>
        <td>{money(row.sentSource, corridor.sourceCurrency)}</td>
        <td>{money(row.feesSource, corridor.sourceCurrency)}</td>
        <td>{money(row.spreadSource, corridor.sourceCurrency)}</td>
        <td>{money(row.totalCostSource, corridor.sourceCurrency)}</td>
        <td>{row.effectiveCostPct === null ? NOT_COSTED : formatPercent(row.effectiveCostPct)}</td>
        <td>{row.avgRate ?? "No achieved rate"}</td>
      </>
    );
  }

  /** The count, plus why a cost beside it may be less than the whole of it. Both notes are
   * string comparisons and counts — the browser never adds or divides an amount. */
  function transferCount(row: ProviderRowOut) {
    return (
      <>
        {row.transferCount}
        {row.totalCostSource === null && <small> · none costed yet</small>}
        {row.totalCostSource !== null && row.costedSentSource !== row.sentSource && (
          <small> · cost covers part of it</small>
        )}
        {row.approximateCount > 0 && (
          <small> · {row.approximateCount} on a carried-forward rate</small>
        )}
      </>
    );
  }

  function yearTable(corridor: CorridorOut, year: CorridorYearOut) {
    // The region takes a name of its own: the metric strip above already owns "<corridor> in
    // <year>", and two regions sharing one name leave a screen reader unable to tell them apart.
    return (
      <DataTableRegion label={`${corridorLabel(corridor)} providers in ${year.year}`}>
        <table className="console-table remittance-providers-table">
          <caption className="visually-hidden">
            {corridorLabel(corridor)} by provider in {year.year}
          </caption>
          <thead>
            <tr>
              <th scope="col">Provider</th>
              <th scope="col">Transfers</th>
              <th scope="col">Sent</th>
              <th scope="col">Fees</th>
              <th scope="col">Spread</th>
              <th scope="col">Total cost</th>
              <th scope="col">Effective</th>
              <th scope="col">Average rate</th>
            </tr>
          </thead>
          <tbody>
            {year.providers.map((row) => (
              <tr key={row.provider ?? "unattributed"}>
                <th scope="row">
                  {row.provider}
                  {row.provider === year.cheapestProvider && (
                    <>
                      {" "}
                      <StatusLabel tone="success">Cheapest</StatusLabel>
                    </>
                  )}
                </th>
                <td>{transferCount(row)}</td>
                {providerCells(row, corridor)}
              </tr>
            ))}
            {year.unattributed && (
              <tr className="remittance-unattributed-row" data-testid={`unattributed-${year.year}`}>
                <th scope="row">
                  No provider recorded
                  <small>
                    {" "}
                    Outside the comparison ·{" "}
                    <Link href="/transactions">label {year.unattributed.transferCount} on Transactions</Link>
                  </small>
                </th>
                <td>{transferCount(year.unattributed)}</td>
                {providerCells(year.unattributed, corridor)}
              </tr>
            )}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">Total</th>
              <td>{year.providers.reduce((count, row) => count + row.transferCount, 0)
                + (year.unattributed?.transferCount ?? 0)}</td>
              <td>{money(year.totalSentSource, corridor.sourceCurrency)}</td>
              <td>{money(year.totalFeesSource, corridor.sourceCurrency)}</td>
              <td>{money(year.totalSpreadSource, corridor.sourceCurrency)}</td>
              <td>{money(year.totalCostSource, corridor.sourceCurrency)}</td>
              <td>
                {year.totalEffectiveCostPct === null
                  ? NOT_COSTED
                  : formatPercent(year.totalEffectiveCostPct)}
              </td>
              <td aria-hidden="true" />
            </tr>
          </tfoot>
        </table>
      </DataTableRegion>
    );
  }

  return (
    <section className="workspace-content remittances-screen">
      <ConsolePageHeader
        title="Remittances"
        description="What each channel has cost to send money abroad, from the rate and fee recorded on every confirmed remittance."
      />

      {state === "loading" && <p role="status">Loading remittance analysis…</p>}
      {state === "error" && (
        <div className="inline-alert" role="alert">
          <p>Remittance analysis could not be loaded.</p>
          <Button
            onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}
            type="button"
            variant="outline"
          >Try again</Button>
        </div>
      )}

      {state === "ready" && corridors.length === 0 && (
        <p className="lede">
          No confirmed remittance yet. Confirm a cross-border transfer on{" "}
          <Link href="/transactions">Transactions</Link>, then record who carried it.
        </p>
      )}

      {state === "ready" && corridors.map((corridor) => {
        const [latest] = corridor.years;
        return (
          <section
            aria-label={corridorLabel(corridor)}
            className="console-section"
            key={corridorKey(corridor)}
          >
            <div className="console-section-head">
              <h2>{corridorLabel(corridor)}</h2>
            </div>

            {latest && (
              <MetricStrip
                label={`${corridorLabel(corridor)} in ${latest.year}`}
                items={[
                  {
                    label: `Sent in ${latest.year}`,
                    value: money(latest.totalSentSource, corridor.sourceCurrency),
                    detail: `${corridor.sourceCurrency} out, ${corridor.destinationCurrency} in`,
                  },
                  {
                    label: "Cost",
                    value: money(latest.totalCostSource, corridor.sourceCurrency),
                    detail: latest.totalEffectiveCostPct === null
                      ? "No transfer this year has a recorded cost"
                      : `${formatPercent(latest.totalEffectiveCostPct)} of the money it was charged on`,
                  },
                  {
                    label: "Cheapest channel",
                    value: latest.cheapestProvider ?? "No comparison",
                    detail: latest.cheapestProvider === null
                      ? "A comparison needs two providers with a recorded cost"
                      : latest.potentialSavingsSource === null
                        ? "Recorded as cheapest"
                        : `Routing everything through it would have saved ${money(latest.potentialSavingsSource, corridor.sourceCurrency)}`,
                  },
                ]}
              />
            )}

            {corridor.years.map((year) => (
              <div className="remittance-year" key={`${corridorKey(corridor)}-${year.year}`}>
                <div className="console-section-head">
                  <h3>{year.year}</h3>
                  <span className="remittance-year-summary">
                    {year.cheapestProvider === null ? (
                      <StatusLabel tone="neutral">
                        A cheapest channel needs two providers with a recorded cost
                      </StatusLabel>
                    ) : (
                      <StatusLabel tone="success">
                        Cheapest {year.cheapestProvider}
                        {year.potentialSavingsSource !== null
                          && ` · ${money(year.potentialSavingsSource, corridor.sourceCurrency)} not saved`}
                      </StatusLabel>
                    )}
                  </span>
                </div>
                {yearTable(corridor, year)}
              </div>
            ))}
          </section>
        );
      })}
    </section>
  );
}
