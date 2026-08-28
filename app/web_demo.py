# ruff: noqa

import os
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from google.adk.runners import InMemoryRunner
from google.genai import types


# ---------------------------------------------------------
# Load local .env without printing or exposing the API key
# ---------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"


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


# Import only after environment is loaded.
from app.agent import root_agent


APP_NAME = "leadpilot_web_demo"


# Reuse one runner.
runner = InMemoryRunner(
    app_name=APP_NAME,
    agent=root_agent,
)


web_app = FastAPI(
    title="LeadPilot AI Sales Operator",
)


class LeadRequest(BaseModel):
    message: str


async def run_leadpilot(message: str) -> str:
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

    if not final_text:
        return "LeadPilot completed the workflow, but no final text response was returned."

    return final_text


@web_app.get("/", response_class=HTMLResponse)
async def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>LeadPilot — AI Sales Operator</title>

    <style>
        * {
            box-sizing: border-box;
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
                radial-gradient(circle at top left, #172554 0%, transparent 35%),
                radial-gradient(circle at bottom right, #064e3b 0%, transparent 30%),
                #070b14;

            color: #f8fafc;
            min-height: 100vh;
        }

        .shell {
            width: min(1180px, calc(100% - 40px));
            margin: 0 auto;
            padding: 38px 0 60px;
        }

        .topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 34px;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .logo {
            width: 42px;
            height: 42px;
            border-radius: 13px;
            display: grid;
            place-items: center;
            font-weight: 800;
            font-size: 20px;
            background: linear-gradient(135deg, #2563eb, #10b981);
            box-shadow: 0 10px 40px rgba(37, 99, 235, .25);
        }

        .brand-title {
            font-size: 20px;
            font-weight: 750;
        }

        .brand-subtitle {
            color: #94a3b8;
            font-size: 13px;
            margin-top: 2px;
        }

        .status {
            border: 1px solid rgba(52, 211, 153, .35);
            background: rgba(16, 185, 129, .08);
            color: #6ee7b7;
            padding: 8px 12px;
            border-radius: 999px;
            font-size: 13px;
        }

        .hero {
            margin-bottom: 30px;
        }

        .hero h1 {
            font-size: clamp(34px, 5vw, 58px);
            line-height: 1.02;
            letter-spacing: -2px;
            margin: 0;
            max-width: 850px;
        }

        .hero h1 span {
            background: linear-gradient(90deg, #60a5fa, #34d399);
            -webkit-background-clip: text;
            color: transparent;
        }

        .hero p {
            color: #94a3b8;
            max-width: 760px;
            line-height: 1.6;
            margin-top: 18px;
            font-size: 16px;
        }

        .flow {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 20px;
        }

        .flow span {
            border: 1px solid #263249;
            background: rgba(15, 23, 42, .72);
            color: #cbd5e1;
            padding: 7px 11px;
            border-radius: 9px;
            font-size: 12px;
        }

        .grid {
            display: grid;
            grid-template-columns: 0.9fr 1.1fr;
            gap: 22px;
        }

        .card {
            background: rgba(12, 18, 32, .88);
            border: 1px solid rgba(100, 116, 139, .22);
            border-radius: 20px;
            padding: 23px;
            box-shadow: 0 20px 70px rgba(0, 0, 0, .22);
            backdrop-filter: blur(12px);
        }

        .label {
            font-size: 13px;
            color: #94a3b8;
            margin-bottom: 10px;
        }

        textarea {
            width: 100%;
            min-height: 270px;
            resize: vertical;

            background: #080d18;
            color: #f8fafc;

            border: 1px solid #263249;
            border-radius: 14px;

            padding: 16px;

            font-family: inherit;
            font-size: 15px;
            line-height: 1.55;

            outline: none;
        }

        textarea:focus {
            border-color: #3b82f6;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, .12);
        }

        button {
            width: 100%;
            margin-top: 14px;

            border: none;
            border-radius: 13px;

            padding: 14px 18px;

            color: white;
            font-weight: 700;
            font-size: 15px;

            background: linear-gradient(90deg, #2563eb, #059669);

            cursor: pointer;
            transition: transform .15s ease, opacity .15s ease;
        }

        button:hover {
            transform: translateY(-1px);
        }

        button:disabled {
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
            width: auto;
            background: #111827;
            color: #cbd5e1;
            border: 1px solid #263249;
            font-size: 12px;
            padding: 8px 10px;
            margin: 0;
        }

        .result-title {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
        }

        .result-title strong {
            font-size: 17px;
        }

        .badge {
            font-size: 12px;
            color: #93c5fd;
        }

        #result {
            min-height: 375px;

            white-space: pre-wrap;
            word-break: break-word;

            background: #080d18;

            border: 1px solid #263249;
            border-radius: 14px;

            padding: 17px;

            color: #dbeafe;
            font-size: 14px;
            line-height: 1.6;

            overflow: auto;
        }

        .placeholder {
            color: #64748b;
        }

        .footer {
            margin-top: 24px;
            color: #64748b;
            font-size: 12px;
            text-align: center;
        }

        @media (max-width: 820px) {
            .grid {
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
            <div class="logo">L</div>

            <div>
                <div class="brand-title">LeadPilot</div>
                <div class="brand-subtitle">AI Sales Operator</div>
            </div>
        </div>

        <div class="status">
            ● Agent online
        </div>
    </div>


    <section class="hero">
        <h1>
            Turn incoming leads into
            <span>next actions.</span>
        </h1>

        <p>
            LeadPilot analyzes a customer request, qualifies the lead,
            decides what should happen next, updates CRM data,
            schedules follow-up, and escalates only high-priority opportunities.
        </p>

        <div class="flow">
            <span>Incoming lead</span>
            <span>→</span>
            <span>AI qualification</span>
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
                placeholder="Example: Хочу тепловий насос для утепленого будинку 160 м² у Нетішині..."
            ></textarea>

            <button id="analyzeButton" onclick="analyzeLead()">
                Analyze & execute workflow
            </button>

            <div class="examples">

                <button class="example" onclick="setHot()">
                    HOT example
                </button>

                <button class="example" onclick="setWarm()">
                    WARM example
                </button>

                <button class="example" onclick="setCold()">
                    COLD example
                </button>

            </div>

        </section>


        <section class="card">

            <div class="result-title">
                <strong>LeadPilot decision</strong>
                <span class="badge">Gemini + Google ADK</span>
            </div>

            <div id="result">
                <span class="placeholder">
                    Submit a lead to see qualification, next action,
                    customer reply and manager note.

                    The agent will also execute the appropriate Firestore CRM workflow.
                </span>
            </div>

        </section>

    </div>


    <div class="footer">
        LeadPilot Agent · Google Cloud · Firestore · Gemini · ADK
    </div>

</div>


<script>

function setHot() {
    document.getElementById("message").value =
        "Хочу тепловий насос для утепленого будинку 160 м² у Нетішині. " +
        "Є водяна тепла підлога і 3 фази. Хочу купити найближчими днями, " +
        "передзвоніть мені для підбору.";
}

function setWarm() {
    document.getElementById("message").value =
        "Цікавить тепловий насос для будинку приблизно 140 м². " +
        "Будинок ще будується, систему опалення остаточно не вирішив. " +
        "Хотів би зрозуміти що потрібно і які наступні кроки.";
}

function setCold() {
    document.getElementById("message").value =
        "Просто цікавлюсь тепловими насосами. Будинок поки не будую " +
        "і купувати найближчим часом нічого не планую. " +
        "Хотів лише приблизно зрозуміти як це працює.";
}


async function analyzeLead() {

    const message = document.getElementById("message").value.trim();
    const button = document.getElementById("analyzeButton");
    const result = document.getElementById("result");

    if (!message) {
        result.textContent = "Enter a customer message first.";
        return;
    }

    button.disabled = true;
    button.textContent = "LeadPilot is working...";

    result.textContent =
        "Analyzing lead...\\n" +
        "Updating CRM...\\n" +
        "Deciding follow-up and escalation...";

    try {

        const response = await fetch("/analyze", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message: message
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Request failed");
        }

        result.textContent = data.response;

    } catch (error) {

        result.textContent =
            "ERROR\\n\\n" +
            error.message;

    } finally {

        button.disabled = false;
        button.textContent = "Analyze & execute workflow";

    }
}

</script>

</body>
</html>
"""


@web_app.post("/analyze")
async def analyze_lead(request: LeadRequest):
    message = request.message.strip()

    if not message:
        return {
            "response": "Please provide a customer message."
        }

    try:
        response = await run_leadpilot(message)

        return {
            "response": response,
        }

    except Exception as exc:
        # Keep secrets/internal credentials out of the browser response.
        print(f"LeadPilot execution error: {type(exc).__name__}: {exc}")

        return {
            "response": (
                "LeadPilot could not complete this request. "
                "Check the local server console for details."
            )
        }


@web_app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": "leadpilot_sales_operator",
    }


# Uvicorn looks for this variable.
app = web_app