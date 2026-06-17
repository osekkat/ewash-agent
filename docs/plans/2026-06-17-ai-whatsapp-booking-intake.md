# AI WhatsApp Booking Intake Bot Implementation Plan

> **For Hermes:** Use `subagent-driven-development` to implement this plan task-by-task. Load `ewash-platform-workflows`, `whatsapp-business-operations`, and `software-quality-workflows` before editing code.

**Goal:** Add an AI-assisted intake layer to the existing eWash WhatsApp Business bot so customers can write naturally in French, Darija, Arabic, or English, while the platform still validates, prices, persists, and routes every booking as `pending_ewash_confirmation` for human eWash confirmation.

**Architecture:** Keep the current Meta WhatsApp Cloud API webhook and the existing deterministic booking engine as the source of truth. Insert a narrow AI intake layer only at safe conversation boundaries: it classifies free-text customer intent, extracts candidate booking fields, asks for missing details, and hands a validated draft into the existing booking/persistence path. The AI never confirms bookings, never computes prices independently, never bypasses catalog validation, and never exposes internal system/Hermes messages to clients.

**Tech Stack:** FastAPI, Meta WhatsApp Cloud API, SQLAlchemy/Alembic, existing `app/handlers.py` state machine, existing `/api/v1` booking validation helpers, Python provider abstraction for LLM calls, pytest, SQLite in tests, Postgres in production.

---

## 1. Product decision

Do **not** replace the WhatsApp bot with a fully autonomous free-form assistant.

Build a hybrid:

```text
Customer free text on eWash WhatsApp Business number
  -> app/main.py POST /webhook
  -> app/handlers.py
  -> AI intake classifier/extractor only when safe
  -> deterministic catalog/validation/session state
  -> existing Booking dataclass
  -> assign_booking_ref + persist_confirmed_booking(...)
  -> status = pending_ewash_confirmation
  -> staff/admin confirms from /admin/bookings
```

The AI layer improves the customer experience, but the platform remains the booking authority.

---

## 2. Non-negotiable invariants

1. Customer-facing paths must never write `status = "confirmed"` directly.
2. Admin confirmation remains the only path from `pending_ewash_confirmation` to `confirmed`.
3. Pricing must come from `app/catalog.py::service_price()` or the already-validated API/domain helpers.
4. AI output is advisory only; every extracted field is revalidated by deterministic code before any state transition or database write.
5. The bot must not expose provider names, prompts, tokens, stack traces, auto-compaction messages, internal logs, code/config details, or Hermes process notes to customers.
6. When uncertain, ask one concise customer-facing question or route to staff; do not invent data.
7. The first production target should be the official WhatsApp Business Platform / Meta Cloud API path already used by this repo, not a QR/Web session bridge.

---

## 3. Current codebase anchors

Important existing files:

- `app/main.py` — Meta webhook receiver and `/webhook` entrypoint.
- `app/handlers.py` — current WhatsApp state machine and booking conversation.
- `app/state.py` — in-memory per-phone sessions.
- `app/booking.py` — `Booking` dataclass and `from_api_payload()` conversion.
- `app/api.py` — PWA-facing API router with mature booking validation and persistence orchestration.
- `app/api_schemas.py` — Pydantic request/response schema definitions for API booking creation.
- `app/api_validation.py` — deterministic validation/cleaning helpers.
- `app/catalog.py` — services, active centers, slots, closed dates, pricing source of truth.
- `app/persistence.py` — ref allocation, booking persistence, customer history.
- `app/models.py` — booking status FSM and database models.
- `app/meta.py` — Meta Cloud API send/extract helpers.
- `app/notifications.py` — phone normalization and staff notification.
- `tests/` — existing booking, API, state machine, rate-limit, and persistence coverage.

Known current state:

- The PWA API work from `plan.md` appears implemented in the repo now (`app/api.py`, `app/api_schemas.py`, many `tests/test_api_*` files, migrations through `0006+`). The AI intake implementation should reuse those validation/conversion patterns instead of rebuilding booking rules.
- `app/handlers.py` currently sends `IDLE` free text to `_handle_idle()`, which shows the menu unless it is an owner cash-ledger note. This is the right place to introduce AI classification for customer booking intent.

---

## 4. Target customer experience

### 4.1 Natural first message

Customer:

```text
Salam bghit lavage complet demain matin à Maarif pour une Clio blanche
```

