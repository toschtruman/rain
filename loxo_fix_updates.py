#!/usr/bin/env python3
"""
loxo_fix_updates.py

Updates Loxo people records (staffing instance: rain-global) from two CSVs:
  1. willo-fixes.csv            → patches the "Willo" custom field URL
  2. customer-support-fixes.csv → appends "Customer Support/Customer Care"
                                   to "Roles Experienced in - Registration"

Usage:
  python loxo_fix_updates.py --dry-run        # print what would happen, no API calls
  python loxo_fix_updates.py --limit 5        # process only first 5 rows of each CSV
  python loxo_fix_updates.py                  # run everything

Requires: LOXO_RAIN_STAFFING_API_KEY in environment or .env file.
"""

import argparse
import csv
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STAFFING_BASE_URL = "https://rain-global.app.loxo.co/api/rain-global/"
API_KEY_ENV = "LOXO_RAIN_STAFFING_API_KEY"

HERE = Path(__file__).parent
WILLO_CSV = HERE / "willo-fixes.csv"
CS_CSV = HERE / "customer-support-fixes.csv"
FAILURES_CSV = HERE / "failures.csv"

RATE_LIMIT_SLEEP = 0.5  # seconds between API calls

WILLO_FIELD_LABEL = "Willo"
ROLES_FIELD_LABEL = "Roles Experienced in - Registration"
NEW_ROLE = "Customer Support/Customer Care"


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class LoxoStaffingClient:
    def __init__(self, api_key: str):
        self.base_url = STAFFING_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return self.base_url + path.lstrip("/")

    def get(self, path: str, params: dict = None) -> dict:
        resp = self.session.get(self._url(path), params=params or {})
        if not resp.ok:
            return {"_error": resp.status_code, "detail": resp.text[:400]}
        try:
            return resp.json()
        except Exception:
            return {"_error": "bad_json", "detail": resp.text[:400]}

    def patch(self, path: str, body: dict) -> dict:
        resp = self.session.patch(self._url(path), json=body)
        if not resp.ok:
            return {"_error": resp.status_code, "detail": resp.text[:400]}
        try:
            return resp.json()
        except Exception:
            return {"_error": "bad_json", "detail": resp.text[:400]}

    def find_person_by_email(self, email: str) -> dict | None:
        """Search for a person by email; return the first exact match or None."""
        time.sleep(RATE_LIMIT_SLEEP)
        raw = self.get("people", {"query": email.strip()})
        if "_error" in raw:
            return {"_error": raw["_error"], "detail": raw.get("detail", "")}
        people = (
            raw if isinstance(raw, list)
            else raw.get("people", raw.get("candidates", raw.get("data", [])))
        )
        if not people:
            return None
        # Prefer exact email match
        for p in people:
            if isinstance(p, dict):
                if (p.get("email") or "").strip().lower() == email.strip().lower():
                    return p
        return people[0] if isinstance(people[0], dict) else None

    def get_person(self, person_id) -> dict:
        """Fetch full person record (includes custom_fields)."""
        time.sleep(RATE_LIMIT_SLEEP)
        return self.get(f"people/{person_id}")


# ---------------------------------------------------------------------------
# Custom field helpers
# ---------------------------------------------------------------------------

def _cf_label(cf: dict) -> str:
    """Extract the label from a custom field dict, trying common key names."""
    return (
        cf.get("label")
        or cf.get("name")
        or cf.get("field_name")
        or cf.get("title")
        or ""
    ).strip()


def find_custom_field(person: dict, label: str) -> dict | None:
    """Find a custom field by label (case-insensitive)."""
    for cf in person.get("custom_fields") or []:
        if isinstance(cf, dict) and _cf_label(cf).lower() == label.lower():
            return cf
    return None


def field_id(cf: dict):
    """Extract the field identifier, trying common key names."""
    return cf.get("id") or cf.get("field_id") or cf.get("custom_field_id")


