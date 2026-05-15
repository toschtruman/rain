#!/usr/bin/env python3
"""
loxo_fix_updates.py

Updates Loxo people records from two CSVs:
  1. willo-fixes.csv       → patches the "Willo" custom field URL
  2. customer-support-fixes.csv → appends "Customer Support/Customer Care" to roles

Usage:
  python loxo_fix_updates.py --discover          # inspect custom fields on one person
  python loxo_fix_updates.py --dry-run           # print what would happen, no API calls
  python loxo_fix_updates.py --limit 5           # process only first 5 rows of each CSV
  python loxo_fix_updates.py                     # run everything
"""

import argparse
import csv
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
load_dotenv()  # loads .env from cwd or any parent directory

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STAFFING_BASE_URL = "https://rain-global.app.loxo.co/api/rain-global/"
API_KEY_ENV = "LOXO_RAIN_STAFFING_API_KEY"

WILLO_CSV = Path(__file__).parent / "willo-fixes.csv"
CS_CSV = Path(__file__).parent / "customer-support-fixes.csv"
FAILURES_CSV = Path(__file__).parent / "failures.csv"

RATE_LIMIT_SLEEP = 0.5  # seconds between API calls
NEW_ROLE = "Customer Support/Customer Care"

# These will be populated after --discover confirms the correct IDs
WILLO_FIELD_LABEL = "Willo"
ROLES_FIELD_LABEL = "Roles Experienced in - Registration"


# ---------------------------------------------------------------------------
# Minimal API client (staffing instance only)
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
            return {"_error": resp.status_code, "detail": resp.text[:300]}
        try:
            return resp.json()
        except Exception:
            return {"_error": "bad_json", "detail": resp.text[:300]}

    def patch(self, path: str, body: dict) -> dict:
        resp = self.session.patch(self._url(path), json=body)
        if not resp.ok:
            return {"_error": resp.status_code, "detail": resp.text[:300]}
        try:
            return resp.json()
        except Exception:
            return {"_error": "bad_json", "detail": resp.text[:300]}

    def find_person_by_email(self, email: str) -> dict | None:
        """Return the first matching person dict, or None."""
        time.sleep(RATE_LIMIT_SLEEP)
        raw = self.get("people", {"query": email.strip()})
        if "_error" in raw:
            print(f"    ⚠️  Search error for {email}: {raw}", flush=True)
            return None
        people = (
            raw if isinstance(raw, list)
            else raw.get("people", raw.get("candidates", raw.get("data", [])))
        )
        if not people:
            return None
        # Prefer exact email match
        for p in people:
            if isinstance(p, dict):
                p_email = (p.get("email") or "").strip().lower()
                if p_email == email.strip().lower():
                    return p
        # Fall back to first result
        return people[0] if isinstance(people[0], dict) else None

    def get_person(self, person_id) -> dict:
        """Fetch full person record (includes all custom fields)."""
        time.sleep(RATE_LIMIT_SLEEP)
        return self.get(f"people/{person_id}")


# ---------------------------------------------------------------------------
# Custom field helpers
# ---------------------------------------------------------------------------

def find_custom_field(person: dict, label: str) -> dict | None:
    """Find a custom field by label (case-insensitive) in a person record."""
    cfs = person.get("custom_fields") or []
    for cf in cfs:
        if isinstance(cf, dict):
            cf_label = (cf.get("label") or cf.get("name") or cf.get("field_name") or "").strip()
            if cf_label.lower() == label.lower():
                return cf
    return None


def print_custom_fields(person: dict):
    """Pretty-print all custom fields on a person record."""
    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
    pid = person.get("id", "?")
    print(f"\n{'='*70}")
    print(f"Person: {name}  (id={pid})")
    print(f"{'='*70}")

    cfs = person.get("custom_fields") or []
    if not cfs:
        print("  ⚠️  No custom_fields found on this person record.")
        print("  Top-level keys:", list(person.keys()))
        return

    print(f"  Found {len(cfs)} custom field(s):\n")
    for i, cf in enumerate(cfs, 1):
        label = cf.get("label") or cf.get("name") or cf.get("field_name") or "(no label)"
        cf_id = cf.get("id") or cf.get("field_id") or "(no id)"
        value = cf.get("value")
        cf_type = cf.get("field_type") or cf.get("type") or "(unknown type)"
        print(f"  [{i:03d}] label={label!r:45s} id={str(cf_id):10s} type={cf_type!r:20s} value={str(value)[:60]!r}")

    print(f"\n  Raw person keys: {list(person.keys())}")


# ---------------------------------------------------------------------------
# --discover mode
# ---------------------------------------------------------------------------

