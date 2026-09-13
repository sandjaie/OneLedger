"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import type { apiFetch } from "@/lib/api/client";
import type {
  AccountRow,
  AcquisitionCreate,
  AcquisitionPosition,
  InvestmentEventRow,
  ManualPriceCreate,
  ManualPriceRow,
  PortfolioPositions,
  SecurityCandidate,
  SecurityResolution,
  SecurityResolveRequest,
} from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  request: typeof apiFetch;
  createIdempotencyKey?: () => string;
};

type LookupMethod = "isin" | "ticker" | "name";
type LoadState = "loading" | "ready" | "error";
type MutationProgress = "none" | "purchase" | "complete";

type Draft = {
  eventDate: string;
  eventKind: AcquisitionCreate["eventKind"];
  units: string;
  price: string;
  grossAmount: string;
  charges: string;
  withheldTax: string;
  netAmount: string;
  addValuation: boolean;
  valuationDate: string;
  valuationPrice: string;
};

const EMPTY_DRAFT: Draft = {
  eventDate: "",
  eventKind: "purchase",
  units: "",
  price: "",
  grossAmount: "",
  charges: "",
  withheldTax: "",
  netAmount: "",
  addValuation: false,
  valuationDate: "",
  valuationPrice: "",
};

const INVESTMENT_ACCOUNT_TYPES = new Set(["brokerage", "mutual_fund_folio"]);

function accountLabel(account: AccountRow) {
  const masked = account.identifiers.find(({ isActive }) => isActive)?.masked;
  return masked ? `${account.name} · ${masked}` : account.name;
}

