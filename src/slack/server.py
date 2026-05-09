"""
Slack server — handles both /jarvis slash commands and @Jarvis mentions.

Slash commands: one-shot Q&A via /jarvis <question>
App mentions: conversational @Jarvis with full thread context
"""
import hashlib
import hmac
import json
import os
import re
import threading
import time
from urllib.parse import parse_qs

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

app = FastAPI()
slack = WebClient(token=os.environ.get("SLACK_BOT_TOKEN", ""))

# Daily digest — 9:00 AM Central Time, Monday–Friday
def _run_digest():
    from src.agent.digest import post_digest_to_slack
    channel = os.environ.get("SLACK_OPS_CHANNEL_ID", "")
    if channel:
        post_digest_to_slack(slack, channel)

_scheduler = BackgroundScheduler(timezone="America/Chicago")
_scheduler.add_job(_run_digest, CronTrigger(day_of_week="mon-fri", hour=9, minute=0))
_scheduler.start()


def verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        return True

    if abs(time.time() - int(timestamp)) > 300:
        return False

    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def get_bot_user_id() -> str:
    try:
        return slack.auth_test()["user_id"]
    except Exception:
        return ""


def build_thread_history(channel: str, thread_ts: str, bot_user_id: str) -> list[dict]:
    """Fetch thread messages and format as conversation history for the agent."""
    try:
        result = slack.conversations_replies(channel=channel, ts=thread_ts, limit=20)
        messages = result.get("messages", [])
    except Exception:
        return []

    history = []
    for msg in messages:
        user = msg.get("user", "")
        text = re.sub(r"<@[A-Z0-9]+>", "", msg.get("text", "")).strip()
        if not text:
            continue
        role = "assistant" if user == bot_user_id else "user"
        history.append({"role": role, "content": text})

    # Remove last message — that's the current question, handled separately
    if history:
        history.pop()

    return history


def run_agent_threaded(question: str, channel: str, thread_ts: str,
                       user_name: str, history: list[dict]):
    import traceback
    print(f"[agent] starting for: {question}", flush=True)
    try:
        from src.agent.ops_agent import run_agent
        answer = run_agent(question, history=history)
        print(f"[agent] completed, length: {len(answer)}", flush=True)
    except Exception as e:
        answer = f"Sorry, something went wrong: {e}"
        print(f"[agent] ERROR: {traceback.format_exc()}", flush=True)

    try:
        slack.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=answer,
        )
    except Exception as e:
        print(f"[agent] failed to post to Slack: {e}", flush=True)


def run_agent_and_reply(question: str, response_url: str, user_name: str):
    import traceback
    print(f"[agent] starting for: {question}", flush=True)
    try:
        from src.agent.ops_agent import run_agent
        answer = run_agent(question)
        print(f"[agent] completed, answer length: {len(answer)}", flush=True)
    except Exception as e:
        answer = f"Sorry, something went wrong: {e}"
        print(f"[agent] ERROR: {traceback.format_exc()}", flush=True)

    try:
        resp = requests.post(response_url, json={
            "response_type": "in_channel",
            "text": f"*{user_name} asked:* {question}\n\n{answer}",
        })
        print(f"[agent] posted to Slack: {resp.status_code}", flush=True)
    except Exception as e:
        print(f"[agent] failed to post to Slack: {e}", flush=True)


@app.post("/slack/events")
async def slack_events(request: Request):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)

    # Slack URL verification handshake
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    event = payload.get("event", {})
    if event.get("type") != "app_mention":
        return {"ok": True}

    # Ignore bot's own messages
    bot_user_id = get_bot_user_id()
    if event.get("user") == bot_user_id or event.get("bot_id"):
        return {"ok": True}

    channel = event["channel"]
    text = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
    user_name = event.get("user", "someone")
    thread_ts = event.get("thread_ts") or event.get("ts")

    if not text:
        return {"ok": True}

    # Fetch thread history for context
    history = build_thread_history(channel, thread_ts, bot_user_id)

    # Post "thinking" indicator in thread
    slack.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f"On it... _{text}_",
    )

    thread = threading.Thread(
        target=run_agent_threaded,
        args=(text, channel, thread_ts, user_name, history),
        daemon=True,
    )
    thread.start()

    return {"ok": True}


@app.post("/slack/ops")
async def slack_ops_command(request: Request):
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

    return {"response_type": "ephemeral", "text": f"On it... _{text}_"}


@app.post("/internal/digest")
async def trigger_digest(request: Request):
    """Manually trigger the daily digest (for testing or on-demand use)."""
    token = request.headers.get("X-Internal-Token", "")
    if token != os.environ.get("INTERNAL_TOKEN", ""):
        raise HTTPException(status_code=401, detail="Unauthorized")
    thread = threading.Thread(target=_run_digest, daemon=True)
    thread.start()
    return {"status": "digest started"}


@app.get("/health")
def health():
    return {"status": "ok"}
