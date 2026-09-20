"use client";

import { Button } from "@/components/ui/button";

import Link from "next/link";
import { useEffect, useState } from "react";

import { reasonText } from "@/app/investments/review-reasons";
import { DataTableRegion } from "@/app/ui/data-table-region";
import { StatusLabel } from "@/app/ui/status-label";
import { errorText, type apiFetch } from "@/lib/api/client";
import type { ReviewItemRow } from "@/types/api";

type Props = {
  request: typeof apiFetch;
  /** Keep actionable import reviews visible when the portfolio has no holdings. */
  quietEmpty?: boolean;
};

type LoadState = "loading" | "ready" | "error";
const PAGE_SIZE = 20;

/** Every open investment review this household owns — a holding-scoped row (lot allocation)
 * links to its holding, an import-scoped row to its import. */
export function ReviewQueue({ request, quietEmpty = false }: Props) {
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

  if (quietEmpty && (state === "loading" || (state === "ready" && rows.length === 0))) return null;

  return (
    <section aria-labelledby="investment-reviews-title" className="investment-reviews">
      <div className="console-section-head">
        <h2 id="investment-reviews-title">Reviews</h2>
        <span>{rows.length} open</span>
      </div>
      {state === "loading" && <p className="investment-state" role="status">Loading reviews…</p>}
      {state === "error" && <p className="inline-alert" role="alert">Open reviews could not be loaded.</p>}
      {state === "ready" && rows.length === 0 && <p className="investment-state">No open reviews.</p>}
      {rows.length > 0 && (
        <DataTableRegion label="Investment reviews table">
          <table className="console-table investment-reviews-table">
            <thead><tr><th>Review</th><th>Scope</th><th>Status</th><th>Action</th></tr></thead>
            <tbody>{rows.map((row) => (
              <tr key={row.id}>
                <th scope="row">{reasonText(row.reason)}</th>
                <td>{row.securityName ?? row.accountName ?? "Imported evidence"}</td>
                <td><StatusLabel tone={row.isBlocking ? "danger" : "attention"}>{row.isBlocking ? "Blocking" : "Needs review"}</StatusLabel></td>
                <td>{row.holdingId
                  ? <Link href={`/investments/${row.holdingId}`}>View holding</Link>
                  : row.importId ? <Link href={`/investments/import/${row.importId}`}>View import</Link> : "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        </DataTableRegion>
      )}
      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {state === "ready" && hasMore && (
        <Button variant="outline" disabled={busy} onClick={() => void loadMore()} type="button">
          {busy ? "Loading reviews…" : "Load more reviews"}
        </Button>
      )}
    </section>
  );
}
