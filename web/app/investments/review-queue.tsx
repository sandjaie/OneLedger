"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { reasonText } from "@/app/investments/review-reasons";
import { errorText, type apiFetch } from "@/lib/api/client";
import type { ReviewItemRow } from "@/types/api";

type Props = {
  request: typeof apiFetch;
};

type LoadState = "loading" | "ready" | "error";
const PAGE_SIZE = 20;

/** Every open investment review this household owns — a holding-scoped row (lot allocation)
 * links to its holding, an import-scoped row to its import. */
export function ReviewQueue({ request }: Props) {
  const [state, setState] = useState<LoadState>("loading");
  const [rows, setRows] = useState<Array<ReviewItemRow>>([]);
  const [hasMore, setHasMore] = useState(true);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let current = true;
    request<Array<ReviewItemRow>>(`/investment-reviews?limit=${PAGE_SIZE}&offset=0`)
      .then((page) => {
        if (!current) return;
        setRows(page);
        setHasMore(page.length === PAGE_SIZE);
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [request]);

  async function loadMore() {
    if (busy || !hasMore) return;
    setBusy(true);
    setProblem("");
    try {
      const page = await request<Array<ReviewItemRow>>(
        `/investment-reviews?limit=${PAGE_SIZE}&offset=${rows.length}`,
      );
      setRows((current) => [...current, ...page]);
      setHasMore(page.length === PAGE_SIZE);
    } catch (error) {
      setProblem(errorText(error, "More reviews could not be loaded."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="investment-reviews-title" className="investment-reviews">
      <p className="section-label" id="investment-reviews-title">Reviews</p>
      {state === "loading" && <p className="investment-state" role="status">Loading reviews…</p>}
      {state === "error" && <p className="inline-alert" role="alert">Open reviews could not be loaded.</p>}
      {state === "ready" && rows.length === 0 && <p className="investment-state">No open reviews.</p>}
      {rows.length > 0 && (
        <ul>
          {rows.map((row) => (
            <li key={row.id}>
              <span>{reasonText(row.reason)}</span>
              {row.securityName && <span> · {row.securityName}</span>}
              {row.accountName && <span> · {row.accountName}</span>}
              {row.holdingId && <Link href={`/investments/${row.holdingId}`}>View holding</Link>}
              {!row.holdingId && row.importId && (
                <Link href={`/investments/import/${row.importId}`}>View import</Link>
              )}
            </li>
          ))}
        </ul>
      )}
      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {state === "ready" && hasMore && (
        <button className="text-action" disabled={busy} onClick={() => void loadMore()} type="button">
          {busy ? "Loading reviews…" : "Load more reviews"}
        </button>
      )}
    </section>
  );
}
