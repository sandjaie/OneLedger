import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { proposedPattern } from "@/app/transactions/classify-prompt";

type Vector = { description: string; pattern: string };

const vectors: Array<Vector> = JSON.parse(
  readFileSync(`${process.cwd()}/../proposed-pattern-vectors.json`, "utf8"),
);

describe("proposedPattern", () => {
  it("agrees with the Python twin on every shared vector", () => {
    for (const vector of vectors) {
      expect(proposedPattern(vector.description)).toBe(vector.pattern);
    }
  });
});
