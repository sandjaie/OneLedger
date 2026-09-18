# Phase 4.7 — owner workspace experience

## Authority and outcome

This is the single design specification for OneLedger's workspace experience refresh. It
consolidates the earlier [workspace refresh](2026-09-17-workspace-experience-refresh-design.md)
and [decision-first workspace](2026-09-17-decision-first-workspace-design.md) designs. Those
documents and their dispatched plans remain historical evidence; this specification owns the
remaining visual and interaction decisions. Task 1's implemented page grammar, `SectionNav`, and
`RowActions` are the baseline, not work to redo.

The owner should experience one coherent, visually confident financial operating desk across
every existing sidebar destination. In the first viewport, each screen makes it easy to answer:
what is true, what needs attention, and what can I do next? Repeated records are scannable;
details, provenance, and less-used actions remain easy to find without creating walls of text.
The checked-in mockups are references, not pixel or topology requirements. The owner will assess
the finished experience after the planned screen branches and cross-screen audit are complete.

This is Phase 4.7, pulled forward after Phase 4.3 at the owner's request. Existing Phase 4.4–4.6
identifiers retain their meanings. Phase 4.7 neither changes their financial scope nor makes an
uncompleted optional outcome a prerequisite for this UI work.

## Visual direction

Keep OneLedger's established cool canvas `#F1F5F3`, white surface `#FFFFFF`, deep ink `#172A24`,
action green `#176B52`, secondary slate `#52665D`, attention amber `#9A5C1B`, and established
danger red. IBM Plex Sans carries controls and body copy; Plex Serif gives a restrained page-title
contrast; Plex Mono is for genuine codes. Amount columns use tabular figures and align for
comparison. These are shared design tokens, not page-specific color inventions.

The signature is an **owner decision band**: one visually distinct, calm focus area that frames
a real status and its next action. It earns its prominence through evidence and task priority,
not a fabricated score, decorative chart, or giant number. The surrounding records use tighter
rows, purposeful dividers, stronger label/value alignment, and selective surface elevation.
This should feel more tactile and interactive than the current subtle treatment, without turning
every paragraph into an identical card. Long explanations are shortened or put in a named
disclosure beside the fact they explain. Typography, whitespace, contrast, and clear interaction
states carry the identity; gradients and repeated all-caps eyebrows do not.

Every interactive element has distinct rest, hover, focus, selected, expanded, disabled, loading,
success, and error treatment where applicable. Motion is brief and tied to an action; reduced
motion removes nonessential transitions. The design is evaluated on real screens, not on a
standalone component gallery.

## Shared structure and content rules

- Preserve the persistent grouped desktop rail, compact dismissible mobile navigation, skip
  link, and top bar's reporting-currency and owner controls. The active destination remains
  obvious while the page scrolls. The page grid aligns headings, filters, records, and actions.
- A page starts with a title, at most one useful purpose sentence, and one context-specific
  primary action. Show a factual status or next-action strip only when a real warning or useful
  next step exists. Do not make loading look like an empty result.
- Give one focus surface to the next decision or noteworthy state. Put repeated items in compact
  rows with a clear status, key facts, and a review affordance. Keep infrequent operations in a
  labelled secondary disclosure. Use `RowActions` and `SectionNav` from Task 1 where their
  contracts fit; do not replace them merely to use a library.
- Use plain, action-specific language. Preserve all financial caveats, confidence, coverage,
  provenance, confirmation, and recovery information, but place each beside the affected fact
  or behind an explicit explanation. Do not invent dates, totals, valuations, prices, returns,
  cost basis, or reassuring clean states.
- Wide data may scroll in a named region. The page itself does not scroll horizontally at 360px,
  588px, or desktop widths. Empty and error states give a real next step when one exists.

## Screen outcomes

### Settings

