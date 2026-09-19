// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DecisionBand } from "@/app/ui/decision-band";
import { MetricStrip } from "@/app/ui/metric-strip";
import { PageHeader } from "@/app/ui/page-header";
import { Button } from "@/components/ui/button";

describe("editorial page grammar", () => {
  afterEach(() => cleanup());

  it("keeps one visible page title and a keyboard-reachable primary action", () => {
    const longTitle = "A deliberately long synthetic household position label that must stay inside its page header";
    render(
      <PageHeader
        eyebrow="Your money"
        title={longTitle}
        description="Exact evidence and the next owner decision."
        primaryAction={<Button type="button">Review evidence</Button>}
      />,
    );

    expect(screen.getByRole("heading", { level: 1, name: longTitle })).toBeTruthy();
    const action = screen.getByRole("button", { name: "Review evidence" });
    action.focus();
    expect(document.activeElement).toBe(action);
  });

  it("labels a real decision and preserves its selected semantic tone", () => {
    render(
      <DecisionBand
        tone="attention"
        title="Evidence needs review"
        summary="One synthetic item is unresolved."
        action={<Button type="button">Review now</Button>}
      />,
    );

    const region = screen.getByRole("region", { name: "Evidence needs review" });
    expect(region.getAttribute("data-tone")).toBe("attention");
    expect(within(region).getByText("One synthetic item is unresolved.")).toBeTruthy();
    expect(within(region).getByRole("button", { name: "Review now" })).toBeTruthy();
  });

  it("renders exact metric strings without deriving replacement values", () => {
    render(
      <MetricStrip
        label="Position summary"
        items={[
          { label: "Available", value: "-1234567890.1234 XTS", detail: "Server string" },
          { label: "Coverage", value: "Unavailable" },
        ]}
      />,
    );

    const region = screen.getByRole("region", { name: "Position summary" });
    expect(within(region).getByText("-1234567890.1234 XTS")).toBeTruthy();
    expect(within(region).getByText("Unavailable")).toBeTruthy();
  });
});