Bot:

```text
Avec plaisir. J’ai noté : lavage complet, demain matin, Renault Clio blanche, secteur Maarif.

Il me manque juste l’adresse exacte et votre nom pour préparer la demande.
```

### 4.2 Complete one-shot request

Customer:

```text
Bonjour, je veux réserver un lavage extérieur pour une Dacia Duster noire samedi à 10h, à domicile à Bouskoura. Je m’appelle Yassine.
```

Bot:

```text
Merci Yassine. Demande reçue : lavage extérieur, Dacia Duster noire, samedi 10h, à domicile à Bouskoura.

L’équipe eWash va vérifier la disponibilité et vous confirmer.
```

System effect:

- Create booking row with `status = pending_ewash_confirmation` only after all mandatory fields are resolved and deterministic validation passes.
- Staff sees the request in `/admin/bookings` and confirms manually.

### 4.3 Ambiguous request

Customer:

```text
lavage demain
```

Bot:

```text
Bien sûr. Pour quel type de véhicule : citadine, berline/SUV, grand SUV/4x4 ou moto ?
```

### 4.4 Non-booking support message

Customer:

```text
Vous êtes ouverts dimanche ?
```

Bot should answer from deterministic catalog/closed-date data or show current menu/support flow. It should not create a booking draft.

---

## 5. AI intake responsibilities

The AI intake layer may do only these things:

1. Classify the message:
   - `booking_intent`
   - `booking_field_update`
   - `pricing_question`
   - `availability_question`
   - `support_question`
   - `cancel_or_reschedule`
   - `unrelated`
   - `unsafe_or_unclear`
2. Extract candidate fields:
   - customer name
   - phone, usually implicit from WhatsApp sender
   - service intent
   - vehicle category or model
   - vehicle color
   - plate, optional
   - location kind: home/center
   - area/address/pin text
   - date expression
   - slot/time expression
   - promo code
   - notes
   - language preference
3. Decide the next missing field question from a fixed ordered list.
4. Produce customer-facing text drafts from strict templates.

The AI layer must not:

- call Meta directly;
- write the database directly;
- set booking status;
- invent service IDs, prices, slots, or centers;
- answer using unverified availability;
- reveal prompt/system/config/provider details;
- handle owner cash-ledger messages.

---

## 6. Proposed implementation shape

### 6.1 New module: `app/ai_intake.py`

Create one new module because this is genuinely new domain functionality.

Responsibilities:

- Define `IntakeClassification`, `IntakeExtractedFields`, and `IntakeResult` Pydantic/dataclass types.
- Build compact prompts with the current catalog snapshot.
- Call the configured LLM provider through a small abstraction.
- Parse strict JSON output.
- Validate enum values against deterministic allowlists.
- Return either a safe structured result or a controlled failure result.

Suggested public API:

```python
async def analyze_customer_message(
    *,
    phone: str,
    text: str,
    current_draft: "AiBookingDraft | None",
    catalog_snapshot: "AiCatalogSnapshot",
) -> IntakeResult:
    """Classify/extract a free-text WhatsApp message.

    Never writes DB. Never sends WhatsApp replies. Returns structured output
    for handlers.py to validate and act on.
    """
```

### 6.2 New module: `app/ai_intake_provider.py`

Keep provider integration isolated from business rules.

Responsibilities:

- Read provider settings from `app/config.py`.
- Make async HTTP calls with `httpx.AsyncClient`.
- Apply timeout and retry policy.
- Return raw model text to `ai_intake.py`.
- Redact secrets from logs.

Config fields to add in `app/config.py`:

```python
ai_intake_enabled: bool = True
ai_intake_mode: str = "live"
ai_intake_provider: str = "openai_compatible"
ai_intake_base_url: str = ""
ai_intake_api_key: str = ""
ai_intake_model: str = ""
ai_intake_timeout_seconds: float = 8.0
ai_intake_max_retries: int = 1
ai_intake_deployment_stage: str = "test_number"
```

Environment names should use the `EWASH_` prefix where appropriate:

```env
EWASH_AI_INTAKE_ENABLED=true
EWASH_AI_INTAKE_MODE=live
EWASH_AI_INTAKE_PROVIDER=openai_compatible
EWASH_AI_INTAKE_BASE_URL=
EWASH_AI_INTAKE_API_KEY=
EWASH_AI_INTAKE_MODEL=
EWASH_AI_INTAKE_TIMEOUT_SECONDS=8
EWASH_AI_INTAKE_MAX_RETRIES=1
EWASH_AI_INTAKE_DEPLOYMENT_STAGE=test_number
```

