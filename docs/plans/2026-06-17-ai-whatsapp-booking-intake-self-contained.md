# Self-Contained Proposal: AI WhatsApp Booking Intake for eWash

> This document is intentionally self-contained. It is written so that an external reviewer or another model (for example GPT Pro) can understand the business, the existing product, the technical architecture, the constraints, and the proposed AI intake plan without reading the rest of the repository first.

**Date:** 2026-06-17

**Repository:** `osekkat/ewash`

**Local project path:** `/home/ubuntu/Projects/ewash`

**Primary question for review:** How should eWash add an AI-assisted WhatsApp booking intake layer while preserving the existing bot’s safety, operational control, pricing correctness, and two-step human confirmation workflow?

---

## 1. Executive summary

eWash is an ecological waterless car wash company based in Casablanca / Bouskoura, Morocco. WhatsApp is the natural customer channel in this market, so the product is built around WhatsApp-first booking and operations.

The current platform already has a deterministic WhatsApp booking bot, an admin portal, a database-backed booking lifecycle, service catalog/pricing logic, staff notifications, and operational tracking features. The existing bot is safe and structured, but it is not conversationally flexible: if a customer writes naturally, such as “Salam bghit lavage complet demain matin à Maarif pour une Clio blanche,” the bot may fall back to a menu instead of extracting the booking intent.

The proposed feature is not to replace the bot with an unconstrained AI agent. The recommended design is a hybrid:

```text
Customer natural-language WhatsApp message
  -> existing Meta WhatsApp Cloud API webhook
  -> existing FastAPI backend
  -> AI intake classifier/extractor only at safe boundaries
  -> deterministic validation against catalog, slots, locations, and pricing
  -> existing booking persistence path
  -> status = pending_ewash_confirmation
  -> human eWash admin/staff confirms before the booking becomes confirmed
```

This preserves the most important invariant: customer-facing flows must only create pending booking requests. A human eWash operator must confirm operational feasibility before the platform treats a booking as confirmed.

The AI layer should do only what AI is good at: understand messy customer phrasing, infer likely intent, identify missing fields, and ask natural follow-up questions. It must not become the booking authority, pricing authority, availability authority, or database writer.

---

## 2. Why eWash is compelling

### 2.1 Business positioning

eWash offers ecological waterless car washing. The operational promise is convenient vehicle cleaning without the water use and logistics burden of traditional washing. In a city like Casablanca, the value proposition combines:

- convenience: customers can request a wash from WhatsApp;
- environmental positioning: waterless / lower-water vehicle cleaning;
- mobility: service can happen at home, work, or partner sites depending on the operational setup;
- local fit: WhatsApp-first customer communication matches Moroccan user behavior;
- operational scalability: jobs, staff, vehicles, locations, and client reporting can all be coordinated from the same platform over time.

### 2.2 Why WhatsApp-first matters

In Morocco, WhatsApp is not just a chat tool; it is often the default interface for small-business commerce. Customers expect to send informal messages in French, Darija, Arabic, or a mix of languages. They may not follow a rigid form.

Examples of realistic customer messages:

```text
Salam bghit lavage demain matin ila kayn créneau
```

```text
Bonjour, lavage complet pour Duster noire à Bouskoura samedi matin svp
```

```text
Chhal lavage extérieur SUV ?
```

```text
Je veux réserver pour une Clio blanche, adresse Maarif, demain 10h
```

A deterministic menu bot is reliable, but it forces the customer into the bot’s structure. AI-assisted intake can make the product feel much more natural while still preserving deterministic business rules underneath.

### 2.3 Why the platform is more than a chatbot

The important product is not “an AI that chats.” The important product is an operational system that turns customer intent into auditable work:

- service requests become booking rows;
- booking rows have stable references like `EW-YYYY-####`;
- prices come from the service catalog;
- staff confirm feasibility;
- reminders and operational statuses can be driven from the database;
- admin users can review, confirm, reschedule, complete, and track jobs;
- customer and vehicle history can improve repeat bookings;
- future reporting, accounting, payroll, and B2B client workflows can use the same source of truth.

That is why the AI intake must plug into the platform rather than living as a separate conversational agent with its own memory.

---

## 3. Current project overview

### 3.1 High-level product components

The repository contains two main runtimes:

1. **Backend / WhatsApp bot / admin portal**
   - Directory: `app/`
   - Framework: FastAPI
   - Deployment target: Railway
   - Channel: Meta WhatsApp Cloud API
   - Persistence: SQLAlchemy + Alembic, Postgres in production, SQLite in tests
   - Admin portal: `/admin`, password-gated, inline HTML

2. **Mobile PWA prototype**
   - Directory: `mobile-app/`
   - Runtime: zero-build React loaded from CDN with Babel Standalone
   - Deployment target: Vercel
   - Languages: French and Arabic / RTL support
   - Purpose: customer-facing app prototype and eventual second booking client

The backend is the source of truth for bookings, catalog, pricing, operational records, and admin workflows.

### 3.2 Current backend capabilities

The backend currently includes:

- Meta WhatsApp webhook verification and inbound message handling;
- deterministic WhatsApp booking state machine;
- service catalog with categories, services, promo pricing, centers, slots, and closed dates;
- database-backed booking persistence;
- monotonic booking reference allocation;
- two-step staff confirmation lifecycle;
- admin portal for bookings and operations;
- staff WhatsApp notification templates;
- reminder row creation for H-2 reminders after staff confirmation;
- PWA-facing `/api/v1` endpoints for catalog/bootstrap/bookings in the current repo state;
- rate limiting for API surfaces;
- customer token model for anonymous PWA reads;
- operational tracking surfaces for WhatsApp-derived service records;
- cash ledger and payroll-related operational modules.

