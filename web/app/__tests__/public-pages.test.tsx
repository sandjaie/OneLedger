// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import LoginPage from "@/app/login/page";
import UnauthorizedPage from "@/app/unauthorized/page";

describe("public authentication pages", () => {
  afterEach(() => document.body.replaceChildren());

  it("starts sign-in as a top-level API navigation and only shows known failure text", async () => {
    const page = await LoginPage({
      searchParams: Promise.resolve({ reason: "<img src=x onerror=alert(1)>" }),
    });
    render(page);
    const signIn = screen.getByRole("link", { name: "Continue with Google" });
    expect(signIn.getAttribute("href")).toBe("http://localhost:8000/auth/login");
    expect(document.body.textContent).not.toContain("<img");
  });

  it("explains an owner rejection without exposing service details", () => {
    render(<UnauthorizedPage />);
    expect(screen.getByRole("heading", { name: "This account is not authorized" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sign in again" })).toBeTruthy();
  });
});
