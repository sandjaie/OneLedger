# Encrypted statement identities and automatic account resolution

Owner-approved design, 11 September 2026. This extends slice 1.8 after Tasks 8a1–8b were
delivered. It replaces the remaining manual institution/card mapping acceptance with
statement-derived institution and account resolution, and adds masked-by-default owner reveal.
[ADR-0021](../../adr/0021-encrypted-owner-financial-identifiers.html) owns the policy decision.

Workspace: `/Users/sandjaieravi/dev/OneLedger`. Design branch:
`docs/p1-8-encrypted-account-identifiers`. Base and PR target: `develop`.

## Product outcome

An uploaded supported PDF, not its filename, supplies the issuer, account type, currency and
account/card identity. OneLedger reuses the institution and account already in the owner's
database when the evidence identifies them uniquely. It asks once before creating or enriching a
new institution/account, then rechecks every selected statement for that account and resolves
later statements automatically.

Accounts remain masked in ordinary views. The owner can deliberately reveal a complete identifier
when a source statement supplied it. A PDF that supplied only a mask can reveal no more than that
mask and says why.

The outcome includes this scenario: an Axis Bank savings account already exists; a later supported
Axis statement carries a different account number and states a current account. The institution
resolves to the existing Axis row, the savings account does not match, and one confirmation creates
a new current-account UUID beneath the same institution UUID. No second Axis institution and no
attachment to the savings account are permitted.

## Scope boundary

This work supports the four current statement formats: card, current account, passbook and
password-protected ledger. It does not promise format-independent PDF interpretation, OCR or an
embedded model. A future issuer or changed format that does not match a supported layout stops with
an unsupported-format result. Supporting it is a later focused PR carrying a data layout, synthetic
fixture and regression evidence.

A new account at a currently supported issuer needs no code change when its statement still fits a
supported layout. The identity resolver and encrypted store are format-independent once extraction
has produced typed evidence.

## Canonical identities and evidence

- `institution.id` remains the canonical institution identity.
- `account.id` remains the canonical account identity.
- Institution branding captured from a PDF is evidence resolved through alias data to an
  institution UUID; a bank name is not used as a primary or foreign key.
- A filename, path, transaction narration, amount, balance or account display name is never
  institution/account identity evidence.
- A complete identifier captured from a PDF is normalised once for matching and encrypted from the
  source spelling for owner reveal. Formatting may be preserved for display while matching ignores
  the separators the identifier kind defines.
- A source mask contains only what the PDF exposed. Missing digits are unknown, not null characters
  to reconstruct.

ADR-0012 remains in force: layouts and institution aliases are data, and UUIDs remain identifiers.
Public issuer names and statement vocabulary may appear in a layout/fixture; private account
values, owner names, amounts and statement text may not.

## Data model

### Institution aliases

Add a household-aware `institution_alias` relation. Each row references an institution UUID and
holds a bounded normalised public alias, country and provenance. Shared starter aliases may
reference shared institutions; owner-confirmed aliases are household-private. Resolution is exact
after explicit Unicode/case/space normalisation, not fuzzy. No match proposes; more than one match
after country scoping asks rather than resolving by UUID order. Deactivation preserves history.

Aliases are lookup evidence, not alternate institution identities. Creating a newly confirmed
institution and its first alias is one transaction. Reusing an alias never creates another
institution row.

### Account identifiers

Add an `account_identifier` relation owned by one household account. It supports `bank_account`,
`iban` and `card` kinds independently so one account can hold more than one source identity and a
replaced card does not rewrite ledger history. Each row carries:

- its UUID, household/account UUIDs, kind and active state;
- a safe mask and visible suffix;
- an authenticated-encryption envelope, nonce/parameters and key version when the full value is
  known;
- a keyed matching fingerprint and fingerprint scheme when exact matching is possible;
- source document and capture provenance; and
- creation/deactivation timestamps.

The authoritative final state lives in this relation. Existing `account_number_masked` and
`account_number_hash` values need a forward-only migration path: copy their safe evidence into
identifier rows, keep the legacy hash usable during guarded backfill, move all product reads/writes
to the new boundary, then remove or mechanically prevent new use of the legacy columns in the task
that completes the transition. No migration can manufacture an encrypted value from a hash.

