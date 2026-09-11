# Local statement identities implementation plan

> **For the implementation agent:** Execute this plan sequentially in the active checkout. Use
> `superpowers:test-driven-development` for every behavior change and
> `superpowers:verification-before-completion` before claiming a task complete.

**Goal:** Make each supported PDF identify its institution and account, reuse exact existing
records, confirm genuinely new identities once, and let the owner reveal source-complete account
identifiers without exposing them through ordinary application surfaces.

**Architecture:** UUIDs remain the canonical institution/account identities. PDF evidence resolves
through household-aware institution aliases and a separate local `account_identifier` store.
Source-complete values are stored in plaintext with a kind-normalized value for exact matching;
mask-only evidence uses a deliberately narrower unique-candidate fallback. Masking is an ordinary
UI/API presentation boundary, not encryption or a storage limitation. The approved behavior lives
in the [spec](../specs/2026-09-11-local-statement-identities.md) and
[ADR-0021](../../adr/0021-local-owner-financial-identifiers.html); this document maps that design
into reviewable tasks.

**Stack:** PostgreSQL 18/Flyway, Python 3.14/FastAPI/Pydantic/psycopg, Next.js/React/
TypeScript, pytest and Vitest.

## Delivery and dependency map

All tasks use `/Users/sandjaieravi/dev/OneLedger`, branch from current `develop`, target `develop`,
and receive one focused review after `/verify` and before their first push. The orchestrator owns
branch switching, rebases immediately before push and merge, and does not create worktrees. The
next free migration number is V28 today; if another migration lands first, renumber before writing
the test. PR titles carry `[P1.8]`.

| Task | Branch | Forecast production / test lines | Depends on |
| --- | --- | ---: | --- |
| 8c1 — identifier relations | `feature/p1-8c1-identifier-schema` | 220 / 500 | none |
| 8c2 — identifier value boundary | `feature/p1-8c2-identifier-values` | 150 / 280 | 8c1 |
| 8c3 — account identifier API | `feature/p1-8c3-account-identifier-api` | 540 / 900 | 8c2 |
| 8c4 — account reveal screen | `feature/p1-8c4-account-reveal-screen` | 180 / 270 | 8c3 |
| 8c5 — PDF institution resolution | `feature/p1-8c5-statement-institution-resolution` | 390 / 650 | 8c3 |
| 8c6 — PDF account resolution | `feature/p1-8c6-statement-account-resolution` | 500 / 800 | 8c5 |
| 8c7a — safe proposal grouping contract | `feature/p1-8c7a-proposal-group-key` | 100 / 160 | 8c6 |
| 8c7b — import confirmation UI | `feature/p1-8c7b-import-identity-confirmation` | 460 / 600 | 8c7a |
| 8c8 — identity backfill preview | `feature/p1-8c8-identity-backfill-preview` | 280 / 440 | 8c6 |
| 8c9 — guarded identity backfill | `feature/p1-8c9-identity-backfill-apply` | 260 / 460 | 8c8 |
| 8c10 — transition cleanup | `fix/p1-8c10-identity-contract-cleanup` | 170 / 280 | 8c4, 8c7b, 8c9 |

No forecast reaches the 800 production / 1,600 combined soft budget. If discovery raises either
estimate above its budget, split before implementation and update this plan plus `progress.md`.
Configuration, documentation and generated API types do not count toward the size gate.

## Cross-task contracts

- Identifier kinds are `bank_account`, `iban` and `card`; kinds do not match across one another.
- A complete identifier is stored in its source spelling and normalised by kind for exact lookup.
  A source mask never acquires invented digits or a fabricated full value.
- OneLedger introduces no identifier encryption key, key rotation flow or key-dependent startup.
  Plaintext storage in the local database and its backups is an explicit owner-approved trade-off.
