# ruff: noqa

import asyncio
import os
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from google.adk.runners import InMemoryRunner
from google.genai import types


# =========================================================
# CONFIG
# =========================================================

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"

APP_NAME = "leadpilot_web_demo"

# Public demo protection
RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 10 * 60
MAX_MESSAGE_CHARS = 2000


# =========================================================
# LOCAL ENV LOADER
# Cloud Run receives GEMINI_API_KEY from Secret Manager.
# This section is only for local development.
# =========================================================

def load_local_env():
    if not ENV_FILE.exists():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)

        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and value:
            os.environ.setdefault(key, value)


load_local_env()


# Import after environment is available
from app.agent import root_agent, db


# =========================================================
# ADK
# =========================================================

runner = InMemoryRunner(
    app_name=APP_NAME,
    agent=root_agent,
)


web_app = FastAPI(
    title="LeadPilot AI Sales Operator",
    version="1.0.0-demo",
)


class LeadRequest(BaseModel):
    message: str


# =========================================================
# RATE LIMIT
# 5 AI executions / 10 minutes / IP
# =========================================================

_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = asyncio.Lock()


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client:
        return request.client.host

    return "unknown"


async def enforce_rate_limit(client_ip: str):
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS

    async with _rate_lock:
        bucket = _rate_buckets[client_ip]

        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            oldest_request = bucket[0]

            retry_after = max(
                1,
                int(
                    RATE_LIMIT_WINDOW_SECONDS
                    - (now - oldest_request)
                ),
            )

            raise HTTPException(
                status_code=429,
                detail=(
                    "Demo limit reached. "
                    "Maximum 5 AI workflows are allowed "
                    "per 10 minutes from one IP address."
                ),
                headers={
                    "Retry-After": str(retry_after),
                },
            )

        bucket.append(now)


# =========================================================
# FIRESTORE HELPERS
# Used to verify what the agent ACTUALLY executed.
# =========================================================

def collection_ids(collection_name: str) -> set[str]:
    return {
        doc.id
        for doc in db.collection(collection_name).stream()
    }


def snapshot_database() -> dict[str, set[str]]:
    return {
        "leads": collection_ids("leads"),
        "followups": collection_ids("followups"),
        "manager_notifications": collection_ids(
            "manager_notifications"
        ),
    }


def new_documents(
    collection_name: str,
    previous_ids: set[str],
):
    documents = []

    for document in db.collection(collection_name).stream():
        if document.id not in previous_ids:
            documents.append(document)

    return documents


def newest_document(documents):
    if not documents:
        return None

    def created_at(document):
        data = document.to_dict()
        value = data.get("created_at")

        if value is None:
            return 0

        try:
            return value.timestamp()
        except Exception:
            return 0

    return max(
        documents,
        key=created_at,
    )


def format_delay_hours(created_at, due_at):
    if not created_at or not due_at:
        return None

    try:
        seconds = (
            due_at - created_at
        ).total_seconds()

        hours = seconds / 3600

        if abs(hours - round(hours)) < 0.05:
            return str(int(round(hours)))

        return f"{hours:.1f}"

    except Exception:
        return None


# =========================================================
# AGENT EXECUTION
# =========================================================