def run_discover(client: LoxoStaffingClient):
    """Fetch the first person from the staffing instance and print their custom fields."""
    print("\n🔍 DISCOVER MODE — fetching first person from /people ...\n", flush=True)
    raw = client.get("people", {"per_page": 1})
    if "_error" in raw:
        print(f"❌ Failed to fetch people: {raw}")
        sys.exit(1)

    people = (
        raw if isinstance(raw, list)
        else raw.get("people", raw.get("candidates", raw.get("data", [])))
    )
    if not people:
        print("❌ No people returned from /people endpoint.")
        sys.exit(1)

    person_stub = people[0]
    pid = person_stub.get("id")
    print(f"  Got stub for person id={pid}, fetching full record ...", flush=True)

    full = client.get_person(pid)
    if "_error" in full:
        print(f"❌ Failed to fetch person {pid}: {full}")
        sys.exit(1)

    print_custom_fields(full)

    # Also try fetching from the Willo CSV — use first email there for a more relevant example
    if WILLO_CSV.exists():
        with open(WILLO_CSV, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if rows:
            sample_email = rows[0].get("Email", "").strip()
            if sample_email:
                print(f"\n🔍 Also fetching a real person from Willo CSV: {sample_email} ...\n")
                p2 = client.find_person_by_email(sample_email)
                if p2:
                    full2 = client.get_person(p2["id"])
                    if "_error" not in full2:
                        print_custom_fields(full2)
                    else:
                        print(f"  ⚠️  Could not fetch full record: {full2}")
                else:
                    print(f"  ⚠️  Person not found by email: {sample_email}")

    print("\n✅ Discovery complete. Confirm the field labels/IDs above, then re-run without --discover.\n")


# ---------------------------------------------------------------------------
# Task 1: Willo URL updates
# ---------------------------------------------------------------------------

def run_willo_updates(client: LoxoStaffingClient, dry_run: bool, limit: int | None) -> list[dict]:
    failures = []

    if not WILLO_CSV.exists():
        print(f"❌ CSV not found: {WILLO_CSV}")
        return failures

    with open(WILLO_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if limit:
        rows = rows[:limit]

    print(f"\n{'='*70}")
    print(f"TASK 1: Willo URL updates  ({len(rows)} rows)")
    print(f"{'='*70}\n")

    for row in rows:
        first = row.get("First Name", "").strip()
        last = row.get("Last Name", "").strip()
        email = row.get("Email", "").strip()
        willo_url = row.get("Correct Willo URL", "").strip()
        name = f"{first} {last}".strip()

        if not email or not willo_url:
            print(f"  ⚠️  Skipping row with missing email or URL: {row}")
            continue

        if dry_run:
            print(f"  🔎 [DRY RUN] Would update Willo URL for {name} ({email}) → {willo_url}")
            continue

        # Find person
        person = client.find_person_by_email(email)
        if not person:
            msg = "Person not found by email"
            print(f"  ❌ Failed {name}: {msg}")
            failures.append({"task": "willo", "name": name, "email": email, "reason": msg})
            continue

        pid = person.get("id")

        # Fetch full record to inspect custom fields
        full = client.get_person(pid)
        if "_error" in full:
            msg = f"Could not fetch full record: {full}"
            print(f"  ❌ Failed {name}: {msg}")
            failures.append({"task": "willo", "name": name, "email": email, "reason": msg})
            continue

        willo_field = find_custom_field(full, WILLO_FIELD_LABEL)
        if not willo_field:
            msg = f"Custom field '{WILLO_FIELD_LABEL}' not found on person record"
            print(f"  ❌ Failed {name}: {msg}")
            failures.append({"task": "willo", "name": name, "email": email, "reason": msg})
            continue

        field_id = willo_field.get("id") or willo_field.get("field_id")

        # Build PATCH body
        body = {
            "person": {
                "custom_fields": [
                    {"id": field_id, "value": willo_url}
                ]
            }
        }

        time.sleep(RATE_LIMIT_SLEEP)
        result = client.patch(f"people/{pid}", body)
        if "_error" in result:
            msg = f"PATCH failed: {result}"
            print(f"  ❌ Failed {name}: {msg}")
            failures.append({"task": "willo", "name": name, "email": email, "reason": msg})
        else:
            print(f"  ✅ Updated {name} — Willo URL set")

    return failures


# ---------------------------------------------------------------------------
# Task 2: Customer Support role additions
# ---------------------------------------------------------------------------

def parse_roles(value) -> list[str]:
    """Parse a roles value that may be a list, comma-separated string, or None."""
    if not value:
        return []
    if isinstance(value, list):
        return [r.strip() for r in value if r and str(r).strip()]
    # string — try splitting on comma
    return [r.strip() for r in str(value).split(",") if r.strip()]


def run_cs_updates(client: LoxoStaffingClient, dry_run: bool, limit: int | None) -> list[dict]:
    failures = []

    if not CS_CSV.exists():
        print(f"❌ CSV not found: {CS_CSV}")
        return failures

    with open(CS_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if limit:
        rows = rows[:limit]

    print(f"\n{'='*70}")
    print(f"TASK 2: Customer Support role additions  ({len(rows)} rows)")
    print(f"{'='*70}\n")

    for row in rows:
        first = row.get("First Name", "").strip()
        last = row.get("Last Name", "").strip()
        email = row.get("Email", "").strip()
        name = f"{first} {last}".strip()

        if not email:
            print(f"  ⚠️  Skipping row with missing email: {row}")
            continue

        if dry_run:
            print(f"  🔎 [DRY RUN] Would append '{NEW_ROLE}' to roles for {name} ({email})")
            continue

        # Find person
        person = client.find_person_by_email(email)
        if not person:
            msg = "Person not found by email"
            print(f"  ❌ Failed {name}: {msg}")
            failures.append({"task": "customer_support", "name": name, "email": email, "reason": msg})
            continue

        pid = person.get("id")

        # Fetch full record
        full = client.get_person(pid)
        if "_error" in full:
            msg = f"Could not fetch full record: {full}"
            print(f"  ❌ Failed {name}: {msg}")
            failures.append({"task": "customer_support", "name": name, "email": email, "reason": msg})
            continue

        roles_field = find_custom_field(full, ROLES_FIELD_LABEL)
        if not roles_field:
            msg = f"Custom field '{ROLES_FIELD_LABEL}' not found on person record"
            print(f"  ❌ Failed {name}: {msg}")
            failures.append({"task": "customer_support", "name": name, "email": email, "reason": msg})
            continue

        field_id = roles_field.get("id") or roles_field.get("field_id")
        current_roles = parse_roles(roles_field.get("value"))

        if NEW_ROLE in current_roles:
            print(f"  ⏭️  Skipped {name} — '{NEW_ROLE}' already present")
            continue

        updated_roles = current_roles + [NEW_ROLE]

        # Build PATCH body
        body = {
            "person": {
                "custom_fields": [
                    {"id": field_id, "value": updated_roles}
                ]
            }
        }

        time.sleep(RATE_LIMIT_SLEEP)
        result = client.patch(f"people/{pid}", body)
        if "_error" in result:
            msg = f"PATCH failed: {result}"
            print(f"  ❌ Failed {name}: {msg}")
            failures.append({"task": "customer_support", "name": name, "email": email, "reason": msg})
        else:
            old_str = ", ".join(current_roles) if current_roles else "(none)"
            print(f"  ✅ Updated {name} — roles: [{old_str}] + '{NEW_ROLE}'")

    return failures


# ---------------------------------------------------------------------------
# Write failures CSV
# ---------------------------------------------------------------------------

def write_failures(failures: list[dict]):
    if not failures:
        print("\n✅ No failures — skipping failures.csv")
        return
    with open(FAILURES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["task", "name", "email", "reason"])
        writer.writeheader()
        writer.writerows(failures)
    print(f"\n⚠️  {len(failures)} failure(s) written to {FAILURES_CSV}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Update Loxo people records from CSVs.")
    parser.add_argument("--discover", action="store_true",
                        help="Inspect custom fields on a real person and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without making any API calls")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Process only first N rows of each CSV")
    parser.add_argument("--instance", default="staffing",
                        help="Loxo instance to use (default: staffing)")
    args = parser.parse_args()

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(f"❌ Missing environment variable: {API_KEY_ENV}")
        print(f"   Export it or add it to your .env file and re-run.")
        sys.exit(1)

    client = LoxoStaffingClient(api_key)

    if args.discover:
        run_discover(client)
        return

    if args.dry_run:
        print("\n🔎 DRY RUN MODE — no API calls will be made\n")

    all_failures = []
    all_failures += run_willo_updates(client, dry_run=args.dry_run, limit=args.limit)
    all_failures += run_cs_updates(client, dry_run=args.dry_run, limit=args.limit)

    if not args.dry_run:
        write_failures(all_failures)

    total = (
        sum(1 for _ in open(WILLO_CSV, encoding="utf-8-sig")) - 1 if WILLO_CSV.exists() else 0
    ) + (
        sum(1 for _ in open(CS_CSV, encoding="utf-8-sig")) - 1 if CS_CSV.exists() else 0
    )
    print(f"\nDone. {len(all_failures)} failure(s) out of ~{total} total rows.\n")


if __name__ == "__main__":
    main()