- Institution text is Unicode-NFKC/casefolded, surrounding/collapsed whitespace is normalised,
  and matching is exact after that transform. No filename, narration, amount or display name is
  evidence. Multiple scoped matches are `ambiguous`, never resolved by score or row order.
- Ordinary responses carry only identifier UUID, kind, safe mask, `canReveal` and active state.
  Full values are returned only in the two owner-only reveal responses, both with
  `Cache-Control: no-store`; it is forbidden from errors, logs and serialized model reprs.
- All new financial routes use `require_owner()` and the existing Origin protection. SQL joins,
  composite references and RLS enforce household ownership even when application checks regress.
- Money remains `NUMERIC(20,4)`/`Decimal`/decimal string. No report query changes, and no LLM call is
  introduced. Real documents, values, screenshots and extracted text never enter a branch.

---

## Task 8c1 — identifier relations

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `feature/p1-8c1-identifier-schema`  
**Base and PR target:** `develop`  
**Migration:** `V28__institution_and_account_identifiers.sql`. **Depends on:** nothing unmerged.

**Outcome.** PostgreSQL holds household-aware issuer aliases and multiple typed identifiers per
account without pretending legacy hashes or masks contain recoverable plaintext.

**Files.** Create V28 and `db/tests/0025_account_identifiers.sql`; update
`docs/superpowers/schema.md` and `docs/design/data-model.html`.

**Schema contract.** `institution_alias` carries UUID, nullable household UUID, institution UUID,
original bounded public alias, normalised alias, country, provenance, active/deactivated dates and
timestamps. `account_identifier` carries UUID, household/account UUIDs, kind, mask, visible suffix,
nullable full source value and normalized match value, a nullable legacy fingerprint pair, nullable
source document, source SHA-256, layout/version/capture provenance, active/deactivated dates and
timestamps. Composite foreign keys and scope checks prevent cross-household parents. Active exact
uniqueness is `(household, kind, normalized_value)`; mask-only rows are intentionally non-unique.
Full/normalized columns and legacy fingerprint columns are each all-present or all-null, and new
source-complete rows do not use the legacy fingerprint. Both tables have RLS; shared aliases are
read-only to households.

**Migration behavior.** Copy each legacy account mask/hash into typed rows: valid legacy hashes are
temporary `legacy_sha256_v1` fingerprints with no full value; hashless values remain mask-only;
cards become `card`, other account masks become `bank_account`, and `iban_masked` becomes a separate
`iban` row. Keep the three legacy columns until 8c3. Do not synthesize full values, normalized values
or suffixes.

**Tests/validation.** Fail first on RLS, parent scope, full/normalized completeness, active exact-
identity collision, allowed duplicate masks, multi-kind accounts, provenance, deactivation and
lossless legacy copying.
Run `scripts/test-db.sh reset`, `scripts/test-db.sh test`, then `/verify`.

**Acceptance.** An upgraded copy preserves every account and safe mask while exposing no new full
value; a fresh database enforces the same ownership and uniqueness rules.

## Task 8c2 — identifier value boundary

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `feature/p1-8c2-identifier-values`
**Base and PR target:** `develop`  
**Migration:** none. **Depends on:** 8c1 merged.

**Outcome.** One small service is the sole normalize, mask and validate boundary for complete or
source-masked identifiers; it has no key or environment dependency.

**Files.** Create `api/oneledger/identifiers.py` and `api/tests/test_identifiers.py`; update the
identifier section of `docs/operations/security-and-privacy.html`.

**Interfaces.** Typed value objects distinguish complete from masked captures. The service accepts
an identifier kind plus source spelling and returns its full source value, normalized exact-match
value, mask and visible suffix. Validation errors never echo the input. It performs no database I/O
and reads no environment variable.

**Tests/validation.** Fail first for kind-specific normalization; ASCII, formatted and Unicode
digits; round-trip source spelling; stable normalized output; complete versus mask-only capture;
safe repr/errors; masking and suffix extraction. Run the focused pytest file, then `/verify`.

