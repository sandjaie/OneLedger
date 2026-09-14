"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import type { HoldingDetail, ReviewReason } from "@/types/api";

type Props = {
  holdingId: string;
  request: typeof apiFetch;
};

type LoadState = "loading" | "ready" | "error" | "not_found";

const REASON_TEXT: Record<ReviewReason, string> = {
  unsupported_format: "This statement's layout is not supported yet.",
  unknown_security: "The security referenced here is not in your catalogue yet.",
  ambiguous_account: "More than one of your accounts could match this statement.",
  ambiguous_identifier: "This statement's account identifier matches more than one account.",
  unrecognized_event: "This row's transaction type is not recognized.",
  duplicate_conflict: "This event conflicts with one already recorded.",
  unit_mismatch: "The reported units do not reconcile with the statement.",
  cost_mismatch: "The reported cost does not reconcile with the statement.",
  missing_price: "No price is recorded for this date.",
  unmatched_cash: "This cash movement has no matching event.",
  incomplete_lot_allocation: "This disposal is not fully allocated to existing lots.",
  stale_valuation: "The recorded valuation is older than expected.",
};

function dateLabel(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function reasonText(reason: ReviewReason) {
  return REASON_TEXT[reason] ?? reason.replaceAll("_", " ");
}

export function HoldingScreen({ holdingId, request }: Props) {
  const [state, setState] = useState<LoadState>("loading");
  const [attempt, setAttempt] = useState(0);
  const [detail, setDetail] = useState<HoldingDetail>();

  useEffect(() => {
    let current = true;
    request<HoldingDetail>(`/portfolio/holdings/${holdingId}`)
      .then((next) => {
        if (!current) return;
        setDetail(next);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        setState(error instanceof ApiError && error.status === 404 ? "not_found" : "error");
      });
    return () => {
      current = false;
    };
  }, [holdingId, request, attempt]);

  return (
    <section className="workspace-content holding-screen" aria-labelledby="holding-title">
      <header className="investment-entry-header">
        <div>
          <p className="eyebrow">Holding detail</p>
          <h1 className="workspace-title" id="holding-title">
            {state === "ready" && detail ? detail.securityName : "Holding"}
          </h1>
        </div>
        <Link className="text-action" href="/investments">Back to portfolio</Link>
      </header>

      {state === "loading" && <p className="investment-state" role="status">Loading holding…</p>}
      {state === "not_found" && <p className="investment-state" role="alert">This holding could not be found.</p>}
      {state === "error" && (
        <div className="investment-state" role="alert">
          <p>The holding could not be loaded.</p>
          <button className="text-action" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }} type="button">Try again</button>
        </div>
      )}

      {state === "ready" && detail && (
        <>
          <dl className="portfolio-metrics" aria-label="Holding facts">
            <div><dt>Asset class</dt><dd>{detail.assetClass}</dd></div>
            <div><dt>Units</dt><dd>{detail.units}</dd></div>
            <div><dt>Opened</dt><dd>{dateLabel(detail.openedOn)}</dd></div>
            <div><dt>Closed</dt><dd>{detail.closedOn ? dateLabel(detail.closedOn) : "Open"}</dd></div>
          </dl>

          {detail.openReviewReasons.length > 0 && (
            <section aria-labelledby="holding-reviews-title" className="investment-state">
              <p className="section-label" id="holding-reviews-title">Open reviews</p>
              <ul>
                {detail.openReviewReasons.map((reason) => <li key={reason}>{reasonText(reason)}</li>)}
              </ul>
            </section>
          )}

          <section aria-labelledby="holding-events-title">
            <p className="section-label" id="holding-events-title">Events ({detail.eventsTotal})</p>
            {detail.events.length === 0 && <p className="investment-state">No events recorded.</p>}
            {detail.events.length > 0 && (
              <ul className="investment-holding-grid">
                {detail.events.map((event) => (
                  <li aria-label={`${event.eventKind} on ${event.eventDate}`} className="investment-holding" key={event.id}>
                    <div>
                      <p className="section-label">{event.eventKind.replaceAll("_", " ")}{event.correctionState === "corrected" ? " · corrected" : ""}</p>
                      <h3>{dateLabel(event.eventDate)}</h3>
                      {event.units !== null && <p className="investment-exact">{event.units} units</p>}
                    </div>
                    <div className="investment-holding-value">
                      {event.netAmount !== null && <strong>{event.currency} {event.netAmount}</strong>}
                      {event.price !== null && <p>Price {event.currency} {event.price}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="holding-cash-links-title">
            <p className="section-label" id="holding-cash-links-title">Cash links</p>
            {detail.cashLinks.length === 0 && <p className="investment-state">No confirmed cash links.</p>}
            {detail.cashLinks.length > 0 && (
              <dl>
                {detail.cashLinks.map((link) => (
                  <div key={`${link.investmentEventId}:${link.cashTransactionId}`}>
                    <dt>{link.role.replaceAll("_", " ")}</dt>
                    <dd>{link.allocatedAmount}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>

          <section aria-labelledby="holding-prices-title">
            <p className="section-label" id="holding-prices-title">Recent prices</p>
            {detail.prices.length === 0 && <p className="investment-state">No dated prices recorded.</p>}
            {detail.prices.length > 0 && (
              <dl>
                {detail.prices.map((price) => (
                  <div key={price.priceDate}>
                    <dt>{dateLabel(price.priceDate)}</dt>
                    <dd>{detail.currency} {price.price}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </>
      )}
    </section>
  );
}
