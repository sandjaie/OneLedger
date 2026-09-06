import { execFileSync } from "node:child_process";
import {
  closeSync,
  mkdtempSync,
  openSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

const mode = process.argv[2];
if (!["write", "check"].includes(mode)) {
  throw new Error("usage: node scripts/generate-api-types.mjs write|check");
}

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(webRoot, "..");
const committed = join(webRoot, "types", "api");
const scratch = mkdtempSync(join(tmpdir(), "oneledger-api-types-"));
const schemaPath = join(scratch, "openapi.json");
const generated = mode === "write" ? committed : join(scratch, "api");

function run(command, args, options = {}) {
  execFileSync(command, args, {
    cwd: options.cwd ?? repoRoot,
    stdio: options.stdio ?? "inherit",
  });
}

try {
  const schemaFd = openSync(schemaPath, "w");
  try {
    run("uv", ["run", "--project", "api", "python", "scripts/openapi.py"], {
      stdio: ["ignore", schemaFd, "inherit"],
    });
  } finally {
    closeSync(schemaFd);
  }

  run(
    join(webRoot, "node_modules", ".bin", "openapi-ts"),
    [
      "-i",
      schemaPath,
      "-o",
      generated,
      "-p",
      "@hey-api/typescript",
      "--no-log-file",
      "--silent",
    ],
    { cwd: webRoot },
  );

  if (mode === "check") {
    try {
      run("diff", ["-ru", committed, generated], { stdio: "pipe" });
    } catch (error) {
      const stdout = error.stdout?.toString() ?? "";
      writeFileSync(
        process.stderr.fd,
        `Generated API types are stale. Run npm run api:types.\n${stdout}`,
      );
      process.exitCode = 1;
    }
  }
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