**Acceptance.** Synthetic full values retain their source spelling, match deterministically after
normalization and require no key to read after an application restart.

## Task 8c3 — account identifier API

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `feature/p1-8c3-account-identifier-api`  
**Base and PR target:** `develop`  
**Migration:** `V29__retire_legacy_account_identity_columns.sql`. **Depends on:** 8c2 merged.

**Outcome.** Account create/update/list use `account_identifier` as the only identity store, and an
owner can deliberately reveal one source-complete identifier.

**Files.** Modify `api/oneledger/routes/{accounts,ingest}.py`,
`api/tests/{test_accounts,test_ingest_route_live,test_account_from_statement_live}.py` and
`scripts/openapi.py`; create V29 and `db/tests/0026_retired_account_identity_columns.sql`;
regenerate `web/types/api/{types.gen,index}.ts`; update API, schema/data-model and security docs.

**API contract.** `AccountRow.identifiers` is a list of `{id, kind, masked, canReveal, isActive}`.
Temporarily retain `accountNumberMasked` derived from the preferred active identifier. Existing
`accountNumber` input is stored with `owner_entered` provenance through the shared service.
`POST /accounts/{accountId}/identifiers/{identifierId}/reveal` returns the discriminated union
`revealed(value, kind)` or `unavailable(source_mask_only)`, always `no-store`; reveal reads the
stored full value and requires no separate key or password.

**Transition.** Account routes and the importer's existing resolver/confirmation path stop touching
legacy columns before V29 drops `account_number_masked`, `account_number_hash`, `iban_masked`, their
index and V27 constraint. This task preserves the current exact-match/proposal semantics; 8c5–8c6
change the evidence and decisions. Legacy fingerprints remain readable for later backfill; new
complete writes store the source and normalized values directly. Database errors map to stable
service-owned messages.

**Tests/validation.** Fail first for create/update/statement-confirmation storage, existing parse
matching after the column drop, safe list serialization, duplicate exact identity, multi-kind and
mask-only rows, reveal/no-store, 401/403/404 cross-household paths, restart readability, and raw plus
serialized Unicode/formatted leak scans outside reveal. Run focused account/ingest/SQL tests, API
type generation and check, then `/verify`.

**Acceptance.** Account APIs retain current behavior, ordinary reads never disclose full values,
and only the authenticated single-identifier reveal returns a stored complete source value.

## Task 8c4 — account reveal screen

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `feature/p1-8c4-account-reveal-screen`  
**Base and PR target:** `develop`  
**Migration:** none. **Depends on:** 8c3 merged.

**Outcome.** Every account identifier is masked by default and has an accessible eye control when
its complete source value is available.

**Files.** Modify `web/app/accounts/accounts-screen.tsx`, its test and relevant account styles.

**Behavior.** The eye posts to the one-identifier reveal route, replaces only that rendered mask,
toggles closed without a request, and clears plaintext on account/card unmount, edit transition,
navigation and failure. Mask-only rows disable the eye and explain that the statement supplied no
complete value. No URL, storage, form default, analytics or screen-wide state receives plaintext.

**Tests/validation.** Fail first for masked default, deliberate reveal, no prefetch, close/cleanup,
one identifier not revealing siblings, unavailable/error states, keyboard name/focus and absence
from browser persistence. Run the focused Vitest file, `npm --prefix web run build`, then `/verify`.

**Acceptance.** The owner can inspect and re-mask a complete synthetic identifier; a mask-only card
honestly remains mask-only.

## Task 8c5 — PDF institution resolution

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `feature/p1-8c5-statement-institution-resolution`  
**Base and PR target:** `develop`  
**Migration:** none. **Depends on:** 8c3 merged.

**Outcome.** The four supported layouts extract issuer branding from page-one PDF content and map
it exactly to a permitted institution UUID or an explicit confirmation state.

