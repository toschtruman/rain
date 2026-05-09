"""
Daily digest — runs the ops agent with a structured briefing prompt
and posts the result to the Slack ops channel.
"""
import os
from datetime import datetime

from src.agent.ops_agent import run_agent

DIGEST_PROMPT = """Generate the daily morning ops briefing for the Rain recruiting team. Pull data from both Loxo instances and Monday.com, then produce a concise summary covering:

1. Open jobs across staffing and leadership — how many, and flag any that look stuck (no recent candidate movement)
2. Monday.com pipeline — deal counts by stage, highlight anything in early stages that may need a push
3. Recent placements in the last 7 days (both instances)

Max 10 bullet points. Lead with anything urgent. No preamble."""


def generate_digest() -> str:
    return run_agent(DIGEST_PROMPT)


def post_digest_to_slack(slack_client, channel_id: str) -> None:
    import traceback
    print("[digest] generating daily digest...", flush=True)
    try:
        answer = generate_digest()
        date_str = datetime.now().strftime("%A, %B %-d")
        slack_client.chat_postMessage(
            channel=channel_id,
            text=f"*Daily Ops Digest — {date_str}*\n\n{answer}",
        )
        print("[digest] posted successfully", flush=True)
    except Exception:
        print(f"[digest] ERROR:\n{traceback.format_exc()}", flush=True)