### 3.3 Important code files

Core backend files:

- `app/main.py` — FastAPI app, health endpoint, Meta webhook, internal ingestion endpoints, router mounting, CORS, rate-limit exception handlers.
- `app/handlers.py` — customer WhatsApp state machine and booking flow.
- `app/state.py` — in-memory per-phone conversation state.
- `app/meta.py` — Meta Cloud API helpers: verify signature, extract inbound text/interactive IDs/locations, send text/buttons/lists/templates/images.
- `app/booking.py` — `Booking` dataclass and conversion helpers.
- `app/catalog.py` — service catalog, categories, centers, closed dates, slots, promo codes, and `service_price()`.
- `app/persistence.py` — all critical database writes and reads for bookings/customers/admin queries.
- `app/models.py` — SQLAlchemy models, booking status enum/FSM, reminders, customer tokens, operational records, cash ledger, payroll, etc.
- `app/api.py` — PWA-facing `/api/v1` router and booking validation/persistence orchestration.
- `app/api_schemas.py` — Pydantic API request/response models.
- `app/api_validation.py` — deterministic validation and text cleaning helpers.
- `app/notifications.py` — phone normalization and staff notification logic.
- `app/admin.py` — admin portal routes and HTML rendering.
- `app/config.py` — environment-driven settings via `pydantic-settings`.
- `app/db.py` — engine creation, session scope, metadata initialization.

Important docs/plans:

- `AGENTS.md` — coding rules and project constraints for agents. Some historical sections may be stale because the repo has evolved, but the invariants are still important.
- `plan.md` — PWA/backend integration plan. Much of this appears implemented in the current codebase.
- `docs/plans/2026-06-17-ai-whatsapp-booking-intake.md` — first AI intake implementation plan.
- This file — self-contained version intended for external review.

### 3.4 Current repository state at time of this proposal

At the time this self-contained document was created:

- The repo is on `main` and synced to `origin/main` at commit `cd7eea2 docs: add ai whatsapp booking intake plan`.
- There are pre-existing untracked local directories `data/` and `documents/`; they are intentionally untouched.
- The previous AI intake plan was committed and pushed.

---

## 4. Current customer booking lifecycle

### 4.1 WhatsApp customer flow today

The deterministic WhatsApp flow is roughly:

```text
Customer sends WhatsApp message
  -> Meta sends webhook to POST /webhook
  -> app/main.py verifies request signature
  -> app/handlers.py loads per-phone session
  -> handler asks menu/category/service/location/date/slot/customer info questions
  -> customer confirms recap
  -> assign_booking_ref()
  -> persist_confirmed_booking()
  -> booking row is written with status pending_ewash_confirmation
  -> staff notification is sent
  -> admin/staff reviews /admin/bookings
  -> staff clicks Confirmer eWash
  -> confirm_booking_by_ewash() promotes status to confirmed
  -> reminder row is created
```

The function name `persist_confirmed_booking()` is historically confusing: despite the name, the product invariant is that customer confirmation writes `pending_ewash_confirmation`, not `confirmed`.

### 4.2 The critical two-step confirmation invariant

This is the single most important product rule:

> A customer request is not an operationally confirmed booking until eWash staff confirms it.

Reason: eWash is a physical service with finite staff, travel, locations, vehicle complexity, and time slots. A customer can ask for a booking, but eWash must verify feasibility before promising the slot.

Therefore:

- Customer-facing WhatsApp path must not write `confirmed`.
- Customer-facing PWA API path must not write `confirmed`.
- AI intake must not write `confirmed`.
- No auto-confirm timer should promote stale requests.
- No unauthenticated API should promote a booking.
- Admin confirmation must remain the only path to `confirmed`.

Important code locations:

- `app/persistence.py::persist_confirmed_booking()` — writes pending eWash confirmation by design.
- `app/persistence.py::confirm_booking_by_ewash()` — admin-only promotion to confirmed.
- `app/admin.py` — admin route that calls confirmation.
- `app/models.py::ALLOWED_STATUS_TRANSITIONS` — status transition rules.
- `app/handlers.py` — WhatsApp confirmation branch must never promote directly to confirmed.

### 4.3 Pricing invariant

All prices must come from:

```python
app/catalog.py::service_price(service_id, category, promo_code=None)
```

or helpers that call it.

Do not add:

- a separate AI pricing table;
- hardcoded prices in an AI prompt as source of truth;
- a second pricing function;
- provider-side pricing logic;
- a “discount” rule inside an LLM prompt.

If the AI sees “lavage complet,” it may propose a service intent. Deterministic code must map that to a catalog service ID and call `service_price()`.

### 4.4 Booking references

Bookings use stable references:

```text
EW-YYYY-####
```

The reference counter is database-backed and monotonic per year. The AI intake should use the existing `assign_booking_ref()` path.

### 4.5 Status lifecycle

The status lifecycle is enforced in `app/models.py`. The important high-level transition is:

```text
draft / customer request
  -> pending_ewash_confirmation
  -> confirmed
  -> technician_en_route / completed / rescheduled / cancelled / etc.
```

The exact enum has more statuses, but AI intake needs to understand only that customer-facing paths stop at `pending_ewash_confirmation`.

---

## 5. Existing technical architecture

### 5.1 Backend architecture diagram