**Files.** Create `api/oneledger/institutions.py` and its tests; modify
`api/oneledger/{ingest/engine.py,routes/layouts.py,routes/ingest.py}` and focused tests; update the
four `db/seeds/*-layout.json` files, `db/seeds/README.md` and statement-ingestion/API docs.

**Contract.** Add reserved capture `institution_name` with exactly one group. Resolution considers
canonical name, short name and active shared/household aliases after the cross-task normalisation,
then country when evidence supplies it. Return exactly `matched`, `needs_confirmation` or
`ambiguous` with public institution candidates. `statement_layout.institution_id` may remain layout
catalogue metadata but cannot decide the statement's institution. This task returns prospective
reuse/create/ambiguous evidence without writing; 8c6 owns atomic confirmation.

**Tests/validation.** Fail first for filename independence, canonical/short/alias matches, country
scoping, shared plus household visibility, ambiguity/no-write, no-match proposal and
literal-private-data rejection in seeds. Each current seed must validate and capture only generic
public issuer text. Run focused layout/ingest tests and `/verify`.

**Acceptance.** Synthetic versions of all four formats derive their institution from PDF text;
renaming a file changes nothing and conflicting issuer evidence never auto-selects.

## Task 8c6 — PDF account resolution

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `feature/p1-8c6-statement-account-resolution`  
**Base and PR target:** `develop`  
**Migration:** none. **Depends on:** 8c5 merged.

**Outcome.** A complete or masked statement identity reuses one exact account, proposes a distinct
new account under an existing institution, or asks on ambiguity/conflict.

**Files.** Create `api/oneledger/account_resolution.py` and its tests; modify
`api/oneledger/routes/ingest.py`, `api/tests/{test_ingest_engine,test_ingest_route_live,
test_account_from_statement_live}.py`; regenerate API types and update API/ingestion docs.

**API contract.** `ParseResponse.identityResolution` distinguishes `matched`,
`needs_confirmation`, `ambiguous` and includes safe institution/account actions (`reuse`, `enrich`,
`create`, `choose`), candidates and masks. Complete evidence matches the active normalized value
first, then the temporary legacy digest. Mask fallback requires one active account with the institution,
captured account type and visible suffix. Type/currency/issuer conflicts write nothing.
`POST /parse/account` re-reads the PDF and accepts corrected non-identity fields plus an explicit
institution reuse/create decision; it never accepts a replacement identifier. Because confirmation
precedes staging, its identifier records the source content hash and layout/version/capture key with
a null document UUID; a later successful stage attaches the household document UUID only when that
hash and capture provenance agree. Institution/alias, account and identifier reuse or creation is
one database transaction; an explicit candidate choice persists as a household owner-confirmed
alias which wins over generic catalogue matches on later parses. Corrected reuse and a new
institution plus its first alias are tested here. `POST /parse/identifier/reveal` is the separate
owner-authenticated multipart preview
reveal. It
re-runs layout detection/extraction against the submitted PDF/password and returns only
`revealed(value, kind)` or `unavailable(source_mask_only)` with `no-store`; it accepts no identifier
text, persists nothing and is never confirmation input.

**Tests/validation.** Fail first for exact full match, unique/zero/multiple mask results, same bank
savings versus new current account, reuse/enrichment without duplicate rows, conflicting selected
account, atomic confirmation, concurrent duplicate refusal, unsupported layout and raw/serialized
privacy; preview-reveal authentication/origin/no-store/no-write behavior; and reveal refusal when
layout or capture is ambiguous. Preserve reconciliation/staging gates. Run focused unit/live tests,
generated-types check, then `/verify`.

**Acceptance.** The approved Axis savings/current scenario produces one institution and two account
UUIDs, while a later statement for either resolves with no confirmation.

## Task 8c7a — safe proposal grouping contract

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `feature/p1-8c7a-proposal-group-key`
**Base and PR target:** `develop`  
**Migration:** none. **Depends on:** 8c6 merged.