function dateLabel(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function coverageMessages(position: AcquisitionPosition) {
  const messages: Array<string> = [];
  if (position.priceStatus === "future_price_only") {
    messages.push("Only future-dated price evidence is recorded.");
  }
  if (position.priceStatus === "missing") messages.push("No dated price is recorded.");
  if (position.priceStatus === "stale") messages.push("The latest recorded price is stale.");
  if (position.valueStatus === "outside_supported_range") {
    messages.push("The exact value is outside the supported reporting range.");
  }
  return messages.length > 0 ? messages : ["Dated value available."];
}

function InstrumentCandidate({ candidate }: { candidate: SecurityCandidate }) {
  const publicIdentity = candidate.isin
    ?? (candidate.ticker && candidate.exchange ? `${candidate.ticker} · ${candidate.exchange}` : null);
  return (
    <span className="investment-candidate-copy">
      <strong>{candidate.name}</strong>
      <small>{candidate.securityType.replaceAll("_", " ")} · {candidate.currency}</small>
      {publicIdentity && <small>{publicIdentity}</small>}
    </span>
  );
}

export function InvestmentEntryScreen({
  accounts,
  request,
  createIdempotencyKey = () => crypto.randomUUID(),
}: Props) {
  const investmentAccounts = useMemo(
    () => accounts.filter((account) => account.isActive && INVESTMENT_ACCOUNT_TYPES.has(account.accountType)),
    [accounts],
  );
  const [accountId, setAccountId] = useState(investmentAccounts[0]?.id ?? "");
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [portfolio, setPortfolio] = useState<PortfolioPositions>();
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [lookupMethod, setLookupMethod] = useState<LookupMethod>("isin");
  const [identity, setIdentity] = useState("");
  const [exchange, setExchange] = useState("");
  const [resolving, setResolving] = useState(false);
  const [resolution, setResolution] = useState<SecurityResolution>();
  const [candidateId, setCandidateId] = useState("");
  const [security, setSecurity] = useState<SecurityCandidate>();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [confirmation, setConfirmation] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(false);
  const [mutationProgress, setMutationProgress] = useState<MutationProgress>("none");
  const [feedback, setFeedback] = useState("");
  const confirmButton = useRef<HTMLButtonElement>(null);
  const lookupGeneration = useRef(0);
  const lookupRequest = useRef<AbortController>(null);

  useEffect(() => {
    let current = true;
    request<PortfolioPositions>("/portfolio/positions")
      .then((next) => {
        if (!current) return;
        setPortfolio(next);
        setLoadState("ready");
      })
      .catch(() => {
        if (current) setLoadState("error");
      });
    return () => { current = false; };
  }, [loadAttempt, request]);

  useEffect(() => {
    if (confirmation) confirmButton.current?.focus();
  }, [confirmation]);

  const account = investmentAccounts.find(({ id }) => id === accountId);
  const completeDraft = Boolean(
    account && security && draft.eventDate && draft.units && draft.price && draft.grossAmount
      && draft.charges && draft.withheldTax && draft.netAmount
      && (!draft.addValuation || (draft.valuationDate && draft.valuationPrice)),
  );
  const purchaseLocked = mutationProgress !== "none";

  function changeDraft<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
    setConfirmation(false);
    setProblem(false);
    setFeedback("");
  }

  function invalidateResolution() {
    lookupGeneration.current += 1;
    lookupRequest.current?.abort();
    lookupRequest.current = null;
    setResolving(false);
    setResolution(undefined);
    setCandidateId("");
    setSecurity(undefined);
    setConfirmation(false);
  }

  function changeLookup(method: LookupMethod) {
    setLookupMethod(method);
    setIdentity("");
    setExchange("");
    invalidateResolution();
  }

  async function resolveInstrument() {
    const body: SecurityResolveRequest = lookupMethod === "isin"
      ? { isin: identity }
      : lookupMethod === "ticker"
        ? { ticker: identity, exchange }
        : { name: identity };
    lookupRequest.current?.abort();
    const controller = new AbortController();
    const generation = lookupGeneration.current + 1;
    lookupGeneration.current = generation;
    lookupRequest.current = controller;
    setResolving(true);
    setProblem(false);
    setFeedback("");
    setResolution(undefined);
    setCandidateId("");
    setSecurity(undefined);
    setConfirmation(false);
    try {
      const next = await request<SecurityResolution>("/investment-catalogue/securities/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (generation !== lookupGeneration.current) return;
      setResolution(next);
      if (next.status === "matched" && next.candidates.length === 1) {
        setSecurity(next.candidates[0]);
        if (!next.candidates[0].manualPriceEligible) {
          setDraft((current) => ({ ...current, addValuation: false }));
        }
      }
    } catch {
      if (generation === lookupGeneration.current) setProblem(true);
    } finally {
      if (generation === lookupGeneration.current) setResolving(false);
    }
  }

  function confirmCandidate() {
    const selected = resolution?.candidates.find(({ id }) => id === candidateId);
    if (!selected) return;
    setSecurity(selected);
    if (!selected.manualPriceEligible) changeDraft("addValuation", false);
    setConfirmation(false);
  }

  function reviewPurchase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!completeDraft) return;
    if (mutationProgress === "none") setIdempotencyKey(createIdempotencyKey());
    setProblem(false);
    setFeedback("");
    setConfirmation(true);
  }

  async function submitPurchase() {
    if (!account || !security || !idempotencyKey || busy) return;
    const acquisition: AcquisitionCreate = {
      accountId: account.id,
      securityId: security.id,
      eventDate: draft.eventDate,
      eventKind: draft.eventKind,
      units: draft.units,
      price: draft.price,
      grossAmount: draft.grossAmount,
      charges: draft.charges,
      withheldTax: draft.withheldTax,
      netAmount: draft.netAmount,
      currency: security.currency,
      idempotencyKey,
    };
    setBusy(true);
    setProblem(false);
    setFeedback("");
    let progress = mutationProgress;
    try {
      if (progress === "none") {
        await request<InvestmentEventRow>("/investment-events/acquisitions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(acquisition),
        });
        progress = draft.addValuation ? "purchase" : "complete";
        setMutationProgress(progress);
      }
      if (draft.addValuation && progress === "purchase") {
        const valuation: ManualPriceCreate = {
          priceDate: draft.valuationDate,
          price: draft.valuationPrice,
        };
        await request<ManualPriceRow>(
          `/investment-catalogue/securities/${security.id}/manual-prices`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(valuation),
          },
        );
        progress = "complete";
        setMutationProgress(progress);
      }
      const next = await request<PortfolioPositions>("/portfolio/positions");
      setPortfolio(next);
      setLoadState("ready");
      setConfirmation(false);
      setIdempotencyKey("");
      setMutationProgress("none");
      setFeedback(
        draft.addValuation
          ? "Purchase and dated price recorded."
          : "Purchase recorded. A current value still needs a dated price.",
      );
    } catch {
      setProblem(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="workspace-content investment-entry-screen" aria-labelledby="investments-title">
      <header className="investment-entry-header">
        <div>
          <p className="eyebrow">Recorded evidence</p>
          <h1 className="workspace-title" id="investments-title">Investments</h1>
        </div>
        <p>Record an exact acquisition and, when available, a separate dated valuation.</p>
      </header>

      <ol className="investment-evidence-rail" aria-label="Investment entry steps">
        <li className={account ? "complete" : "current"}><span>1</span>Account</li>
        <li className={security ? "complete" : account ? "current" : ""}><span>2</span>Instrument</li>
        <li className={confirmation ? "complete" : security ? "current" : ""}><span>3</span>Purchase</li>
        <li className={draft.addValuation ? "current" : "optional"}><span>4</span>Dated value</li>
      </ol>

      <section className="investment-holdings" aria-labelledby="recorded-holdings-title">
        <div className="investment-section-head">
          <div>
            <p className="section-label">Current projection</p>
            <h2 id="recorded-holdings-title">Recorded holdings</h2>
          </div>
          {portfolio && <small>As of {dateLabel(portfolio.asOf)}</small>}
        </div>
        {loadState === "loading" && <p className="investment-state" role="status">Loading recorded holdings…</p>}
        {loadState === "error" && (
          <div className="investment-state" role="alert">
            <p>Recorded holdings could not be loaded.</p>
            <button className="text-action" onClick={() => { setLoadState("loading"); setLoadAttempt((value) => value + 1); }} type="button">Try again</button>
          </div>
        )}
        {loadState === "ready" && portfolio?.positions.length === 0 && (
          <p className="investment-state">No investment holdings are recorded yet.</p>
        )}
        {loadState === "ready" && portfolio && portfolio.positions.length > 0 && (
          <div className="investment-holding-grid">
            {portfolio.positions.map((item) => (
              <article aria-label={item.securityName} className="investment-holding" key={item.holdingId}>
                <div>
                  <p className="section-label">{item.assetClass}</p>
                  <h3>{item.securityName}</h3>
                  <p className="investment-exact">{item.units} units</p>
                </div>
                <div className="investment-holding-value">
                  <small>Current value</small>
                  <strong>{item.currentValueNative === null ? "Current value unavailable" : `${item.currency} ${item.currentValueNative}`}</strong>
                  {item.price !== null && item.priceDate !== null && (
                    <p>Price {item.currency} {item.price} · {dateLabel(item.priceDate)}</p>
                  )}
                  {coverageMessages(item).map((message) => <p key={message}>{message}</p>)}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {investmentAccounts.length === 0 ? (
        <section className="investment-entry-empty" aria-labelledby="investment-account-required">
          <p className="section-label">Account required</p>
          <h2 id="investment-account-required">Set up the holding container first</h2>
          <p>An investment account is required before recording a purchase.</p>
          <Link className="primary-action" href="/accounts">Add an investment account</Link>
        </section>
      ) : (
        <form className="investment-entry-docket" onSubmit={reviewPurchase}>
          <div className="investment-docket-head">
            <div>
              <p className="section-label">Owner entry</p>
              <h2>Acquisition docket</h2>
            </div>
            <small>Exact strings are sent to the ledger unchanged.</small>
          </div>

          <label className="select-field">
            <span>Investment account</span>
            <select disabled={purchaseLocked} onChange={(event) => { setAccountId(event.target.value); setConfirmation(false); }} value={accountId}>
              {investmentAccounts.map((item) => (
                <option key={item.id} value={item.id}>{accountLabel(item)}</option>
              ))}
            </select>
          </label>

          <fieldset className="investment-instrument-fields">
            <legend>Catalogue instrument</legend>
            <label className="select-field">
              <span>Lookup method</span>
              <select disabled={purchaseLocked} onChange={(event) => changeLookup(event.target.value as LookupMethod)} value={lookupMethod}>
                <option value="isin">Public identifier</option>
                <option value="ticker">Ticker and exchange</option>
                <option value="name">Instrument name</option>
              </select>
            </label>
            <label className="text-field">
              <span>{lookupMethod === "name" ? "Instrument name" : lookupMethod === "ticker" ? "Ticker" : "Public identifier"}</span>
              <input
                autoComplete="off"
                disabled={purchaseLocked}
                onChange={(event) => { setIdentity(event.target.value); invalidateResolution(); }}
                required
                value={identity}
              />
            </label>
            {lookupMethod === "ticker" && (
              <label className="text-field">
                <span>Exchange</span>
                <input autoComplete="off" disabled={purchaseLocked} onChange={(event) => { setExchange(event.target.value); invalidateResolution(); }} required value={exchange} />
              </label>
            )}
            <button
              className="text-action"
              disabled={purchaseLocked || resolving || !identity || (lookupMethod === "ticker" && !exchange)}
              onClick={() => void resolveInstrument()}
              type="button"
            >{resolving ? "Checking catalogue…" : "Find instrument"}</button>

            {problem && !confirmation && <p className="inline-alert" role="alert">The catalogue request could not be completed. Check the entry and try again.</p>}
            {security && resolution?.status === "matched" && (
              <div className="investment-resolution" role="status">
                <InstrumentCandidate candidate={security} />
                <span>Matched by exact public identity.</span>
              </div>
            )}
            {security && resolution?.status !== "matched" && (
              <div className="investment-resolution" role="status">
                <InstrumentCandidate candidate={security} />
                <span>Instrument confirmed by owner.</span>
              </div>
            )}
            {resolution && resolution.status !== "matched" && resolution.candidates.length === 0 && (
              <p className="investment-state" role="status">No catalogue match is available. Add supported evidence before recording this purchase.</p>
            )}
            {resolution && resolution.status !== "matched" && resolution.candidates.length > 0 && !security && (
              <fieldset className="investment-candidate-list" aria-label="Confirm instrument">
                <legend>Choose the instrument supported by your evidence</legend>
                {resolution.candidates.map((item) => (
                  <label key={item.id}>
                    <input checked={candidateId === item.id} name="security-candidate" onChange={() => setCandidateId(item.id)} type="radio" />
                    <InstrumentCandidate candidate={item} />
                  </label>
                ))}
                <button className="text-action" disabled={!candidateId} onClick={confirmCandidate} type="button">Use selected instrument</button>
              </fieldset>
            )}
          </fieldset>

          <div className="investment-purchase-fields">
            <label className="select-field">
              <span>Entry kind</span>
              <select disabled={purchaseLocked} onChange={(event) => changeDraft("eventKind", event.target.value as Draft["eventKind"])} value={draft.eventKind}>
                <option value="purchase">Purchase</option>
                <option value="recurring_purchase">Recurring purchase</option>
              </select>
            </label>
            <label className="date-field"><span>Purchase date</span><input disabled={purchaseLocked} onChange={(event) => changeDraft("eventDate", event.target.value)} required type="date" value={draft.eventDate} /></label>
            <label className="text-field"><span>Units acquired</span><input disabled={purchaseLocked} inputMode="decimal" onChange={(event) => changeDraft("units", event.target.value)} required value={draft.units} /></label>
            <label className="text-field"><span>Transaction price</span><input disabled={purchaseLocked} inputMode="decimal" onChange={(event) => changeDraft("price", event.target.value)} required value={draft.price} /></label>
            <label className="text-field"><span>Gross amount</span><input disabled={purchaseLocked} inputMode="decimal" onChange={(event) => changeDraft("grossAmount", event.target.value)} required value={draft.grossAmount} /></label>
            <label className="text-field"><span>Charges</span><input disabled={purchaseLocked} inputMode="decimal" onChange={(event) => changeDraft("charges", event.target.value)} required value={draft.charges} /></label>
            <label className="text-field"><span>Withheld tax</span><input disabled={purchaseLocked} inputMode="decimal" onChange={(event) => changeDraft("withheldTax", event.target.value)} required value={draft.withheldTax} /></label>
            <label className="text-field"><span>Net cash amount</span><input disabled={purchaseLocked} inputMode="decimal" onChange={(event) => changeDraft("netAmount", event.target.value)} required value={draft.netAmount} /></label>
          </div>

          {security?.manualPriceEligible ? (
            <label className="check-field investment-valuation-toggle">
              <input checked={draft.addValuation} disabled={purchaseLocked} onChange={(event) => changeDraft("addValuation", event.target.checked)} type="checkbox" />
              <span>Record a dated valuation price after this purchase</span>
            </label>
          ) : security ? (
            <p className="investment-state">Manual dated prices are unavailable for this shared catalogue instrument.</p>
          ) : null}
          {draft.addValuation && (
            <div className="investment-valuation-fields">
              <label className="date-field"><span>Valuation date</span><input onChange={(event) => changeDraft("valuationDate", event.target.value)} required type="date" value={draft.valuationDate} /></label>
              <label className="text-field"><span>Valuation price</span><input inputMode="decimal" onChange={(event) => changeDraft("valuationPrice", event.target.value)} required value={draft.valuationPrice} /></label>
              <p>This is separate dated valuation evidence, not a change to the transaction price.</p>
            </div>
          )}

          <button className="primary-action" disabled={!completeDraft || busy || mutationProgress === "complete"} type="submit">
            {mutationProgress === "purchase" ? "Review dated price" : "Review purchase"}
          </button>

          {confirmation && account && security && (
            <fieldset className="investment-confirmation" aria-label={mutationProgress === "purchase" ? "Confirm dated price" : mutationProgress === "complete" ? "Reload holdings" : "Confirm purchase"}>
              <legend>{mutationProgress === "purchase" ? "Confirm dated price" : mutationProgress === "complete" ? "Reload holdings" : "Confirm purchase"}</legend>
              <p>{mutationProgress === "purchase"
                ? "The purchase is already recorded. Confirm only the remaining dated price."
                : mutationProgress === "complete"
                  ? "The purchase facts are recorded. Reload holdings to display the resulting position."
                  : "This appends a new owner-entered event. Check each exact fact before recording it."}</p>
              <dl>
                <div><dt>Account</dt><dd>{accountLabel(account)}</dd></div>
                <div><dt>Instrument</dt><dd>{security.name}</dd></div>
                <div><dt>Date</dt><dd>{dateLabel(draft.eventDate)}</dd></div>
                <div><dt>Units</dt><dd>{draft.units} units</dd></div>
                <div><dt>Gross</dt><dd>{security.currency} {draft.grossAmount}</dd></div>
                <div><dt>Net cash</dt><dd>Net cash {security.currency} {draft.netAmount}</dd></div>
              </dl>
              {draft.addValuation && <p>Then record valuation price {security.currency} {draft.valuationPrice} dated {dateLabel(draft.valuationDate)}.</p>}
              {problem && (
                <p className="inline-alert" role="alert">
                  {mutationProgress === "purchase"
                    ? "The purchase was recorded, but the dated price was not. Review the price or retry."
                    : mutationProgress === "complete"
                      ? "The facts were recorded, but holdings could not be reloaded. Retry the refresh."
                      : "The purchase was not recorded. Review the exact facts or retry."}
                </p>
              )}
              <div className="investment-confirm-actions">
                <button className="primary-action" disabled={busy} onClick={() => void submitPurchase()} ref={confirmButton} type="button">
                  {busy
                    ? "Recording purchase…"
                    : problem && mutationProgress === "purchase"
                      ? "Retry dated price"
                      : problem && mutationProgress === "complete"
                        ? "Reload holdings"
                        : problem
                          ? "Retry purchase"
                          : mutationProgress === "purchase"
                            ? "Confirm dated price"
                            : "Confirm purchase"}
                </button>
                {mutationProgress !== "complete" && (
                  <button className="text-action" disabled={busy} onClick={() => { setConfirmation(false); setProblem(false); }} type="button">
                    {mutationProgress === "purchase" ? "Edit dated price" : "Go back"}
                  </button>
                )}
              </div>
            </fieldset>
          )}
          {feedback && <p className="investment-feedback" role="status">{feedback}</p>}
        </form>
      )}
    </section>
  );
}
