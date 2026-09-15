"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

import { EventCorrectionForm, isFundedEvent } from "@/app/investments/event-correction-form";
import { FundingPanel, type FundableEvent } from "@/app/investments/funding-panel";
import { InvestmentEventForm } from "@/app/investments/investment-event-form";
import { reasonText } from "@/app/investments/review-reasons";
import { WealthRefreshNotice } from "@/app/investments/wealth-refresh-notice";
import type { apiFetch } from "@/lib/api/client";
import { ApiError, errorText } from "@/lib/api/client";
import { dateLabel } from "@/lib/format/date";
import type { AccountRow, HoldingDetail, ManualPriceCreate, ManualPriceRow } from "@/types/api";

type Props = {
  holdingId: string;
  request: typeof apiFetch;
  accounts: Array<AccountRow>;
};

type LoadState = "loading" | "ready" | "error" | "not_found";

const PURCHASE_KINDS = new Set(["purchase", "recurring_purchase"]);

/** Active purchases with a recorded net amount — the ones a funding action can ever apply to. */
function purchaseEvents(detail: HoldingDetail) {
  return detail.events.filter(
    (event) => PURCHASE_KINDS.has(event.eventKind) && event.correctionState === "active" && event.netAmount !== null,
  );
}

function fundableEvent(detail: HoldingDetail, eventId: string): FundableEvent | undefined {
  const event = detail.events.find((item) => item.id === eventId);
  if (!event || event.netAmount === null) return undefined;
  return { id: event.id, eventDate: event.eventDate, eventKind: event.eventKind, currency: event.currency, netAmount: event.netAmount };
}

