"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { AttentionRow } from "@/app/ui/attention-row";
import { type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  Candidate,
  CandidatesResponse,
  CurrencyRow,
  HouseholdResponse,
  PeersResponse,
  TransactionRow,
  TransferRow,
  TransfersResponse,
} from "@/types/api";

// Transfers are confirmed here, on the ledger, because a transfer's two legs are almost never
// in the same import batch (ADR-0017). Scoring proposes and nothing links without a click: the
// high band is pre-ticked as a checkbox state, and the POST happens on the owner's click.
//
// The browser formats money and computes none of it. Every amount, rate and cost arrives as a
// decimal string and is rendered through `formatMoney` with the currency row that owns it — a
// leg's amount with that leg's own currency, the frozen base figure with the household's.

type Shared = {
  currencies: Array<CurrencyRow>;
  household: HouseholdResponse;
  request: typeof apiFetch;
  onChanged: () => void;
};

const BANDS = [
  { band: "high", title: "High confidence", note: "Pre-ticked. Still needs your click." },
  { band: "probable", title: "Probable", note: "Check the pair before confirming." },
  { band: "weak", title: "Weak suggestions", note: "Shown so nothing is missed." },
] as const;

const JSON_HEADERS = { "Content-Type": "application/json" };

function pairKey(candidate: Candidate) {
  return `${candidate.debit.id}:${candidate.credit.id}`;
}

/** The sentence the service wrote, not the status line wrapped round it. */
function useMoney(currencies: Array<CurrencyRow>, household: HouseholdResponse) {
  function money(value: string, code: string) {
    return moneyText(value, code, currencies);
  }
  // A leg's amount in its own currency, then — when that is not the base — the frozen base
  // figure in the household's. Never the conversion alone.
  function legAmount(leg: TransactionRow) {
    const own = money(leg.amount, leg.currency);
    return leg.currency === household.baseCurrency
      ? own
      : `${own} (${money(leg.amountBase, household.baseCurrency)})`;
  }
  return { money, legAmount };
}

function describeLeg(leg: TransactionRow, legAmount: (leg: TransactionRow) => string) {
  return `${leg.bookedDate} · ${leg.accountName} · ${leg.descriptionRaw} · ${legAmount(leg)}`;
}

