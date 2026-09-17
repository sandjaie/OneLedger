# Decision-first owner workspace implementation plan

**Goal:** Give every owner-workspace destination one shared decision-first hierarchy — what is
true, what needs attention, what can I do next — without changing a financial contract, a
calculation, an authorization boundary, or the source of any displayed fact.

**Architecture:** Re-bind the existing CSS token layer to the approved palette and add one shared
page-grammar class layer in `web/app/globals.css`, deleting the duplicate per-screen rule families
it replaces. Presentation stays CSS-only; React components are added only where behaviour is
shared, which is two pieces — a labelled secondary-action disclosure and a keyboard-operable
section selector. The five screens the live review named get real information-architecture work on
their own branches, then the remaining destinations inherit the same grammar. No route, endpoint,
schema or response shape changes.

**Spec:** [Decision-first owner workspace](../specs/2026-09-17-decision-first-workspace-design.md)

**Workspace:** `/Users/sandjaieravi/dev/OneLedger` — the active checkout. Do not create worktrees.

**Base and PR target:** every branch starts from current `develop` and targets `develop`. `develop`
is `f48f8950371f24fb5423e01dbc895afa4c044ad0` plus the merge of
`feature/ui-decision-first-workspace`, which carries the approved design and this plan. Re-read
`git rev-parse origin/develop` before starting each branch; branches are sequential, one writer at
a time.

## Global constraints

Every task below inherits these. They are not repeated per task.

- **Web-only.** No change to `api/`, `db/`, generated API types, or `docs/design/`. If a task
  appears to need a backend field, stop and record it — a backend gap needs its own contract and
  the owner/RLS/`Decimal` test floor first.
- **Money.** Amounts arrive as server-formatted strings and TypeScript only formats them. No
  arithmetic, no `parseFloat`/`Number()`/`toFixed` on a money value, no client-side totalling. A
  survey of `web/app` and `web/lib` found no existing violation; keep it that way.