async def run_leadpilot(message: str):
    before = snapshot_database()

    user_id = f"demo-{uuid.uuid4().hex}"

    session = await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
    )

    user_message = types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text=message,
            )
        ],
    )

    final_text = ""

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=user_message,
    ):
        if event.is_final_response() and event.content:
            text_parts = []

            for part in event.content.parts or []:
                if getattr(part, "text", None):
                    text_parts.append(part.text)

            if text_parts:
                final_text = "\n".join(text_parts)

    # -----------------------------------------------------
    # Verify actual Firestore side effects
    # -----------------------------------------------------

    new_leads = new_documents(
        "leads",
        before["leads"],
    )

    new_followups = new_documents(
        "followups",
        before["followups"],
    )

    new_notifications = new_documents(
        "manager_notifications",
        before["manager_notifications"],
    )

    lead_doc = newest_document(new_leads)

    lead_id = None
    lead_quality = "UNKNOWN"

    if lead_doc:
        lead_id = lead_doc.id

        lead_data = lead_doc.to_dict()

        lead_quality = str(
            lead_data.get(
                "lead_quality",
                "UNKNOWN",
            )
        ).upper()

    # -----------------------------------------------------
    # Find downstream actions linked to this exact lead
    # -----------------------------------------------------

    followup_doc = None

    for doc in new_followups:
        data = doc.to_dict()

        if (
            lead_id
            and data.get("lead_id") == lead_id
        ):
            followup_doc = doc
            break

    notification_doc = None

    for doc in new_notifications:
        data = doc.to_dict()

        if (
            lead_id
            and data.get("lead_id") == lead_id
        ):
            notification_doc = doc
            break

    # -----------------------------------------------------
    # CRM status
    # -----------------------------------------------------

    crm_action = {
        "status": (
            "completed"
            if lead_doc
            else "not_created"
        ),
        "lead_id": lead_id,
    }

    # -----------------------------------------------------
    # Follow-up status
    # -----------------------------------------------------

    if followup_doc:
        followup_data = followup_doc.to_dict()

        delay_hours = format_delay_hours(
            followup_data.get("created_at"),
            followup_data.get("due_at"),
        )

        followup_action = {
            "status": "completed",
            "followup_id": followup_doc.id,
            "delay_hours": delay_hours,
            "action": followup_data.get(
                "action",
                "",
            ),
        }

    else:
        followup_action = {
            "status": "skipped",
            "followup_id": None,
            "delay_hours": None,
            "action": "",
        }

    # -----------------------------------------------------
    # Manager escalation status
    # -----------------------------------------------------

    if notification_doc:
        notification_data = (
            notification_doc.to_dict()
        )

        manager_action = {
            "status": "completed",
            "notification_id": (
                notification_doc.id
            ),
            "urgency": notification_data.get(
                "urgency",
                "normal",
            ),
        }

    else:
        manager_action = {
            "status": "skipped",
            "notification_id": None,
            "urgency": None,
        }

    if not final_text:
        final_text = (
            "LeadPilot completed the workflow, "
            "but no final text response was returned."
        )

    return {
        "response": final_text,
        "lead_quality": lead_quality,
        "actions": {
            "crm": crm_action,
            "followup": followup_action,
            "manager": manager_action,
        },
    }


# =========================================================
# UI
# =========================================================