A unique active keyed fingerprint per household and kind prevents duplicate exact identities.
Mask-only rows are not globally unique; ambiguity is a valid state handled by the resolver.

## Cryptographic boundary

Use authenticated encryption with an independent 256-bit environment key and a unique nonce for
every stored value. Use HMAC-SHA-256 under a separate 256-bit environment key for deterministic
matching fingerprints. Bind authenticated additional data to household UUID, account UUID,
identifier UUID and kind so ciphertext moved to another row cannot authenticate. The encryption
envelope and fingerprint record a scheme/key version; no code silently tries an unrelated key.

Development keys live in `.env`. The isolated local-production stack generates independent keys
in `.env.production`. `.env.example` and production examples contain empty placeholders and setup
instructions only. Keys never live in PostgreSQL, responses, logs, backups beside their encrypted
payloads, fixtures or Git.

If a required key is missing, malformed or the wrong version, the API fails closed with a generic
configuration/decryption refusal. A dry parse may still show a safe mask, but no response may claim
that a complete identifier was stored or revealed. Rotation is a separately guarded operation:
preview, decrypt/authenticate with the old version, re-encrypt with the new version, verify, then
retire the old key only after zero old envelopes remain.

This encryption limits a database-only or backup-only disclosure when the private environment is
absent. It does not claim to protect identifiers from an attacker controlling the running process
or the owner's unlocked machine.

## Statement resolution

The supported layout extracts issuer branding, account type, currency and every identifier shape
the PDF actually states. Institution resolution precedes the mask-only account fallback.

Resolution order is deterministic:

1. Identify one supported layout from page-one PDF content. A filename contributes nothing.
2. Resolve captured issuer branding through institution aliases. One best match selects its UUID;
   no match proposes a new institution; conflicting matches ask.
3. For a complete identifier, compute the keyed fingerprint and seek one active household
   identifier of that kind. One exact match selects its account regardless of its display name.
4. For a source mask, seek an active household account with the resolved institution UUID, captured
   account type and visible suffix. Exactly one selects it; zero proposes a new account; more than
   one asks.
5. Verify captured account type/currency against the resolved account. A conflict asks and writes
   nothing; it is not silently corrected to make the statement fit.

An explicit account picker remains a correction path, not a way to override contradictory source
identity. Selecting an account whose identifier conflicts with the PDF is refused.

## First-time confirmation and batch behaviour

A dry parse writes nothing. A proposal shows:

- whether the institution will be reused or created;
- whether the account will be reused, enriched or created;
- institution, type, country and currency, all correctable;
- the safe identifier mask and a source-backed reveal when a full value exists;
- opening-balance and card facts already defined by slice 1.8; and
- the number of selected documents that appear to share the proposed identity.

The identifier is source evidence and is not replaced by arbitrary browser text. If it parsed
incorrectly, the owner corrects the layout/capture and rechecks. Confirmation re-reads the file and
verifies the source identity before writing. Institution/alias, account and identifier creation or
enrichment are atomic; statement staging remains a second act.

After confirmation the browser rechecks every selected proposal-state document with the same
resolved identity. Each must identify, normalise and reconcile independently before its Stage
button appears. A different account at the same institution gets its own confirmation. Later
documents resolve without another click.

## API boundary

Ordinary account lists/details return identifier IDs, kinds, safe masks and `canReveal`; they never
return ciphertext, fingerprints, nonces or full values. Preserve the existing masked field during
client migration only as a compatibility representation sourced from the identifier relation.

An owner-authenticated reveal operation addresses one account and one identifier, verifies both
household links, decrypts exactly that value and returns it with `Cache-Control: no-store`. It is a
deliberate operation rather than a query parameter on `GET /accounts`, so caches, prefetch and list
rendering cannot reveal every account. A mask-only row returns a typed unavailable result rather
than pretending its suffix is complete.

The first-time proposal's eye control uses a separate owner-authenticated multipart reveal
operation that re-reads the still-selected PDF and returns only the captured identifier with the
same no-store boundary. It does not accept a client-supplied identifier or create a server-side
plaintext draft. Confirmation independently re-reads the file before encryption, so the reveal
response is never trusted as write input.

`POST /parse` continues to own supported-layout analysis. Its resolution result distinguishes
`matched`, `needs_confirmation` and `ambiguous`, and names safe institution/account candidates only.
The confirmation operation re-reads the uploaded file and accepts corrected non-identity account
fields; it never accepts a client-supplied replacement full identifier.