### 6.3 New model/table: `ai_intake_events`

Add audit-friendly logging without storing secrets or full prompts.

Suggested fields:

- `id`
- `created_at`
- `phone_hash`
- `source_message_id`
- `session_state_before`
- `classification`
- `confidence`
- `extracted_json`
- `missing_fields_json`
- `action_taken`
- `error_code`
- `latency_ms`
- `intake_mode`
- `deployment_stage`

Do not store API keys, provider responses containing internal prompt text, or any token. Raw customer message is already captured by `persist_whatsapp_inbound_message`; if linking is needed, use message ID and phone hash.

Migration:

- Create `migrations/versions/20260617_0011_ai_intake_events.py` or next available revision number.
- Do not edit existing migrations.

### 6.4 Extend session state

Modify `app/state.py` only as needed to support an AI draft.

Suggested new session fields:

```python
ai_draft: AiBookingDraft | None = None
ai_last_question: str = ""
ai_intake_attempts: int = 0
```

If state currently uses simple classes without dataclasses, follow existing style rather than overengineering.

Draft fields should map cleanly to the existing `Booking` dataclass or `BookingCreateRequest` schema.

### 6.5 Reuse existing API booking validation

Do not create a parallel validator for AI.

Preferred approach:

1. Convert a complete AI draft into an internal `BookingCreateRequest`-like object.
2. Reuse the same validation helpers used by `app/api.py`:
   - service/category validation
   - addon validation
   - center validation
   - slot/date validation
   - phone normalization
   - free-text cleaning
3. Reuse `booking.from_api_payload()` where possible to produce `Booking`.
4. Persist through `assign_booking_ref()` + `persist_confirmed_booking(..., source="whatsapp_ai")` if a new source value is added, or `source="whatsapp"` plus `intake_channel="ai"` if the existing model should avoid source expansion.

Decision to make during implementation:

- If `bookings.source` currently restricts values to `whatsapp|api|admin`, either:
  - add `whatsapp_ai` with migration and admin badge, or
  - keep `source="whatsapp"` and record AI involvement only in `ai_intake_events`.

Recommended v1: keep `source="whatsapp"` to avoid report churn, and log AI involvement in `ai_intake_events`.

---

## 7. Handler routing rules

Modify `app/handlers.py` carefully.

### 7.1 Entry conditions

AI intake may run only when:

- `settings.ai_intake_enabled` is true;
- inbound message has text;
- current state is `IDLE` or an explicit AI intake state;
- message is not a global command (`menu`, `start`, `bonjour`, `salam`, `reset`, etc.);
- message was not captured by `_try_capture_cash_ledger_message()`;
- sender is a normal customer, not an owner-only operations workflow message.

### 7.2 Live test-number mode

The first rollout target is a live WhatsApp test number, not shadow mode. On that test number, AI intake should be customer-visible from the start so Omar/Oussama can evaluate the real conversational experience end to end.

When `ai_intake_enabled = true` and `ai_intake_mode = "live"` on the test number:

- If `classification=booking_intent` and confidence is above threshold, start/continue AI intake.
- If enough fields exist, show a deterministic recap and ask for final customer confirmation.
- On customer confirmation, persist as `pending_ewash_confirmation` only.
- If low confidence or unsupported intent, fall back to existing menu/state machine.
- Log every AI intake attempt to `ai_intake_events` for review.

### 7.3 Production eWash number mode

After live validation on the test number, deploy the same live intake behavior directly to the official eWash WhatsApp Business number. Keep the safety rails unchanged: deterministic validation, fixed reply templates, no direct `confirmed` writes, and technical-leakage bans.

### 7.4 Preserve existing deterministic buttons/lists

The AI can skip ahead only when it has reliable fields. For ambiguous choices, continue using existing buttons/lists because they are safer and fit WhatsApp constraints.

Examples:

- If the AI detects “SUV” but not exact category, ask with existing category list/buttons.
- If the AI detects “lavage complet”, map to the catalog service only if deterministic synonym matching agrees.
- If the AI detects “demain matin”, choose date = tomorrow but ask the customer to choose a slot from active slots if no exact slot is present.

---

## 8. Deterministic field mapping