@web_app.get("/", response_class=HTMLResponse)
async def home():
    return """
<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>LeadPilot — Autonomous AI Sales Operator</title>


<style>

* {
    box-sizing: border-box;
}

html {
    scroll-behavior: smooth;
}

body {
    margin: 0;

    font-family:
        Inter,
        ui-sans-serif,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background:
        radial-gradient(
            circle at 12% 10%,
            rgba(37, 99, 235, .24),
            transparent 32%
        ),
        radial-gradient(
            circle at 88% 88%,
            rgba(5, 150, 105, .22),
            transparent 30%
        ),
        #060a12;

    color: #f8fafc;

    min-height: 100vh;
}


.shell {
    width: min(
        1240px,
        calc(100% - 40px)
    );

    margin: 0 auto;

    padding:
        34px
        0
        58px;
}


.topbar {
    display: flex;

    align-items: center;

    justify-content: space-between;

    margin-bottom: 35px;
}


.brand {
    display: flex;

    align-items: center;

    gap: 12px;
}


.logo {
    width: 44px;

    height: 44px;

    display: grid;

    place-items: center;

    border-radius: 13px;

    font-weight: 850;

    font-size: 20px;

    background:
        linear-gradient(
            135deg,
            #2563eb,
            #10b981
        );

    box-shadow:
        0 12px 42px
        rgba(37, 99, 235, .28);
}


.brand-title {
    font-size: 20px;

    font-weight: 780;
}


.brand-subtitle {
    color: #94a3b8;

    font-size: 13px;

    margin-top: 2px;
}


.status-area {
    display: flex;

    align-items: center;

    gap: 8px;

    flex-wrap: wrap;
}


.pill {
    padding:
        8px
        12px;

    border-radius: 999px;

    font-size: 12px;

    border:
        1px solid
        rgba(100, 116, 139, .25);

    background:
        rgba(15, 23, 42, .72);

    color: #94a3b8;
}


.online {
    border-color:
        rgba(52, 211, 153, .35);

    background:
        rgba(16, 185, 129, .08);

    color: #6ee7b7;
}


.hero {
    margin-bottom: 28px;
}


.hero h1 {
    font-size:
        clamp(
            34px,
            5vw,
            60px
        );

    line-height: 1.02;

    letter-spacing: -2.1px;

    margin: 0;

    max-width: 880px;
}


.hero h1 span {
    background:
        linear-gradient(
            90deg,
            #60a5fa,
            #34d399
        );

    -webkit-background-clip: text;

    color: transparent;
}


.hero p {
    color: #94a3b8;

    max-width: 790px;

    line-height: 1.65;

    margin-top: 18px;

    font-size: 16px;
}


.flow {
    display: flex;

    flex-wrap: wrap;

    gap: 8px;

    margin-top: 19px;
}


.flow span {
    border:
        1px solid
        #263249;

    background:
        rgba(15, 23, 42, .72);

    color: #cbd5e1;

    padding:
        7px
        11px;

    border-radius: 9px;

    font-size: 12px;
}


.grid {
    display: grid;

    grid-template-columns:
        .88fr
        1.12fr;

    gap: 22px;
}


.card {
    background:
        rgba(12, 18, 32, .91);

    border:
        1px solid
        rgba(100, 116, 139, .22);

    border-radius: 20px;

    padding: 22px;

    box-shadow:
        0 20px 70px
        rgba(0, 0, 0, .24);

    backdrop-filter:
        blur(12px);
}


.label {
    font-size: 13px;

    color: #94a3b8;

    margin-bottom: 10px;
}


textarea {
    width: 100%;

    min-height: 245px;

    resize: vertical;

    background: #080d18;

    color: #f8fafc;

    border:
        1px solid
        #263249;

    border-radius: 14px;

    padding: 16px;

    font-family: inherit;

    font-size: 15px;

    line-height: 1.55;

    outline: none;
}


textarea:focus {
    border-color: #3b82f6;

    box-shadow:
        0 0 0 3px
        rgba(59, 130, 246, .12);
}


.character-row {
    display: flex;

    justify-content: space-between;

    margin-top: 7px;

    font-size: 11px;

    color: #64748b;
}


button {
    border: none;

    cursor: pointer;

    transition:
        transform .15s ease,
        opacity .15s ease,
        border-color .15s ease;
}


.primary {
    width: 100%;

    margin-top: 14px;

    border-radius: 13px;

    padding:
        14px
        18px;

    color: white;

    font-weight: 750;

    font-size: 15px;

    background:
        linear-gradient(
            90deg,
            #2563eb,
            #059669
        );

    box-shadow:
        0 10px 30px
        rgba(37, 99, 235, .12);
}


.primary:hover {
    transform:
        translateY(-1px);
}


.primary:disabled {
    opacity: .55;

    cursor: wait;

    transform: none;
}


.examples {
    display: flex;

    gap: 8px;

    flex-wrap: wrap;

    margin-top: 14px;
}


.example {
    background: #111827;

    color: #cbd5e1;

    border:
        1px solid
        #263249;

    border-radius: 10px;

    font-size: 12px;

    padding:
        8px
        10px;
}


.example:hover {
    border-color: #475569;
}


.result-header {
    display: flex;

    align-items: center;

    justify-content: space-between;

    gap: 12px;

    margin-bottom: 13px;
}


.result-title {
    font-size: 17px;

    font-weight: 760;
}


.tech {
    color: #93c5fd;

    font-size: 12px;
}


.quality {
    display: none;

    width: fit-content;

    margin-bottom: 13px;

    padding:
        7px
        11px;

    border-radius: 999px;

    font-size: 12px;

    font-weight: 850;

    letter-spacing: .8px;
}


.quality.hot {
    display: inline-block;

    color: #fecaca;

    border:
        1px solid
        rgba(248, 113, 113, .4);

    background:
        rgba(127, 29, 29, .25);
}


.quality.warm {
    display: inline-block;

    color: #fde68a;

    border:
        1px solid
        rgba(251, 191, 36, .4);

    background:
        rgba(120, 53, 15, .25);
}


.quality.cold {
    display: inline-block;

    color: #bae6fd;

    border:
        1px solid
        rgba(56, 189, 248, .35);

    background:
        rgba(7, 89, 133, .22);
}


#result {
    min-height: 365px;

    max-height: 600px;

    overflow: auto;

    white-space: pre-wrap;

    word-break: break-word;

    background: #080d18;

    border:
        1px solid
        #263249;

    border-radius: 14px;

    padding: 17px;

    color: #dbeafe;

    font-size: 14px;

    line-height: 1.58;
}


.placeholder {
    color: #64748b;
}


.workflow {
    margin-top: 22px;

    background:
        rgba(12, 18, 32, .91);

    border:
        1px solid
        rgba(100, 116, 139, .22);

    border-radius: 20px;

    padding: 22px;

    display: none;

    box-shadow:
        0 20px 70px
        rgba(0, 0, 0, .18);
}


.workflow.visible {
    display: block;
}


.workflow-heading {
    display: flex;

    justify-content: space-between;

    align-items: center;

    margin-bottom: 16px;
}


.workflow-heading strong {
    font-size: 17px;
}


.workflow-heading span {
    color: #64748b;

    font-size: 12px;
}


.actions {
    display: grid;

    grid-template-columns:
        repeat(3, 1fr);

    gap: 13px;
}


.action-card {
    background: #080d18;

    border:
        1px solid
        #263249;

    border-radius: 14px;

    padding: 16px;

    min-height: 142px;
}


.action-top {
    display: flex;

    align-items: center;

    justify-content: space-between;

    gap: 8px;

    margin-bottom: 11px;
}


.action-name {
    font-weight: 760;

    font-size: 14px;
}


.action-state {
    font-size: 12px;

    font-weight: 780;
}


.completed {
    color: #6ee7b7;
}


.skipped {
    color: #94a3b8;
}


.failed {
    color: #fca5a5;
}


.action-detail {
    color: #94a3b8;

    font-size: 12px;

    line-height: 1.55;

    word-break: break-word;

    white-space: pre-wrap;
}


.protection {
    display: flex;

    align-items: center;

    gap: 7px;

    margin-top: 14px;

    color: #64748b;

    font-size: 11px;
}


.protection-dot {
    width: 6px;

    height: 6px;

    border-radius: 50%;

    background: #10b981;
}


.footer {
    margin-top: 24px;

    color: #64748b;

    font-size: 12px;

    text-align: center;
}


@media (max-width: 850px) {

    .grid {
        grid-template-columns: 1fr;
    }

    .actions {
        grid-template-columns: 1fr;
    }

    .topbar {
        align-items: flex-start;

        gap: 15px;

        flex-direction: column;
    }
}

</style>

</head>


<body>


<div class="shell">


    <div class="topbar">

        <div class="brand">

            <div class="logo">
                L
            </div>

            <div>

                <div class="brand-title">
                    LeadPilot
                </div>

                <div class="brand-subtitle">
                    Autonomous AI Sales Operator
                </div>

            </div>

        </div>


        <div class="status-area">

            <div class="pill">
                Protected demo
            </div>

            <div class="pill online">
                ● Agent online
            </div>

        </div>

    </div>


    <section class="hero">

        <h1>
            Turn incoming leads into
            <span>next actions.</span>
        </h1>

        <p>
            LeadPilot analyzes incoming demand,
            qualifies buying intent,
            executes CRM actions,
            schedules follow-ups,
            and escalates only the opportunities
            that actually need a human.
        </p>


        <div class="flow">

            <span>Incoming lead</span>

            <span>→</span>

            <span>AI reasoning</span>

            <span>→</span>

            <span>CRM</span>

            <span>→</span>

            <span>Follow-up</span>

            <span>→</span>

            <span>Human escalation</span>

        </div>

    </section>


    <div class="grid">


        <section class="card">

            <div class="label">
                Incoming customer message
            </div>


            <textarea
                id="message"
                maxlength="2000"
                oninput="updateCharacterCount()"
                placeholder="Example: Хочу тепловий насос для утепленого будинку 160 м² у Нетішині..."
            ></textarea>


            <div class="character-row">

                <span>
                    Customer message
                </span>

                <span id="characterCount">
                    0 / 2000
                </span>

            </div>


            <button
                id="analyzeButton"
                class="primary"
                onclick="analyzeLead()"
            >
                Analyze & execute workflow
            </button>


            <div class="examples">

                <button
                    class="example"
                    onclick="setHot()"
                >
                    HOT example
                </button>

                <button
                    class="example"
                    onclick="setWarm()"
                >
                    WARM example
                </button>

                <button
                    class="example"
                    onclick="setCold()"
                >
                    COLD example
                </button>

            </div>


            <div class="protection">

                <div class="protection-dot"></div>

                <span>
                    Demo protected by request and infrastructure limits
                </span>

            </div>

        </section>


        <section class="card">


            <div class="result-header">

                <div class="result-title">
                    LeadPilot decision
                </div>

                <div class="tech">
                    Gemini + Google ADK
                </div>

            </div>


            <div
                id="qualityBadge"
                class="quality"
            ></div>


            <div id="result">

                <span class="placeholder">

Submit a lead to see qualification,
next action, customer reply,
and manager note.

LeadPilot will execute the matching
Firestore workflow automatically.

                </span>

            </div>


        </section>


    </div>


    <section
        id="workflow"
        class="workflow"
    >


        <div class="workflow-heading">

            <strong>
                Executed business actions
            </strong>

            <span>
                verified from Firestore
            </span>

        </div>


        <div class="actions">


            <div class="action-card">

                <div class="action-top">

                    <div class="action-name">
                        CRM
                    </div>

                    <div
                        id="crmState"
                        class="action-state"
                    ></div>

                </div>


                <div
                    id="crmDetail"
                    class="action-detail"
                ></div>

            </div>


            <div class="action-card">

                <div class="action-top">

                    <div class="action-name">
                        Follow-up
                    </div>

                    <div
                        id="followupState"
                        class="action-state"
                    ></div>

                </div>


                <div
                    id="followupDetail"
                    class="action-detail"
                ></div>

            </div>


            <div class="action-card">

                <div class="action-top">

                    <div class="action-name">
                        Human escalation
                    </div>

                    <div
                        id="managerState"
                        class="action-state"
                    ></div>

                </div>


                <div
                    id="managerDetail"
                    class="action-detail"
                ></div>

            </div>


        </div>


    </section>


    <div class="footer">
        LeadPilot · Gemini · Google ADK · Firestore · Google Cloud
    </div>


</div>


<script>


function updateCharacterCount() {

    const message =
        document.getElementById(
            "message"
        );

    document.getElementById(
        "characterCount"
    ).textContent =
        message.value.length +
        " / 2000";
}


function fillExample(text) {

    document.getElementById(
        "message"
    ).value = text;

    updateCharacterCount();
}


function setHot() {

    fillExample(
        "Хочу тепловий насос для утепленого будинку 160 м² у Нетішині. " +
        "Є водяна тепла підлога і 3 фази. " +
        "Хочу купити найближчими днями, " +
        "передзвоніть мені для підбору."
    );
}


function setWarm() {

    fillExample(
        "Цікавить тепловий насос для будинку приблизно 140 м². " +
        "Будинок ще будується, систему опалення остаточно не вирішив. " +
        "Хотів би зрозуміти що потрібно і які наступні кроки."
    );
}


function setCold() {

    fillExample(
        "Просто цікавлюсь тепловими насосами. " +
        "Будинок поки не будую і купувати найближчим часом нічого не планую. " +
        "Хотів лише приблизно зрозуміти як це працює."
    );
}


function setState(
    element,
    state
) {

    element.classList.remove(
        "completed",
        "skipped",
        "failed"
    );

    if (state === "completed") {

        element.textContent =
            "✓ COMPLETED";

        element.classList.add(
            "completed"
        );

    } else if (state === "skipped") {

        element.textContent =
            "— SKIPPED";

        element.classList.add(
            "skipped"
        );

    } else {

        element.textContent =
            "✕ NOT CREATED";

        element.classList.add(
            "failed"
        );
    }
}


function renderQuality(quality) {

    const badge =
        document.getElementById(
            "qualityBadge"
        );

    const value =
        (quality || "UNKNOWN")
        .toUpperCase();

    badge.className =
        "quality";

    if (value === "HOT") {

        badge.classList.add(
            "hot"
        );

    } else if (value === "WARM") {

        badge.classList.add(
            "warm"
        );

    } else if (value === "COLD") {

        badge.classList.add(
            "cold"
        );
    }

    badge.textContent = value;
}


function renderActions(actions) {

    const workflow =
        document.getElementById(
            "workflow"
        );

    workflow.classList.add(
        "visible"
    );


    // CRM

    const crmState =
        document.getElementById(
            "crmState"
        );

    const crmDetail =
        document.getElementById(
            "crmDetail"
        );

    setState(
        crmState,
        actions.crm.status
    );

    if (actions.crm.lead_id) {

        crmDetail.textContent =
            "Lead saved to Firestore\\n\\n" +
            "Lead ID\\n" +
            actions.crm.lead_id;

    } else {

        crmDetail.textContent =
            "No CRM lead was created.";
    }


    // Follow-up

    const followupState =
        document.getElementById(
            "followupState"
        );

    const followupDetail =
        document.getElementById(
            "followupDetail"
        );

    setState(
        followupState,
        actions.followup.status
    );


    if (
        actions.followup.status
        === "completed"
    ) {

        let text =
            "Follow-up scheduled";

        if (
            actions.followup.delay_hours
        ) {

            text +=
                " in " +
                actions.followup.delay_hours +
                "h";
        }

        if (
            actions.followup.action
        ) {

            text +=
                "\\n\\n" +
                actions.followup.action;
        }

        followupDetail.textContent =
            text;

    } else {

        followupDetail.textContent =
            "Skipped by LeadPilot business rules.";
    }


    // Human escalation

    const managerState =
        document.getElementById(
            "managerState"
        );

    const managerDetail =
        document.getElementById(
            "managerDetail"
        );

    setState(
        managerState,
        actions.manager.status
    );


    if (
        actions.manager.status
        === "completed"
    ) {

        managerDetail.textContent =
            "Manager notified\\n\\n" +
            "Urgency: " +
            (
                actions.manager.urgency
                || "normal"
            ).toUpperCase();

    } else {

        managerDetail.textContent =
            "Human escalation not required.";
    }
}


async function analyzeLead() {

    const message =
        document
        .getElementById(
            "message"
        )
        .value
        .trim();

    const button =
        document.getElementById(
            "analyzeButton"
        );

    const result =
        document.getElementById(
            "result"
        );


    if (!message) {

        result.textContent =
            "Enter a customer message first.";

        return;
    }


    if (message.length > 2000) {

        result.textContent =
            "Customer message is too long.";

        return;
    }


    button.disabled = true;

    button.textContent =
        "LeadPilot is working...";


    document
        .getElementById(
            "workflow"
        )
        .classList
        .remove(
            "visible"
        );


    result.textContent =
        "Understanding customer intent...\\n" +
        "Qualifying buying readiness...\\n" +
        "Executing CRM workflow...\\n" +
        "Applying business guardrails...";


    try {

        const response =
            await fetch(
                "/analyze",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            {
                                message: message
                            }
                        )
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail
                || "Request failed"
            );
        }


        result.textContent =
            data.response;


        renderQuality(
            data.lead_quality
        );


        renderActions(
            data.actions
        );


    } catch (error) {

        result.textContent =
            "LeadPilot request failed.\\n\\n" +
            error.message;

    } finally {

        button.disabled =
            false;

        button.textContent =
            "Analyze & execute workflow";
    }
}


</script>


</body>

</html>
"""


# =========================================================
# API
# =========================================================

@web_app.post("/analyze")
async def analyze_lead(
    request: Request,
    payload: LeadRequest,
):
    client_ip = get_client_ip(
        request
    )

    await enforce_rate_limit(
        client_ip
    )

    message = payload.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Customer message is required.",
        )

    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(
            status_code=413,
            detail=(
                "Customer message exceeds "
                "the 2000 character demo limit."
            ),
        )

    try:
        return await run_leadpilot(
            message
        )

    except HTTPException:
        raise

    except Exception as exc:
        print(
            "LeadPilot execution error:",
            type(exc).__name__,
            str(exc),
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "LeadPilot could not complete "
                "the workflow. "
                "Check the server logs."
            ),
        )


@web_app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": "leadpilot_sales_operator",
        "database": "firestore",
        "demo_protection": {
            "requests": RATE_LIMIT_MAX_REQUESTS,
            "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
            "max_message_chars": MAX_MESSAGE_CHARS,
        },
    }


app = web_app