```text
                         Customer WhatsApp
                                |
                                v
                        Meta Cloud API webhook
                                |
                                v
                         app/main.py /webhook
                                |
                                v
                      app/handlers.py state machine
                                |
         +----------------------+-----------------------+
         |                      |                       |
         v                      v                       v
   app/catalog.py        app/booking.py          app/notifications.py
   pricing/catalog       Booking dataclass       staff Meta template
         |                      |                       |
         +----------------------+-----------------------+
                                |
                                v
                         app/persistence.py
                                |
                                v
                          Postgres / SQLAlchemy
                                |
                                v
                         app/admin.py /admin
```

Planned AI insertion point:

```text
app/handlers.py IDLE/free-text boundary
  -> AI classifier/extractor
  -> deterministic mapper/validator
  -> existing booking state/persistence
```

### 5.2 FastAPI and async constraints

The project uses FastAPI native async. It does **not** use Celery, RQ, Redis queues, Dramatiq, or external worker infrastructure for normal deferred work.

Allowed patterns:

- `async def` route handlers;
- `httpx.AsyncClient` for outbound HTTP;
- FastAPI `BackgroundTasks` for simple post-response work where appropriate;
- cron-driven or single-process polling for future reminder dispatch if needed.

The AI provider call should use `httpx.AsyncClient` and a short timeout. It must not block the webhook too long. If provider latency is too high, the product should either fall back to the deterministic menu or use a “typing / follow-up” pattern if WhatsApp UX supports it safely.

### 5.3 State management

Customer WhatsApp sessions are kept in-memory by phone. This means:

- sessions reset on redeploy;
- a partially completed chat flow may restart;
- persistent business facts must be written to the database, not only state;
- AI draft state can be kept in-memory for v1 if it is only a temporary conversation draft.

AI intake should not treat in-memory state as the source of truth for completed bookings.

### 5.4 Database and migrations

The backend uses SQLAlchemy models and Alembic migrations.

Rules:

- New durable tables require a new Alembic migration.
- Do not edit old migrations in place.
- Tests generally use SQLite in-memory, so Postgres-specific constraints need careful handling.
- If using partial indexes/check constraints, follow existing dialect-conditional patterns.

For AI intake, a new audit table such as `ai_intake_events` is appropriate so live test-number and eWash-number behavior can be reviewed later.

### 5.5 Admin portal

The admin portal is server-rendered inline HTML in `app/admin.py`, not a separate frontend framework. It uses a single-password session cookie.

The admin portal should eventually show:

- AI intake live/test-number/eWash-number deployment status;
- current intake mode and deployment stage;
- recent AI classifications;
- error/fallback rates;
- whether a booking was AI-assisted;
- low-confidence messages needing staff review.

Keep internal AI diagnostics out of customer WhatsApp messages.

### 5.6 PWA API relevance

The current codebase includes a PWA-facing API layer in `app/api.py`. This matters because it already contains robust patterns for:

- Pydantic request validation;
- server-side booking validation;
- price recomputation;
- idempotency;
- token-scoped customer booking reads;
- rate limiting;
- structured API errors.

AI intake should reuse these patterns rather than creating a parallel booking engine.

---

## 6. What the current WhatsApp bot does well and poorly

### 6.1 Strengths of the deterministic bot

The existing WhatsApp bot is strong because it is:

- predictable;
- testable;
- bounded by WhatsApp button/list constraints;
- tied directly to the catalog;
- safe around booking confirmation;
- less likely to hallucinate;
- easy to audit in code;
- integrated with persistence and admin flows.

### 6.2 Weaknesses of the deterministic bot

The weakness is customer experience. Customers may not want to navigate a menu. They often write natural messages with multiple details at once.

Examples:

```text
Salam lavage complet demain matin à Maarif
```

```text
Bghit nettoyage intérieur pour Tiguan noire vendredi après-midi
```

```text
Vous pouvez venir à Casa Finance City demain 11h ?
```

A rigid state machine may ignore useful details and force the customer through every step. AI intake can reduce friction by extracting what the customer already provided.

### 6.3 Why not replace the bot entirely with AI?

A pure AI assistant is riskier because it may:

- promise availability without checking capacity;
- quote wrong prices;
- forget edge cases;
- fail to persist data consistently;
- expose internal errors;
- behave differently across model/provider changes;
- handle cancellation/rescheduling incorrectly;
- create operational commitments eWash cannot honor.

The correct design is a deterministic core with an AI intake layer.

---

## 7. Proposed feature in one sentence

Add a live AI intake layer to the existing WhatsApp bot, validate it first on a dedicated WhatsApp test number, then deploy the same live behavior directly to the official eWash WhatsApp Business number. The intake layer understands natural customer messages, extracts candidate booking fields, asks concise missing-field questions, and hands complete drafts to the existing deterministic booking validation and pending-confirmation persistence path.

---

## 8. Target customer experience

### 8.1 Natural booking intent

Customer:

```text
Salam bghit lavage complet demain matin à Maarif pour une Clio blanche
```

Good bot response:

```text
Avec plaisir. J’ai noté : lavage complet, demain matin, Clio blanche, secteur Maarif.

Il me manque juste l’adresse exacte et votre nom pour préparer la demande.
```

### 8.2 Complete request

Customer:

```text
Bonjour, je veux réserver un lavage extérieur pour une Dacia Duster noire samedi à 10h, à domicile à Bouskoura. Je m’appelle Yassine.
```

Good bot response:

```text
Merci Yassine. Demande reçue : lavage extérieur, Dacia Duster noire, samedi 10h, à domicile à Bouskoura.

L’équipe eWash va vérifier la disponibilité et vous confirmer.
```

System result:

- If all mandatory fields are complete and validated, create a pending booking request.
- Do not mark it confirmed.
- Notify staff/admin.

### 8.3 Ambiguous booking request