Open `/settings` on General, with editable household fields and one Save action visible before
diagnostics. Use General, Data health, Categories, Rules, and Exchange rates as one-at-a-time
sections. Keep all sections mounted so changing sections cannot discard an unfinished form.
Selection is reflected in the URL; direct hash links, Back/Forward, keyboard use, and focus on
the requested heading work. A genuine data-health finding may appear above the selector and
links to its full report; loading or unavailable data never claims health. Existing validation,
fixed reporting-pair behavior, and links into Rules, Data health, and Exchange rates survive.

### Subscriptions and Recurring

Lead with the next real renewal or dated recurring payment, or a truthful absence of one. Show
Current, Upcoming, and Past subscriptions and Active and Ended recurring payments in compact
rows. A row gives name, status, amount/cadence, and next or last date before payment source and
provenance. Keep subscription detail routes and recurring inline detail, lifecycle controls,
corrections, and exact destructive confirmations. An ended record must never appear active
because a lifecycle selector says Active.

### Documents and Classify

Documents displays type, account, period, import state, and next relevant action as readable
records instead of a squeezed table. Type and Account filters remain visible; advanced filters
can collapse at narrow widths. Provenance and maintenance actions stay reachable. Classify uses
the same decision/record hierarchy while retaining evidence, confidence, reversible review,
and clear recovery paths. Raw enum identifiers are not owner-facing labels.

### Investments and investment import

Separate portfolio status, holdings, valuation confidence, review work, and analysis. When
holdings are absent, show the two real entry routes and one concise explanation of limitations.
Do not imply value for unvalued holdings. Import retains its explicit review and commit stages;
coverage exclusions and methodology remain available without occupying the first screen.

### Remaining destinations

Overview and Accounts keep their useful summaries and adopt the common hierarchy. Transactions
and Spending use compact filters and contained dense data. Salary, Plans, Coverage, and Wealth
surface the next real task ahead of long methodology or history. Existing detail routes inherit
the visual system. Every sidebar destination retains its current owner operation, linkable
action, and recovery path; no decorative metric or chart is added without a truthful source.

## Selective shadcn/ui adoption

Task 1's custom page grammar, `SectionNav`, and `RowActions` remain. Task 2 pilots shadcn/ui's
grouped fields and inputs in Settings General, plus an Alert for a genuine data-health finding.
Task 3 uses an AlertDialog for destructive recurring/subscription actions, preserving each
existing confirmation sentence and the refusal to send a request when the owner cancels. Later
tasks may add a complex filter or selection control only where a native control is insufficient.
Do not replace working markup or wrap every record in a Card merely to use the library.

The web project already uses Tailwind CSS 4 but has no shadcn project configuration. Initialize
once in `web/` when the first chosen component is needed; inspect every generated configuration,
CSS, package, and component diff. Map its semantic tokens to OneLedger's palette and typography.
Do not apply a preset that overwrites the current theme, fonts, or Task 1 CSS. Keep components
as reviewed source in the repository, use the project's actual aliases and package runner, and
avoid unused registry dependencies. A component with client-side state uses the appropriate
client boundary. Its keyboard behavior, focus return, labels, validation, and narrow-screen
layout are tested in the consuming workflow, not assumed from the library.

## Safety, verification, and owner acceptance

This phase is web-only. It adds no financial endpoint, schema, calculation, or authorization
exception. Screens continue using Workspace's authenticated request wrapper. Exact monetary
strings remain display-only in TypeScript. A discovered backend gap is recorded for a separate
contract and the owner/RLS/Decimal test floor before implementation.

Each screen branch runs affected component tests, web lint/types, and live keyboard and viewport
checks; the final web-only verifier runs after local review on the intended commit. Inspect every
sidebar destination and relevant detail route at 360px, 588px, and desktop in the cross-screen
audit. Use synthetic fixtures; never commit real ledger screenshots, values, filenames, or full
identifiers. The owner checks the finished application after the plan is implemented.

Acceptance means the owner can identify state and next action in the first viewport, navigate
without losing the rail or unsaved Settings edits, scan dense records, reach all existing actions
and evidence, and understand empty/error/loading states. The pages look unmistakably related,
have visible and responsive interactions, and preserve every existing financial meaning.