**Outcome.** Every unresolved identity carries an opaque service-owned key which lets the browser
group the same proposed identity without seeing or reconstructing complete identifiers.

**Files.** Modify `api/oneledger/account_resolution.py`, `api/oneledger/routes/ingest.py` and their
focused tests; regenerate API types and update the API/ingestion docs.

**Contract.** Add nullable `proposalGroupKey` to `IdentityResolution`: it carries a key only for an
unresolved result backed by exactly one identifier capture, and is `null` for `matched` results or
documents with conflicting identifier captures. The service computes a domain-separated
HMAC-SHA256 with the already-required session secret over household UUID, resolved/proposed
institution evidence, account type, captured currency, identifier kind and either the complete
normalized value or source-mask suffix. It is stable for equivalent evidence while that server
configuration remains active, differs across households and distinct complete values sharing a
suffix, is never persisted, and contains no identifier text. This adds no encryption key or stored
data dependency; rotating the session secret only changes future proposal keys.

**Tests/validation.** Fail first for equivalent complete formatting, distinct complete values with
the same suffix, mask composites, household separation, matched nullability and raw/serialized
privacy. Regenerate API types, run focused API tests, then `/verify`.

**Acceptance.** Two selected statements for one complete identity receive the same opaque key,
while two complete identities sharing the displayed suffix do not.

## Task 8c7b — import identity confirmation

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-8c7b-import-identity-confirmation`
**Base and PR target:** `develop`
**Migration:** none. **Depends on:** 8c7a merged.

**Outcome.** The import screen explains automatic reuse/new/ambiguous states, asks once for a new
identity, and rechecks all selected documents that share it.

**Files.** Modify `web/app/import/{account-proposal,import-screen}.tsx`, their tests and styles.

**Behavior.** Group proposal leaders by the service's opaque `proposalGroupKey`, not filename or
mask alone. The form shows reuse/create actions, correctable institution/type/country/currency and
mask. Its eye uses `POST /parse/identifier/reveal` with the still-selected multipart PDF and
password; the route re-reads the capture and returns `revealed` or `source_mask_only` with
`no-store`. Confirmation never reuses that response as write input. Success rechecks every group
member independently before Stage appears; ambiguity requires an explicit existing choice.

**Tests/validation.** Fail first for all resolution states, one confirmation per identity group,
same institution/different account groups, multipart reveal and cleanup, mask-only explanation,
password re-send/clear, corrected non-identity fields, conflict display, and per-file
reconciliation failure after confirmation. Run focused Vitest, web build, then `/verify`.

**Acceptance.** Multiple synthetic statements for one new account need one confirmation, while a
second account at that bank gets its own; every file earns Stage independently.

## Task 8c8 — identity backfill preview

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `feature/p1-8c8-identity-backfill-preview`  
**Base and PR target:** `develop`  
**Migration:** none. **Depends on:** 8c6 merged.

**Outcome.** The owner can safely see which existing accounts can be upgraded by re-reading their
stored source documents, with no write and no private value in the response.

**Files.** Create `api/oneledger/routes/identity_backfill.py` and
`api/tests/test_identity_backfill_live.py`; register the router; update API/security docs and
generated types.

**Contract.** `POST /account-identifiers/backfill/preview` accepts one optional household account
UUID and request-only password. It selects documents only through completed `import_batch`
provenance, verifies storage path and SHA-256, and uses the newest layout version with the same
household and name as the batch's recorded immutable layout. It reuses the current resolver and
groups complete/mask-only/missing/conflicting/unsupported outcomes per account. The response
contains safe account/document UUIDs, reason codes, counts, `previewedAt` and an `expectedState`
digest over that timestamp plus the ordered safe provenance/version state—never names, storage
paths, captures or amounts.

**Tests/validation.** Fail first for read-only behavior, owner scope, path/symlink/hash guards,
password handling, per-account agreement/conflict, unsupported sources, safe serialization and a
stable digest that changes with any relevant row/layout/document state. Run the focused live test,
API type check, then `/verify`.

**Acceptance.** Preview can classify every synthetic historical source without changing any table
or document and gives apply enough state to detect staleness.

## Task 8c9 — guarded identity backfill

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `feature/p1-8c9-identity-backfill-apply`  
**Base and PR target:** `develop`  
**Migration:** none. **Depends on:** 8c8 merged.

**Outcome.** A confirmed, fresh preview upgrades only internally consistent historical identities
through the same alias/identifier writer as a new import.

**Files.** Modify the backfill route/live test and `api/oneledger/routes/doctor.py`; update API,
security and ingestion docs.

**Contract.** `POST /account-identifiers/backfill/apply` accepts account UUID, request-only password,
`previewedAt` and `expectedState`. Under the household lock it re-runs every preview guard, requires
the digest to reproduce exactly, refuses stale state or any disagreement for the whole account, and
atomically creates or updates aliases and identifiers. It replaces legacy hashes with complete local
values only from authenticated source evidence and is idempotent. It does not gate the operation on
an application key or backup; backups remain the owner's operational choice.
For a historical source mask that differs from its account's legacy mask, completed-import
provenance remains the selection boundary: adopt the captured mask only when issuer, account type
and currency agree, no different account matches the suffix, and all usable documents for that
account agree. Persist only the source mask and its provenance, never fabricated missing digits.

**Tests/validation.** Fail first for no-preview/stale-state refusal, whole-account
conflict rollback, cross-household denial, idempotence, concurrent changes and exact before/after
equality of transaction/batch/document/balance/card-cycle/transfer/reportable facts. Run focused
doctor/backfill tests and `/verify`.

**Acceptance.** A second preview reports zero eligible changes, and only permitted alias/identifier
records differ from the pre-run snapshot.

## Task 8c10 — transition cleanup

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`  
**Branch:** `fix/p1-8c10-identity-contract-cleanup`  
**Base and PR target:** `develop`  
**Migration:** none. **Depends on:** 8c4, 8c7 and 8c9 merged.