Customer:

```text
lavage demain
```

Good bot response:

```text
Bien sûr. Pour quel type de véhicule : citadine, berline/SUV, grand SUV/4x4 ou moto ?
```

### 8.4 Price question

Customer:

```text
Chhal lavage complet SUV ?
```

Good behavior:

- Detect pricing question.
- Map SUV to likely category only if deterministic mapping is confident, or ask for category.
- Answer using `catalog.service_price()`.
- Offer to continue booking.

### 8.5 Availability question

Customer:

```text
Vous avez un créneau demain matin ?
```

Good behavior:

- Check active slots and closed dates deterministically.
- Ask for missing service/vehicle/location if needed.
- Avoid promising final availability before staff confirmation.

### 8.6 Support or unrelated message

Customer:

```text
Vous êtes ouverts dimanche ?
```

Good behavior:

- Answer from configured business data if available.
- Otherwise route to existing support/menu flow.
- Do not create a booking draft.

---

## 9. AI intake scope

### 9.1 The AI layer may do this

The AI layer may:

1. Classify customer messages.
2. Extract candidate fields from free text.
3. Detect language preference.
4. Identify missing booking fields.
5. Suggest the next question to ask.
6. Summarize what the customer already provided.
7. Support French, Darija, Arabic, and English understanding.

### 9.2 The AI layer must not do this

The AI layer must not:

1. Send WhatsApp messages directly.
2. Write database rows directly.
3. Confirm bookings.
4. Compute or invent prices.
5. Invent unavailable slots.
6. Invent customer address details.
7. Skip deterministic validation.
8. Override `ALLOWED_STATUS_TRANSITIONS`.
9. Expose prompts, system messages, provider names, stack traces, “auto-compaction,” logs, code details, or config details to customers.
10. Capture Omar/owner cash-ledger operational messages.

### 9.3 Recommended classification labels

Use a bounded set like:

```text
booking_intent
booking_field_update
pricing_question
availability_question
support_question
cancel_or_reschedule
unrelated
unsafe_or_unclear
```

### 9.4 Candidate fields to extract

```text
customer_name
phone (usually implicit from WhatsApp sender)
language
service_intent
service_id_candidate (not trusted until deterministic validation)
vehicle_category_hint
vehicle_model
vehicle_color
license_plate
location_kind (home / center / unknown)
area_or_address_text
center_hint
date_expression
date_iso_candidate
time_expression
slot_candidate
promo_code
notes
confidence
missing_fields
```

---

## 10. Proposed architecture

### 10.1 Modules to add

#### `app/ai_intake.py`

Core business-facing AI intake module.

Responsibilities:

- define strict result schemas;
- build compact prompts from current catalog snapshot;
- call provider abstraction;
- parse JSON;
- sanitize output;
- apply deterministic mapping gates;
- return structured `IntakeResult` objects;
- never send WhatsApp replies;
- never write bookings directly.

Suggested API:

```python
async def analyze_customer_message(
    *,
    phone: str,
    text: str,
    current_draft: AiBookingDraft | None,
    catalog_snapshot: AiCatalogSnapshot,
) -> IntakeResult:
    ...
```

#### `app/ai_intake_provider.py`

Provider abstraction.

Responsibilities:

- read AI provider settings;
- use `httpx.AsyncClient`;
- support OpenAI-compatible chat completions initially;
- enforce timeout;
- optionally retry once;
- never log secrets;
- return raw model text to parser.

#### Optional `app/ai_intake_mapping.py`

If mapping logic grows, put deterministic mapping here:

- service synonyms to service IDs;
- vehicle hints to categories;
- date/time normalization;
- center matching;
- missing-field ordering.

### 10.2 Config to add

Live test-number-first settings:

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

Suggested env names:

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

For the first deployment, `EWASH_AI_INTAKE_DEPLOYMENT_STAGE` should be `test_number`. After live validation, switch the connected WhatsApp Business configuration to the official eWash number and set the stage to `ewash_number` for logs/admin visibility.

### 10.3 Audit table to add

Create `ai_intake_events` for review/debugging.

Suggested fields:

```text
id
created_at
phone_hash
source_message_id
session_state_before
classification
confidence
extracted_json
missing_fields_json
action_taken
error_code
latency_ms
intake_mode
deployment_stage
model_label (non-secret, optional)
```

Do **not** store:

- API keys;
- provider credentials;
- full system prompts;
- hidden chain-of-thought;
- internal logs;
- raw provider payloads containing secrets.

Raw customer message may already exist in inbound WhatsApp persistence; prefer linking by message ID and phone hash rather than duplicating raw text.

### 10.4 Session changes

Add temporary AI draft state to the per-phone session.

Suggested fields:

```python
ai_draft: AiBookingDraft | None = None
ai_last_question: str = ""
ai_intake_attempts: int = 0
```

Reset these on:

- `menu`;
- `start`;
- `reset`;
- `annuler`;
- session stale timeout;
- successful booking persistence.

### 10.5 Handler integration

AI intake should run only after global commands and owner operational shortcuts have had a chance to handle the message.

Pseudo-flow inside `app/handlers.py::handle_message()`:

```python
# existing extraction
payload_id = meta.extract_interactive_id(message)
text = meta.extract_text(message)
location = meta.extract_location(message)
sess = state.get(phone)

# existing global commands first
if text in reset/menu/start/greetings:
    ...

# existing owner cash ledger first
if current_state == "IDLE" and await _try_capture_cash_ledger_message(...):
    return

# new AI intake only when safe
if should_try_ai_intake(settings, sess, text, payload_id, phone):
    handled = await _try_ai_intake_message(phone, sess, message, text, location)
    if handled:
        return

# existing deterministic dispatch
handler = _DISPATCH.get(sess.state, _handle_idle)
await handler(...)
```

