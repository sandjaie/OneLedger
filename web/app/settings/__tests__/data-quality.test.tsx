// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DataQuality } from "@/app/settings/data-quality";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { DoctorReport } from "@/types/api";

const report: DoctorReport = {
  sections: [
    {
      title: "Backups",
      findings: [
        {
          level: "warning",
          message:
            "The newest backup is 3 days old (2026-09-07 11:47 UTC), and the ledger has 4,184 " +
            "transactions it does not hold.",
        },
      ],
    },
  ],
};

afterEach(cleanup);

describe("Settings › Data Quality", () => {
  it("shows each section's findings, marked by level", async () => {
    const request = vi.fn().mockResolvedValue(report) as unknown as typeof apiFetch;
    render(<DataQuality request={request} />);

    expect(await screen.findByText("Backups")).toBeTruthy();
    const finding = screen.getByText(/4,184 transactions it does not hold/);
    expect(finding.textContent).toContain("⚠");
    expect(request).toHaveBeenCalledWith("/doctor");
  });

  it("says so when the report cannot be loaded", async () => {
    const request = vi
      .fn()
      .mockRejectedValue(new ApiError(500, "The report failed.")) as unknown as typeof apiFetch;
    render(<DataQuality request={request} />);

    expect((await screen.findByRole("alert")).textContent).toBe("The report failed.");
  });
});