- **Never imply an unestablished fact.** Where the server withholds a value, the screen says so
  ("Unavailable", "—", or the server's own sentence). Never substitute `0`, never infer a price,
  return, cost basis, valuation or next date the response does not carry.
- **Authorization.** Screens call the `request` prop, which is Workspace's `wrappedRequest`
  (`web/app/workspace.tsx:425-436`) and routes 401 to `/login` and 403 to `/unauthorized`. Never
  call `apiFetch` directly from a screen and never bypass that wrapper.
- **The Workspace fetch sequence is pinned positionally.** `web/app/__tests__/workspace.test.tsx`
  mocks `request` as a positional queue (`requestThat`, line 158) with an `outOfBand()` whitelist
  (lines 61-146). Adding, removing or reordering any fetch in the `workspace.tsx` effect chain
  mis-feeds every later screen in that file. Prefer adding no fetch at all; if one is unavoidable,
  add its path to `outOfBand()` in the same commit.
- **The shell is preserved, not redesigned.** The persistent grouped rail, the compact mobile
  navigation and the top bar's reporting-currency and owner controls stay. `workspace.test.tsx`
  pins the header carrying `.app-toolbar`, exactly one `banner`, `#workspace-content` with
  `tabindex="-1"`, the skip link targeting it, the anchor following the nav in document order, the
  `is-open` nav modifier, the "Open workspace menu"/"Close workspace menu" names with
  `aria-expanded`, `role="group"` named "Reporting currency", `role="navigation"` named
  "Workspace", and the nav link names with `aria-current="page"`. Keep all of it.
- **Privacy.** No real ledger value, filename, complete identifier, statement content or real
  screenshot enters code, fixtures, commits, logs, errors, PR text or agent messages. Test data is
  synthetic.
- **Tests are adapted, never weakened.** A deliberate IA change may require a test to select a
  view or open a disclosure before querying. Rewriting the query is correct; deleting the
  assertion, loosening an exact string to a regex, or dropping a confirmation check is not.
- **Accessibility, every branch.** Semantic headings in order; labelled navigation and regions;
  native controls where possible; keyboard-operable selectors and disclosures; visible focus that
  moves predictably after navigation, disclosure and action; state never by colour alone;
  `prefers-reduced-motion` respected and motion only in response to an action. No horizontal page
  scroll at 360px, 588px or desktop; genuinely wide data scrolls inside a named region.
- **Diff budget** (`docs/git-workflow.md`): 1,500 added production lines and 3,000 production plus
  test lines per branch; `scripts/check-diff-size.sh` refuses a push above 2,000 / 4,000.
  Documentation and configuration do not count. Deleting a duplicate CSS family buys headroom.
  If a branch nears the hard stop, finish its coherent part and name the remainder as the next
  branch in the same PR.
- **Cycle, every branch** (`docs/development-cycle.md`): implement with tests for major behaviour →
  run affected suites → local commit → one read-only review → one consolidated repair →
  `scripts/verify.sh` once on the clean head → first push → one PR to `develop`. No database test
  server is needed; this work is web-only.
- **Validation, every branch:** `cd web && npm run lint`, `npm run typecheck`, `npx vitest run`
  over the affected suites, and the live app at 360px, 588px and desktop. `npm run build` runs
  inside `scripts/verify.sh`; do not run it separately.

## Work sequence

Task 1 is a hard dependency for Tasks 2-7. Tasks 2-7 are independent of each other and may be
delivered in any order, one at a time. Task 8 runs last.

---

### Task 1 — Shared page grammar

**Outcome.** The approved palette and one shared grammar exist as CSS, and the per-screen rule
families that duplicate each other are gone. Two behavioural components exist for later tasks to
consume. Three low-risk screens adopt the grammar as proof it works before five branches depend on
it.

**Branch.** `feature/ui-page-grammar`

**Dependencies.** None.

**Files.**
- Modify: `web/app/globals.css` (2,215 lines — the whole token and class layer)
- Create: `web/app/ui/row-actions.tsx`, `web/app/ui/section-nav.tsx`
- Create: `web/app/ui/__tests__/row-actions.test.tsx`, `web/app/ui/__tests__/section-nav.test.tsx`
- Modify: `web/app/coverage/coverage-screen.tsx`, `web/app/wealth/wealth-screen.tsx`,
  `web/app/spending/spending-screen.tsx` (the three adoption proofs)
- Modify: the affected suites under `web/app/coverage/__tests__/`, `web/app/wealth/__tests__/`,
  `web/app/spending/__tests__/`

**Contracts.**
- **Palette by re-binding, not by a parallel scheme.** `:root` in `globals.css:3-16` already
  defines `--paper`, `--surface`, `--ink`, `--muted`, `--rule`, `--blue`, `--green`, `--danger`.
  Re-point them: `--paper: #F1F5F3` (cool canvas), `--surface: #FFFFFF`, `--ink: #172A24` (deep
  ink), `--blue: #176B52` (action green — the name stays so no call site churns), `--muted:
  #52665D` (secondary slate). Add `--attention: #9A5C1B` (attention amber). `--danger` keeps the
  established red. Do not introduce a second naming scheme for the same colours.
- **Add only the tokens the grid needs:** a page maximum reading width, a gutter, and a small
  stack scale. There is no spacing or type-scale token layer today; every size is a literal
  `clamp()`/px/rem. Add the few used by the new classes and leave the rest alone.
- **Fonts are already correct.** `web/app/layout.tsx` loads IBM Plex Sans, Serif and Mono as
  `--font-plex-*`. Serif is reserved for a single page title or long-form analysis; monospace is
  for genuine codes only, not every small caption. Do not add a font.
- **Typography rules from the spec.** Numeric data uses tabular figures
  (`font-variant-numeric: tabular-nums`) and is right-aligned wherever columns compare. Retire the
  repeated all-caps `.eyebrow` label that currently opens most screens — the page head's title
  carries the page, and a second stacked label above every title is the "wall of copy" this
  redesign removes. Remove decorative gradients. Hierarchy comes from spacing, alignment, type and
  a small amount of colour, not from card borders and buttons on every item.
- **New shared classes**, each with one responsibility: `.page-head` (title, at most one purpose
  sentence, one primary action, aligned to the page grid); `.page-status` (the factual
  status/next-action strip, rendered only when a real warning or next step exists); `.focus-panel`
  (the single focus surface for the next decision); `.record-list` and `.record-row` (compact
  repeatable record: name, status, key facts, route to detail); `.record-facts` (the row's
  secondary metadata region); `.chip` with state modifiers including an amber attention state and a
  neutral ended state; `.empty-state`; `.loading-frame` (keeps the frame stable and must not read
  as an empty result); `.filter-bar` with a narrow-screen advanced region; `.section-head`;
  `.section-nav`. Control hierarchy stays the existing `.primary-action` / `.text-action` pair plus
  a destructive modifier.
- **Delete what these replace.** `.experience-record` has no rule anywhere and is used only by
  `subscriptions-screen.tsx` and `recurring-screen.tsx` — `.record-row` supersedes it. Fold the
  shared-rule lists at `globals.css:1776-1830` (`.plans-commitment-list`, `.needs-attention-list`,
  `.plans-register-list`, `.plans-goal-list`, `.plans-account-list`, `.plans-currency-list`,
  `.recurring-confirmed-list`, and the second group) into `.record-list`, keeping the old selectors
  only as aliases where a screen this branch does not touch still needs them. Collapse the
  inline-form list at `globals.css:1840` into one shared rule. Collapse `.spending-chart*` and
  `.wealth-chart*`, which are byte-for-byte parallel, into one family with two modifiers.
  `.plans-resources-table` carries a comment admitting it duplicates `.account-table`; share them.
  The near-identical `.text-field`/`.select-field`/`.date-field`/`.month-field`/`.file-field`/
  `.password-field`/`.check-field` rules share one base with per-type deltas.
- **`RowActions`** (`web/app/ui/row-actions.tsx`): a labelled secondary-action disclosure. Props:
  a required visible label naming what is inside, children, and an optional id. Renders a real
  `<button>` with `aria-expanded` and `aria-controls`, content in a region referenced by that id,
  closed by default, toggled by click and by keyboard, and focus returning to the trigger on close.
  It changes no action's semantics — it only relocates the controls placed inside it.
- **`SectionNav`** (`web/app/ui/section-nav.tsx`): a keyboard-operable section selector. Props: an
  ordered list of `{ id, label }`, the active id, and an `onSelect` callback. Renders a labelled
  `<nav>` of real controls with `aria-current` on the active one. It owns no URL behaviour — Task 2
  supplies that.
- Presentation stays CSS. Do not wrap page heads, empty states or chips in React components; the
  codebase's shared vocabulary is global class names and 20 screens already hand-roll this markup.

**Invariants and security.** CSS and two presentational components only. No fetch is added, so the
Workspace positional queue is untouched. No money is formatted or computed here.

**Required test behaviour.**
- `RowActions`: closed by default; the trigger exposes its expanded state; content is reachable and
  operable by keyboard; a control placed inside still fires its own handler unchanged; focus
  returns to the trigger on close.
- `SectionNav`: the active section is marked for assistive technology; selection is operable by
  keyboard; `onSelect` fires with the selected id.
- The three adopted screens keep every assertion in their existing suites passing, with queries
  updated only where markup deliberately moved.
- Colour-contrast and reduced-motion behaviour are checked live, not asserted in a unit test.

**Validation.** `cd web && npm run lint && npm run typecheck`, then
`npx vitest run app/ui app/coverage app/wealth app/spending`, then the live app at 360px, 588px and
desktop on Coverage, Wealth and Spending.

**Acceptance.** The palette, grid and grammar exist and are documented by use on three screens; the
duplicate rule families named above are gone rather than merely supplemented; `RowActions` and
`SectionNav` are keyboard-operable; no screen scrolls horizontally at 360px; no fetch order changed.

---

### Task 2 — Settings section architecture

**Outcome.** `/settings` opens on **General** with the household fields and one Save action instead
of a diagnostic paragraph. A section selector exposes General, Data health, Categories, Rules
(including merchant knowledge) and Exchange rates, one visible at a time, with selection reflected
in the URL, every existing deep link preserved, and no pending edit silently discarded.

**Branch.** `feature/ui-settings-sections`

**Dependencies.** Task 1 (`SectionNav`, `.page-head`, `.section-nav`, `.page-status`).

**Files.**
- Create: `web/app/settings/settings-screen.tsx`
- Modify: `web/app/workspace.tsx` — remove the inlined settings block at lines 722-858 and render
  the new screen; move the FX state (`fxDate` 216, `fx` 217, `backfillStart` 219, `backfillEnd`
  220) and the `loadFx` (438) and `runBackfill` handlers with it
- Modify: `web/app/settings/household-form.tsx` (becomes the General section's body),
  `web/app/settings/data-quality.tsx`, `web/app/settings/categories-manager.tsx`,
  `web/app/settings/rules-manager.tsx`, `web/app/settings/merchant-knowledge.tsx`
- Modify: `web/app/review/classification-review-screen.tsx:464`,
  `web/app/investments/import/investment-import-screen.tsx:342`,
  `web/app/wealth/wealth-screen.tsx:32`
- Modify: `web/app/globals.css` (settings-specific rules only)
- Create: `web/app/settings/__tests__/settings-screen.test.tsx`
- Modify: `web/app/__tests__/workspace.test.tsx` (settings tests at 396, 405, 502, 606, 1018)

**Contracts.**
- **Five sections, in this order:** General (household name, display name, tax residency, fixed
  reporting pair, one Save), Data health, Categories, Rules (the rules manager, its analytics and
  suggestions, and merchant knowledge), Exchange rates (the stored-rate inspector and the backfill
  card, both moved out of `workspace.tsx`). General is first — today household is rendered second,
  after data quality.
- **The canonical URL hash is the section's own primary heading id.** There is exactly one hash
  scheme, so the existing anchor-based deep links already fit it and no second slug vocabulary is
  introduced. Canonical: `#household-title` → General, `#data-quality-title` → Data health,
  `#categories-title` → Categories, `#rules-title` → Rules, `#fx-title` → Exchange rates.
- **The id→section map must also admit every secondary id**, so a deep link to a target inside a
  section selects that section and focuses the target: `#merchant-knowledge-title`,
  `#rules-analytics-title`, `#rules-suggestions-title` → Rules; `#fx-backfill-title` → Exchange
  rates. A bare `/settings` with no hash opens General. An unrecognised hash is ignored silently
  and General opens, matching the established ignore-invalid rule.
- **Retarget the two bare links.** `investment-import-screen.tsx:342` links to `/settings` with the
  text "Settings › Data quality" — it becomes `/settings#data-quality-title`.
  `wealth-screen.tsx:32` has `STATUS_HREF.missing_rate = "/settings"` — it becomes
  `/settings#fx-title`. `classification-review-screen.tsx:464` already points at
  `/settings#merchant-knowledge-title` and must keep working, now by selecting Rules and focusing
  the merchant-knowledge heading.
- **Read the hash with the established pattern, then keep listening.** Initial selection is a lazy
  `useState` initializer guarded by `typeof window === "undefined"`, as
  `web/app/accounts/accounts-screen.tsx:65-77` documents for `?valuation=`. Selecting a section
  writes the canonical hash with `history.pushState`, and a `popstate` listener re-reads it, so
  browser Back and Forward move between sections. Both the writing and the listener are net-new —
  nothing in the tree does either today. Replace the ad-hoc scroll-only effect at
  `merchant-knowledge.tsx:91-95` with this shared behaviour.
- **Focus moves predictably.** After a selection, focus moves to the selected section's heading
  (`tabIndex={-1}`). For a deep link naming a secondary id, focus moves to that element instead.
  The existing effect only scrolled and never focused.
- **Keep all five sections mounted and hide the inactive ones with `hidden`.** This is the load-
  bearing decision. It satisfies "only one long section is visible at a time" and "retain mounted
  form state" in one move: a pending edit in the household form's five fields
  (`household-form.tsx:21-27`), the category draft (`categories-manager.tsx:20`), the rule draft
  and its preview (`rules-manager.tsx:492-497`), an in-flight merchant-knowledge row edit
  (`merchant-knowledge.tsx:83-84`) and the FX date inputs all survive a section change with no
  dirty tracking and no warning dialog. It also keeps each section's mount-time fetch firing
  exactly once, in today's order, so the positional queue and `outOfBand()` whitelist in
  `workspace.test.tsx` keep working. Lazy-mounting sections would change that sequence and break
  tests in unrelated places.
- **Data health stays honest.** A concise summary sits above the section selector only when there
  are genuine findings, links to the full report, and never claims a clean state while the
  `/doctor` response is loading or unavailable.
- **Preserve server-side behaviour verbatim:** every current validation, the same-currency refusal,
  the disabled frozen reporting-pair control, each section's own error sentence, and the
  `onSaved`/`onChanged` callbacks that bump Workspace's `attempt` counter.

**Invariants and security.** No endpoint, request body or query string changes. Section state lives
in the URL, not only in component memory. `hidden` content is outside the accessibility tree, which
is the intended behaviour and the reason the workspace settings tests must select a section first.

**Required test behaviour.**
- A bare `/settings` opens General and shows the household fields.
- Each of the five canonical hashes selects its section on direct load.
- Each secondary hash — `#merchant-knowledge-title`, `#rules-analytics-title`,
  `#rules-suggestions-title`, `#fx-backfill-title` — selects the owning section and focuses the
  named target.
- An unrecognised hash opens General without throwing.
- Selecting a section updates the URL; a `popstate` returns to the previously selected section.
- A section is selectable by keyboard and focus lands on the selected section's heading.
- A pending edit in the household form survives a section change and a return.
- Existing workspace settings tests keep their assertions and gain a section selection first: the
  `/doctor` call assertion still holds because the fetch still fires on mount, and the FX
  provenance and `/fx/backfill?start=…&end=…` tests select Exchange rates before querying.
- The three retargeted links resolve to their intended sections.

**Validation.** `cd web && npm run lint && npm run typecheck`, then
`npx vitest run app/settings app/__tests__/workspace.test.tsx app/wealth app/review app/investments`,
then live: direct load of each hash, Back/Forward, keyboard-only selection, and a pending edit kept
across a section change.

**Acceptance.** The first Settings viewport communicates what can be edited and offers one Save;
the four named deep links land on the right section with the right element focused; Back and
Forward work; no pending edit is lost; every current validation and the disabled reporting-pair
behaviour survive.

---

### Task 3 — Subscriptions and Recurring records

**Outcome.** Both screens lead with the next real renewal or a truthful empty state, then a small
number of useful totals. Subscriptions has Current, Upcoming and Past views; Recurring has Active
and Ended. Every row is one compact record, and the infrequent lifecycle and correction actions sit
in a labelled disclosure with their semantics and confirmations unchanged. An ended item no longer
reads as active.

**Branch.** `feature/ui-recurring-records`

**Dependencies.** Task 1 (`RowActions`, `.record-row`, `.focus-panel`, `.chip`).

**Files.**
- Modify: `web/app/subscriptions/subscriptions-screen.tsx`,
  `web/app/subscriptions/subscription-detail.tsx`, `web/app/recurring/recurring-screen.tsx`
- Modify: `web/app/globals.css` (screen rules at 2042-2081; remove `.experience-record` usage)
- Modify: `web/app/subscriptions/__tests__/subscriptions-screen.test.tsx`,
  `web/app/subscriptions/__tests__/subscription-detail.test.tsx`,
  `web/app/recurring/__tests__/recurring-screen.test.tsx`

**Contracts.**
- **Every field used already exists on `RecurringSeriesRow`** — one type serves both screens. Next
  date is `nextExpectedOn` (nullable), last date is `lastSeenOn` (always present), and an ended
  item also has `lastPeriodEndedOn` with `lastEndReason`. `monthlyAmount`, `monthlyAmountBase` and
  `annualAmountBase` exist but are unread today, as are `nextExpectedOn` and `annualAmount` on
  Recurring. Invent nothing.
- **Lead with the real next renewal.** Use `RecurringSummary.upcoming[0]` (`UpcomingRow`:
  `seriesId`, `label`, `nextExpectedOn`, `nativeAmount`, `nativeCurrency`, `payingAccountName`) in
  the focus surface. When it is absent, keep the existing truthful sentence "Nothing is expected to
  renew" rather than an empty tile.
- **Totals shrink from five tiles to three:** Monthly equivalent, Annual spend, Current. Keep the
  "Annual spend" label — a test reads it. `renewingIn30Days` moves into the focus surface's
  sentence and `priceChangesIn90Days` becomes a chip or a line on the Current view; neither figure
  is dropped and neither is recomputed.
- **Views.** Subscriptions: Current, Upcoming, Past. Recurring: Active, Ended, filtered as today to
  `!row.isSubscription`. Views are the shared selector pattern and are keyboard-operable.
- **Record row shows** label, a status chip, amount with cadence, and the next date or — for an
  ended item — the last date with its end reason. **Payment source and provenance move to the
  expansion or the detail screen**: `payingAccountName`/`payingAccountMasked`,
  `paymentChannelName`, `detectionMethod`, `identity`, `periodCount`, `resumed`,
  `priceChangedFrom`/`priceChangedOn`. `needsName` keeps its visible "Needs a name" badge.
- **The ended-item defect.** Render the row's visible status from `row.status`
  (`active`/`overdue`/`ended`) with `STATUS_TEXT` and, when ended, `END_REASON_TEXT` — never from
  `row.lifecycle`. The lifecycle `<select aria-label="Status">`, whose options are only Active,
  Trial and Paused, moves inside `RowActions`. The control keeps its exact semantics and its
  `PATCH /recurring/{id}` `{lifecycle}` call; it simply no longer sits on an ended row's face
  asserting "Active".
- **Primary versus secondary.** A row's one obvious route is to review the item — its detail
  screen. Everything else goes inside `RowActions`, unchanged: `Not a subscription` /
  `A subscription` (`PATCH {isSubscription}`), `Rename` with `Save name`/`Cancel`
  (`PATCH {label}`), the status select, `Cadence` with its custom "Days between charges" 7-730
  input (`PATCH {cadence, customIntervalDays}`), `Cancel` (`POST /recurring/{id}/cancel`),
  `Resume` (`POST /recurring/{id}/resume`), and `Remove` (`POST /recurring/{id}/remove`, no body).
- **Both confirmations survive verbatim**, including their exact sentences: cancel's "Cancel
  {label}? This closes its current billing period." and remove's "Remove {label}? This is not \"not
  a subscription\" — it stops tracking this payment, and it will not come back on its own." Two
  tests spy `window.confirm` and assert no POST on a false answer.
- **Do not change the load sequence.** Subscriptions fires `POST /recurring/refresh` then a
  `Promise.all` of `GET /recurring`, `/recurring/summary`, `/recurring/by-account` — exactly four
  calls, order asserted. Recurring fires `POST /recurring/refresh` then `GET /recurring` — exactly
  two. The detail screen loads `GET /recurring/{seriesId}` and `GET /payment-channels` in parallel.
- **Detail screen.** Keep the eight facts, the `Payment channel` select
  (`PATCH {paymentChannelId}`, `null` when cleared), the billing periods with their charge links to
  `/transactions?dateFrom=…&dateTo=…`, the price history, and the 404 sentence "This subscription
  could not be found." Note that two tests read `getByText("Billing periods").nextElementSibling`
  and the same for "Price history" — inserting a wrapper between a heading and its list breaks
  them, so either keep the heading adjacent to its list or update both queries.
- The shared `CADENCE_TEXT`, `STATUS_TEXT` and `END_REASON_TEXT` maps are exported from
  `recurring-screen.tsx` and imported by both subscription files and the transactions screen. If
  they move to a shared module, update every importer in the same commit.

**Invariants and security.** No endpoint, body or call-count change. No money arithmetic — monthly
and annual figures are server strings. Each action keeps its method, path, body and confirmation.

**Required test behaviour.**
- The focus surface shows the next renewal from `summary.upcoming[0]`, and the truthful empty
  sentence when there is none.
- Each view shows only its own records; the four existing empty sentences still appear in their
  views ("Nothing is expected to renew", "No current subscription has been found yet", "No past
  subscription is recorded", "No subscription is linked to a card yet", and Recurring's "no
  recurring payment other than a subscription has been found yet").
- An ended row shows `ended {date}` with its end reason and does **not** present a status control
  reading Active on its face; opening `RowActions` still offers the status select.
- Every action still fires its exact call: existing suites resolve `Not a subscription`, `Cancel`,
  `Resume`, `A subscription` and `Remove` with a global `getByRole` and must now open the row's
  disclosure first — rewrite the query, keep the assertion.
- Both `window.confirm` tests keep their sentences and still assert no POST on a false answer.
- The pinned call counts and order — four and two — still hold.
- Payment source and provenance are reachable from the row's expansion or its detail screen.

**Validation.** `cd web && npm run lint && npm run typecheck`, then
`npx vitest run app/subscriptions app/recurring app/transactions`, then live at 360px, 588px and
desktop: an ended subscription, a current one, an empty ledger, and one cancel and one remove
confirmation.

**Acceptance.** The first viewport of each screen names the next real renewal or says truthfully
that there is none; every action from the inventory above still exists with the same semantics and
confirmation; no ended item reads as active; rows stay legible at 360px.

---

### Task 4 — Documents and Classify

**Outcome.** Documents shows readable records instead of a squeezed seven-column table, with
filters that stay meaningful and advanced ones that collapse on narrow screens. Classify uses the
same decision-and-record hierarchy while keeping its evidence, confidence and reversible review.

**Branch.** `feature/ui-documents-classify`

**Dependencies.** Task 1.

**Files.**
- Modify: `web/app/documents/documents-screen.tsx`,
  `web/app/review/classification-review-screen.tsx`
- Modify: `web/app/globals.css` (`.documents-table-wrap`, `.document-actions`, classification rules)
- Modify: `web/app/documents/__tests__/documents-screen.test.tsx`,
  `web/app/review/__tests__/classification-review-screen.test.tsx`

**Contracts.**
- **The record shows five facts:** type, account, period, import state, and the next relevant
  action. Today's seven columns are Type, Account, Period, Pages, Uploaded, Import, Actions. Pages
  (`pageCount`), Uploaded (`uploadedOn`), the password-protected marker and the unrendered
  `parsedStatus`, `layoutId` and `layoutVersion` move into the row's expansion — reachable, not
  dominant. The `role="region"` scroll wrapper with its 840px minimum goes away with the table.
- **Humanise the enums.** `documentType` and `importStatus` are rendered raw today
  (`bank_statement`, `awaiting_review`) in both the cells and the 12- and 14-value filter options.
  Add label maps for both. This closes the same defect the Phase 3 UI audit recorded — raw enum
  values shown to the owner — and it will break test assertions that pin the raw strings; update
  them to the labels.
- **Dates are dates.** `uploadedOn.slice(0, 10)` is a string trim of a timestamp; print it with the
  existing `dateLabel` from `web/lib/format/date.ts`. Keep the period's "(derived from the rows)"
  qualifier, which states provenance.
- **Filters.** Type and Account stay visible at every width. Period from, Period to and Import
  status are the advanced region and collapse into a disclosure under 588px. The submitted query
  string keeps its exact parameter order —
  `documentType`, `accountId`, `periodFrom`, `periodTo`, `importStatus`, `limit`, `offset` — because
  a test pins the literal string. Keep the `Apply filters` label and the form's accessible name
  "Filter documents".
- **Every row action survives with its current condition:** `Original` (a plain link to
  `apiUrl(/documents/{id}/original)`, new tab), `Transactions` (only when
  `importKind === "statement"`), `Review` (statement + `awaiting_review` → `/import/{importId}`;
  investment + `staged`/`awaiting_review`/`partially_committed` → `/investments/import/{importId}`),
  and `Re-read descriptions` (only when `newerLayoutId` and `importStatus === "completed"`). The
  next relevant action is the one that moves the document forward — Review when it is awaiting
  review, otherwise Re-read descriptions when offered, otherwise Transactions or Original.
- **`Re-read descriptions` keeps its two-step gate.** `Preview re-read` then `Apply`, both
  `POST /import/{importId}/reparse-descriptions` with `{layoutId, apply, password}`. There is no
  `window.confirm` here; the preview step *is* the safety gate and must not be collapsed into one
  click.
- **`Load {n} more`** keeps appending with `offset=rows.length`.
- **Classify** applies the same hierarchy: the unresolved group needing a decision is the focus
  surface, resolved and repeatable items are records, and the evidence, confidence figures and
  reversible review behaviour are unchanged. Keep the `Recent changes` list and its Undo rule —
  offered only where `sourceRunId` is absent and `undone` is false — and the sentence that the
  remembered merchant stays active after an undo. The link to
  `/settings#merchant-knowledge-title` must keep working against Task 2's section map.

**Invariants and security.** No endpoint, query-parameter order or body change. `pageCount` and
`uploadedOn` are displayed, never computed. Classification confidence and evidence keep their
current server-provided form.

**Required test behaviour.**
- A document record shows its type, account, period and import state, and offers the next relevant
  action; the three existing tests that reach for `getByRole("table")` are rewritten against the
  record markup rather than deleted.
- Enum labels replace the raw strings, and the filter options read as labels.
- The submitted filter query string is unchanged, character for character.
- Advanced filters are reachable at 360px and visible at desktop.
- `Original`, `Transactions`, `Review` and `Re-read descriptions` keep their accessible names and
  their conditions; the preview-then-apply sequence still takes two steps.
- `Load {n} more` still appends from `offset=rows.length`.
- Classify keeps its evidence, confidence and undo assertions.

**Validation.** `cd web && npm run lint && npm run typecheck`, then
`npx vitest run app/documents app/review`, then live at 360px, 588px and desktop with a
statement document, an investment document awaiting review, and a completed one with a newer layout.

**Acceptance.** A document's state and next action are readable without horizontal scrolling at
360px; no raw enum or raw timestamp is shown; every action and its gate survive; the filter
contract is byte-identical.

---

### Task 5 — Investments focus and analysis depth

**Outcome.** With no holdings, the two real entry routes lead and the limitations are one concise
explanation. With holdings, holdings, valuation confidence, review work and analysis are visually
distinct. Coverage exclusions and methodology stay available without occupying the first screen.

**Branch.** `feature/ui-investments-focus`

**Dependencies.** Task 1.

**Files.**
- Modify: `web/app/investments/portfolio-screen.tsx`, `web/app/investments/investment-analysis.tsx`
- Modify: `web/app/investments/import/investment-import-screen.tsx` (grammar only)
- Create: `web/app/investments/return-reasons.ts` (the shared reason dictionaries)
- Modify: `web/app/globals.css` (`.portfolio-*`, `.investment-*` rules)
- Modify: `web/app/investments/__tests__/portfolio-screen.test.tsx`,
  `web/app/investments/__tests__/investment-analysis.test.tsx`

**Contracts.**
- **Today the empty screen renders all nine blocks** — analysis above every portfolio number, then
  metrics, unvalued line, price status, allocation, reviews, holdings and the record-event form —
  and the two entry links are the smallest element in the header. Invert that.
- **Empty state.** When there are no positions, the focus surface presents the two real entry
  routes as the primary choice: `Record a purchase` → `/investments/entry` (manual acquisition
  docket) and `Import a statement` → `/investments/import`. No test asserts these two labels today,
  so the wording is free to improve. Beneath them, **one** concise explanation of what analysis
  cannot yet say — replacing the scattered "No holdings to analyse.", "No year figures yet.", "No
  month figures yet." and the wealth-history sentence as separate blocks. Keep the existing
  "An investment account is required before recording a purchase." path with its `/accounts` link.
- **Order with holdings:** metrics (Value, Contributions, Gain, XIRR) → valuation confidence →
  holdings → review work → analysis. `InvestmentAnalysis` moves below the portfolio numbers; no
  test asserts cross-component order, so this is safe.
- **Valuation confidence is its own visible group**, built only from server fields:
  `PortfolioSummary.valueComplete`, `valuedCount`, `unvaluedCount`, `staleCount`, and
  `priceStatus.{lastSuccessfulCheck, mappedCount, unmappedCount, latestPriceDate}`; per position,
  `priceStatus` (`current`/`stale`/`missing`/`future_price_only`) and `valueStatus`
  (`available`/`missing_price`/`outside_supported_range`). Keep the existing unvalued sentence and
  the "Check for new prices" button with its 24-hour "Checked within the last 24 hours." state, and
  keep the refresh firing at most once. `missingFx` and `fxRates` are unrendered today; leave them
  unrendered — surfacing them is a separate decision, not this task's.
- **Never imply a value.** Keep `money()`/`ratio()` returning "Unavailable", "Current value
  unavailable", "Gain unavailable" and "—", never `0`. Keep `xirrReason` and `dietzReason`
  sentences. `XIRR_REASON_TEXT` and `DIETZ_REASON_TEXT` are currently duplicated in
  `portfolio-screen.tsx:30-38` and `investment-analysis.tsx:36-49`; move them to
  `return-reasons.ts` and import from both.
- **Methodology goes behind a labelled disclosure, still mounted on open.** Coverage exclusions
  (`data.coverage`, rendered as "{accountName} · {valuationMode} — excluded from holdings
  analysis"), Inception, Years, Months, Allocation target and Benchmarks are roughly 300 of
  `investment-analysis.tsx`'s 534 lines. They stay complete and editable — the target table still
  saves, the benchmark select still works — but they are not the first screen. Their tests query
  `getByRole("table", { name: … })` and the exact coverage sentence; since a closed disclosure's
  content is outside the accessibility tree, those tests must open it first. Rewrite the queries;
  keep the assertions, including "Incomplete month", "1 missing price", "drift 0.0000000000" and
  the "No sums — a month in this year is incomplete." sentence.
- **Review work** keeps `ReviewQueue` (`GET /investment-reviews?limit=20&offset=0`, "Load more
  reviews", `View holding` / `View import` links and their hrefs) and the holding screen's own
  "Open reviews" list.
- **Do not change the portfolio fetch shape:** `/portfolio/summary` and
  `/portfolio/positions?includeClosed=true` in one `Promise.all`, plus `/portfolio/analysis`.
- **Import keeps its stages explicit:** `Check statement` → `Stage import` → `Commit ready groups`,
  with `expectedState` on commit, the `Cancel this import` path, and the call order its test pins
  by index (`calls[2]` stage, `calls[3]` commit). Grammar only on this screen — no flow change.

**Invariants and security.** No endpoint or call-order change. No money arithmetic; every amount is
a server string. No recommendation language anywhere — an existing test asserts the document body
never matches `buy|sell|hold|recommend|rebalance`.

**Required test behaviour.**
- With no positions, the two entry routes are the prominent choice and the limitations are one
  explanation; `queryByText(/XAA 0/)` stays null and no fabricated value appears.
- With positions, valuation confidence, holdings, review work and analysis are distinguishable
  regions.
- The existing exact strings still hold once the disclosure is opened: the coverage sentence, the
  three table accessible names, "Incomplete month", "No sign change across the cash flows.",
  "Unavailable", "Incomplete — one or more holdings are unvalued.", "Price XAA 10.000000", "No
  dated price recorded."
- "Check for new prices" still fires the refresh exactly once and is absent within 24 hours.
- The allocation target still saves and the benchmark select still applies.
- The import screen's three stage labels and its commit body are unchanged.
- The no-recommendation assertion still passes.

**Validation.** `cd web && npm run lint && npm run typecheck`, then
`npx vitest run app/investments`, then live at 360px, 588px and desktop: an empty portfolio, a
portfolio with an unvalued holding, and one with a stale price.

**Acceptance.** An owner with no holdings sees the two ways in first and one honest explanation of
what is missing; an owner with holdings can tell valued from unvalued from under-review at a
glance; methodology is one disclosure away, complete and editable; no unestablished figure is
implied anywhere.

---

### Task 6 — Ledger destinations

**Outcome.** Overview, Accounts, Transactions and Import inherit the shared grammar — one page
head, a real status strip only when warranted, a focus surface for the next task, compact records
and contained dense data — with every existing workflow, recovery path and deep link intact.

**Branch.** `feature/ui-ledger-destinations`

**Dependencies.** Task 1.

**Files.**
- Modify: `web/app/overview/overview-screen.tsx`, `web/app/accounts/accounts-screen.tsx`,
  `web/app/transactions/transactions-screen.tsx`, `web/app/import/import-screen.tsx`,
  `web/app/import/review-screen.tsx`, `web/app/globals.css`
- Modify: the suites under `web/app/overview/__tests__/`, `web/app/accounts/__tests__/`,
  `web/app/transactions/__tests__/`, `web/app/import/__tests__/`

**Contracts.**
- Overview keeps its useful summaries and every link it already offers — net worth to Wealth,
  Invested to Investments, Coverage, and each `netWorth.investmentCoverage` reason linking to its
  holding or to Accounts. The Needs attention panel always renders; do not make it conditional.
- Accounts keeps its `?valuation=` deep link read once by a lazy initializer
  (`accounts-screen.tsx:65-77`), the valuation panel, the two-step review-then-record flow, and the
  per-account row actions.
- Transactions keeps all three deep-link query forms its tests pin by exact string
  (`sourceDocumentId`, the `categoryId`+`dateFrom`+`dateTo` form, and the bare paged form), the
  transfer panel, and the compact-filter split of main versus advanced.
- Import keeps its stages, its verdicts, the account-proposal flow and the layout mapper
  unchanged in behaviour; the wide contained tables scroll inside named regions.
- Dense tables stay tables where columns genuinely compare, with tabular figures and right-aligned
  numerics; they do not become cards.

**Invariants and security.** No fetch added to the Workspace chain. Every pinned query string
preserved character for character. No money arithmetic. Import's immutability and review gates
untouched.

**Required test behaviour.** Every existing assertion in the four suites holds, with queries
updated only where markup deliberately moved. The three transactions deep-link query strings, the
Overview exact-array fetch assertion (`["/household", "/currencies", "/dashboard", "/actions"]`),
and the import stage labels are unchanged.

**Validation.** `cd web && npm run lint && npm run typecheck`, then
`npx vitest run app/overview app/accounts app/transactions app/import`, then live at 360px, 588px
and desktop across all four.

**Acceptance.** Each of the four screens opens with its state and next action visible, keeps every
deep link and workflow, and does not scroll horizontally at 360px.

---

### Task 7 — Analysis destinations

**Outcome.** Spending, Salary, Plans, Coverage and Wealth surface the next real task before long
methodology or history, under the same grammar.

**Branch.** `feature/ui-analysis-destinations`

**Dependencies.** Task 1. (Coverage, Wealth and Spending received the grammar in Task 1; this task
completes them and brings Salary and Plans across.)

**Files.**
- Modify: `web/app/spending/spending-screen.tsx`, `web/app/spending/budget-panel.tsx`,
  `web/app/salary/payslips-screen.tsx`, `web/app/salary/salary-analysis.tsx`,
  `web/app/plans/plans-screen.tsx` and its sections (`needs-attention.tsx`, `cash-forecast.tsx`,
  `goals.tsx`, `emergency-reserve.tsx`, `register.tsx`, `settlement-panel.tsx`),
  `web/app/coverage/coverage-screen.tsx`, `web/app/wealth/wealth-screen.tsx`,
  `web/app/globals.css`
- Modify: the suites under `web/app/spending/__tests__/`, `web/app/salary/__tests__/`,
  `web/app/plans/__tests__/`, `web/app/coverage/__tests__/`, `web/app/wealth/__tests__/`

**Contracts.**
- Plans opens on Needs attention, as it does today, and keeps its in-page navigation over all seven
  sections, every settlement and resolution flow, the confirmed `Mark achieved`, the shortfall
  callout and the account-value reason sentences in `web/lib/plans/account-value-reasons.ts`.
- Wealth keeps its chart driven by the server's `chartRatio` string set as a CSS custom property —
  no browser arithmetic — its base-currency statement, each position's status label with its link,
  each incomplete month's `statusCounts` line, and "Last refreshed".
- Salary keeps the deduction-rules section with its note that a rule affects payslips linked
  afterwards, and the payslip import flow.
- Spending keeps its budget panel, its range control and its chart, which shares the consolidated
  chart family from Task 1.
- Coverage keeps its contained wide table inside a named scroll region and its legend, which must
  not convey state by colour alone.
- Methodology and long history move below the next real task or behind a disclosure; none of it is
  removed.

**Invariants and security.** No endpoint change. No money or ratio arithmetic in TypeScript — the
Wealth chart ratio and every figure stay server strings. Every confirmation preserved.

**Required test behaviour.** Every existing assertion in the five families holds, with queries
updated only where markup deliberately moved. The Wealth chart still reads its ratio from the
server string; the Plans settlement, goal, reserve and register flows keep their assertions and
confirmations.

**Validation.** `cd web && npm run lint && npm run typecheck`, then
`npx vitest run app/spending app/salary app/plans app/coverage app/wealth`, then live at 360px,
588px and desktop across all five.

**Acceptance.** Each screen shows its next real task before methodology; every flow, confirmation
and link survives; no horizontal scroll at 360px.

---

### Task 8 — Cross-screen audit and repair

**Outcome.** One read-only audit of all 20 destinations against the spec's acceptance, with its
findings fixed on this single branch.

**Branch.** `fix/ui-decision-first-audit`

**Dependencies.** Tasks 1-7 merged.

**Files.** Determined by the audit; expect `web/app/globals.css` and the screens holding findings.

**Contracts.**
- Audit every destination for: a class name used with no rule in `globals.css`; a raw enum, ISO
  timestamp or snake_case value shown to the owner; a control that cannot submit or changes nothing
  on screen; an empty picker where an operation needs a selection; state conveyed by colour alone;
  focus that does not move or is invisible after navigation, disclosure or action; a heading order
  break; an unlabelled region or navigation; horizontal page scroll at 360px or 588px; wide data
  outside a named scroll region; a loading state that reads as an empty result; and any figure the
  server did not establish.
- Fix findings on this branch. Anything that would need an API or schema change is recorded in
  `docs/superpowers/project-register.md` instead, not implemented here.

**Invariants and security.** Web-only. No endpoint, schema or contract change.

**Required test behaviour.** Each repaired finding that concerns a behaviour gets an assertion in
the owning screen's suite; purely visual repairs are checked live.

**Validation.** `cd web && npm run lint && npm run typecheck && npx vitest run`, then the live app
at 360px, 588px and desktop across all 20 destinations, keyboard-only.

**Acceptance.** The spec's acceptance holds on every destination: the owner can identify the
current state and next action in the first viewport, reach every existing operation without hunting
through prose, and navigate Settings without losing a deep link or an unsaved edit; documents and
recurring records are legible on desktop and mobile; every financial, import and correction
workflow keeps its meaning.

## Status and evidence

`docs/superpowers/progress.md` owns live status. Record each branch as it merges with its PR link
only; the detail belongs in the PR body. Do not claim the redesign complete before Task 8's
acceptance holds — owner acceptance of the visual outcome is owner-operated and is not satisfied by
a passing suite.