Entry conditions:

- feature enabled;
- text exists;
- no interactive payload is being processed;
- state is `IDLE` or explicit AI intake state;
- not an owner cash-ledger/operations route;
- message length within configured cap.

### 10.6 Live test-number mode

The first validation environment is a live WhatsApp test number. AI intake should reply to that test-number conversation from the start so Omar/Oussama can evaluate the actual customer experience, not only hidden classifications.

When `ai_intake_enabled=true`, `ai_intake_mode="live"`, and `ai_intake_deployment_stage="test_number"`:

- high-confidence booking intent starts or updates an AI booking draft;
- missing fields are asked one at a time;
- complete draft triggers deterministic recap;
- customer must explicitly confirm sending the request;
- only then persistence happens;
- persistence writes `pending_ewash_confirmation` only;
- every attempt is logged to `ai_intake_events`.

This test-number run gives real-world data to evaluate:

- classification accuracy;
- missing-field logic;
- language coverage;
- provider latency;
- failure rate;
- false positive booking detection;
- the actual customer-visible tone and flow.

### 10.7 Production eWash number mode

After the test-number behavior is accepted, deploy the same live flow directly to the official eWash WhatsApp Business number. Do not insert a shadow-mode or small-allowlist phase unless Omar explicitly changes this rollout decision. Keep the safety rails unchanged: deterministic validation, fixed reply templates, no direct `confirmed` writes, and technical-leakage bans.

---

## 11. Deterministic validation and mapping

### 11.1 Principle

Never trust AI output as final. Treat it as a candidate extraction.

Every field must pass deterministic checks before it can affect booking state.

### 11.2 Service mapping

The AI can output human intent, such as:

```text
lavage_exterieur
lavage_complet
nettoyage_interieur
polissage
ceramique
moto
```

Deterministic code maps these to current catalog service IDs. If mapping is not clear, ask the customer.

### 11.3 Vehicle mapping

Vehicle mapping should be conservative.

Possible categories:

- `A` — small city car / citadine;
- `B` — sedan / compact SUV;
- `C` — large SUV / 4x4 / large vehicle;
- `MOTO` — motorcycle/scooter.

If the model is known but category is uncertain, ask with buttons/list. Do not overfit or invent a category from weak evidence.

### 11.4 Date and slot mapping

The AI may interpret:

- `demain`;
- `ce samedi`;
- `matin`;
- `après-midi`;
- `10h`;
- `fin de journée`.

Deterministic code must verify:

- date is parseable;
- date is not closed;
- slot exists;
- slot is at least two hours in the future using Africa/Casablanca server time;
- ambiguous weekday is resolved safely.

For “demain matin,” it may be better to ask the customer to choose from available morning slots rather than picking one silently.

### 11.5 Location mapping

Location inputs may be:

- WhatsApp location pin;
- text address;
- neighborhood/area only;
- center name;
- vague phrase like “chez moi.”

Rules:

- Existing WhatsApp location handling should remain authoritative for pins.
- Area-only text should trigger request for exact address or pin.
- Center hints must match active centers deterministically.
- Do not invent location coordinates.

### 11.6 Missing-field priority

A good order:

1. service;
2. vehicle category/model;
3. location/address;
4. date;
5. slot;
6. customer name;
7. optional notes/promo.

However, the best UX may ask for name late, after the customer sees that eWash can satisfy the service request.

---

## 12. Customer-facing copy policy

### 12.1 Fixed templates, not raw model prose

The LLM should not directly author final WhatsApp messages in v1. It can provide structured fields and language hints. Code should render replies using templates.

Reason:

- avoids technical leakage;
- keeps brand tone consistent;
- reduces hallucination;
- keeps WhatsApp messages concise;
- makes tests possible.

### 12.2 Tone

Customer-facing tone should be:

- concise;
- friendly;
- French-first;
- comfortable with Darija understanding;
- operational, not technical;
- clear that eWash will confirm availability.

### 12.3 Example templates

```python
MISSING_SERVICE = "Bien sûr. Quel service souhaitez-vous : lavage extérieur, lavage complet, intérieur/salon, ou esthétique ?"
MISSING_CATEGORY = "Pour quel type de véhicule : citadine, berline/SUV, grand SUV/4x4 ou moto ?"
MISSING_ADDRESS = "Pouvez-vous m’envoyer l’adresse exacte ou une localisation WhatsApp ?"
MISSING_SLOT = "Quel créneau préférez-vous ?"
MISSING_NAME = "Parfait. À quel nom dois-je préparer la demande ?"
GENERIC_FALLBACK = "Désolé, je n’ai pas pu traiter votre message. Vous pouvez utiliser le menu ou nous envoyer votre demande en une phrase."
REQUEST_RECEIVED = "Merci. Votre demande {ref} est bien reçue. L’équipe eWash va vérifier la disponibilité et vous confirmer."
```

### 12.4 Banned customer-visible content

Never send customers:

- “AI model”;
- “prompt”;
- “system message”;
- “tool call”;
- “auto-compaction”;
- “database error” with raw details;
- stack trace;
- provider name;
- internal config;
- debug logs;
- code paths;
- tokens or IDs not meant for the customer.

This is especially important because Omar specifically does not want technical/internal messages appearing in WhatsApp operations.

---

## 13. WhatsApp Business connection strategy

### 13.1 Recommended production route

Use the official WhatsApp Business Platform / Meta Cloud API path, not a QR-paired WhatsApp Web bridge, for the main eWash customer-facing number.