**Outcome.** Temporary mask/hash-era compatibility is removed and active documentation describes
one identity contract.

**Files.** Remove `AccountRow.accountNumberMasked`, the old `accountProposal` response and dead hash
helpers/tests from account/ingest APIs and generated web types; update callers and reconcile
`docs/design/{api-contracts,data-model,statement-ingestion}.html`, `docs/superpowers/schema.md`,
`docs/operations/security-and-privacy.html`, `.env.example` and seed notes.

**Tests/validation.** Static searches must find no product read/write of retired columns, old
mask-only claims, legacy proposal shape or filename identity hints. `ACCOUNT_HASH_SALT` remains a
deprecated upgrade-only secret used solely to compare `legacy_sha256_v1` rows and by guarded
backfill; no new row uses it. Removing it needs a later migration strategy that is safe for clones
which have not yet reprocessed their sources. Run focused API/web tests, generated-type check,
`git diff --check`, docs checks and `/verify`.

**Acceptance.** Fresh and upgraded installations expose one documented local-identifier model;
the only retained legacy path is read-only matching/backfill for pre-V28 fingerprints.

## Local acceptance and stop boundary

After 8c10 merges, use only authenticated application/API
operations on the active local stack. Re-check a representative real statement for each affected
account and all selected same-identity statements. Record only pass/fail and aggregate counts while
verifying: all four institutions came from PDF content; existing institution/account UUIDs were
reused; the second account at one bank remained distinct; complete source identifiers reveal and
re-mask; source-masked cards stay unrevealable; no duplicate institution/account appeared; no full
identifier entered logs or ordinary responses; and ledger facts/reportable totals did not change.

If every check passes, update only `docs/superpowers/progress.md` and deliver that acceptance record
through a documentation-only PR. Stop for the owner's roadmap discussion. Do not start slice 1.5.
