# ruff: noqa

from datetime import datetime, timedelta, timezone

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.cloud import firestore
from google.genai import types


MODEL = "gemini-3.7-flash"

db = firestore.Client(project="leadpilot-agent-hackathon")


def save_lead_to_firestore(
    name: str,
    contact: str,
    need: str,
    missing_data: list[str],
    lead_quality: str,
    next_action: str,
    customer_reply: str,
    manager_note: str,
) -> dict:
    """Save an analyzed sales lead to the LeadPilot CRM."""

    quality = lead_quality.upper()

    priority_map = {
        "HOT": "high",
        "WARM": "medium",
        "COLD": "low",
    }

    doc_ref = db.collection("leads").document()

    doc_ref.set(
        {
            "name": name,
            "contact": contact,
            "need": need,
            "missing_data": missing_data,
            "lead_quality": quality,
            "priority": priority_map.get(quality, "medium"),
            "next_action": next_action,
            "customer_reply": customer_reply,
            "manager_note": manager_note,
            "created_at": firestore.SERVER_TIMESTAMP,
            "source": "leadpilot_agent",
        }
    )

    return {
        "status": "saved",
        "lead_id": doc_ref.id,
        "lead_quality": quality,
    }


def create_followup(
    lead_id: str,
    action: str,
    delay_hours: int = 24,
) -> dict:
    """Create a follow-up task for an existing CRM lead."""

    lead_ref = db.collection("leads").document(lead_id)
    lead_snapshot = lead_ref.get()

    if not lead_snapshot.exists:
        return {
            "status": "error",
            "error": (
                f"lead_id '{lead_id}' does not exist. "
                "Save the lead first and use the exact returned lead_id."
            ),
        }

    lead = lead_snapshot.to_dict()
    quality = str(lead.get("lead_quality", "")).upper()

    # Hard safety rule:
    # cold leads only get a follow-up when there is an explicit future opportunity.
    if quality == "COLD":
        return {
            "status": "skipped",
            "reason": "Automatic follow-up disabled for COLD leads.",
            "lead_id": lead_id,
        }

    # Enforce sensible timing regardless of what the model requests.
    if quality == "HOT":
        delay_hours = max(1, min(delay_hours, 4))
    elif quality == "WARM":
        delay_hours = max(12, min(delay_hours, 48))

    due_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)

    doc_ref = db.collection("followups").document()

    doc_ref.set(
        {
            "lead_id": lead_id,
            "action": action,
            "status": "pending",
            "created_at": firestore.SERVER_TIMESTAMP,
            "due_at": due_at,
            "source": "leadpilot_agent",
        }
    )

    return {
        "status": "created",
        "followup_id": doc_ref.id,
        "lead_id": lead_id,
        "lead_quality": quality,
        "delay_hours": delay_hours,
    }


def notify_manager(
    lead_id: str,
    message: str,
    urgency: str = "high",
) -> dict:
    """Notify the human manager only for HOT leads."""

    lead_ref = db.collection("leads").document(lead_id)
    lead_snapshot = lead_ref.get()

    if not lead_snapshot.exists:
        return {
            "status": "error",
            "error": (
                f"lead_id '{lead_id}' does not exist. "
                "Save the lead first and use the exact returned lead_id."
            ),
        }

    lead = lead_snapshot.to_dict()
    quality = str(lead.get("lead_quality", "")).upper()

    # Deterministic guardrail:
    # manager notifications are reserved for HOT leads.
    if quality != "HOT":
        return {
            "status": "skipped",
            "reason": (
                f"Manager notification not created because lead quality is {quality}."
            ),
            "lead_id": lead_id,
        }

    doc_ref = db.collection("manager_notifications").document()

    doc_ref.set(
        {
            "lead_id": lead_id,
            "message": message,
            "urgency": "high",
            "status": "unread",
            "created_at": firestore.SERVER_TIMESTAMP,
            "source": "leadpilot_agent",
        }
    )

    return {
        "status": "created",
        "notification_id": doc_ref.id,
        "lead_id": lead_id,
    }


LEADPILOT_INSTRUCTION = """
You are LeadPilot, an autonomous AI Sales Operator for small service businesses.

Your goal is not merely to chat.
Your goal is to understand an incoming lead, decide the correct sales action,
execute the appropriate CRM workflow, and involve a human only when necessary.

For every new customer message:

1. Identify what the customer needs.
2. Extract facts already provided.
3. Never invent information.
4. Identify the most valuable missing information.
5. Classify the lead as HOT, WARM, or COLD.
6. Determine the next best sales action.
7. Prepare a natural customer reply.
8. Prepare a concise internal manager note.
9. Execute the CRM workflow using the available tools.

TECHNICAL SAFETY:

Never invent or estimate:
- equipment capacity;
- price;
- savings;
- compatibility;
- installation requirements;
- technical measurements.

If technical calculation or engineering verification is required, explicitly
state that it requires calculation/review instead of guessing.

Never say that a model, capacity, building, or solution definitely fits unless
it has actually been calculated or verified.

Never invent customer names, contacts, locations, budgets, deadlines, or other
missing facts.

LEAD CLASSIFICATION:

HOT:
- clear real need;
- strong buying intent;
- customer wants to proceed soon;
- sales action is justified now.

WARM:
- real need;
- buying opportunity exists;
- important information, timing, or readiness is still unclear.

COLD:
- informational interest only;
- customer explicitly says they are not planning to buy;
- weak or absent near-term buying intent.

MANDATORY TOOL ORDER:

1. Analyze the message.
2. Call save_lead_to_firestore exactly once.
3. Read the exact lead_id returned by the tool.
4. Never invent, transform, shorten, or guess a lead_id.
5. Use that exact lead_id for every downstream action.

For HOT:
- save lead;
- create exactly one follow-up;
- follow-up should occur within 1–4 hours;
- notify manager exactly once.

For WARM:
- save lead;
- create exactly one useful follow-up, normally around 24 hours later;
- DO NOT notify the manager.

For COLD:
- save lead;
- do not notify manager;
- do not create an automatic follow-up unless there is an explicit future
  opportunity stated by the customer.

The tool implementations contain hard business rules.
If a tool returns "skipped", accept that result.
Do not retry it or try to bypass the rule.

CUSTOMER COMMUNICATION:

Use the same language as the customer.

Ask at most 3 high-value questions at one time.

After tools finish, return exactly:

NEED:
<customer need>

KNOWN_DATA:
- <fact>
- <fact>

MISSING_DATA:
- <missing fact>
- <missing fact>

LEAD_QUALITY:
<HOT | WARM | COLD>

NEXT_ACTION:
<single best next action>

CUSTOMER_REPLY:
<message suitable to send to customer>

MANAGER_NOTE:
<concise internal note>
"""


root_agent = Agent(
    name="leadpilot_sales_operator",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=LEADPILOT_INSTRUCTION,
    tools=[
        save_lead_to_firestore,
        create_followup,
        notify_manager,
    ],
)


app = App(
    root_agent=root_agent,
    name="leadpilot_agent",
)