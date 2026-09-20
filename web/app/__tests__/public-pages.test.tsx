// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const redirectMock = vi.hoisted(() =>
  vi.fn((destination: string): never => {
    throw new Error(`NEXT_REDIRECT:${destination}`);
  }),
);

vi.mock("next/navigation", () => ({ redirect: redirectMock }));

import LoginPage from "@/app/login/page";
import UnauthorizedPage from "@/app/unauthorized/page";

describe("public authentication pages", () => {
  afterEach(() => {
    document.body.replaceChildren();
    redirectMock.mockClear();
  });

  it("sends an initial visit directly to the configured owner login", async () => {
    await expect(
      LoginPage({ searchParams: Promise.resolve({}) }),
    ).rejects.toThrow("NEXT_REDIRECT:http://localhost:8000/auth/login");
  });

  it("shows a retry instead of redirecting an authentication failure forever", async () => {
    const page = await LoginPage({
      searchParams: Promise.resolve({ reason: "authentication_failed" }),
    });
    render(page);
    const retry = screen.getByRole("link", { name: "Try again" });
    expect(retry.getAttribute("href")).toBe("http://localhost:8000/auth/login");
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByRole("button", { name: /sign out/i })).toBeNull();
    expect(screen.queryByRole("group", { name: /reporting currency/i })).toBeNull();
    expect(redirectMock).not.toHaveBeenCalled();
  });

  it("explains an owner rejection without exposing service details", () => {
    render(<UnauthorizedPage />);
    expect(screen.getByRole("heading", { name: "This account is not authorized" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sign in again" })).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByRole("button", { name: /sign out/i })).toBeNull();
    expect(screen.queryByRole("group", { name: /reporting currency/i })).toBeNull();
  });
});
