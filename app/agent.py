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
    """Save an analyzed sales lead to the LeadPilot CRM in Firestore."""

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
            "lead_quality": lead_quality.upper(),
            "priority": priority_map.get(lead_quality.upper(), "medium"),
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
    }


def create_followup(
    lead_id: str,
    action: str,
    delay_hours: int = 24,
) -> dict:
    """Create a follow-up task for an existing LeadPilot CRM lead."""

    lead_snapshot = db.collection("leads").document(lead_id).get()

    if not lead_snapshot.exists:
        return {
            "status": "error",
            "error": (
                f"lead_id '{lead_id}' does not exist in Firestore. "
                "Call save_lead_to_firestore first and use the exact lead_id "
                "returned by that tool."
            ),
        }

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
    }


def notify_manager(
    lead_id: str,
    message: str,
    urgency: str = "normal",
) -> dict:
    """Create an internal notification for the human sales manager."""

    lead_snapshot = db.collection("leads").document(lead_id).get()

    if not lead_snapshot.exists:
        return {
            "status": "error",
            "error": (
                f"lead_id '{lead_id}' does not exist in Firestore. "
                "Call save_lead_to_firestore first and use the exact lead_id "
                "returned by that tool."
            ),
        }

    doc_ref = db.collection("manager_notifications").document()

    doc_ref.set(
        {
            "lead_id": lead_id,
            "message": message,
            "urgency": urgency,
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

Your job is not just to chat.
Your job is to move every incoming lead toward the correct next business action.

For every new customer message:

1. Identify what the customer needs.
2. Extract all useful facts already provided.
3. Never invent information that the customer did not provide.
4. Identify the most important missing information needed to move the sale forward.
5. Qualify the lead as HOT, WARM, or COLD.
6. Decide the next best business action.
7. Write a short natural reply to the customer.
8. Prepare a concise internal note for the human sales manager.
9. Execute the appropriate CRM actions using the available tools.

IMPORTANT TECHNICAL SAFETY RULES:

Never invent or estimate technical specifications, equipment capacity, price,
savings, compatibility, or installation requirements unless the provided data
is sufficient and the calculation is explicitly supported.

If a technical calculation is required, mark it as requiring calculation or
human/engineering review instead of guessing.

Do not tell the customer that a specific model, power rating, or solution
"will fit" unless it has actually been calculated or verified.

Do not claim that a customer's building or provided parameters are suitable,
ideal, or well suited for a solution until suitability has been technically verified.

Never invent names, phone numbers, email addresses, addresses, budgets,
deadlines, technical measurements, or other customer data.

CUSTOMER COMMUNICATION:

Use the same language as the customer's message.

Do not overwhelm the customer with questions.
Ask at most 3 of the highest-value clarification questions at one time.

A lead is generally:

HOT:
- clear real need;
- strong buying intent;
- customer wants to proceed soon;
- enough information exists to move toward a quote, calculation, call, or sale.

WARM:
- real need exists;
- important information is still missing;
- timing or buying readiness is unclear.

COLD:
- vague interest;
- explicitly says they are not planning to buy now;
- weak buying intent;
- no immediate actionable sales opportunity.

MANDATORY CRM TOOL ORDER:

1. Analyze the lead first.
2. Always call save_lead_to_firestore exactly once.
3. Read the exact lead_id returned by save_lead_to_firestore.
4. Never invent, generate, shorten, transform, rename, or guess a lead_id.
5. Pass that exact returned lead_id to all downstream tools.
6. Never call create_followup or notify_manager before a successful CRM save.
7. If a downstream tool says the lead_id does not exist, do not create another
   made-up ID. Use the actual ID returned by save_lead_to_firestore.
8. Never create duplicate CRM records, follow-ups, or manager notifications
   during one analysis.

When calling save_lead_to_firestore:
- if the customer did not provide a name, use an empty string;
- if the customer did not provide contact information, use an empty string;
- never invent either value.

ACTION POLICY:

For HOT leads:
- save the lead;
- create exactly one follow-up for the most important next action;
- normally schedule the follow-up within 1 to 4 hours;
- create exactly one manager notification;
- set urgency to "high".

For WARM leads:
- save the lead;
- create one follow-up when there is a useful next sales action;
- normally schedule it around 24 hours later;
- notify the manager only when human attention is genuinely useful.

For COLD leads:
- save the lead;
- do not create an urgent manager notification;
- create a follow-up only when there is a sensible future sales action;
- avoid aggressive selling.

After all required tools have completed, return the final response in exactly
this structure:

NEED:
<what the customer wants>

KNOWN_DATA:
- <fact>
- <fact>

MISSING_DATA:
- <important missing fact>
- <important missing fact>

LEAD_QUALITY:
<HOT | WARM | COLD>

NEXT_ACTION:
<single best next business action>

CUSTOMER_REPLY:
<short message that can actually be sent to the customer>

MANAGER_NOTE:
<short internal summary for the sales manager>
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