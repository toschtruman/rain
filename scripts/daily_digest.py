"""
Daily digest script — run via cron or manually.

Generates a pipeline health summary and posts it to #operations-channel.

Usage:
    python scripts/daily_digest.py

Cron (9am Mon-Fri):
    0 9 * * 1-5 cd /path/to/rain && .venv/bin/python scripts/daily_digest.py
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.ops_agent import run_agent
from src.slack.client import SlackClient

DIGEST_PROMPT = """Generate a daily operations digest for Rain's team Slack channel.

Pull data from both Loxo instances (staffing and leadership) and Monday.com CRM, then produce a structured summary covering:

1. **Active Searches** — how many open jobs in staffing vs leadership, which ones are most active
2. **Pipeline Health** — deals by stage in Monday.com, highlight any wins or new proposals this week
3. **Stale Items** — any deals or candidate processes with no apparent recent activity (flag anything >14 days)
4. **Candidate Activity** — recent candidate additions or notable pipeline movements
5. **Action Items** — 2-3 specific things the team should focus on today

Format for Slack: use *bold* for headers, bullet points for lists, and keep each section tight (2-4 bullets max).
Start with a one-line summary like: "🌧️ Rain Daily Digest — [date]"
"""


def main():
    print("Running daily digest...")
    digest = run_agent(DIGEST_PROMPT)
    print("\n--- DIGEST ---")
    print(digest)
    print("--------------\n")

    slack = SlackClient()
    slack.post_digest(digest)
    print("Posted to Slack.")


if __name__ == "__main__":
    main()
