# LeadPilot

### Autonomous AI Sales Operator for service businesses

LeadPilot turns incoming customer messages into concrete business actions.

Instead of only generating a chatbot response, LeadPilot autonomously:

- understands customer intent;
- extracts known and missing information;
- qualifies buying readiness;
- saves the lead to CRM;
- schedules the appropriate follow-up;
- escalates high-priority opportunities to a human manager;
- applies deterministic business guardrails before taking actions.

**Live demo:**  
https://leadpilot-agent-590163484801.europe-central2.run.app

---

## The problem

Small service businesses lose revenue because incoming leads are handled inconsistently.

A customer may arrive from a website, advertisement, messenger, email, or lead form.

The business then has to manually:

1. understand what the customer wants;
2. identify missing information;
3. determine whether the lead is serious;
4. enter the lead into a CRM;
5. decide when to follow up;
6. decide whether a salesperson needs to act immediately;
7. write the customer response;
8. remember to perform the next action.

Traditional chatbots solve only a small part of this workflow.

They talk.

They do not reliably operate the sales process.

---

# The solution

LeadPilot is an **AI Sales Operator**.

It combines Gemini reasoning with deterministic business rules and persistent CRM state.

The core workflow is:

```text
Incoming customer message
          ↓
     Gemini reasoning
          ↓
Intent + facts + missing data
          ↓
 HOT / WARM / COLD qualification
          ↓
     CRM persistence
          ↓
 Business action decision
       ↙          ↘
 Follow-up     Human escalation
          ↓
   Customer response
```

LeadPilot is designed to move a lead toward the next useful business state — not simply generate text.

---

# Autonomous workflow

Every incoming lead passes through the same operating loop.

## 1. Understand

LeadPilot identifies:

- what the customer wants;
- what information is already known;
- what important information is still missing;
- what action would move the sale forward.

## 2. Qualify

LeadPilot assigns one of three buying-readiness states.

### HOT

The customer has a clear need and strong near-term buying intent.

```text
CRM SAVE          ✓
FOLLOW-UP         ✓ 1–4 hours
MANAGER ALERT     ✓ HIGH priority
```

### WARM

The opportunity is real, but timing or important information is still unclear.

```text
CRM SAVE          ✓
FOLLOW-UP         ✓
MANAGER ALERT     — SKIPPED
```

### COLD

The customer is primarily researching or explicitly has no near-term intention to buy.

```text
CRM SAVE          ✓
FOLLOW-UP         — SKIPPED
MANAGER ALERT     — SKIPPED
```

This prevents sales teams from wasting time treating every inquiry as equally urgent.

---

# AI + deterministic guardrails

Gemini handles semantic reasoning:

- intent detection;
- information extraction;
- qualification;
- natural language responses;
- next-action reasoning.

Python business logic enforces deterministic rules:

```text
HOT
→ manager notification allowed
→ follow-up constrained to 1–4 hours

WARM
→ follow-up allowed
→ manager notification blocked

COLD
→ manager notification blocked
→ automatic follow-up blocked
```

A model can request an invalid action, but the underlying tool rejects it.

---

# No fabricated technical recommendations

LeadPilot is explicitly prohibited from inventing:

- equipment capacity;
- technical compatibility;
- pricing;
- projected savings;
- installation requirements;
- customer details;
- measurements that were not supplied.

If an engineering calculation is required, LeadPilot requests the necessary information or escalates the task instead of guessing.

---

# Real business actions

After Gemini finishes reasoning, LeadPilot executes tools that create real documents in Google Cloud Firestore.

The web application then verifies the resulting Firestore state and displays what was actually executed.

Example HOT workflow:

```text
Lead quality
HOT

CRM
✓ COMPLETED
Lead ID: <real Firestore document ID>

Follow-up
✓ COMPLETED
Scheduled in 2h

Human escalation
✓ COMPLETED
Urgency: HIGH
```

The UI therefore shows **verified side effects**, not only the model's claimed actions.

---

# CRM data model

LeadPilot currently uses three Firestore collections.

## `leads`

```text
name
contact
need
missing_data
lead_quality
priority
next_action
customer_reply
manager_note
created_at
source
```

## `followups`

```text
lead_id
action
status
created_at
due_at
source
```

## `manager_notifications`

```text
lead_id
message
urgency
status
created_at
source
```

All downstream actions reference the real Firestore `lead_id`.

---

# Architecture

