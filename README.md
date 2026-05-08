# Rain Internal Ops Agent

Claude-powered internal operations tool that connects Loxo ATS, Monday.com CRM, and Slack.

## What it does

- **Daily digest** — posts a pipeline health summary to #operations-channel every morning
- **On-demand Q&A** — answer questions like "which deals have gone stale?" or "how many open searches in leadership?"
- **Dual Loxo support** — queries both `rain-staffing` and `rain-leadership` instances

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Fill in `.env`:
- `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com)
- `LOXO_RAIN_STAFFING_API_KEY` / `LOXO_RAIN_LEADERSHIP_API_KEY` — already set
- `MONDAY_API_KEY` — from Monday.com → Admin → API
- `SLACK_BOT_TOKEN` — see Slack setup below
- `SLACK_OPS_CHANNEL_ID` — right-click #operations-channel → Copy channel ID

### 3. Slack app setup

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch
2. Add Bot Token Scopes: `chat:write`, `chat:write.public`
3. Install to workspace → copy the Bot User OAuth Token into `.env`
4. Invite the bot to #operations-channel: `/invite @your-bot-name`

## Usage

### Ask a question
```bash
python scripts/ask.py "How many open searches do we have right now?"
python scripts/ask.py "Which deals in Monday have gone stale?"
python scripts/ask.py "What candidates were added to rain-leadership this week?"
```

### Run the daily digest manually
```bash
python scripts/daily_digest.py
```

### Schedule daily digest (cron)
```
# Run at 9am Mon-Fri
0 9 * * 1-5 cd /path/to/rain && .venv/bin/python scripts/daily_digest.py >> /var/log/rain-digest.log 2>&1
```

## Project structure

```
rain/
├── src/
│   ├── loxo/client.py       # Loxo ATS wrapper (both instances)
│   ├── monday/client.py     # Monday.com GraphQL client
│   ├── slack/client.py      # Slack posting
│   └── agent/ops_agent.py   # Claude agent with tool use
├── scripts/
│   ├── daily_digest.py      # Cron-triggered daily report
│   └── ask.py               # Interactive Q&A CLI
├── .env                     # Credentials (never committed)
├── .env.example             # Template
└── requirements.txt
```