export function HoldingScreen({ holdingId, request, accounts }: Props) {
  const [state, setState] = useState<LoadState>("loading");
  const [attempt, setAttempt] = useState(0);
  const [detail, setDetail] = useState<HoldingDetail>();
  const [priceDate, setPriceDate] = useState("");
  const [priceValue, setPriceValue] = useState("");
  const [priceBusy, setPriceBusy] = useState(false);
  const [priceProblem, setPriceProblem] = useState("");
  const [priceFeedback, setPriceFeedback] = useState("");
  const [fundingEventId, setFundingEventId] = useState<string>();
  const [correctingEventId, setCorrectingEventId] = useState<string>();
  const [correctionFeedback, setCorrectionFeedback] = useState("");
  // Bumped only after a fresh holding has been stored, so the funding panel re-seeds from the
  // reloaded `cashLinks` rather than remounting against the stale ones.
  const [loadCount, setLoadCount] = useState(0);
  const [fundingNotice, setFundingNotice] = useState("");
  const [recordNotice, setRecordNotice] = useState("");

  useEffect(() => {
    let current = true;
    request<HoldingDetail>(`/portfolio/holdings/${holdingId}`)
      .then((next) => {
        if (!current) return;
        setDetail(next);
        setLoadCount((value) => value + 1);
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

  async function submitPrice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || priceBusy) return;
    setPriceBusy(true);
    setPriceProblem("");
    setPriceFeedback("");
    try {
      const body: ManualPriceCreate = { priceDate, price: priceValue };
      await request<ManualPriceRow>(`/investment-catalogue/securities/${detail.securityId}/manual-prices`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setPriceDate("");
      setPriceValue("");
      setPriceFeedback("Dated price recorded.");
      setAttempt((value) => value + 1);
    } catch (error) {
      setPriceProblem(errorText(error, "This price was not recorded. Retry."));
    } finally {
      setPriceBusy(false);
    }
  }

  const purchases = detail ? purchaseEvents(detail) : [];
  const fundingEvent = detail && fundingEventId ? fundableEvent(detail, fundingEventId) : undefined;
  const fundingEventRow = fundingEventId ? purchases.find((event) => event.id === fundingEventId) : undefined;

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
            {correctionFeedback && <WealthRefreshNotice message={correctionFeedback} />}
            {detail.events.length === 0 && <p className="investment-state">No events recorded.</p>}
            {detail.events.length > 0 && (
              <ul className="investment-holding-grid">
                {detail.events.map((event) => {
                  const funded = isFundedEvent(event, detail.cashLinks);
                  return (
                    <li aria-label={`${event.eventKind} on ${event.eventDate}`} className="investment-holding" key={event.id}>
                      <div>
                        <p className="section-label">{event.eventKind.replaceAll("_", " ")}{event.correctionState === "corrected" ? " · corrected" : ""}</p>
                        <h3>{dateLabel(event.eventDate)}</h3>
                        {event.units !== null && <p className="investment-exact">{event.units} units</p>}
                      </div>
                      <div className="investment-holding-value">
                        {event.netAmount !== null && <strong>{event.currency} {event.netAmount}</strong>}
                        {event.price !== null && <p>Price {event.currency} {event.price}</p>}
                        {event.correctionState === "active" && (
                          funded ? (
                            <p>Unlink its funding first to correct this event.</p>
                          ) : correctingEventId === event.id ? (
                            <button className="text-action" onClick={() => setCorrectingEventId(undefined)} type="button">Cancel correction</button>
                          ) : (
                            <button className="text-action" onClick={() => { setCorrectionFeedback(""); setCorrectingEventId(event.id); }} type="button">Correct</button>
                          )
                        )}
                      </div>
                      {correctingEventId === event.id && (
                        <EventCorrectionForm
                          event={event}
                          holding={detail}
                          onCorrected={() => {
                            setCorrectingEventId(undefined);
                            setCorrectionFeedback("Correction recorded. The events below show the refreshed state.");
                            setAttempt((value) => value + 1);
                          }}
                          request={request}
                        />
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section aria-labelledby="holding-record-event-title">
            <p className="section-label" id="holding-record-event-title">Record an event</p>
            {recordNotice && <WealthRefreshNotice message={recordNotice} />}
            <InvestmentEventForm
              accounts={accounts}
              holding={{ accountId: detail.accountId, securityId: detail.securityId, securityName: detail.securityName, currency: detail.currency }}
              onRecorded={() => {
                setRecordNotice("Event recorded.");
                setAttempt((value) => value + 1);
              }}
              request={request}
            />
          </section>

          <section aria-labelledby="holding-cash-links-title">
            <p className="section-label" id="holding-cash-links-title">Cash links</p>
            {detail.cashLinks.length === 0 && <p className="investment-state">No confirmed cash links.</p>}
            {detail.cashLinks.length > 0 && (
              <dl>
                {detail.cashLinks.map((link) => (
                  <div key={`${link.investmentEventId}:${link.cashTransactionId}`}>
                    <dt>{link.role.replaceAll("_", " ")}</dt>
                    <dd>{link.currency} {link.allocatedAmount}</dd>
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
            <form className="investment-record-price" onSubmit={(event) => void submitPrice(event)}>
              <h3>Record a dated price</h3>
              {priceProblem && <p className="inline-alert" role="alert">{priceProblem}</p>}
              {priceFeedback && <WealthRefreshNotice message={priceFeedback} />}
              <label className="date-field">
                <span>Price date</span>
                <input disabled={priceBusy} onChange={(event) => setPriceDate(event.target.value)} required type="date" value={priceDate} />
              </label>
              <label className="text-field">
                <span>Price ({detail.currency})</span>
                <input disabled={priceBusy} inputMode="decimal" onChange={(event) => setPriceValue(event.target.value)} required value={priceValue} />
              </label>
              <button className="primary-action" disabled={priceBusy || !priceDate || !priceValue} type="submit">
                {priceBusy ? "Recording price…" : "Record price"}
              </button>
            </form>
          </section>

          {purchases.length > 0 && (
            <section aria-labelledby="holding-funding-title">
              <p className="section-label" id="holding-funding-title">Purchase funding</p>
              <ul className="investment-holding-grid">
                {purchases.map((event) => (
                  <li aria-label={`Funding for the ${event.eventKind.replaceAll("_", " ")} on ${event.eventDate}`} className="investment-holding" key={event.id}>
                    <div>
                      <p className="section-label">{event.eventKind.replaceAll("_", " ")}</p>
                      <h3>{dateLabel(event.eventDate)}</h3>
                      <p className="investment-exact">{event.currency} {event.netAmount}</p>
                      {event.fundingComplete
                        ? <p>Fully funded. Unlink existing funding before correcting this purchase.</p>
                        : <p>Not fully funded.</p>}
                    </div>
                    <button
                      className="text-action"
                      onClick={() => { setFundingNotice(""); setFundingEventId(fundingEventId === event.id ? undefined : event.id); }}
                      type="button"
                    >
                      {fundingEventId === event.id ? "Hide funding" : event.fundingComplete ? "Review funding" : "Fund this purchase"}
                    </button>
                  </li>
                ))}
              </ul>
              {fundingNotice && <WealthRefreshNotice message={fundingNotice} />}
              {fundingEvent && (
                // Keyed on the event and the reload counter: switching purchases must not carry
                // over another purchase's draft or seeded links, and a confirm/unlink reload
                // must re-seed from the freshly loaded `cashLinks` rather than the stale ones
                // this instance mounted with.
                <FundingPanel
                  allowAdditionalFunding={!(fundingEventRow?.fundingComplete ?? false)}
                  events={[fundingEvent]}
                  existingLinks={detail.cashLinks}
                  key={`${fundingEvent.id}-${loadCount}`}
                  onChanged={() => {
                    setFundingNotice("Funding updated. The purchase below shows the refreshed status.");
                    setAttempt((value) => value + 1);
                  }}
                  request={request}
                />
              )}
            </section>
          )}
        </>
      )}
    </section>
  );
}
