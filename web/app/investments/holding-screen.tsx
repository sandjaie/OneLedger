"use client";

import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import Link from "next/link";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { EventCorrectionForm, isFundedEvent } from "@/app/investments/event-correction-form";
import { FundingPanel, type FundableEvent } from "@/app/investments/funding-panel";
import { InvestmentEventForm } from "@/app/investments/investment-event-form";
import { LotAllocationPanel, LotAllocationResult } from "@/app/investments/lot-allocation-panel";
import { reasonText } from "@/app/investments/review-reasons";
import { WealthRefreshNotice } from "@/app/investments/wealth-refresh-notice";
import { ConsolePageHeader } from "@/app/ui/console-page-header";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { MetricStrip } from "@/app/ui/metric-strip";
import { StatusLabel } from "@/app/ui/status-label";
import type { apiFetch } from "@/lib/api/client";
import { ApiError, errorText } from "@/lib/api/client";
import { dateLabel } from "@/lib/format/date";
import type {
  AccountRow,
  HoldingDetail,
  HoldingEventRow,
  HoldingLotRow,
  LotAllocationResponse,
  ManualPriceCreate,
  ManualPriceRow,
} from "@/types/api";

type Props = {
  holdingId: string;
  request: typeof apiFetch;
  accounts: Array<AccountRow>;
};

type LoadState = "loading" | "ready" | "error" | "not_found";
type LotsResult = { key: string; state: "ready" | "error"; rows: Array<HoldingLotRow> };

const PURCHASE_KINDS = new Set(["purchase", "recurring_purchase"]);

function InvestmentContextPanel({
  label,
  title,
  closeLabel,
  onClose,
  children,
}: {
  label: string;
  title: string;
  closeLabel?: string;
  onClose?: () => void;
  children: ReactNode;
}) {
  return (
    <section aria-label={label} className="investment-context-panel">
      <div className="investment-context-panel-head">
        <div>
          <p>Contextual workflow</p>
          <h3>{title}</h3>
        </div>
        {onClose && (
          <Button aria-label={closeLabel} onClick={onClose} type="button" variant="outline">
            Close
          </Button>
        )}
      </div>
      {children}
    </section>
  );
}

/** Active purchases with a recorded net amount — the ones a funding action can ever apply to. */
function purchaseEvents(events: Array<HoldingEventRow>) {
  return events.filter(
    (event) => PURCHASE_KINDS.has(event.eventKind) && event.correctionState === "active" && event.netAmount !== null,
  );
}

function fundableEvent(events: Array<HoldingEventRow>, eventId: string): FundableEvent | undefined {
  const event = events.find((item) => item.id === eventId);
  if (!event || event.netAmount === null) return undefined;
  return { id: event.id, eventDate: event.eventDate, eventKind: event.eventKind, currency: event.currency, netAmount: event.netAmount };
}

