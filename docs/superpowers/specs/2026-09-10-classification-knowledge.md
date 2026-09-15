# Classification knowledge and correction safety

Owner direction: 10 September 2026. The statement collection is real financial data, not mock
fixtures. Use it privately to discover patterns; publish only synthetic examples and structural
findings. The approved direction is automatic categorisation, reusable learning and grouped
questions for uncertainty. A local LLM is to be planned, not assumed necessary or installed now.

Workspace: `/Users/sandjaieravi/dev/OneLedger`. First branch:
`fix/p1-3-loan-rule-boundary`. Base and PR target: `develop`.

## Evidence and immediate safety issue

The ledger's merchant and alias stores are empty; its saved rules are the generic narration
pack. The import cascade and unclassified-only backfill already exist. Missing household
knowledge is therefore distinct from a missing classifier.

The loan seed uses a substring matcher for a short abbreviation and assigns `loan_repayment`.
Unrelated merchant and remittance words can contain that substring. The reporting view excludes
that type, so this is a financial correctness defect, not merely an untidy category. First fix
token boundaries and direction with synthetic regressions. Changing the shipped seed does not
repair already-loaded rules or historical rows; their correction is a separate, evidenced step.

Some stored narrations contain more than one apparent payment header. Treat these as ambiguous
until checked against the source page; an appended provider name must not decide the preceding
posting. Directory coverage also includes investment detail/summary documents that are not
bank/card transaction imports. A readable PDF is not necessarily an imported ledger document.

## What the application must distinguish

1. **Direction:** debit or credit from the original signed posting, immutable.
2. **Counterparty:** business, person, own account, institution, or unresolved entity.
3. **Intermediary/service:** payment processor, wallet, marketplace or specific sub-service.
   A processor is not automatically the final merchant; one brand can expose several services.
4. **Economic role:** purchase, earned income, refund, reimbursement, external support, internal
   transfer, investment funding/redemption, principal repayment, fee or interest. Credit alone
   does not establish income. A payment rail or personal name does not establish a purchase type.
5. **Category:** the supported level in the existing taxonomy, with household defaults permitted
   when the owner supplies them. Broad shopping is preferable to invented item-level detail.

Investment funding does not establish units, NAV, cost basis or current asset value. A redemption
is not all investment income. A loan instalment does not identify its principal/interest split.
Card repayments are not new purchases. Keep these distinctions in reports as well as labels.

## Reuse before schema expansion

Retain `merchant`, `merchant_alias`, `classification_rule`, category taxonomy and transaction
source/provenance columns. Merchant knowledge is household-scoped data, not producer-specific
code. Preserve stable IDs and forward-only migrations; do not reset or recreate any database.

Add the following concepts in bounded migrations after checking existing `audit_log` coverage:

- **Counterparty identity:** a household-scoped entity with kind and display name, an optional
  merchant reference, and separately represented aliases/roles. Own-account relations point to
  an existing household account; a person must not be created as a merchant merely to categorise
  a payment. Reuse merchant identity rather than maintaining a second business-name catalogue.
- **Knowledge evidence:** source kind (`owner`, `public_source`, `statement`, `model_proposal`),
  public source URL where applicable, verification time, author/source attribution, matcher
  version, scope and `active`/`pending`/`revoked` state — a version created pending is invisible to
  every classifier and to `GET /merchant-knowledge` until a conflict-free apply activates it, or an
  owner revokes it unused. Store no copied web pages or private search queries.
- **Classification decision:** immutable before/after values for changed derived fields,
  transaction, household, knowledge version, actor, reason, run ID and time; corrections append
  a superseding event. Undo is a conditional new event, never deletion of history.
- **Proposal/run:** a persisted preview of exact IDs and expected prior derived state, result
  and reason, expiry, applied/rejected/conflict state, and idempotency key. No preview may become
  an unbounded global write when applied.

Every reference must belong to the same household, enforced in database and API tests. Fields
holding evidence or model explanations need explicit length limits and the existing redaction
boundary. Money remains Decimal / NUMERIC(20,4) / strings on the wire.