Why:

- official API is designed for business production use;
- webhook delivery is cleaner;
- avoids conflicts with WhatsApp app/web sessions;
- supports templates and business messaging policies;
- gives better reliability and observability;
- integrates naturally with the existing `POST /webhook` backend.

### 13.2 QR/session bridge role

A QR-paired bridge can be useful for:

- prototypes;
- internal assistant channels;
- owner operations;
- test groups;
- non-critical workflows.

It should not be the main production client booking line unless there is a deliberate temporary tradeoff.

---

## 14. Detailed implementation plan

### Task 1: Add AI intake configuration

**Objective:** Add live-mode settings and env documentation for a test-number-first rollout.

**Files:**

- `app/config.py`
- `.env.example`
- `tests/test_config_defaults.py`

**Requirements:**

- live mode enabled for the configured WhatsApp test-number deployment;
- deployment stage can be labelled `test_number` or `ewash_number` for logs/admin visibility;
- missing/invalid provider configuration fails closed with a customer-safe fallback;
- provider secret never printed.

**Test:**

```bash
source .venv/bin/activate && python -m pytest -q tests/test_config_defaults.py
```

---

### Task 2: Add strict intake schemas

**Objective:** Define structured outputs before calling any model.

**Files:**

- `app/ai_intake.py`
- `tests/test_ai_intake_schemas.py`

**Schemas:**

- `IntakeClassification`
- `IntakeExtractedFields`
- `IntakeResult`
- `AiBookingDraft`
- `AiCatalogSnapshot`

**Tests:**

- valid JSON parse;
- unknown classification rejected/coerced safely;
- confidence bounded 0..1;
- overlong fields cleaned;
- no arbitrary keys accepted unless explicitly in `metadata`.

---

### Task 3: Build catalog snapshot

**Objective:** Give the AI only public, necessary, current booking context.

**Files:**

- `app/ai_intake.py`
- `tests/test_ai_intake_catalog_snapshot.py`

**Snapshot includes:**

- category labels;
- service names and IDs;
- centers;
- slots;
- closed dates;
- optional public prices from `service_price()` if needed.

**Snapshot excludes:**

- secrets;
- database URLs;
- admin password;
- internal tokens;
- raw customer history unless specifically needed.

---

### Task 4: Implement provider abstraction

**Objective:** Isolate provider-specific HTTP from business logic.

**Files:**

- `app/ai_intake_provider.py`
- `tests/test_ai_intake_provider.py`

**Requirements:**

- `httpx.AsyncClient`;
- configurable timeout;
- one retry max by default;
- OpenAI-compatible response parsing;
- clean failure modes;
- mocked tests only.

---

### Task 5: Implement prompt and parser

**Objective:** Classify/extract via strict JSON.

**Files:**

- `app/ai_intake.py`
- `tests/test_ai_intake_parser.py`

**Prompt principles:**

- model is an intake parser, not booking confirmer;
- JSON only;
- unknown fields must be `null`;
- no customer-facing prose;
- do not invent price/date/address/service;
- use allowed classification labels only.

**Parser tests:**

- normal JSON;
- markdown fenced JSON;
- extra prose;
- invalid JSON;
- unknown service/category;
- missing required keys.

---

### Task 6: Implement deterministic mapping gate

**Objective:** Convert model output into trusted draft updates only when safe.

**Files:**

- `app/ai_intake.py` or `app/ai_intake_mapping.py`
- `tests/test_ai_intake_mapping.py`

**Tests:**

- French examples;
- Darija examples;
- English examples;
- vague service;
- invalid category;
- closed date;
- too-soon slot;
- center matching;
- location text requiring exact address.

---

### Task 7: Extend session state for AI draft

**Objective:** Keep partial booking details between messages.

**Files:**

- `app/state.py`
- `tests/test_ai_intake_state.py`

**Requirements:**

- reset clears draft;
- stale cleanup clears draft;
- successful persistence clears draft;
- deterministic menu can still take over.

---

### Task 8: Add live test-number handler integration

**Objective:** Enable customer-visible AI intake on the WhatsApp test number while preserving all deterministic booking safeguards.

**Files:**

- `app/handlers.py`
- `app/models.py`
- `app/persistence.py`
- new Alembic migration for `ai_intake_events`
- `tests/test_ai_intake_live_test_number.py`

**Tests:**

- classifier called for natural booking text in IDLE;
- not called for menu/reset/start;
- not called after cash-ledger capture;
- provider failure falls back to the existing menu or generic safe message;
- customer-visible AI replies are rendered only from fixed templates;
- event row logged.

---

### Task 9: Add live AI intake conversation state

**Objective:** Ask missing questions and collect fields.

**Files:**

- `app/handlers.py`
- `tests/test_ai_intake_live_flow.py`

**Behavior:**

- high-confidence booking intent starts draft;
- one missing-field question at a time;
- low confidence falls back to menu;
- `menu` / `annuler` resets;
- no DB write until explicit customer final confirmation.

---

### Task 10: Add recap and explicit send-request confirmation

**Objective:** Prevent accidental persistence from a single parse.

**Files:**

- `app/handlers.py`
- `tests/test_ai_intake_confirmation.py`

**Buttons:**

- `✅ Envoyer la demande`
- `Modifier`
- `Annuler`

Persistence must occur only after explicit confirmation.

---

### Task 11: Persist through existing booking path

**Objective:** Create normal pending booking rows from confirmed AI drafts.

**Files:**

- `app/handlers.py`
- maybe `app/booking.py`
- `tests/test_ai_intake_persistence.py`

