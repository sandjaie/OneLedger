"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

import { useEffect, useState } from "react";

import { ApiError, type apiFetch, errorText } from "@/lib/api/client";
import type {
  AssetClassName,
  SecurityCandidate,
  SecurityCreate,
  SecurityResolution,
  SecurityResolveRequest,
  SecurityRow,
  SecurityTypeName,
} from "@/types/api";

type LookupMethod = "isin" | "ticker" | "name";

type Props = {
  request: typeof apiFetch;
  security: SecurityCandidate | undefined;
  onChange: (security: SecurityCandidate | undefined) => void;
  /** A household-owned security the extraction's own ISIN already matched. Shown as a suggestion
   * only — the owner still confirms it — never applied silently (Task 17 contract). */
  suggestedSecurityId?: string | null;
  /** Statement text, offered only as a prefill for the inline "create security" form. */
  suggestedIsin?: string | null;
  suggestedLabel?: string | null;
  disabled?: boolean;
};

const SECURITY_TYPES: Array<SecurityTypeName> = [
  "mutual_fund",
  "equity",
  "exchange_traded_fund",
  "bond",
  "deposit",
  "commodity",
  "real_asset",
  "alternative",
  "other",
];

const ASSET_CLASSES: Array<AssetClassName> = [
  "equity",
  "fixed_income",
  "cash",
  "commodity",
  "real_asset",
  "alternative",
  "other",
];

function fromRow(row: SecurityRow): SecurityCandidate {
  // Just created by this owner in this household, so it is eligible for a manual price the
  // instant it exists — there is no shared catalogue row here to gate on.
  return {
    id: row.id,
    securityType: row.securityType,
    name: row.name,
    currency: row.currency,
    country: row.country,
    assetClass: row.assetClass,
    subAssetClass: null,
    isin: row.isin,
    ticker: null,
    exchange: null,
    manualPriceEligible: true,
  };
}

export function InstrumentCandidate({ candidate }: { candidate: SecurityCandidate }) {
  const publicIdentity =
    candidate.isin ??
    (candidate.ticker && candidate.exchange ? `${candidate.ticker} · ${candidate.exchange}` : null);
  return (
    <span className="investment-candidate-copy">
      <strong>{candidate.name}</strong>
      <small>{candidate.securityType.replaceAll("_", " ")} · {candidate.currency}</small>
      {publicIdentity && <small>{publicIdentity}</small>}
    </span>
  );
}

/** Lookup, confirm, or create a household security — shared shape for the import review and the
 * manual entry docket. Never applies a suggestion or a candidate on its own; every path here ends
 * with the owner clicking something before `onChange` fires. */
