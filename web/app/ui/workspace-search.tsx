"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { SearchIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api/client";
import type { SearchResponse, SearchResult } from "@/types/api";

const GROUPS: ReadonlyArray<{ kind: SearchResult["kind"]; label: string }> = [
  { kind: "account", label: "Accounts" },
  { kind: "transaction", label: "Transactions" },
  { kind: "document", label: "Documents" },
  { kind: "holding", label: "Holdings" },
  { kind: "goal", label: "Goals" },
  { kind: "commitment", label: "Commitments" },
  { kind: "register", label: "Register" },
];

type Props = {
  request?: typeof apiFetch;
  navigate?: (path: string) => void;
};

export function WorkspaceSearch({
  request = apiFetch,
  navigate = (path) => window.location.assign(path),
}: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Array<SearchResult>>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [announcement, setAnnouncement] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const requestSequence = useRef(0);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) return;
    const sequence = ++requestSequence.current;

    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      request<SearchResponse>(`/search?q=${encodeURIComponent(trimmed)}&limit=5`, {
        signal: controller.signal,
      })
        .then((response) => {
          if (sequence !== requestSequence.current) return;
          setResults(response.results);
          setOpen(true);
          setActive(-1);
          setAnnouncement(
            `${response.results.length} ${response.results.length === 1 ? "result" : "results"}`,
          );
        })
        .catch((error: unknown) => {
          if (sequence !== requestSequence.current || controller.signal.aborted) return;
          setResults([]);
          setOpen(true);
          setAnnouncement("Search is unavailable");
          if (process.env.NODE_ENV === "test") return;
          console.error(error);
        });
    }, 250);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, request]);

  function changeQuery(value: string) {
    setQuery(value);
    if (value.trim().length >= 2) return;
    requestSequence.current += 1;
    setResults([]);
    setOpen(false);
    setActive(-1);
    setAnnouncement("");
  }

  function clear() {
    requestSequence.current += 1;
    setQuery("");
    setResults([]);
    setOpen(false);
    setActive(-1);
    setAnnouncement("");
    inputRef.current?.focus();
  }

  function choose(result: SearchResult) {
    setOpen(false);
    navigate(result.route);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      clear();
      return;
    }
    if (!open || results.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((value) => (value + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((value) => (value <= 0 ? results.length - 1 : value - 1));
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      choose(results[active]);
    }
  }

  return (
    <div className="workspace-search">
      <SearchIcon aria-hidden="true" className="workspace-search-icon" />
      <Input
        aria-activedescendant={active >= 0 ? `workspace-search-result-${active}` : undefined}
        aria-autocomplete="list"
        aria-controls="workspace-search-results"
        aria-expanded={open}
        aria-label="Search OneLedger"
        autoComplete="off"
        onChange={(event) => changeQuery(event.currentTarget.value)}
        onKeyDown={onKeyDown}
        placeholder="Search accounts, transactions…"
        ref={inputRef}
        role="combobox"
        type="search"
        value={query}
      />
      {query && (
        <Button aria-label="Clear search" className="workspace-search-clear" onClick={clear} size="icon-sm" type="button" variant="ghost">
          <XIcon aria-hidden="true" data-icon="inline-start" />
        </Button>
      )}
      <p aria-live="polite" className="sr-only">{announcement}</p>
      {open && (
        <div className="workspace-search-popover" id="workspace-search-results" role="listbox" aria-label="Search results">
          {results.length === 0 ? (
            <p className="workspace-search-empty">No matching workspace records.</p>
          ) : GROUPS.map((group) => {
            const grouped = results
              .map((result, index) => ({ result, index }))
              .filter(({ result }) => result.kind === group.kind);
            if (grouped.length === 0) return null;
            return (
              <div aria-label={group.label} className="workspace-search-group" key={group.kind} role="group">
                <p className="workspace-search-group-label">{group.label}</p>
                {grouped.map(({ result, index }) => (
                  <button
                    aria-selected={active === index}
                    className="workspace-search-result"
                    id={`workspace-search-result-${index}`}
                    key={`${result.kind}-${result.resourceId}`}
                    onClick={() => choose(result)}
                    onMouseEnter={() => setActive(index)}
                    role="option"
                    type="button"
                  >
                    <span className="workspace-search-primary">{result.primaryLabel}</span>
                    {result.secondaryLabel && <span className="workspace-search-secondary">{result.secondaryLabel}</span>}
                  </button>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