**Requirements:**

- use `catalog.service_price()`;
- use `assign_booking_ref()`;
- use `persist_confirmed_booking()`;
- status is `pending_ewash_confirmation`;
- staff notification sent like existing WhatsApp path;
- AI event links to booking ref if useful.

---

### Task 12: Add admin visibility

**Objective:** Make AI behavior reviewable by Omar/eWash internally.

**Files:**

- `app/admin.py`
- `tests/test_admin_ai_intake_events.py`

**Admin UI v1:**

- live/test-number/eWash-number deployment indicator;
- current intake mode and deployment stage;
- recent intake events;
- classification counts;
- parse/provider errors;
- fallback counts.

Do not expose full prompts, secrets, or raw provider payloads.

---

### Task 13: Add failure fallback tests

**Objective:** Guarantee safe behavior under provider failure.

**Files:**

- `app/ai_intake.py`
- `app/handlers.py`
- `tests/test_ai_intake_failures.py`

**Cases:**

- timeout;
- HTTP 500;
- bad JSON;
- low confidence;
- unsupported language;
- message too long.

Expected behavior:

- no booking write;
- no technical customer message;
- fallback to deterministic menu or generic helpful reply;
- event logged for internal review.

---

### Task 14: Existing-flow regression coverage

**Objective:** Prove AI does not break the bot.

**Files:**

- `tests/test_ai_intake_existing_flow_regression.py` or existing handler tests.

**Regression cases:**

- menu flow;
- reset flow;
- returning customer flow;
- button/list booking path;
- cash ledger owner route;
- provider-failure fallback behavior;
- staff/admin confirmation invariant.

---

### Task 15: Rollout runbook

**Objective:** Deploy safely.

**Files:**

- `docs/runbooks/ai-whatsapp-intake.md`
- `.env.example`

**Stages:**

1. Configure the WhatsApp test number with `EWASH_AI_INTAKE_ENABLED=true`, `EWASH_AI_INTAKE_MODE=live`, and `EWASH_AI_INTAKE_DEPLOYMENT_STAGE=test_number`.
2. Run live end-to-end booking conversations on the test number using realistic French, Darija, Arabic, and English messages.
3. Review `ai_intake_events`, created pending bookings, staff notifications, and customer replies from the test-number run.
4. Fix prompt/mapping/template issues found during live test-number validation.
5. Point the same live flow at the official eWash WhatsApp Business number with `EWASH_AI_INTAKE_DEPLOYMENT_STAGE=ewash_number`.
6. Monitor the official number closely after launch for no status violations, no technical leakage, no pricing drift, and acceptable fallback rate.

---

## 15. Testing strategy

Run targeted tests after each implementation step:

```bash
source .venv/bin/activate && python -m pytest -q tests/test_ai_intake_schemas.py
source .venv/bin/activate && python -m pytest -q tests/test_ai_intake_provider.py
source .venv/bin/activate && python -m pytest -q tests/test_ai_intake_mapping.py
source .venv/bin/activate && python -m pytest -q tests/test_ai_intake_live_test_number.py
source .venv/bin/activate && python -m pytest -q tests/test_ai_intake_live_flow.py
source .venv/bin/activate && python -m pytest -q tests/test_ai_intake_persistence.py
```

Then full backend suite:

```bash
source .venv/bin/activate && python -m pytest -q
```

No real AI provider calls should occur in tests.

---

## 16. Observability and metrics

Track:

- number of AI-classified messages;
- classification distribution;
- confidence distribution;
- provider latency p50/p95;
- provider failure rate;
- parse failure rate;
- missing-field distribution;
- draft-to-pending-booking conversion rate;
- fallback-to-menu rate;
- staff correction rate;
- customer abandonment during AI intake;
- any technical leakage incidents;
- any pricing mismatch incidents;
- any status invariant violations, which should be zero.

Admin dashboard can start simple. A log/event table is enough for v1.

---

## 17. Security, privacy, and compliance

### 17.1 Data sent to AI provider

Customer WhatsApp messages may include personal data:

- name;
- phone number;
- address;
- vehicle details;
- schedule preferences.

Before choosing a provider, decide:

- whether customer data leaves eWash infrastructure;
- provider retention policy;
- whether data is used for training;
- regional/data-processing implications;
- deletion/erasure responsibilities.

### 17.2 Prompt/data minimization

The prompt should include only what is necessary:

- current message;
- minimal current draft;
- public catalog/slot/center facts;
- strict extraction instructions.

Avoid including:

- internal logs;
- database URLs;
- admin data;
- unrelated customer history;
- secrets;
- staff-only notes.

### 17.3 Customer erasure

If AI event rows can be linked to customers, future data-erasure flows should include or anonymize them.

### 17.4 Abuse

AI intake should preserve existing rate limits and should not create an easier spam path. Consider:

- per-phone rate limits;
- per-IP rate limits if available from webhook metadata/proxy;
- max message length;
- provider cost caps;
- separate test-number validation before the eWash number;
- fallback to deterministic menu when abused.

---

## 18. Risks and mitigations

### Risk: AI confirms bookings or implies final availability

Mitigation:

- fixed templates say “demande reçue” and “l’équipe eWash va vérifier”; never “réservation confirmée” unless staff confirmed.
- tests assert no AI path writes `confirmed`.

### Risk: AI quotes wrong prices

Mitigation:

- AI never computes prices;
- deterministic code calls `catalog.service_price()`;
- price questions use catalog only.

### Risk: AI maps vehicle category incorrectly

Mitigation:

- conservative mapping;
- ask category when uncertain;
- staff confirmation catches operational mismatch.