Create a small deterministic mapper before trusting AI fields.

Suggested module: `app/ai_intake_mapping.py` if it grows, otherwise keep inside `app/ai_intake.py`.

Rules:

### 8.1 Vehicle category

Map model/category hints to current pricing categories:

- `MOTO` for moto/scooter terms.
- `A` for citadine/small city cars where clear.
- `B` for berline/SUV compact where clear.
- `C` for grand SUV/4x4/van where clear.

If uncertain, ask the category question.

### 8.2 Service intent

Allowed outputs are existing catalog IDs only.

The AI should output a human intent like `lavage_complet`; deterministic mapping chooses a service ID only when one synonym has a clear match.

Examples:

- “extérieur” -> exterior wash service ID if present.
- “complet” -> complete wash service ID if present.
- “intérieur/salon” -> salon/detailing service if present.
- “polissage” -> polishing detailing service if present.

If the customer asks “lavage” generically, ask which service or show service list.

### 8.3 Dates and times

The AI may normalize expressions (`demain`, `samedi`, `matin`) into candidate date/slot hints, but deterministic code must validate:

- closed dates;
- active time slots;
- at least two hours in the future, server-side Africa/Casablanca;
- ambiguous weekday beyond one week should ask.

### 8.4 Location

- If WhatsApp location pin exists, keep existing location handling.
- If text contains area but not exact address, ask for exact address or location pin.
- If center name matches active center, set `location_mode=center` and validate center ID.

---

## 9. Customer-facing reply rules

All AI-assisted replies must be rendered through fixed templates in code, not copied verbatim from the LLM.

Templates should be short, French-first, and optionally Darija-friendly when the customer's language is detected.

Examples:

```python
MISSING_NAME = "Parfait. À quel nom dois-je préparer la demande ?"
MISSING_CATEGORY = "Pour quel type de véhicule : citadine, berline/SUV, grand SUV/4x4 ou moto ?"
MISSING_ADDRESS = "Pouvez-vous m’envoyer l’adresse exacte ou une localisation WhatsApp ?"
MISSING_SLOT = "Quel créneau préférez-vous ?"
FINAL_RECAP = (
    "Merci {name}. Demande reçue : {service}, {vehicle}, {date} {slot}, {location}.\n\n"
    "L’équipe eWash va vérifier la disponibilité et vous confirmer."
)
```

Hard ban in customer replies:

- “AI model”
- “prompt”
- “system message”
- “auto-compaction”
- “tool call”
- “database error” with raw details
- stack traces
- provider/API names
- internal configuration

If an internal failure occurs, send:

```text
Désolé, je n’ai pas pu traiter votre message. Vous pouvez utiliser le menu ou nous envoyer votre demande en une phrase.
```

---

## 10. Detailed task breakdown

### Task 1: Add AI intake configuration

**Objective:** Add live-mode AI intake settings and `.env.example` documentation for a test-number-first rollout.

**Files:**

- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_config_defaults.py`

**Steps:**

1. Add the `ai_intake_*` fields to `Settings` for live operation on the configured WhatsApp test number.
2. Document env vars in `.env.example` without real secrets.
3. Extend config default tests to assert:
   - AI intake mode is `live` for the configured deployment.
   - deployment stage can be labelled `test_number` or `ewash_number` for logs/admin visibility.
   - provider calls fail closed with a customer-safe fallback when credentials are missing or invalid.
4. Run:

```bash
source .venv/bin/activate && python -m pytest -q tests/test_config_defaults.py
```

Expected: pass.

---

### Task 2: Create AI intake schemas and result types

**Objective:** Define strict structured outputs before provider integration.

**Files:**

- Create: `app/ai_intake.py`
- Test: `tests/test_ai_intake_schemas.py`

**Steps:**

1. Add enums/literals for allowed classifications.
2. Add `IntakeExtractedFields` with optional fields only.
3. Add `IntakeResult` with confidence, missing fields, and safe error code.
4. Add tests for:
   - valid JSON parse;
   - unknown classification rejected or converted to `unsafe_or_unclear`;
   - confidence outside 0..1 rejected;
   - overlong text fields cleaned/truncated.
5. Run targeted test.

---

### Task 3: Add deterministic catalog snapshot builder

**Objective:** Give the AI a compact, current, non-secret view of bookable options.

**Files:**

- Modify: `app/ai_intake.py`
- Test: `tests/test_ai_intake_catalog_snapshot.py`

**Steps:**

1. Build snapshot from `app/catalog.py`:
   - categories;
   - services and IDs;
   - active centers;
   - active slots;
   - closed dates.
2. Ensure prices are not necessary in the AI prompt unless asking pricing questions; if included, they must come from `service_price()`.
3. Test snapshot contains only public booking facts and no secrets.

---

### Task 4: Implement provider abstraction with fake-first tests

**Objective:** Isolate external LLM calls behind a testable async interface.

**Files:**

- Create: `app/ai_intake_provider.py`
- Test: `tests/test_ai_intake_provider.py`

**Steps:**

1. Implement `call_ai_intake_model(prompt, *, timeout_seconds)` using `httpx.AsyncClient` for OpenAI-compatible chat completions.
2. Read config from `settings.ai_intake_*`.
3. Add timeout and one retry by config.
4. Never log API key or full request body.
5. Tests should monkeypatch the HTTP transport; no real network calls.
6. Test missing/invalid provider config returns a controlled `provider_unavailable` result and never exposes technical details to customers.

---

### Task 5: Implement prompt and JSON parser

**Objective:** Convert customer text + current draft into strict JSON output.

**Files:**

- Modify: `app/ai_intake.py`
- Test: `tests/test_ai_intake_parser.py`

**Prompt requirements:**

- Explain the assistant is an intake parser, not a booking confirmer.
- Require JSON only.
- Restrict classifications to allowed labels.
- Require `null` for unknown fields.
- Require no customer-facing prose in the JSON.
- Tell the model not to invent dates, addresses, services, or prices.

**Tests:**

- JSON fenced in markdown is parsed safely.
- Extra prose causes controlled failure or extraction of the first JSON object only.
- Missing required top-level keys returns `parse_error`.
- Unknown service/category stays as free-text intent, not trusted ID.

---

### Task 6: Add deterministic mapping and validation gate

**Objective:** Convert AI suggestions into trusted booking draft updates only when deterministic checks agree.

**Files:**

- Modify: `app/ai_intake.py` or create `app/ai_intake_mapping.py` if needed.
- Test: `tests/test_ai_intake_mapping.py`

**Steps:**

1. Map service intents to catalog IDs using explicit synonyms.
2. Map vehicle hints to category only when clear.
3. Normalize date/slot hints and validate with existing helpers.
4. Reject invalid center/service/addon/category combinations.
5. Return missing fields in deterministic priority order:
   - name
   - vehicle category/model
   - service
   - location/address
   - date
   - slot
   - optional notes
6. Test French, Darija, and English examples.

---

### Task 7: Add AI draft to session state

**Objective:** Store partial AI-assisted booking details between messages.

**Files:**

- Modify: `app/state.py`
- Test: `tests/test_ai_intake_state.py`

**Steps:**

1. Add a small draft object or dict to session.
2. Ensure `state.reset(phone)` clears it.
3. Ensure stale session cleanup clears it.
4. Test reset/menu/cancel behavior.

---

### Task 8: Wire live test-number classifier in `handlers.py`

**Objective:** Enable customer-visible AI intake on the WhatsApp test number while preserving all deterministic booking safeguards.

**Files:**

- Modify: `app/handlers.py`
- Modify/Create: `app/persistence.py` helper for event logging if needed.
- Modify: `app/models.py`
- Create: Alembic migration for `ai_intake_events`.
- Test: `tests/test_ai_intake_live_test_number.py`

**Steps:**

1. In `handle_message()`, after global commands and cash-ledger capture, call AI intake only if enabled and state is safe.
2. In live test-number mode, let high-confidence booking messages enter the AI intake flow immediately.
3. Add tests with monkeypatched `analyze_customer_message()`:
   - called for natural booking text in IDLE;
   - not called for menu/reset;
   - not called when cash-ledger capture handled;
   - provider failure falls back to the existing menu or generic safe message;
   - customer-visible AI replies are rendered only from fixed templates.

---

### Task 9: Add live AI intake state flow

**Objective:** Let AI-assisted intake ask missing questions and collect fields.

**Files:**

- Modify: `app/handlers.py`
- Test: `tests/test_ai_intake_live_flow.py`

**Steps:**

1. Add a state such as `AI_BOOKING_INTAKE` or keep IDLE with `sess.ai_draft`; choose the least invasive style consistent with `state.py`.
2. When AI detects booking intent with high confidence, create/update draft.
3. Ask exactly one missing-field question at a time using fixed templates.
4. If low confidence, fall back to `_send_menu()`.
5. If customer types `menu` or `annuler`, reset draft and use existing global behavior.
6. Tests should simulate multi-turn messages:
   - first text gives service/date/vehicle;
   - second gives address/name;
   - bot asks next missing slot;
   - no DB write occurs until final confirmation.

---

### Task 10: Build final recap and explicit customer confirmation

**Objective:** Prevent accidental bookings from one vague AI parse.

**Files:**

- Modify: `app/handlers.py`
- Test: `tests/test_ai_intake_confirmation.py`

**Steps:**

1. When all required fields are available, send a deterministic recap.
2. Ask the customer to confirm with buttons:
   - `✅ Envoyer la demande`
   - `Modifier`
   - `Annuler`
3. Do not persist before the customer taps/sends confirmation.
4. If “Modifier”, ask what to change and continue AI intake.
5. If “Annuler”, reset state.

---

### Task 11: Persist confirmed intake draft through existing booking path

**Objective:** On explicit customer confirmation, create a normal pending eWash booking.

**Files:**

- Modify: `app/handlers.py`
- Possibly modify: `app/booking.py` conversion helper if API schema reuse needs a small adapter.
- Test: `tests/test_ai_intake_persistence.py`

**Steps:**

1. Convert draft to the existing `Booking` dataclass.
2. Compute price through `catalog.service_price()`.
3. Allocate reference with `assign_booking_ref()`.
4. Persist with `persist_confirmed_booking()`.
5. Ensure resulting DB status is `pending_ewash_confirmation`.
6. Call `notify_booking_confirmation` exactly like the existing WhatsApp path.
7. Send customer message:

```text
Merci. Votre demande {ref} est bien reçue. L’équipe eWash va vérifier la disponibilité et vous confirmer.
```

8. Tests assert no path writes `confirmed`.

---

### Task 12: Add audit table and admin visibility

**Objective:** Let Omar/eWash review AI behavior without exposing it to customers.

**Files:**

- Modify: `app/models.py`
- Migration: next Alembic file.
- Modify: `app/admin.py`
- Test: `tests/test_admin_ai_intake_events.py`

**Admin page/card v1:**

- Add a small section in admin dashboard:
  - AI intake live/test-number/eWash-number deployment status.
  - current intake mode and deployment stage.
  - last 20 intake events.
  - classification counts.
  - parse/provider errors.

Do not include full secrets or raw prompts.

---

### Task 13: Add safe fallback/error handling

**Objective:** Keep customer flow reliable when the AI provider fails.

**Files:**

- Modify: `app/handlers.py`
- Modify: `app/ai_intake.py`
- Test: `tests/test_ai_intake_failures.py`

**Cases:**

- Provider timeout.
- Provider HTTP 500.
- Invalid JSON.
- Low confidence.
- Unsupported language.
- Message too long.

Expected behavior:

- Log/audit internally.
- Customer sees existing menu or one generic helpful fallback.
- No DB write.
- No stack trace or technical text sent to customer.

---

### Task 14: Add regression tests for current bot behavior

**Objective:** Prove AI intake does not break existing deterministic booking flows.

**Files:**

- Modify existing handler tests or add `tests/test_ai_intake_existing_flow_regression.py`.

**Tests:**

1. `menu` still opens menu.
2. `reset` still resets.
3. Returning customer prompt still works.
4. Button/list booking path still persists pending eWash confirmation.
5. Cash ledger owner messages still route to ledger before AI intake.
6. Provider failure means current deterministic menu booking remains available.

---

### Task 15: Test-number-to-eWash-number rollout controls

**Objective:** Support Omar’s chosen rollout: test live on a WhatsApp test number first, then deploy directly to the official eWash number.

**Files:**

- Modify: `.env.example`
- Modify: `docs/runbooks/pwa-api.md` or create `docs/runbooks/ai-whatsapp-intake.md`
- Test: config tests only.

**Rollout stages:**

1. Configure the WhatsApp test number and set `EWASH_AI_INTAKE_ENABLED=true`, `EWASH_AI_INTAKE_MODE=live`, and `EWASH_AI_INTAKE_DEPLOYMENT_STAGE=test_number`.
2. Run live end-to-end booking conversations on the test number using realistic French, Darija, Arabic, and English messages.
3. Review `ai_intake_events`, created pending bookings, staff notifications, and customer replies from the test-number run.
4. Fix prompt/mapping/template issues found during live test-number validation.
5. Point the same live flow at the official eWash WhatsApp Business number with `EWASH_AI_INTAKE_DEPLOYMENT_STAGE=ewash_number`.
6. Monitor the official number closely after launch for:
   - no confirmed-status violations;
   - no technical leakage;
   - no pricing drift;
   - acceptable fallback rate.

No shadow-mode or small-allowlist phase is required by this rollout plan. The safety boundary is the separate test number first, followed by direct deployment to the official eWash number once the test-number behavior is accepted.

---

## 11. Test strategy

Run targeted tests after each task, then full suite at the end:

```bash
source .venv/bin/activate && python -m pytest -q tests/test_ai_intake_*.py
source .venv/bin/activate && python -m pytest -q
```

For any `mobile-app` changes: none are expected in v1. If later changes touch `mobile-app/*.jsx`, manually open `mobile-app/index.html` and walk the flow because there is no JS test suite.

No real AI provider calls in tests. All provider tests use mocked HTTP transport or monkeypatched provider functions.

---

## 12. Security, privacy, and compliance notes

1. Never log API keys or full provider payloads.
2. Prefer phone hashes in AI event logs.
3. Keep raw customer message references through existing WhatsApp inbound persistence rather than duplicating raw text everywhere.
4. Treat AI-extracted PII as customer booking data.
5. Add a data-retention decision for `ai_intake_events` before long-term production use.
6. Customer deletion/data-erasure flows should eventually include AI event rows if they are linked to customer identity.
7. Provider choice matters: if customer messages leave the eWash infrastructure, document the processor and retention policy.

---

## 13. WhatsApp Business connection recommendation

For production client intake, use the official WhatsApp Business Platform / Meta Cloud API path already represented by this repo, not a QR-paired WhatsApp Web bridge.

Reason:

- More reliable for a business number.
- Cleaner webhooks into `app/main.py`.
- Avoids conflicts with the existing bot/session.
- Supports templates, quality/rate controls, and business verification.
- Keeps the eWash platform as the source of truth.

A QR/Web bridge can be used for internal demos, but should not be the main eWash customer booking line.

---

## 14. Acceptance criteria

The feature is complete only when all criteria pass:

1. Live mode works end to end on the configured WhatsApp test number.
2. The same live mode can be moved directly to the official eWash WhatsApp Business number.
3. Live mode can intake a natural booking request and ask only missing questions.
4. AI-assisted booking persistence creates `pending_ewash_confirmation`, never `confirmed`.
5. All prices come from `catalog.service_price()`.
6. Invalid/ambiguous AI fields are rejected or clarified, not persisted silently.
7. Provider failures fall back safely with no technical leakage.
8. Admin can see basic AI intake diagnostics.
9. Tests cover happy path, ambiguity, provider failure, live test-number routing, and existing-flow regression.
10. Full backend test suite passes:

```bash
source .venv/bin/activate && python -m pytest -q
```

---

## 15. Suggested commit sequence

1. `feat: add ai intake config and schemas`
2. `feat: add ai intake provider abstraction`
3. `feat: map ai intake fields to booking drafts`
4. `feat: wire live ai intake for whatsapp test number`
5. `feat: enable live ai booking intake flow`
6. `feat: persist ai intake events for admin review`
7. `test: cover ai intake regressions and failures`
8. `docs: add ai whatsapp intake rollout runbook`

---

## 16. Open questions before implementation

1. Which LLM provider should production use, and what are its customer-data retention terms?
2. Which WhatsApp test number should be used for live validation before switching to the eWash number?
3. Should AI-assisted bookings be distinguished in `bookings.source`, or only in `ai_intake_events`?
4. Should customers receive Arabic/Darija replies, or should the bot stay French-first with Darija understanding?
5. What exact staff handoff should happen when AI confidence is low but the customer intent is urgent?

Recommended defaults:

- Start with live mode on a WhatsApp test number.
- Keep customer replies French-first.
- Keep `bookings.source="whatsapp"` for v1 and record AI involvement in `ai_intake_events`.
- Use official Meta WhatsApp Business Platform webhooks.
- After test-number approval, deploy the same live flow directly to the official eWash WhatsApp Business number.