```text
Customer / Lead
      ↓
FastAPI Web Demo
Google Cloud Run
      ↓
LeadPilot Agent
Google ADK
      ↓
Gemini
      ↓
Deterministic tools
      ↓
Google Firestore CRM
      ↓
Follow-up / Human escalation
```

---

# Google technologies used

- **Gemini** — reasoning over incoming sales requests.
- **Google Agent Development Kit** — agent runtime and tool calling.
- **Google Cloud Firestore** — persistent CRM state.
- **Google Cloud Run** — public application hosting.
- **Google Secret Manager** — Gemini API credential storage.
- **Google Cloud IAM** — dedicated runtime service account and scoped permissions.

---

# Demo

Live application:

https://leadpilot-agent-590163484801.europe-central2.run.app

The demo includes HOT, WARM and COLD scenarios.

---

# Why this is different from a chatbot

A traditional chatbot performs:

```text
message
→ answer
```

LeadPilot performs:

```text
message
→ understand
→ qualify
→ persist
→ decide
→ act
→ schedule
→ escalate when necessary
→ answer
```

The useful output is not only language. It is **business state change**.

---

# Human-in-the-loop by design

```text
COLD lead
→ handled without interrupting salesperson

WARM lead
→ stored and scheduled for follow-up

HOT lead
→ salesperson alerted immediately
```

---

# Initial vertical

The first implementation focuses on HVAC and heat-pump sales, where qualification requires both commercial and technical reasoning.

The same architecture can later be configured for:

```text
HVAC
Solar
Plumbing
Electrical services
Roofing
Home renovation
Maintenance companies
Equipment distributors
B2B service providers
```

---

# Product vision

A commercial LeadPilot platform can extend this architecture with:

```text
Website lead forms
Email ingestion
Messenger integrations
WhatsApp / Telegram
Calendar booking
Pipeline dashboard
Multi-user CRM
Automated follow-up sequences
Quotation generation
Product catalogs
Knowledge retrieval
Sales analytics
Conversion tracking
Multi-company accounts
White-label agency mode
Billing and subscriptions
```

---

# Business model

Potential SaaS monetization can combine:

- subscription;
- usage-based pricing;
- setup and onboarding;
- agency / white-label plans.

---

# Public demo protection

Current safeguards include:

```text
Maximum Cloud Run instances: 1
Cloud Run concurrency limit
Maximum message size: 2000 characters
Per-IP AI workflow rate limiting
Gemini credential stored in Secret Manager
.env excluded from source deployment
Dedicated Cloud Run service account
```

---

# Local development

Requirements:

```text
Python 3.12+
uv
Google Cloud authentication
Gemini API access
Firestore project
```

Install dependencies:

```bash
uv sync
```

Create `.env`:

```text
GEMINI_API_KEY=<your Gemini API key>
```

Authenticate ADC:

```bash
gcloud auth application-default login
```

Run locally:

```bash
uv run uvicorn app.web_demo:app --host 127.0.0.1 --port 8081
```

Open:

```text
http://127.0.0.1:8081
```

---

# Deployment

```bash
gcloud run deploy leadpilot-agent \
  --source . \
  --region europe-central2 \
  --allow-unauthenticated
```

Production credentials should be supplied through Secret Manager rather than committed `.env` files.

---

# Current status

```text
✓ Gemini agent reasoning
✓ HOT / WARM / COLD qualification
✓ Firestore CRM persistence
✓ Real lead IDs
✓ Automatic HOT follow-up
✓ Automatic WARM follow-up
✓ COLD follow-up guardrail
✓ Human escalation for HOT opportunities
✓ Deterministic business guardrails
✓ Customer response generation
✓ Internal manager notes
✓ Public FastAPI interface
✓ Cloud Run deployment
✓ Secret Manager integration
✓ Dedicated runtime service account
✓ Public demo limits
✓ Firestore-verified action UI
```

---

# Next steps

1. persistent conversation state;
2. real messaging integrations;
3. scheduling and calendar integration;
4. CRM dashboard;
5. customizable business rules;
6. knowledge and product catalogs;
7. quotation workflows;
8. analytics and conversion attribution;
9. multi-tenant SaaS architecture;
10. billing and commercial onboarding.

---

# Core idea

> AI should not only tell a salesperson what to do next.

> It should safely execute the repetitive parts of the sales process and bring the human in when human judgment has the highest value.

That is LeadPilot.