export function HoldingScreen({ holdingId, request, accounts }: Props) {
  const [state, setState] = useState<LoadState>("loading");
  const [attempt, setAttempt] = useState(0);
  const [detail, setDetail] = useState<HoldingDetail>();
  const [lotsResult, setLotsResult] = useState<LotsResult>();
  const [priceDate, setPriceDate] = useState("");
  const [priceValue, setPriceValue] = useState("");
  const [priceBusy, setPriceBusy] = useState(false);
  const [priceProblem, setPriceProblem] = useState("");
  const [priceFeedback, setPriceFeedback] = useState("");
  const [fundingEventId, setFundingEventId] = useState<string>();
  const [correctingEventId, setCorrectingEventId] = useState<string>();
  const [correctionFeedback, setCorrectionFeedback] = useState("");
  const [allocatingEventId, setAllocatingEventId] = useState<string>();
  const [allocationResult, setAllocationResult] = useState<LotAllocationResponse>();
  // Bumped only after a fresh holding has been stored, so the funding panel re-seeds from the
  // reloaded `cashLinks` rather than remounting against the stale ones.
  const [loadCount, setLoadCount] = useState(0);
  const [fundingNotice, setFundingNotice] = useState("");
  const [recordNotice, setRecordNotice] = useState("");
  // Events beyond `detail.events`' own first page, appended by "Load more events" — reset
  // whenever `detail` is freshly loaded from offset 0 (every write reloads this way, the
  // simplest way to keep funding/correction/lot-allocation state consistent with the server).
  const [moreEvents, setMoreEvents] = useState<Array<HoldingEventRow>>([]);
  const [moreEventsBusy, setMoreEventsBusy] = useState(false);
  const [moreEventsProblem, setMoreEventsProblem] = useState("");
  const contextOrigin = useRef<HTMLButtonElement>(null);
  const eventsHeading = useRef<HTMLHeadingElement>(null);
  const restoreContextAfterLoad = useRef(false);

  useEffect(() => {
    let current = true;
    request<HoldingDetail>(`/portfolio/holdings/${holdingId}`)
      .then((next) => {
        if (!current) return;
        setDetail(next);
        setMoreEvents([]);
        setMoreEventsProblem("");
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

  const lotsKey = `${holdingId}:${loadCount}`;

  useEffect(() => {
    if (state !== "ready" || !detail) return undefined;
    let current = true;
    request<Array<HoldingLotRow>>(`/portfolio/holdings/${holdingId}/lots`)
      .then((rows) => {
        if (!current) return;
        setLotsResult({ key: lotsKey, state: "ready", rows });
      })
      .catch(() => {
        if (current) setLotsResult({ key: lotsKey, state: "error", rows: [] });
      });
    return () => {
      current = false;
    };
  }, [holdingId, request, state, detail, lotsKey]);

  useEffect(() => {
    if (!restoreContextAfterLoad.current) return;
    restoreContextAfterLoad.current = false;
    window.setTimeout(() => {
      if (contextOrigin.current?.isConnected) contextOrigin.current.focus();
      else eventsHeading.current?.focus();
    }, 0);
  }, [loadCount]);

  const events = detail ? [...detail.events, ...moreEvents] : [];

  async function loadMoreEvents() {
    if (!detail || moreEventsBusy) return;
    setMoreEventsBusy(true);
    setMoreEventsProblem("");
    try {
      const next = await request<HoldingDetail>(
        `/portfolio/holdings/${holdingId}?eventsOffset=${events.length}`,
      );
      setMoreEvents((current) => [...current, ...next.events]);
    } catch (error) {
      setMoreEventsProblem(errorText(error, "More events could not be loaded. Retry."));
    } finally {
      setMoreEventsBusy(false);
    }
  }

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

  const purchases = purchaseEvents(events);
  const fundingEvent = fundingEventId ? fundableEvent(events, fundingEventId) : undefined;
  const fundingEventRow = fundingEventId ? purchases.find((event) => event.id === fundingEventId) : undefined;
  const correctingEvent = correctingEventId ? events.find((event) => event.id === correctingEventId) : undefined;
  const allocatingEvent = allocatingEventId ? events.find((event) => event.id === allocatingEventId) : undefined;
  const lotsCurrent = lotsResult?.key === lotsKey;
  const lotsState: LoadState = lotsCurrent ? lotsResult.state : "loading";
  const lots = lotsCurrent ? lotsResult.rows : [];

  function rememberContextOrigin(button: HTMLButtonElement) {
    contextOrigin.current = button;
  }

  function closeContext(close: () => void) {
    const origin = contextOrigin.current;
    close();
    window.setTimeout(() => {
      if (origin?.isConnected) origin.focus();
    }, 0);
  }

  function closeContextAfterReload(close: () => void) {
    restoreContextAfterLoad.current = true;
    closeContext(close);
  }

  return (
    <section aria-label="Holding detail" className="workspace-content holding-screen">
      <ConsolePageHeader
        actions={<Link className={buttonVariants({ variant: "outline" })} href="/investments">Back to portfolio</Link>}
        description="Exact dated events, prices, lots and cash evidence for one recorded holding."
        title={state === "ready" && detail ? detail.securityName : "Holding"}
      />

      {state === "loading" && <p className="investment-state" role="status">Loading holding…</p>}
      {state === "not_found" && <p className="investment-state" role="alert">This holding could not be found.</p>}
      {state === "error" && (
        <div className="investment-state" role="alert">
          <p>The holding could not be loaded.</p>
          <Button variant="outline" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }} type="button">Try again</Button>
        </div>
      )}

      {state === "ready" && detail && (
        <>
          <MetricStrip
            items={[
              { label: "Asset class", value: detail.assetClass.replaceAll("_", " ") },
              { label: "Units", value: detail.units },
              { label: "Opened", value: dateLabel(detail.openedOn) },
              { label: "Lifecycle", value: detail.closedOn ? `Closed ${dateLabel(detail.closedOn)}` : "Open" },
            ]}
            label="Holding facts"
          />

          {detail.openReviewReasons.length > 0 && (
            <section aria-labelledby="holding-reviews-title" className="holding-review-band">
              <div className="console-section-head">
                <h2 id="holding-reviews-title">Open reviews</h2>
                <span>{detail.openReviewReasons.length} requiring evidence</span>
              </div>
              <ul>
                {detail.openReviewReasons.map((reason) => <li key={reason}>{reasonText(reason)}</li>)}
              </ul>
            </section>
          )}

          <section aria-labelledby="holding-events-title" className="holding-evidence-section">
            <div className="console-section-head">
              <h2 id="holding-events-title" ref={eventsHeading} tabIndex={-1}>Events ({events.length} of {detail.eventsTotal})</h2>
              <span>Recorded lifecycle evidence</span>
            </div>
            {correctionFeedback && <WealthRefreshNotice message={correctionFeedback} />}
            {allocationResult && <LotAllocationResult result={allocationResult} />}
            {events.length === 0 && <p className="investment-state">No events recorded.</p>}
            {events.length > 0 && (
              <DataTableRegion label="Holding events">
                <table className="console-table holding-events-table">
                  <thead><tr><th scope="col">Event</th><th scope="col">Units</th><th scope="col">Price</th><th scope="col">Net cash</th><th scope="col">State</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                  <tbody>
                    {events.map((event) => {
                      const funded = isFundedEvent(event, detail.cashLinks);
                      return (
                        <tr aria-label={`${event.eventKind} on ${event.eventDate}`} key={event.id}>
                          <th scope="row"><span className="console-entity-title">{event.eventKind.replaceAll("_", " ")}</span><span className="console-entity-sub">{dateLabel(event.eventDate)}</span></th>
                          <td className="console-number">{event.units ?? "—"}</td>
                          <td className="console-number">{event.price === null ? "—" : `${event.currency} ${event.price}`}</td>
                          <td className="console-number">{event.netAmount === null ? "—" : `${event.currency} ${event.netAmount}`}</td>
                          <td>
                            {event.correctionState === "corrected"
                              ? <StatusLabel tone="neutral">Corrected</StatusLabel>
                              : funded
                                ? <><StatusLabel tone="success">Cash linked</StatusLabel><span className="holding-state-note">Unlink its funding first to correct this event.</span></>
                                : <StatusLabel tone="neutral">Active</StatusLabel>}
                          </td>
                          <td className="console-row-actions">
                            <div className="holding-table-actions">
                              {event.correctionState === "active" && !funded && (
                                <Button
                                  aria-pressed={correctingEventId === event.id}
                                  onClick={(click) => {
                                    rememberContextOrigin(click.currentTarget);
                                    setCorrectionFeedback("");
                                    setAllocatingEventId(undefined);
                                    setCorrectingEventId(event.id);
                                  }}
                                  size="sm"
                                  type="button"
                                  variant="outline"
                                >Correct</Button>
                              )}
                              {event.lotAllocationOpen && (
                                <Button
                                  aria-pressed={allocatingEventId === event.id}
                                  onClick={(click) => {
                                    rememberContextOrigin(click.currentTarget);
                                    setAllocationResult(undefined);
                                    setCorrectingEventId(undefined);
                                    setAllocatingEventId(event.id);
                                  }}
                                  size="sm"
                                  type="button"
                                  variant="outline"
                                >Allocate lots</Button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </DataTableRegion>
            )}
            {moreEventsProblem && <p className="inline-alert" role="alert">{moreEventsProblem}</p>}
            {events.length < detail.eventsTotal && (
              <Button variant="outline" disabled={moreEventsBusy} onClick={() => void loadMoreEvents()} type="button">
                {moreEventsBusy ? "Loading more events…" : "Load more events"}
              </Button>
            )}
            {correctingEvent && (
              <InvestmentContextPanel
                closeLabel="Close correction panel"
                label={`Correct ${correctingEvent.eventKind.replaceAll("_", " ")} event`}
                onClose={() => closeContext(() => setCorrectingEventId(undefined))}
                title={`Correct ${dateLabel(correctingEvent.eventDate)} event`}
              >
                <EventCorrectionForm
                  event={correctingEvent}
                  holding={detail}
                  onCorrected={() => {
                    closeContextAfterReload(() => setCorrectingEventId(undefined));
                    setCorrectionFeedback("Correction recorded. The events below show the refreshed state.");
                    setAttempt((value) => value + 1);
                  }}
                  request={request}
                />
              </InvestmentContextPanel>
            )}
            {allocatingEvent && (
              <InvestmentContextPanel
                closeLabel="Close lot allocation panel"
                label="Allocate disposal lots"
                onClose={() => closeContext(() => setAllocatingEventId(undefined))}
                title={`Allocate the ${dateLabel(allocatingEvent.eventDate)} disposal`}
              >
                <LotAllocationPanel
                  disposalDate={allocatingEvent.eventDate}
                  disposalEventId={allocatingEvent.id}
                  holdingId={detail.holdingId}
                  onAllocated={(result) => {
                    setAllocationResult(result);
                    closeContextAfterReload(() => setAllocatingEventId(undefined));
                    setCorrectionFeedback("Lot allocation confirmed. The events below show the refreshed state.");
                    setAttempt((value) => value + 1);
                  }}
                  request={request}
                />
              </InvestmentContextPanel>
            )}
          </section>

          <InvestmentContextPanel label="Record holding event" title="Record an event">
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
          </InvestmentContextPanel>

          <section aria-labelledby="holding-prices-title" className="holding-evidence-section">
            <div className="console-section-head"><h2 id="holding-prices-title">Recent prices</h2><span>Dated valuation evidence</span></div>
            {detail.prices.length === 0 && <p className="investment-state">No dated prices recorded.</p>}
            {detail.prices.length > 0 && (
              <DataTableRegion label="Recent holding prices">
                <table className="console-table holding-prices-table"><thead><tr><th scope="col">Price date</th><th scope="col">Exact price</th></tr></thead><tbody>
                  {detail.prices.map((price) => <tr key={price.priceDate}><th scope="row">{dateLabel(price.priceDate)}</th><td className="console-number">{detail.currency} {price.price}</td></tr>)}
                </tbody></table>
              </DataTableRegion>
            )}
            <InvestmentContextPanel label="Record dated price" title="Record a dated price">
              <form className="investment-record-price" onSubmit={(event) => void submitPrice(event)}>
                {priceProblem && <p className="inline-alert" role="alert">{priceProblem}</p>}
                {priceFeedback && <WealthRefreshNotice message={priceFeedback} />}
                <label className="date-field"><span>Price date</span><Input disabled={priceBusy} onChange={(event) => setPriceDate(event.target.value)} required type="date" value={priceDate} /></label>
                <label className="text-field"><span>Price ({detail.currency})</span><Input disabled={priceBusy} inputMode="decimal" onChange={(event) => setPriceValue(event.target.value)} required value={priceValue} /></label>
                <Button variant="default" disabled={priceBusy || !priceDate || !priceValue} type="submit">{priceBusy ? "Recording price…" : "Record price"}</Button>
              </form>
            </InvestmentContextPanel>
          </section>

          <section aria-labelledby="holding-lots-title" className="holding-evidence-section">
            <div className="console-section-head"><h2 id="holding-lots-title">Lots</h2><span>Acquisition and remaining basis</span></div>
            {lotsState === "loading" && <p className="investment-state" role="status">Loading lots…</p>}
            {lotsState === "error" && <p className="inline-alert" role="alert">Lots could not be loaded.</p>}
            {lotsState === "ready" && lots.length === 0 && <p className="investment-state">No acquisition lots recorded.</p>}
            {lotsState === "ready" && lots.length > 0 && (
              <DataTableRegion label="Holding lots">
                <table className="console-table holding-lots-table"><thead><tr><th scope="col">Acquired</th><th scope="col">Acquired units</th><th scope="col">Remaining units</th><th scope="col">Cost basis</th><th scope="col">Allocation</th></tr></thead><tbody>
                  {lots.map((lot) => <tr key={lot.lotId}><th scope="row">{dateLabel(lot.acquiredOn)}</th><td className="console-number">{lot.acquiredUnits}</td><td className="console-number">{lot.remainingUnits}</td><td className="console-number">{detail.currency} {lot.costBasisNative}</td><td><StatusLabel tone={lot.eligible ? "success" : "neutral"}>{lot.eligible ? "Eligible" : "Not eligible"}</StatusLabel></td></tr>)}
                </tbody></table>
              </DataTableRegion>
            )}
          </section>

          <section aria-labelledby="holding-cash-links-title" className="holding-evidence-section">
            <div className="console-section-head"><h2 id="holding-cash-links-title">Cash links</h2><span>Confirmed ledger evidence</span></div>
            {detail.cashLinks.length === 0 && <p className="investment-state">No confirmed cash links.</p>}
            {detail.cashLinks.length > 0 && (
              <DataTableRegion label="Holding cash links">
                <table className="console-table holding-cash-table"><thead><tr><th scope="col">Role</th><th scope="col">Allocated amount</th></tr></thead><tbody>
                  {detail.cashLinks.map((link) => <tr key={`${link.investmentEventId}:${link.cashTransactionId}`}><th scope="row">{link.role.replaceAll("_", " ")}</th><td className="console-number">{link.currency} {link.allocatedAmount}</td></tr>)}
                </tbody></table>
              </DataTableRegion>
            )}
          </section>

          {purchases.length > 0 && (
            <section aria-labelledby="holding-funding-title" className="holding-evidence-section">
              <div className="console-section-head"><h2 id="holding-funding-title">Purchase funding</h2><span>Match purchases to exact cash debits</span></div>
              <DataTableRegion label="Purchase funding status">
                <table className="console-table holding-funding-table"><thead><tr><th scope="col">Purchase</th><th scope="col">Net cash</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead><tbody>
                  {purchases.map((event) => (
                    <tr aria-label={`Funding for the ${event.eventKind.replaceAll("_", " ")} on ${event.eventDate}`} key={event.id}>
                      <th scope="row"><span className="console-entity-title">{event.eventKind.replaceAll("_", " ")}</span><span className="console-entity-sub">{dateLabel(event.eventDate)}</span></th>
                      <td className="console-number">{event.currency} {event.netAmount}</td>
                      <td>{event.fundingComplete ? <><StatusLabel tone="success">Funded</StatusLabel><span className="holding-state-note">Fully funded. Unlink existing funding before correcting this purchase.</span></> : <><StatusLabel tone="attention">Open</StatusLabel><span className="holding-state-note">Not fully funded.</span></>}</td>
                      <td className="console-row-actions"><Button
                        onClick={(click) => {
                          rememberContextOrigin(click.currentTarget);
                          setFundingNotice("");
                          if (fundingEventId === event.id) closeContext(() => setFundingEventId(undefined));
                          else setFundingEventId(event.id);
                        }}
                        size="sm"
                        type="button"
                        variant="outline"
                      >{fundingEventId === event.id ? "Hide funding" : event.fundingComplete ? "Review funding" : "Fund this purchase"}</Button></td>
                    </tr>
                  ))}
                </tbody></table>
              </DataTableRegion>
              {fundingNotice && <WealthRefreshNotice message={fundingNotice} />}
              {fundingEvent && (
                <InvestmentContextPanel closeLabel="Close funding panel" label="Purchase funding workflow" onClose={() => closeContext(() => setFundingEventId(undefined))} title={`Fund the ${dateLabel(fundingEvent.eventDate)} purchase`}>
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
                </InvestmentContextPanel>
              )}
            </section>
          )}
        </>
      )}
    </section>
  );
}
