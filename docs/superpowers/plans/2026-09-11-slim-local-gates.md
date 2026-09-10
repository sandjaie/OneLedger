# Slim local gates implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ordinary commits fast without removing any financial, database, type, test, or review protection from the delivery cycle.

**Architecture:** The commit stage checks only staged-file hazards and fast syntax/style rules. Whole-project type systems, Markdown lint, tests, database invariants, generated contracts, and the diff budget run at pre-push and in their existing verification/CI gates. A clean task review is the branch review for a one-task branch; only code added by a fix gets a confirmation review.

**Tech stack:** pre-commit, Bash, Ruff, ESLint, pytest, Vitest, mypy, TypeScript, Postgres/Flyway.

**Spec:** [`docs/development-cycle.md`](../../development-cycle.md) and owner direction on 11 September 2026.

## Global constraints

- Workspace: `/Users/sandjaieravi/dev/OneLedger`; branch: `chore/slim-local-gates`; base and PR target: `develop`.
- No test, assertion, financial invariant, secret scan, real-data guard, authorization gate, or database isolation rule is weakened.
- The commit stage must not fetch a checker from the network.
- Expensive checks move to pre-push; they are not deleted.
- One implementation writer, TDD, active checkout, and no worktree.

---

### Task 1: Make commit checks staged-file-fast

**Files:**

- Modify: `.pre-commit-config.yaml`
- Modify: `scripts/hooks/api.sh`
- Modify: `scripts/hooks/web.sh`
- Create: `api/tests/test_local_gate_contract.py`
- Modify: `docs/development-cycle.md`
- Modify: `docs/git-workflow.md`
- Modify: `docs/superpowers/progress.md`

**Interfaces:**

- Consumes: pre-commit hook stages and repository-relative filenames supplied by pre-commit.
- Produces: `scripts/hooks/api.sh <tool> [args/files...]` and `scripts/hooks/web.sh lint [files...]`, translating `api/` and `web/` prefixes after changing directory.

- [ ] **Step 1: Add failing contract tests**

  Parse `.pre-commit-config.yaml` and assert that commit-stage Ruff/format/ESLint receive filenames; mypy, both TypeScript compilers, and Markdown lint are pre-push; the full pytest, Vitest, API-type, database and diff-size hooks remain pre-push. Exercise the wrappers from a temporary tool shim and assert repository-relative staged paths become service-relative paths.

- [ ] **Step 2: Verify RED**

  Run `UV_CACHE_DIR=/private/tmp/oneledger-uv-cache uv run --project api --frozen pytest -q api/tests/test_local_gate_contract.py`. Expected: failures showing whole-project commit hooks and missing filename forwarding.

- [ ] **Step 3: Implement the minimal hook split**

  Make Ruff/format/ESLint file-scoped, forward translated filenames through the existing wrappers, and move mypy, TypeScript compatibility/type checks, and Markdown lint to pre-push. Retain every existing full pre-push gate.

- [ ] **Step 4: Document the non-duplicative review rule**

  State that a clean task review covers a one-task branch and is not repeated over the same range. A fix receives one scoped confirmation; a multi-task branch receives one branch review only for combined integration risk.

- [ ] **Step 5: Verify GREEN and full gate preservation**

  Run the focused contract test, commit-stage hooks on all files, pre-push hook inventory assertions, docs checks, API lint/types/tests, and applicable web checks. The production database and working ledger remain untouched.

- [ ] **Step 6: Commit, push, PR, and merge**

  Commit as `chore(ci): local commit gates are staged-file fast`, push through the retained pre-push suite, open an unprefixed repository-wide PR to `develop`, and merge after the single task review/verification record is clean.