Backfill exposes preview/apply operations over the owner's stored source documents. Preview lists
only safe document/account identifiers and outcome/reason codes. Apply rechecks the same expected
state, is idempotent, writes through the same resolver/encryption service as a new import and
refuses the entire account's update when its source documents disagree.

## User interface

The import screen distinguishes automatic match, new-account confirmation, new-institution
confirmation, ambiguity and unsupported format. It says which institution/account will be reused
or created. One leader panel covers selected statements sharing an identity; every file still shows
its own parse and reconciliation result after confirmation.

Accounts show masks by default. An accessible eye button reveals one full source identifier when
available and toggles it closed again; navigation/removal of the component clears plaintext. The
value is not copied into URLs, local/session storage, analytics, generic application state or
transaction rows. A source mask disables reveal and explains that the statement did not provide a
complete value.

## Existing-ledger backfill

The current ledger's import batches already bind each source document to the account and layout
that produced its transactions. Backfill uses that provenance—not filenames or narrations—to re-read
each stored PDF. This makes the historical account relation the selection boundary while the PDF
supplies issuer and identifier evidence.

Preview groups documents per account, reports complete/mask-only/missing/conflicting captures and
shows the institution resolution without full values. Apply requires a fresh encrypted database
backup, authenticates each source, and updates an account only when all usable documents agree.
Password-protected sources use credentials supplied through the existing private request/environment
boundary and never persist those passwords.

Repeat preview after apply must return no remaining eligible change. Transaction, batch, document,
balance, card-cycle, transfer and reportable totals remain byte/value-identical; only institution
and identifier records may change.

## Failure and privacy rules

- No ambiguous issuer, account or mask is selected by score, filename or row order.
- No full identifier appears in ordinary/raw serialized responses, exception text, logs, tracing,
  URLs, screenshots, public evidence or browser persistence.
- Reveal is the sole general API response allowed to contain a complete identifier.
- Redaction covers ASCII, formatted identifiers and all Unicode decimal digit scripts; opaque
  ciphertext and digests are not mistaken for plaintext identifier leaks.
- Cross-household institution aliases, accounts, identifiers, documents and reveals are refused.
- A statement that cannot identify, normalise or reconcile does not become stageable merely because
  its account identity matched.

## Required evidence

Synthetic tests fail first for: exact full-identifier matching; unique and ambiguous mask matching;
same institution with a second account type; new-account confirmation without institution
duplication; conflicting issuer/type/currency; unsupported layouts; multi-file one-confirmation
behaviour; encryption round trips and nonce uniqueness; missing/wrong/versioned keys; reveal
authorization and no-store headers; mask-only reveal refusal; cross-household isolation; idempotent
backfill and conflict refusal; and raw plus serialized Unicode/formatted identifier leakage.

The local acceptance run reprocesses representative real statements for every affected account
through supported authenticated application operations. It verifies that the current four layouts
derive their institutions from PDF content, existing accounts are reused, complete source numbers
are revealable, source-masked cards remain honest, no duplicate account/institution is created, and
no ledger fact changes. The repository records counts and pass/fail outcomes only—never filenames,
issuer-private wording, identifiers, amounts, screenshots or extracted financial data.

If acceptance passes, update only `docs/superpowers/progress.md` as live status and deliver the
acceptance record through the normal documentation-only PR flow. Stop for the owner's phase naming,
sequence and priority discussion; do not start slice 1.5.

## Documents the implementation must reconcile

The implementation updates `security-and-privacy.html` (application data versus source control,
identifier encryption and reveal), `data-model.html` and `schema.md` (identifier/alias relations),
`api-contracts.html` (parse, confirmation, account representation, reveal and backfill),
`statement-ingestion.html` (issuer/account resolution), `.env.example` and local-production setup,
the four seed-layout notes, ADR index and `progress.md`. Existing text saying complete identifiers
are never stored or returned is superseded by ADR-0021 and must not survive as an active rule.

The earlier slice 1.8 spec remains the record of Tasks 8a1–8b. Its mask/hash-only storage clauses
and manual mapping acceptance are superseded by this design; its dry-run, reconciliation,
create-once, transient-password and no-real-data-in-Git constraints remain in force.
