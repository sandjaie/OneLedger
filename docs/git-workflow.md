# Git workflow

The single reference for branching, integration and release. `CLAUDE.md`, `AGENTS.md`, plans
and specs point here instead of restating the model. When another document disagrees with this
file about a branch, a base, a merge or a release, this file wins and the other document gets
fixed — do not fork the model into a second place.

The one division: **the review gates belong to
[docs/development-cycle.md](development-cycle.md)** — when each runs, who runs it, what it
produces, and that it only ever reports. That file wins on a gate, this one on a branch.

## Branch model

| Branch | Created from | Merges into | Purpose |
|---|---|---|---|
| `main` | — | — | Released code only. Receives merges from `release/*`, nothing else. |
| `develop` | `main` | `main`, via `release/*` | The standing integration branch. Every feature PR targets it. |
| `<type>/<name>` | `develop` | `develop` | One slice of a phase (or a standalone fix), delivered as one PR. A phase spans several such branches; its plan lands with the first. |
| `release/<version>` | `develop` | `main` | Cut for a release; carries the changelog and release notes; produces the tag. |

`<type>` is one of `feature/`, `fix/`, `chore/`, `docs/` — the complete set of branch-name
prefixes. `<name>` names the work the branch carries: `feature/p1-1a-parse-pipeline`,
`fix/auth-database-errors`.

## Flow — the delivery cycle

Every delivery follows one cycle: **plan → implement → review locally → merge.** External
(Codex) review is disabled for this repository — owner decision, 8 September 2026. The review
happens here, before the push, and the PR is the record of it, not a second audit.

**[docs/development-cycle.md](development-cycle.md) specifies the gates** — when each runs and
what it produces. This file says only where the branches go:

1. The phase's dated execution-map plan goes in `docs/superpowers/plans/` and lands with the
   phase's first branch. `/oneledger-plan-review` runs once on a newly written or materially
   changed plan, before its first dispatch — that is where an oversized slice gets split.
2. Each slice — or a coherent part of one — gets its own branch `<type>/<name>` from `develop`,
   implemented TDD in the plan's order. The plan declares `Workspace`, `Branch` and `Base and
   PR target` per slice before implementation starts.
3. Before the **first** push: `/verify`, then one `/oneledger-code-review` round, then the fixes
   it asks for and `/verify` again. Nothing reaches `origin` unreviewed, and the push hooks
   enforce the suite and the diff budget below. A clean task review is the branch review when the
   branch contains that one task; never repeat a review over the same range. Re-review only a fix
   range, or the integration surface of a branch that genuinely contains several reviewed tasks.
4. **One PR to `develop` per branch** from `.github/PULL_REQUEST_TEMPLATE.md`, with honest
   verification evidence, and **titled so the list says which slice it is** (below).
5. **The orchestrator merges** once the local gates are green and the PR's evidence is honestly
   recorded, then deletes the branch and starts the next slice. Two cases stay the owner's: a PR
   whose Verification table records a required check as `Not run`, and anything targeting
   `main`.
6. At a phase boundary, `/oneledger-code-hygiene` sweeps what accumulated. Its findings ship as
   their own `chore/` branch, never folded into a feature PR.
7. **Release.** Branch `release/<version>` from `develop`. On that branch: write the changelog
   entry and the release notes for what shipped since the last release. On owner request, merge
   to `main`, tag the merge commit (`git tag -a v<version>`) and publish the GitHub release
   from that tag with those notes.

## Pull request titles

A phase runs to many PRs, so the title carries the phase and the slice in a bracketed prefix
that scans down a list without opening anything:

```text
[P1.3] feat(api): a remittance and its arrival link as one transfer
```

The prefix is the phase plan's own numbering — phase, then slice — and the rest is the commit
convention unchanged: `type(scope): a lowercase sentence stating what is now true`.

- A PR delivering part of a numbered slice takes the pair: `[P1.3]`. A slice shipped as several
  PRs repeats the same prefix; the sentence tells them apart.
- One scoped to a phase but no single slice takes the phase alone: `[P1]`.
- One that is repo-wide — the workflow, the gates, tooling — takes no prefix, because it belongs
  to no slice and a prefix would claim otherwise.

Merged history under the old `[P1/M2/T4c]` scheme keeps its titles; the record is not renamed.

## Commits

`type(scope): a lowercase sentence stating what is now true`. Types `feat fix docs ci chore
refactor test`; scopes `db api web ci docs plan`. Subjects are declarative, not imperative —
"the ADR folder now lists only decisions in force", not "update ADR folder". The body says what
was wrong and why this is right. `git log` is the style guide.

## Rules

- Commit hooks stay fast and staged-file-scoped: secrets, file/config hygiene, real-data exclusion,
  documentation structure, identifiers, Ruff/format and ESLint. Whole-project types, Markdown
  lint, tests, generated contracts, SQL invariants and the diff budget run at pre-push.
- Nothing commits straight to `main`; it moves only through `release/*` on owner request.
  `develop` moves by PR (owner-directed integrations excepted).
- One coherent slice (or one fix) per branch and PR, inside the **diff budget**:
  **800 lines of added production code, 1,600 added production + test lines**, with a hard stop
  at 1,200 / 2,400 that refuses the push. Documentation, configuration and generated files do
  not count. `scripts/check-diff-size.sh` measures the branch against `develop` and runs on
  push; it prints the largest files so a split is obvious. Past the hard stop,
  `ONELEDGER_SIZE_OVERRIDE="reason"` allows the push and the same reason goes in the PR's Notes
  — an indivisible migration is the case it exists for.
- The budget is looser than it was when every PR bought external review rounds, but it is not
  gone: a branch is still read in full by its local review, and tests in this tree run 1.5–1.8×
  their production code, so the total is the limit that binds first. A slice that outgrows the
  hard stop ships as more than one PR under the same `[P1.n]` prefix.
- Every plan declares its exact `Workspace`, `Branch` and `Base and PR target` before
  implementation starts, using values consistent with this file.
- Delete branches after merge; the PR record is the history.