### Risk: AI leaks internal technical messages

Mitigation:

- raw model prose not sent to customers;
- fixed templates only;
- banned phrase tests;
- generic fallback on errors.

### Risk: provider latency makes WhatsApp feel slow

Mitigation:

- short timeout;
- fallback to menu;
- live test-number latency metrics before eWash-number launch;
- consider async/deferred patterns only if needed and safe.

### Risk: provider outage blocks bookings

Mitigation:

- deterministic bot remains fallback;
- provider failure fallback path;
- provider failures do not stop menu booking.

### Risk: test-number validation misses real customer variety

Mitigation:

- test with realistic French, Darija, Arabic, and English booking messages;
- review event table and created pending bookings from the test number;
- add examples to tests from live test-number failures before switching to the eWash number.

### Risk: duplicated validation logic drifts

Mitigation:

- reuse `app/api.py` / `app/api_validation.py` patterns;
- centralize mapping;
- avoid parallel booking engine.

---

## 19. Open design questions for reviewer

1. Should the AI provider be external OpenAI-compatible API, self-hosted, or through an existing Hermes/Nouse gateway?
2. Should AI-assisted bookings have a new `bookings.source = "whatsapp_ai"`, or should they stay `source="whatsapp"` and be linked only through `ai_intake_events`?
3. Which WhatsApp test number should be used for live validation before switching to the eWash number?
4. Should customer replies remain French-first, or should the assistant respond in the detected language (French/Darija/Arabic/English)?
5. Should price questions be answered by the AI intake layer or routed to deterministic catalog menu responses first?
6. How should cancellation/rescheduling be handled in v1: out of scope, staff handoff, or deterministic flow?
7. What is the acceptable provider latency budget for a WhatsApp message?
8. Should an incomplete AI draft expire sooner than the normal session timeout?
9. How should staff see and correct AI-extracted fields before confirmation?
10. How should data erasure include AI intake events?

---

## 20. Suggested reviewer checklist

A useful reviewer should look for flaws in:

- lifecycle safety: can any customer/AI path write `confirmed`?
- pricing safety: can the AI quote or persist wrong prices?
- validation reuse: does the plan duplicate booking validation?
- timeout/fallback behavior: can provider failure block booking?
- data privacy: what customer data leaves the system?
- WhatsApp UX: are replies concise and natural?
- localization: is Darija/Arabic understanding handled appropriately?
- admin operability: can Omar/eWash diagnose what happened?
- rollout safety: is live test-number validation sufficient before direct eWash-number deployment?
- testing: are all high-risk paths covered?
- implementation complexity: can this be shipped incrementally?
- maintainability: is provider code isolated from business rules?

---

## 21. Acceptance criteria

The implementation is acceptable only if:

1. Live mode works end to end on the configured WhatsApp test number.
2. The same live mode can be moved directly to the official eWash WhatsApp Business number.
3. Live mode can understand a realistic natural booking request.
4. Live mode asks missing-field questions one at a time.
5. AI-assisted persistence creates only `pending_ewash_confirmation` bookings.
6. Admin confirmation remains the only route to `confirmed`.
7. All prices come from `catalog.service_price()`.
8. Provider failures fall back safely.
9. No technical/internal/Hermes/provider messages are sent to customers.
10. Admin can review AI intake behavior.
11. Tests cover live test-number routing, live intake, ambiguity, provider-failure, existing-flow regression, and persistence paths.
12. Full backend test suite passes.

---

## 22. Recommended default decisions

If no further product decisions are made, use these defaults:

- official Meta WhatsApp Business Platform / Cloud API for production;
- no QR/Web bridge for the main customer booking number;
- live mode on the WhatsApp test number first;
- direct deployment to the official eWash number after test-number approval;
- no shadow-mode or small-allowlist phase unless Omar explicitly changes the rollout decision;
- French-first customer replies, with Darija/Arabic/English understanding;
- fixed reply templates, not raw LLM prose;
- `bookings.source="whatsapp"` in v1, with AI involvement recorded in `ai_intake_events`;
- conservative deterministic mapping;
- fallback to existing menu on uncertainty;
- never bypass human eWash confirmation.

---

## 23. Short version for non-technical stakeholders

eWash should keep its existing WhatsApp booking bot because it is safe, structured, and connected to the booking database. The improvement is to add AI at the front of the conversation so customers can speak naturally instead of always using menus.

The AI should understand messages like:

```text
Salam bghit lavage complet demain matin à Maarif pour une Clio blanche
```

and turn them into a structured booking request. But the AI should not confirm the booking. It should only prepare the request, then the eWash team confirms availability as today.

This gives the best of both worlds:

- natural customer experience;
- correct prices;
- reliable database records;
- human operational control;
- no accidental commitments;
- safer rollout through live validation on a separate test number before switching to the official eWash number.

---

## 24. Appendix: concise architecture comparison

### Current deterministic bot only

Pros:

- safe;
- predictable;
- easy to test;
- no AI cost;
- low hallucination risk.

Cons:

- rigid;
- slower for customers who write natural messages;
- may ignore useful details in first message.

### Pure AI agent only

Pros:

- natural;
- flexible;
- can handle messy input.

Cons:

- risky;
- may hallucinate prices/availability;
- harder to test;
- harder to audit;
- could commit eWash operationally without human review.

### Recommended hybrid

Pros:

- natural intake;
- deterministic validation;
- correct prices;
- database source of truth;
- human confirmation;
- safe rollout.

Cons:

- more implementation complexity;
- needs provider/privacy decisions;
- needs careful mapping and testing.

The hybrid is the recommended path.
