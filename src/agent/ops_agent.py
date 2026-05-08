"""
Rain Internal Ops Agent

Claude-powered agent with tool use that answers questions about deal/candidate
status, syncs pipeline data, and generates daily digests.
"""
import json
import os
from datetime import datetime, timedelta
from typing import Any

import anthropic

from src.loxo.client import LoxoClient
from src.monday.client import MondayClient


MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = """You are Rain's internal operations assistant. Rain is a staffing and leadership recruitment firm.

You have access to tools for Loxo ATS (candidates and jobs) and Monday.com (deals, accounts, contacts).

Rules:
- Only call tools that are directly relevant to the question asked. Do NOT pull Monday.com data unless the question is explicitly about deals, accounts, or CRM.
- Do NOT pull both staffing and leadership unless explicitly asked for both.
- Be concise — bullet points, numbers, names, dates. No preamble.
- Flag stale items (no activity > 14 days) with a warning.
- Never ask clarifying questions — make reasonable assumptions and answer immediately.

Today's date: {today}
"""

TOOLS = [
    {
        "name": "get_loxo_candidates",
        "description": "Get candidates from a Loxo ATS instance. Use instance='staffing' for rain-staffing or instance='leadership' for rain-leadership.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance": {
                    "type": "string",
                    "enum": ["staffing", "leadership"],
                    "description": "Which Loxo instance to query",
                },
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
                "query": {"type": "string", "description": "Optional search query"},
            },
            "required": ["instance"],
        },
    },
    {
        "name": "get_loxo_jobs",
        "description": "Get open jobs/searches from a Loxo ATS instance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance": {
                    "type": "string",
                    "enum": ["staffing", "leadership"],
                },
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
            "required": ["instance"],
        },
    },
    {
        "name": "get_loxo_job_candidates",
        "description": "Get all candidates on a specific Loxo job/search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance": {"type": "string", "enum": ["staffing", "leadership"]},
                "job_id": {"type": "integer", "description": "Loxo job ID"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 50},
            },
            "required": ["instance", "job_id"],
        },
    },
    {
        "name": "probe_loxo_endpoints",
        "description": "Diagnostic tool: probe which Loxo API endpoints exist and return data. Use this when other Loxo tools return 404 errors to discover the correct endpoint paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance": {"type": "string", "enum": ["staffing", "leadership"]},
            },
            "required": ["instance"],
        },
    },
    {
        "name": "get_monday_deals",
        "description": "Get deals from Monday.com CRM, grouped by pipeline stage.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_monday_accounts",
        "description": "Get client accounts from Monday.com.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "get_monday_contacts",
        "description": "Get contacts from Monday.com CRM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
    },
]


def execute_tool(name: str, inputs: dict) -> Any:
    if name == "get_loxo_candidates":
        loxo = LoxoClient(instance=inputs["instance"])
        if "query" in inputs and inputs["query"]:
            return loxo.search_candidates(inputs["query"], inputs.get("page", 1), inputs.get("per_page", 25))
        return loxo.get_candidates(inputs.get("page", 1), inputs.get("per_page", 25))

    if name == "get_loxo_jobs":
        loxo = LoxoClient(instance=inputs["instance"])
        return loxo.get_jobs(inputs.get("page", 1), inputs.get("per_page", 25))

    if name == "probe_loxo_endpoints":
        import requests as req
        config = {"staffing": (43483, "LOXO_RAIN_STAFFING_API_KEY"), "leadership": (42823, "LOXO_RAIN_LEADERSHIP_API_KEY")}
        agency_id, key_env = config[inputs["instance"]]
        api_key = os.environ.get(key_env, "")
        headers = {"Authorization": f"Bearer {api_key}"}

        base_urls = [
            f"https://rain-global.app.loxo.co/api/{agency_id}/",
            f"https://rain-global.app.loxo.co/api/agencies/{agency_id}/",
            f"https://app.loxo.co/api/rain-global/",
            f"https://rain-global.app.loxo.co/api/rain-global/",
            f"https://rain-global.app.loxo.co/api/v1/{agency_id}/",
            f"https://rain-global.app.loxo.co/api/v1/agencies/{agency_id}/",
        ]
        endpoints = ["people", "jobs", "candidates"]

        results = {}
        for base in base_urls:
            for ep in endpoints:
                url = base + ep
                try:
                    r = req.get(url, headers=headers, params={"page": 1, "per_page": 1}, timeout=5)
                    results[url] = f"HTTP {r.status_code}"
                    if r.ok:
                        results[url] += f" — keys: {list(r.json().keys())[:4]}"
                except Exception as e:
                    results[url] = f"ERROR: {e}"
        return results

    if name == "get_loxo_job_candidates":
        loxo = LoxoClient(instance=inputs["instance"])
        return loxo.get_job_candidates(inputs["job_id"], inputs.get("page", 1), inputs.get("per_page", 50))

    if name == "get_monday_deals":
        monday = MondayClient()
        return monday.get_deals_by_stage()

    if name == "get_monday_accounts":
        monday = MondayClient()
        return monday.get_accounts(inputs.get("limit", 50))

    if name == "get_monday_contacts":
        monday = MondayClient()
        return monday.get_contacts(inputs.get("limit", 50))

    raise ValueError(f"Unknown tool: {name}")


def run_agent(user_message: str, max_turns: int = 10) -> str:
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_message}]
    system = SYSTEM_PROMPT.format(today=datetime.now().strftime("%Y-%m-%d"))

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[
                {**t, "cache_control": {"type": "ephemeral"}} if i == len(TOOLS) - 1 else t
                for i, t in enumerate(TOOLS)
            ],
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        result = execute_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(result, default=str),
                            }
                        )
                    except Exception as e:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "is_error": True,
                                "content": f"Error: {e}",
                            }
                        )
            messages.append({"role": "user", "content": tool_results})

    return "Agent reached max turns without completing."