## Matching and safe application

Use one knowledge resolver for import and retrospective classification. Prior matches must come
from trusted decisions, not recursively amplified low-confidence guesses or known-defective
seed results. Short operation codes match tokens; Unicode, punctuation, references and truncated
service names get independent negative fixtures. Do not modify raw narration to make a match fit.

Rules/aliases identify a merchant or category only within their evidenced scope: direction,
service, account where appropriate, and unambiguous narration. Conflicting highest-priority
interpretations abstain; arbitrary UUID ordering is not proof of correctness.

Preview lists exact proposed changes and their reasons. Applying it re-checks household, source
identity and prior derived state, refuses stale rows atomically, preserves manual overrides and
confirmed transfer legs, and writes decision history in the same transaction. Repeating an
idempotency key returns the original result. Batches are bounded and resumable.

Historical repair first targets the precise defective rule/version and derived state. A fresh
encrypted backup is verified before applying it. Rows lacking sufficient evidence return to
unresolved classification without inventing a merchant. Correct transaction type and category
together; correcting the category alone leaves a falsely excluded expense hidden. Reportable
totals are compared before/after using frozen values, with each delta explained by selected rows.

Transfer linking uses ADR-0020 and the existing atomic link/unlink functions, not category
backfill. This project does not introduce score-only auto-linking, external bank actions, or
single-sided invented counterpart entries. Loan/investment exclusions require evidence of the
economic role, not merely a provider or abbreviation.

## User experience and learning

Recognised rows arrive categorised. Show merchant, category, economic role and a short “why”
explanation separately. Group unresolved patterns by stable identity and question, not by amount
similarity alone. Ask once where the answer generalises, allow a one-transaction exception, and
preview the scope of any remembered answer. Existing owner overrides survive later imports and
knowledge upgrades. Show changes and conditional undo in an activity view.

The current assisting agent can research public business identities and prepare private knowledge
under ADR-0020. This is not a claim that embedded merchant research or correction learning has
already shipped. No raw narration, personal counterparty, account, amount or portfolio is sent
to a search provider. The production stack remains empty of this household's working data.

## Local LLM fallback: explicit Phase 5 candidate

The roadmap already places AI classification in Phase 5. Retain that phase; plan an offline
evaluation before selecting a runtime or model. Local-first is the deployment requirement for
this candidate, not a promise that a model will know an unfamiliar payee's purpose.

- Evaluate only after deterministic matching abstains. Compare against the rules-only baseline
  on privately labelled, held-out merchants/time periods; prevent near-duplicate leakage.
- Measure category precision/coverage, harmful economic-role mistakes, abstention, memory and
  latency on the owner's hardware. Published reports contain metrics, never real examples.
- A runtime binds to loopback, has outbound access disabled during inference, has no database
  write credentials or tools, and receives bounded redacted text as untrusted data. Downloading
  weights and accepting a model licence is an explicit setup step, not a hidden dependency.
- Constrain output to existing category/role identifiers, evidence spans and an abstention result.
  Validate all output in the API. Model-generated confidence is not a calibrated probability.
- A model proposal cannot confirm a transfer, alter an original, create a holding or overwrite a
  manual decision. It must use the same preview/history/correction boundary as other proposals.
- Record model checksum/version, prompt/schema version and decision provenance. Disable prompts
  and ledger logging in runtime telemetry; test with egress blocked and adversarial narration.
- No model is installed or invoked by this plan. Before runtime implementation, a superseding ADR
  must settle ADR-0003's privacy/evaluation prerequisites and the deployment contract explicitly.

## Acceptance

Synthetic tests cover short-token collisions, Unicode names, processor versus merchant, opposing
directions, refund versus income, competing interpretations, manual overrides, stale preview,
idempotent apply, conditional undo, cross-household references and frozen reporting deltas.
Live acceptance reports actual classified, unresolved, corrected and excluded counts separately.
It does not claim complete classification while personal-purpose questions or source gaps remain.
Investment statement ingestion retains slice 1.5's separate schema/reconciliation gates and must
not double-count bank funding as both a new asset and a purchase lot.