export function SecurityForm({
  request,
  security,
  onChange,
  suggestedSecurityId,
  suggestedIsin,
  suggestedLabel,
  disabled,
}: Props) {
  const [suggestion, setSuggestion] = useState<SecurityCandidate>();
  const [suggestionDismissed, setSuggestionDismissed] = useState(false);
  const [lookupMethod, setLookupMethod] = useState<LookupMethod>("isin");
  const [identity, setIdentity] = useState("");
  const [exchange, setExchange] = useState("");
  const [resolving, setResolving] = useState(false);
  const [resolution, setResolution] = useState<SecurityResolution>();
  const [candidateId, setCandidateId] = useState("");
  const [problem, setProblem] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createProblem, setCreateProblem] = useState<string>();
  const [createDraft, setCreateDraft] = useState({
    name: suggestedLabel ?? "",
    securityType: "equity" as SecurityTypeName,
    assetClass: "equity" as AssetClassName,
    currency: "",
    isin: suggestedIsin ?? "",
    country: "",
  });

  useEffect(() => {
    if (!suggestedSecurityId || security || suggestionDismissed) return;
    let current = true;
    request<SecurityResolution>("/investment-catalogue/securities/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ securityId: suggestedSecurityId }),
    })
      .then((next) => {
        if (current) setSuggestion(next.candidates[0]);
      })
      .catch(() => {
        if (current) setSuggestion(undefined);
      });
    return () => {
      current = false;
    };
  }, [suggestedSecurityId, security, suggestionDismissed, request]);

  function invalidateResolution() {
    setResolving(false);
    setResolution(undefined);
    setCandidateId("");
    setProblem(false);
  }

  function changeLookup(method: LookupMethod) {
    setLookupMethod(method);
    setIdentity("");
    setExchange("");
    invalidateResolution();
  }

  async function resolveInstrument() {
    const body: SecurityResolveRequest =
      lookupMethod === "isin"
        ? { isin: identity }
        : lookupMethod === "ticker"
          ? { ticker: identity, exchange }
          : { name: identity };
    setResolving(true);
    setProblem(false);
    setResolution(undefined);
    setCandidateId("");
    try {
      const next = await request<SecurityResolution>("/investment-catalogue/securities/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setResolution(next);
      if (next.status === "matched" && next.candidates.length === 1) {
        onChange(next.candidates[0]);
      }
    } catch {
      setProblem(true);
    } finally {
      setResolving(false);
    }
  }

  function confirmCandidate() {
    const selected = resolution?.candidates.find(({ id }) => id === candidateId);
    if (!selected) return;
    onChange(selected);
  }

  async function submitCreate() {
    setCreateBusy(true);
    setCreateProblem(undefined);
    const trimmedIsin = createDraft.isin.trim().toUpperCase();
    try {
      const body: SecurityCreate = {
        name: createDraft.name.trim(),
        securityType: createDraft.securityType,
        assetClass: createDraft.assetClass,
        currency: createDraft.currency.trim().toUpperCase(),
        isin: trimmedIsin || undefined,
        country: trimmedIsin ? undefined : createDraft.country.trim().toUpperCase() || undefined,
      };
      const row = await request<SecurityRow>("/investment-catalogue/securities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      onChange(fromRow(row));
      setCreating(false);
    } catch (error) {
      // A 409 here always means the ISIN collision this route documents — its own detail is a
      // structured `{message, existingSecurityId}` object, not the plain sentence `errorText()`
      // reads, so a fixed sentence stands in for it. The draft is left exactly as typed: the
      // owner's next move is to look the existing security up, not to redo this form.
      setCreateProblem(
        error instanceof ApiError && error.status === 409
          ? "A security with this ISIN already exists — use Find instrument to select it."
          : errorText(error, "This security could not be created."),
      );
    } finally {
      setCreateBusy(false);
    }
  }

  if (security) {
    return (
      <div className="investment-resolution" role="status">
        <InstrumentCandidate candidate={security} />
        <Button
          variant="outline"
          disabled={disabled}
          onClick={() => onChange(undefined)}
          type="button"
        >Change security</Button>
      </div>
    );
  }

  if (suggestion && !suggestionDismissed) {
    return (
      <div className="investment-resolution" role="status">
        <p className="section-label">Suggested from the statement&apos;s own identifier</p>
        <InstrumentCandidate candidate={suggestion} />
        <div className="investment-confirm-actions">
          <Button
            variant="default"
            disabled={disabled}
            onClick={() => onChange(suggestion)}
            type="button"
          >Use this security</Button>
          <Button
            variant="outline"
            disabled={disabled}
            onClick={() => setSuggestionDismissed(true)}
            type="button"
          >Search the catalogue instead</Button>
        </div>
      </div>
    );
  }

  return (
    <fieldset className="investment-instrument-fields investment-context-workflow">
      <legend>Catalogue instrument</legend>
      {!creating ? (
        <>
          <label className="select-field">
            <span>Lookup method</span>
            <NativeSelect
              disabled={disabled}
              onChange={(event) => changeLookup(event.target.value as LookupMethod)}
              value={lookupMethod}
            >
              <option value="isin">Public identifier</option>
              <option value="ticker">Ticker and exchange</option>
              <option value="name">Instrument name</option>
            </NativeSelect>
          </label>
          <label className="text-field">
            <span>{lookupMethod === "name" ? "Instrument name" : lookupMethod === "ticker" ? "Ticker" : "Public identifier"}</span>
            <Input
              autoComplete="off"
              disabled={disabled}
              onChange={(event) => { setIdentity(event.target.value); invalidateResolution(); }}
              value={identity}
            />
          </label>
          {lookupMethod === "ticker" && (
            <label className="text-field">
              <span>Exchange</span>
              <Input
                autoComplete="off"
                disabled={disabled}
                onChange={(event) => { setExchange(event.target.value); invalidateResolution(); }}
                value={exchange}
              />
            </label>
          )}
          <div className="investment-confirm-actions">
            <Button
              variant="outline"
              disabled={disabled || resolving || !identity || (lookupMethod === "ticker" && !exchange)}
              onClick={() => void resolveInstrument()}
              type="button"
            >{resolving ? "Checking catalogue…" : "Find instrument"}</Button>
            <Button
              variant="outline"
              disabled={disabled}
              onClick={() => setCreating(true)}
              type="button"
            >Add a new security</Button>
          </div>

          {problem && <p className="inline-alert" role="alert">The catalogue request could not be completed. Check the entry and try again.</p>}
          {resolution && resolution.status !== "matched" && resolution.candidates.length === 0 && (
            <p className="investment-state" role="status">No catalogue match is available. Add it as a new security below.</p>
          )}
          {resolution && resolution.status !== "matched" && resolution.candidates.length > 0 && (
            <fieldset className="investment-candidate-list" aria-label="Confirm instrument">
              <legend>Choose the instrument supported by your evidence</legend>
              <RadioGroup aria-label="Catalogue candidates" name="security-candidate" onValueChange={setCandidateId} value={candidateId}>
                {resolution.candidates.map((item) => (
                  <label htmlFor={`security-candidate-${item.id}`} key={item.id}>
                    <RadioGroupItem id={`security-candidate-${item.id}`} value={item.id} />
                    <InstrumentCandidate candidate={item} />
                  </label>
                ))}
              </RadioGroup>
              <Button variant="outline" disabled={!candidateId} onClick={confirmCandidate} type="button">
                Use selected instrument
              </Button>
            </fieldset>
          )}
        </>
      ) : (
        <div className="investment-create-security">
          <label className="text-field">
            <span>Name</span>
            <Input
              disabled={createBusy}
              onChange={(event) => setCreateDraft((current) => ({ ...current, name: event.target.value }))}
              value={createDraft.name}
            />
          </label>
          <label className="select-field">
            <span>Security type</span>
            <NativeSelect
              disabled={createBusy}
              onChange={(event) => setCreateDraft((current) => ({ ...current, securityType: event.target.value as SecurityTypeName }))}
              value={createDraft.securityType}
            >
              {SECURITY_TYPES.map((type) => <option key={type} value={type}>{type.replaceAll("_", " ")}</option>)}
            </NativeSelect>
          </label>
          <label className="select-field">
            <span>Asset class</span>
            <NativeSelect
              disabled={createBusy}
              onChange={(event) => setCreateDraft((current) => ({ ...current, assetClass: event.target.value as AssetClassName }))}
              value={createDraft.assetClass}
            >
              {ASSET_CLASSES.map((option) => <option key={option} value={option}>{option.replaceAll("_", " ")}</option>)}
            </NativeSelect>
          </label>
          <label className="text-field">
            <span>Currency</span>
            <Input
              disabled={createBusy}
              maxLength={3}
              onChange={(event) => setCreateDraft((current) => ({ ...current, currency: event.target.value }))}
              value={createDraft.currency}
            />
          </label>
          <label className="text-field">
            <span>ISIN (optional)</span>
            <Input
              disabled={createBusy}
              maxLength={12}
              onChange={(event) => setCreateDraft((current) => ({ ...current, isin: event.target.value }))}
              value={createDraft.isin}
            />
          </label>
          {!createDraft.isin.trim() && (
            <label className="text-field">
              <span>Country</span>
              <Input
                disabled={createBusy}
                maxLength={2}
                onChange={(event) => setCreateDraft((current) => ({ ...current, country: event.target.value }))}
                value={createDraft.country}
              />
            </label>
          )}
          {createProblem && <p className="inline-alert" role="alert">{createProblem}</p>}
          <div className="investment-confirm-actions">
            <Button
              variant="default"
              disabled={createBusy || !createDraft.name.trim() || !createDraft.currency.trim() || (!createDraft.isin.trim() && !createDraft.country.trim())}
              onClick={() => void submitCreate()}
              type="button"
            >{createBusy ? "Creating…" : "Create security"}</Button>
            <Button variant="outline" disabled={createBusy} onClick={() => setCreating(false)} type="button">
              Cancel
            </Button>
          </div>
        </div>
      )}
    </fieldset>
  );
}