export function TransferQueue({ currencies, household, request, onChanged }: Shared) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [candidates, setCandidates] = useState<Array<Candidate>>([]);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [confirmed, setConfirmed] = useState<Array<TransferRow>>([]);
  const { money, legAmount } = useMoney(currencies, household);

  async function find() {
    setBusy(true);
    setProblem(undefined);
    try {
      const [found, decided] = await Promise.all([
        request<CandidatesResponse>("/transfers/candidates"),
        request<TransfersResponse>("/transfers?status=confirmed"),
      ]);
      setCandidates(found.candidates);
      setTicked(new Set(found.candidates.filter((c) => c.band === "high").map(pairKey)));
      setConfirmed(decided.transfers);
      setOpen(true);
    } catch (error) {
      setProblem(errorText(error, "Transfer candidates could not be loaded. Retry."));
    } finally {
      setBusy(false);
    }
  }

  function toggle(candidate: Candidate) {
    setTicked((current) => {
      const next = new Set(current);
      if (next.has(pairKey(candidate))) next.delete(pairKey(candidate));
      else next.add(pairKey(candidate));
      return next;
    });
  }

  async function confirmTicked() {
    const chosen = candidates.filter((candidate) => ticked.has(pairKey(candidate)));
    setBusy(true);
    setProblem(undefined);
    let changed = false;
    const used = new Set<string>();
    try {
      for (const candidate of chosen) {
        // Two ticked pairs can share a leg, and only the first can become a transfer. The rest
        // are skipped here, not posted on to a certain 409 — `chosen` is fixed before the
        // loop, so dropping them from the queue alone would not stop them being sent.
        if (used.has(candidate.debit.id) || used.has(candidate.credit.id)) continue;
        const made = await request<TransferRow>("/transfers", {
          method: "POST",
          headers: JSON_HEADERS,
          body: JSON.stringify({
            sourceTransactionId: candidate.debit.id,
            destinationTransactionId: candidate.credit.id,
            fromCandidate: true,
          }),
        });
        changed = true;
        used.add(candidate.debit.id);
        used.add(candidate.credit.id);
        setConfirmed((current) => [made, ...current]);
        // A leg now linked cannot be linked again, so every other pair naming it leaves the
        // queue too. A copy, because the updater runs later and `used` goes on growing.
        const spent = new Set(used);
        setCandidates((current) =>
          current.filter((one) => !spent.has(one.debit.id) && !spent.has(one.credit.id)),
        );
      }
    } catch (error) {
      setProblem(errorText(error, "The transfer was not confirmed. Retry."));
    } finally {
      setBusy(false);
      if (changed) onChanged();
    }
  }

  async function unlink(transfer: TransferRow) {
    setBusy(true);
    setProblem(undefined);
    try {
      await request<TransferRow>(`/transfers/${transfer.id}`, { method: "DELETE" });
      setConfirmed((current) => current.filter((one) => one.id !== transfer.id));
      onChanged();
    } catch (error) {
      setProblem(errorText(error, "The transfer was not unlinked. Retry."));
    } finally {
      setBusy(false);
    }
  }

  // No `onChanged()`: a correction records who carried a transfer and what they charged, and
  // touches no transaction row — unlike confirming or unlinking, which move both legs.
  function replace(corrected: TransferRow) {
    setConfirmed((current) => current.map((one) => (one.id === corrected.id ? corrected : one)));
  }

  // What the movement cost, exactly as the API computed it: nothing here is derived.
  function cost(transfer: TransferRow) {
    if (!transfer.totalCostSource) return "No FX cost recorded.";
    const parts = [
      `cost ${money(transfer.totalCostSource, transfer.sourceCurrency)}`,
      `spread ${transfer.fxSpreadPct}%`,
      `achieved ${transfer.impliedRate} against ${transfer.marketRate}`,
    ];
    if (transfer.explicitFeeAmount && transfer.explicitFeeCurrency) {
      parts.push(`fee ${money(transfer.explicitFeeAmount, transfer.explicitFeeCurrency)}`);
    }
    const qualified = transfer.costIsApproximate
      ? " — approximate: the market rate for that day was carried forward"
      : "";
    return `${parts.join(" · ")}${qualified}`;
  }

  return (
    <section aria-label="Transfer review" className="account-form transaction-transfer-panel console-section">
      <AttentionRow
        action={(
          <Button disabled={busy} onClick={() => void find()} type="button" variant="outline">
            Find transfers
          </Button>
        )}
        detail="Review suggested movements between accounts; nothing links without your confirmation."
        title="Transfers"
        tone="neutral"
      />
      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      {open && (
        <>
          {candidates.length === 0 && <p className="lede">No transfer candidates in this window.</p>}
          {BANDS.map(({ band, title, note }) => {
            const members = candidates.filter((candidate) => candidate.band === band);
            if (members.length === 0) return null;
            return (
              <fieldset key={band} role="group" aria-label={`${title} — ${note}`}>
                <legend>{title}</legend>
                <small>{note}</small>
                {members.map((candidate) => (
                  <label className="checkbox-field" htmlFor={`transfer-${pairKey(candidate)}`} key={pairKey(candidate)}>
                    <Checkbox
                      checked={ticked.has(pairKey(candidate))}
                      disabled={busy}
                      id={`transfer-${pairKey(candidate)}`}
                      onCheckedChange={() => toggle(candidate)}
                    />
                    <span>
                      {describeLeg(candidate.debit, legAmount)} → {describeLeg(candidate.credit, legAmount)}
                      <small> · {candidate.proposedKind} · score {candidate.score.toFixed(2)}</small>
                    </span>
                  </label>
                ))}
              </fieldset>
            );
          })}
          {candidates.length > 0 && (
            <Button
              disabled={busy || ticked.size === 0}
              onClick={() => void confirmTicked()}
              type="button"
            >
              Confirm selected
            </Button>
          )}

          {confirmed.length > 0 && (
            <ul aria-label="Confirmed transfers">
              {confirmed.map((transfer) => (
                <li data-testid={`transfer-${transfer.id}`} key={transfer.id}>
                  {transfer.transferDate} · {transfer.transferKind} ·{" "}
                  {money(transfer.sourceAmount, transfer.sourceCurrency)} →{" "}
                  {money(transfer.destinationAmount, transfer.destinationCurrency)}
                  <small> · {cost(transfer)}</small>
                  <Button
                    aria-label={`Unlink transfer of ${transfer.transferDate}`}
                    disabled={busy}
                    onClick={() => void unlink(transfer)}
                    size="sm"
                    type="button"
                    variant="ghost"
                  >
                    Unlink
                  </Button>
                  {transfer.transferKind === "remittance" && (
                    <RemittanceCorrection
                      currencies={currencies}
                      household={household}
                      onCorrected={replace}
                      request={request}
                      transfer={transfer}
                    />
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

type CorrectionProps = {
  currencies: Array<CurrencyRow>;
  household: HouseholdResponse;
  onCorrected: (corrected: TransferRow) => void;
  request: typeof apiFetch;
  transfer: TransferRow;
};

/** Phase 4.4: the two fields no statement states, recorded where the remittance is.
 *
 * The fee is sent as the owner typed it — a decimal string the service validates, translates
 * once at the transfer's own date and folds into the stored cost. Clearing it sends explicit
 * nulls, which is a different request from leaving it alone: a correction that names no fee
 * leaves the frozen cost untouched. Nothing here computes a total.
 */
export function RemittanceCorrection({
  currencies,
  household,
  onCorrected,
  request,
  transfer,
}: CorrectionProps) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  // What the row stores now, as the inputs show it. Re-derived whenever the form opens, so a
  // second correction starts from what the service stored rather than what was typed before.
  const stored = {
    provider: transfer.provider ?? "",
    fee: transfer.explicitFeeAmount ?? "",
    feeCurrency: transfer.explicitFeeCurrency ?? transfer.sourceCurrency,
    notes: transfer.notes ?? "",
  };
  const [provider, setProvider] = useState(stored.provider);
  const [fee, setFee] = useState(stored.fee);
  const [feeCurrency, setFeeCurrency] = useState(stored.feeCurrency);
  const [notes, setNotes] = useState(stored.notes);

  function reveal() {
    if (!open) {
      setProvider(stored.provider);
      setFee(stored.fee);
      setFeeCurrency(stored.feeCurrency);
      setNotes(stored.notes);
      setProblem(undefined);
    }
    setOpen((current) => !current);
  }

  async function save() {
    setBusy(true);
    setProblem(undefined);
    const trimmedProvider = provider.trim();
    const trimmedFee = fee.trim();
    // Only what the owner actually edited is sent. A key left out leaves that stored value
    // alone, and the fee's absence is what keeps a frozen cost frozen: naming an unchanged fee
    // would have the service re-translate it at the transfer's date, which a rate corrected or
    // removed since then would either move or refuse (ADR-0002). Clearing a recorded fee is the
    // one case that sends explicit nulls — a different request, and deliberately so.
    const body: Record<string, string | null> = {};
    if (trimmedProvider !== stored.provider) {
      body.provider = trimmedProvider === "" ? null : trimmedProvider;
    }
    if (trimmedFee !== stored.fee || feeCurrency !== stored.feeCurrency) {
      body.explicitFeeAmount = trimmedFee === "" ? null : trimmedFee;
      body.explicitFeeCurrency = trimmedFee === "" ? null : feeCurrency;
    }
    if (notes.trim() !== stored.notes) body.notes = notes.trim() === "" ? null : notes;

    try {
      const corrected = await request<TransferRow>(`/transfers/${transfer.id}`, {
        method: "PATCH",
        headers: JSON_HEADERS,
        body: JSON.stringify(body),
      });
      onCorrected(corrected);
      setOpen(false);
    } catch (error) {
      setProblem(errorText(error, "The remittance was not corrected. Retry."));
    } finally {
      setBusy(false);
    }
  }

  const recorded = transfer.provider ?? "No provider recorded";

  return (
    <div className="remittance-correction">
      <Button
        aria-label={`Record who carried the remittance of ${transfer.transferDate}`}
        disabled={busy}
        onClick={reveal}
        size="sm"
        type="button"
        variant="ghost"
      >
        {open ? "Close" : recorded}
      </Button>
      {open && (
        <div className="remittance-correction-form">
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          <label className="text-field" htmlFor={`provider-${transfer.id}`}>
            <span>Provider</span>
            <Input
              id={`provider-${transfer.id}`}
              onChange={(event) => setProvider(event.target.value)}
              placeholder="Who carried it"
              value={provider}
            />
          </label>
          <label className="text-field" htmlFor={`fee-${transfer.id}`}>
            <span>Fee the provider itemised</span>
            <Input
              id={`fee-${transfer.id}`}
              inputMode="decimal"
              onChange={(event) => setFee(event.target.value)}
              placeholder="Leave empty if none"
              value={fee}
            />
          </label>
          <label className="text-field" htmlFor={`fee-currency-${transfer.id}`}>
            <span>Fee currency</span>
            <NativeSelect
              id={`fee-currency-${transfer.id}`}
              onChange={(event) => setFeeCurrency(event.target.value)}
              value={feeCurrency}
            >
              {currencies.map((row) => (
                <option key={row.code} value={row.code}>{row.code}</option>
              ))}
            </NativeSelect>
          </label>
          <label className="text-field" htmlFor={`notes-${transfer.id}`}>
            <span>Note</span>
            <Input
              id={`notes-${transfer.id}`}
              maxLength={500}
              onChange={(event) => setNotes(event.target.value)}
              value={notes}
            />
          </label>
          <p className="lede">
            The fee is converted once, at this transfer&apos;s own date, and added to the spread
            already recorded. A day with no rate for that currency is refused rather than guessed.
            {transfer.sourceCurrency !== household.baseCurrency
              && ` Cost is stated in ${transfer.sourceCurrency}.`}
          </p>
          <Button disabled={busy} onClick={() => void save()} type="button">
            {busy ? "Saving…" : "Save"}
          </Button>
        </div>
      )}
    </div>
  );
}

type PickerProps = Shared & { transaction: TransactionRow; onClose: () => void };

export function PeerPicker({
  currencies,
  household,
  request,
  onChanged,
  onClose,
  transaction,
}: PickerProps) {
  const [peers, setPeers] = useState<Array<Candidate>>();
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const { legAmount } = useMoney(currencies, household);
  // A debit's peers are credits and a credit's are debits; the sign is read from the string,
  // never by turning the amount into a number.
  const isDebit = transaction.amount.startsWith("-");

  useEffect(() => {
    let cancelled = false;
    request<PeersResponse>(`/transactions/${transaction.id}/transfer-candidates`)
      .then((found) => {
        if (!cancelled) setPeers(found.peers);
      })
      .catch((error: unknown) => {
        if (!cancelled) setProblem(errorText(error, "Peers could not be loaded. Retry."));
      });
    return () => {
      cancelled = true;
    };
  }, [request, transaction.id]);

  const needle = search.trim().toLowerCase();
  const shown = (peers ?? []).filter((candidate) => {
    const peer = isDebit ? candidate.credit : candidate.debit;
    return !needle || `${peer.descriptionRaw} ${peer.accountName}`.toLowerCase().includes(needle);
  });

  async function link(candidate: Candidate) {
    setBusy(true);
    setProblem(undefined);
    try {
      await request<TransferRow>("/transfers", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({
          sourceTransactionId: candidate.debit.id,
          destinationTransactionId: candidate.credit.id,
          fromCandidate: false,
        }),
      });
      onChanged();
      onClose();
    } catch (error) {
      setProblem(errorText(error, "The transfer was not linked. Retry."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-label={`Link ${transaction.descriptionRaw} as a transfer`}
      className="account-form transaction-peer-panel"
    >
      <h2>Link as a transfer</h2>
      <p className="lede">{describeLeg(transaction, legAmount)}</p>
      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      <label className="text-field">
        <span>Search peers</span>
        <Input onChange={(event) => setSearch(event.target.value)} value={search} />
      </label>
      {peers === undefined ? (
        <p className="lede">Loading peers…</p>
      ) : shown.length === 0 ? (
        <p className="lede">No peer matches.</p>
      ) : (
        <ol>
          {shown.map((candidate) => {
            const peer = isDebit ? candidate.credit : candidate.debit;
            return (
              <li key={pairKey(candidate)}>
                {describeLeg(peer, legAmount)}
                <small> · score {candidate.score.toFixed(2)}</small>
                <Button
                  aria-label={`Link ${peer.descriptionRaw}`}
                  disabled={busy}
                  onClick={() => void link(candidate)}
                  size="sm"
                  type="button"
                  variant="ghost"
                >
                  Link
                </Button>
              </li>
            );
          })}
        </ol>
      )}
      <Button onClick={onClose} type="button" variant="outline">Close</Button>
    </section>
  );
}
