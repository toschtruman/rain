"""
Slack slash command server.

Handles /jarvis <question> commands from Slack.
Slack requires a response within 3 seconds, so we acknowledge immediately
and send the real answer to response_url in a background thread.
"""
import hashlib
import hmac
import os
import threading
import time
from urllib.parse import parse_qs

import requests
from fastapi import FastAPI, HTTPException, Request
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()


def verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        return True  # skip verification in dev if secret not set

    if abs(time.time() - int(timestamp)) > 300:
        return False

    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def run_agent_and_reply(question: str, response_url: str, user_name: str):
    from src.agent.ops_agent import run_agent
    try:
        answer = run_agent(question)
    except Exception as e:
        answer = f"Sorry, something went wrong: {e}"

    requests.post(response_url, json={
        "response_type": "in_channel",
        "text": f"*{user_name} asked:* {question}\n\n{answer}",
    })


@app.post("/slack/ops")
async def slack_ops_command(request: Request):
    # Read raw body once, then parse form fields from it
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    params = parse_qs(body.decode())
    text = params.get("text", [""])[0].strip()
    user_name = params.get("user_name", ["someone"])[0]
    response_url = params.get("response_url", [""])[0]

    if not text:
        return {"response_type": "ephemeral", "text": "Usage: `/jarvis <your question>`"}

    thread = threading.Thread(
        target=run_agent_and_reply,
        args=(text, response_url, user_name),
        daemon=True,
    )
    thread.start()

    return {
        "response_type": "ephemeral",
        "text": f"On it... _{text}_",
    }


@app.get("/health")
def health():
    return {"status": "ok"}