def parse_roles(value) -> list[str]:
    """Parse roles that may be a list, comma-separated string, or None."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(r).strip() for r in value if str(r).strip()]
    return [r.strip() for r in str(value).split(",") if r.strip()]


def roles_to_original_type(roles: list[str], original_value) -> list | str:
    """Return roles in the same type as the original value (list or comma string)."""
    if isinstance(original_value, list):
        return roles
    return ", ".join(roles)


# ---------------------------------------------------------------------------
# Failure log
# ---------------------------------------------------------------------------

_failures: list[dict] = []


def log_fail(task: str, name: str, email: str, reason: str):
    print(f"  ❌ Failed {name} ({email}): {reason}", flush=True)
    _failures.append({"task": task, "name": name, "email": email, "reason": reason})


def write_failures():
    if not _failures:
        print("\n✅ No failures.")
        return
    with open(FAILURES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["task", "name", "email", "reason"])
        writer.writeheader()
        writer.writerows(_failures)
    print(f"\n⚠️  {len(_failures)} failure(s) written to {FAILURES_CSV}")


# ---------------------------------------------------------------------------
# Task 1: Willo URL updates
# ---------------------------------------------------------------------------

def run_willo_updates(client: LoxoStaffingClient, dry_run: bool, limit: int | None):
    if not WILLO_CSV.exists():
        print(f"❌ CSV not found: {WILLO_CSV}")
        return

    with open(WILLO_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if limit:
        rows = rows[:limit]

    print(f"\n{'='*70}")
    print(f"TASK 1: Willo URL updates  ({len(rows)} rows)")
    print(f"{'='*70}\n")

    for row in rows:
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        email = (row.get("Email") or "").strip()
        willo_url = (row.get("Correct Willo URL") or "").strip()
        name = f"{first} {last}".strip()

        if not email or not willo_url:
            print(f"  ⚠️  Skipping row with missing email or URL: {dict(row)}")
            continue

        if dry_run:
            print(f"  🔎 [DRY RUN] {name} ({email})\n"
                  f"           → set '{WILLO_FIELD_LABEL}' = {willo_url}")
            continue

        # 1. Find person
        person = client.find_person_by_email(email)
        if person is None:
            log_fail("willo", name, email, "Person not found by email")
            continue
        if "_error" in person:
            log_fail("willo", name, email, f"Search error {person['_error']}: {person.get('detail','')}")
            continue

        pid = person.get("id")

        # 2. Fetch full record
        full = client.get_person(pid)
        if "_error" in full:
            log_fail("willo", name, email, f"Could not fetch person {pid}: {full.get('detail','')}")
            continue

        # 3. Locate Willo custom field
        willo_cf = find_custom_field(full, WILLO_FIELD_LABEL)
        if not willo_cf:
            available = [_cf_label(cf) for cf in (full.get("custom_fields") or [])]
            log_fail("willo", name, email,
                     f"Field '{WILLO_FIELD_LABEL}' not found. Available: {available}")
            continue

        fid = field_id(willo_cf)

        # 4. PATCH
        time.sleep(RATE_LIMIT_SLEEP)
        result = client.patch(f"people/{pid}", {
            "person": {
                "custom_fields": [{"id": fid, "value": willo_url}]
            }
        })
        if "_error" in result:
            log_fail("willo", name, email, f"PATCH {result['_error']}: {result.get('detail','')}")
        else:
            print(f"  ✅ Updated {name} — Willo URL set to {willo_url}")


# ---------------------------------------------------------------------------
# Task 2: Customer Support role additions
# ---------------------------------------------------------------------------

def run_cs_updates(client: LoxoStaffingClient, dry_run: bool, limit: int | None):
    if not CS_CSV.exists():
        print(f"❌ CSV not found: {CS_CSV}")
        return

    with open(CS_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if limit:
        rows = rows[:limit]

    print(f"\n{'='*70}")
    print(f"TASK 2: Customer Support role additions  ({len(rows)} rows)")
    print(f"{'='*70}\n")

    for row in rows:
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        email = (row.get("Email") or "").strip()
        name = f"{first} {last}".strip()

        if not email:
            print(f"  ⚠️  Skipping row with missing email: {dict(row)}")
            continue

        if dry_run:
            print(f"  🔎 [DRY RUN] {name} ({email})\n"
                  f"           → append '{NEW_ROLE}' to '{ROLES_FIELD_LABEL}'")
            continue

        # 1. Find person
        person = client.find_person_by_email(email)
        if person is None:
            log_fail("customer_support", name, email, "Person not found by email")
            continue
        if "_error" in person:
            log_fail("customer_support", name, email,
                     f"Search error {person['_error']}: {person.get('detail','')}")
            continue

        pid = person.get("id")

        # 2. Fetch full record
        full = client.get_person(pid)
        if "_error" in full:
            log_fail("customer_support", name, email,
                     f"Could not fetch person {pid}: {full.get('detail','')}")
            continue

        # 3. Locate roles custom field
        roles_cf = find_custom_field(full, ROLES_FIELD_LABEL)
        if not roles_cf:
            available = [_cf_label(cf) for cf in (full.get("custom_fields") or [])]
            log_fail("customer_support", name, email,
                     f"Field '{ROLES_FIELD_LABEL}' not found. Available: {available}")
            continue

        fid = field_id(roles_cf)
        original_value = roles_cf.get("value")
        current_roles = parse_roles(original_value)

        if NEW_ROLE in current_roles:
            print(f"  ⏭️  Skipped {name} — '{NEW_ROLE}' already present")
            continue

        updated_roles = roles_to_original_type(current_roles + [NEW_ROLE], original_value)

        # 4. PATCH
        time.sleep(RATE_LIMIT_SLEEP)
        result = client.patch(f"people/{pid}", {
            "person": {
                "custom_fields": [{"id": fid, "value": updated_roles}]
            }
        })
        if "_error" in result:
            log_fail("customer_support", name, email,
                     f"PATCH {result['_error']}: {result.get('detail','')}")
        else:
            old_str = ", ".join(current_roles) if current_roles else "(none)"
            print(f"  ✅ Updated {name} — added '{NEW_ROLE}' (was: {old_str})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Update Loxo people records from CSVs.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without making any API calls")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Process only first N rows of each CSV")
    args = parser.parse_args()

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(f"❌ Missing environment variable: {API_KEY_ENV}")
        print(f"   Add it to a .env file in the project root or export it in your shell.")
        sys.exit(1)

    client = LoxoStaffingClient(api_key)

    if args.dry_run:
        print("\n🔎 DRY RUN MODE — no API calls will be made\n")

    run_willo_updates(client, dry_run=args.dry_run, limit=args.limit)
    run_cs_updates(client, dry_run=args.dry_run, limit=args.limit)

    if not args.dry_run:
        write_failures()

    print("\nDone.\n")


if __name__ == "__main__":
    main()
