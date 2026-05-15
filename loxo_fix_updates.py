#!/usr/bin/env python3
"""
loxo_fix_updates.py

Updates Loxo people records (staffing instance: rain-global) from two CSVs:
  1. willo-fixes.csv            → patches custom_text_1 (Willo URL)
  2. customer-support-fixes.csv → appends "Customer Support/Customer Care"
                                   to custom_hierarchy_13 (Roles Experienced
                                   in - Registration)

Usage:
  python loxo_fix_updates.py --discover          # print custom_* fields for sample people
  python loxo_fix_updates.py --debug EMAIL       # inspect one person's match and current field values
  python loxo_fix_updates.py --dry-run           # print what would happen, no API calls
  python loxo_fix_updates.py --dry-run --limit 3 # dry-run first 3 rows
  python loxo_fix_updates.py --limit 3           # live test on 3 rows
  python loxo_fix_updates.py                     # run everything

Requires: LOXO_RAIN_STAFFING_API_KEY in environment or .env file.
"""

import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict

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
CS_CSV    = HERE / "customer-support-fixes.csv"
FAILURES_CSV = HERE / "failures.csv"

RATE_LIMIT_SLEEP = 0.5  # seconds between API calls

WILLO_KEY  = "custom_text_1"          # Willo interview URL
ROLES_KEY  = "custom_hierarchy_13"    # Roles Experienced in - Registration
NEW_ROLE   = "Customer Support/Customer Care"


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

    def get(self, path: str, params: Optional[dict] = None) -> dict:
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

    def find_person_by_email(self, email: str) -> Optional[dict]:
        """Search /people?query=email; return exact match or first result."""
        time.sleep(RATE_LIMIT_SLEEP)
        raw = self.get("people", {"query": email.strip()})
        if "_error" in raw:
            return raw
        people = (
            raw if isinstance(raw, list)
            else raw.get("people", raw.get("candidates", raw.get("data", [])))
        )
        if not people:
            return None
        for p in people:
            if isinstance(p, dict):
                if (p.get("email") or "").strip().lower() == email.strip().lower():
                    return p
        return people[0] if isinstance(people[0], dict) else None

    def get_person(self, person_id) -> dict:
        """Fetch full person record."""
        time.sleep(RATE_LIMIT_SLEEP)
        return self.get("people/{}".format(person_id))

    def get_hierarchy_options(self, hierarchy_id: int) -> dict:
        """Fetch all options for a hierarchy field."""
        time.sleep(RATE_LIMIT_SLEEP)
        # Try the most common Loxo endpoint patterns
        for path in (
            "hierarchies/{}".format(hierarchy_id),
            "hierarchy_items?hierarchy_id={}".format(hierarchy_id),
            "custom_hierarchies/{}".format(hierarchy_id),
        ):
            result = self.get(path)
            if "_error" not in result:
                return result
        return {"_error": "not_found", "detail": "No hierarchy endpoint responded for id={}".format(hierarchy_id)}


# ---------------------------------------------------------------------------
# Hierarchy helpers
# ---------------------------------------------------------------------------

def find_cs_role_id(client: LoxoStaffingClient) -> Optional[int]:
    """
    Fetch hierarchy options for ROLES_KEY and return the integer id
    that corresponds to NEW_ROLE ("Customer Support/Customer Care").
    """
    # Extract the numeric id from e.g. "custom_hierarchy_13" -> 13
    hierarchy_id = int(ROLES_KEY.split("_")[-1])
    raw = client.get_hierarchy_options(hierarchy_id)

    if "_error" in raw:
        print("  ⚠️  Could not fetch hierarchy options: {}".format(raw))
        return None

    # The response may be a list directly, or nested under a key
    items = raw if isinstance(raw, list) else (
        raw.get("hierarchy_items")
        or raw.get("items")
        or raw.get("options")
        or raw.get("data")
        or []
    )

    for item in items:
        if not isinstance(item, dict):
            continue
        label = (
            item.get("value")
            or item.get("name")
            or item.get("label")
            or ""
        ).strip()
        if label.lower() == NEW_ROLE.lower():
            return item.get("id")

    print("  ⚠️  '{}' not found in hierarchy options. Raw sample: {}".format(
        NEW_ROLE, str(items[:3])))
    return None


# ---------------------------------------------------------------------------
# Failure log
# ---------------------------------------------------------------------------

_failures = []  # type: List[Dict]


def log_fail(task: str, name: str, email: str, reason: str):
    print("  ❌ Failed {} ({}): {}".format(name, email, reason), flush=True)
    _failures.append({"task": task, "name": name, "email": email, "reason": reason})


def write_failures():
    if not _failures:
        print("\n✅ No failures.")
        return
    with open(FAILURES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["task", "name", "email", "reason"])
        writer.writeheader()
        writer.writerows(_failures)
    print("\n⚠️  {} failure(s) written to {}".format(len(_failures), FAILURES_CSV))


# ---------------------------------------------------------------------------
# --discover mode
# ---------------------------------------------------------------------------

def run_debug(client: LoxoStaffingClient, email: str):
    """For a single email: show search result, person id, and current custom_text_1."""
    print("\n🔍 DEBUG: {!r}\n".format(email), flush=True)

    print("  1. Searching /people?query={} ...".format(email))
    raw = client.get("people", {"query": email.strip()})
    if "_error" in raw:
        print("     ❌ Search failed: {}".format(raw))
        return

    people = (
        raw if isinstance(raw, list)
        else raw.get("people", raw.get("candidates", raw.get("data", [])))
    )
    print("     → {} result(s) returned".format(len(people) if people else 0))
    if not people:
        print("     ❌ No person found for this email.")
        return

    # Show all results so we can spot the right one
    for i, p in enumerate(people):
        if not isinstance(p, dict):
            continue
        print("     result[{}]: id={!r}  email={!r}  name={!r}".format(
            i,
            p.get("id"),
            p.get("email"),
            "{} {}".format(p.get("first_name", ""), p.get("last_name", "")).strip(),
        ))

    # Pick exact match or first
    match = None
    for p in people:
        if isinstance(p, dict) and (p.get("email") or "").strip().lower() == email.strip().lower():
            match = p
            break
    if match is None:
        match = people[0]
        print("     ⚠️  No exact email match — using result[0]")

    pid = match.get("id")
    print("\n  2. Person found: id={}".format(pid))
    print("     Loxo email on record: {!r}".format(match.get("email")))

    print("\n  3. Fetching full record for id={} ...".format(pid))
    full = client.get_person(pid)
    if "_error" in full:
        print("     ❌ Could not fetch: {}".format(full))
        return

    print("     id={}, name={!r}".format(
        full.get("id"),
        "{} {}".format(full.get("first_name", ""), full.get("last_name", "")).strip(),
    ))

    print("\n  4. Current {}: {!r}".format(WILLO_KEY, full.get(WILLO_KEY)))
    print()


def run_discover(client: LoxoStaffingClient):
    """Print all non-empty custom_* keys for a sample person."""

    def print_custom(person: dict):
        name = "{} {}".format(
            person.get("first_name", ""), person.get("last_name", "")
        ).strip()
        print("\n" + "=" * 70)
        print("Person: {}  (id={})".format(name, person.get("id", "?")))
        print("=" * 70)
        custom = {k: v for k, v in person.items()
                  if k.startswith("custom_") and v not in (None, "", [], {})}
        if not custom:
            print("  ⚠️  No non-empty custom_* keys found.")
            print("  All keys:", list(person.keys()))
        for key in sorted(custom):
            print("  {}: {!r}".format(key, custom[key]))

    print("\n🔍 DISCOVER MODE\n", flush=True)

    raw = client.get("people", {"per_page": 1})
    if "_error" in raw:
        print("❌ GET /people failed:", raw)
        sys.exit(1)
    people = raw if isinstance(raw, list) else raw.get("people", raw.get("candidates", raw.get("data", [])))
    if not people:
        print("❌ No people returned.")
        sys.exit(1)

    full = client.get_person(people[0]["id"])
    if "_error" in full:
        print("❌ Could not fetch person:", full)
        sys.exit(1)
    print_custom(full)

    if WILLO_CSV.exists():
        with open(WILLO_CSV, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if rows:
            sample_email = (rows[0].get("Email") or "").strip()
            if sample_email:
                print("\n  Fetching Willo CSV sample person: {} ...".format(sample_email))
                p2 = client.find_person_by_email(sample_email)
                if p2 and "_error" not in p2:
                    full2 = client.get_person(p2["id"])
                    if "_error" not in full2:
                        print_custom(full2)
                    else:
                        print("  ⚠️  Could not fetch full record:", full2)
                else:
                    print("  ⚠️  Not found:", sample_email)

    print("\n✅ Discovery complete.\n")


# ---------------------------------------------------------------------------
# Task 1: Willo URL updates  (custom_text_1)
# ---------------------------------------------------------------------------

def run_willo_updates(client: LoxoStaffingClient, dry_run: bool, limit: Optional[int]):
    if not WILLO_CSV.exists():
        print("❌ CSV not found: {}".format(WILLO_CSV))
        return

    with open(WILLO_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]

    print("\n" + "=" * 70)
    print("TASK 1: Willo URL updates  ({} rows)".format(len(rows)))
    print("=" * 70 + "\n")

    for row in rows:
        first     = (row.get("First Name") or "").strip()
        last      = (row.get("Last Name") or "").strip()
        email     = (row.get("Email") or "").strip()
        willo_url = (row.get("Correct Willo URL") or "").strip()
        name      = "{} {}".format(first, last).strip()

        if not email or not willo_url:
            print("  ⚠️  Skipping — missing email or URL: {}".format(dict(row)))
            continue

        if dry_run:
            print("  🔎 [DRY RUN] {} ({})".format(name, email))
            print("           → {} = {!r}".format(WILLO_KEY, willo_url))
            continue

        person = client.find_person_by_email(email)
        if person is None:
            log_fail("willo", name, email, "Person not found by email")
            continue
        if "_error" in person:
            log_fail("willo", name, email, "Search error {}: {}".format(
                person["_error"], person.get("detail", "")))
            continue

        pid = person.get("id")
        time.sleep(RATE_LIMIT_SLEEP)
        result = client.patch("people/{}".format(pid), {
            "person": {WILLO_KEY: willo_url}
        })
        if "_error" in result:
            log_fail("willo", name, email, "PATCH {}: {}".format(
                result["_error"], result.get("detail", "")))
        else:
            print("  ✅ Updated {} — {} = {!r}".format(name, WILLO_KEY, willo_url))


# ---------------------------------------------------------------------------
# Task 2: Customer Support role additions  (custom_hierarchy_13)
# ---------------------------------------------------------------------------

def run_cs_updates(client: LoxoStaffingClient, dry_run: bool, limit: Optional[int],
                   cs_role_id: Optional[int]):
    if not CS_CSV.exists():
        print("❌ CSV not found: {}".format(CS_CSV))
        return

    with open(CS_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]

    print("\n" + "=" * 70)
    print("TASK 2: Customer Support role additions  ({} rows)".format(len(rows)))
    print("=" * 70 + "\n")

    if not dry_run and cs_role_id is None:
        print("  ❌ Cannot run Task 2: hierarchy id for '{}' not found. "
              "All rows will be skipped.".format(NEW_ROLE))
        for row in rows:
            email = (row.get("Email") or "").strip()
            name  = "{} {}".format(
                (row.get("First Name") or "").strip(),
                (row.get("Last Name") or "").strip()
            ).strip()
            log_fail("customer_support", name, email,
                     "Hierarchy id for '{}' could not be resolved".format(NEW_ROLE))
        return

    for row in rows:
        first = (row.get("First Name") or "").strip()
        last  = (row.get("Last Name") or "").strip()
        email = (row.get("Email") or "").strip()
        name  = "{} {}".format(first, last).strip()

        if not email:
            print("  ⚠️  Skipping — missing email: {}".format(dict(row)))
            continue

        if dry_run:
            print("  🔎 [DRY RUN] {} ({})".format(name, email))
            print("           → append {{'id': {}, 'value': {!r}}} to {}".format(
                cs_role_id or "<??>", NEW_ROLE, ROLES_KEY))
            continue

        # Find person
        person = client.find_person_by_email(email)
        if person is None:
            log_fail("customer_support", name, email, "Person not found by email")
            continue
        if "_error" in person:
            log_fail("customer_support", name, email, "Search error {}: {}".format(
                person["_error"], person.get("detail", "")))
            continue

        pid = person.get("id")

        # Fetch full record to get current roles array
        full = client.get_person(pid)
        if "_error" in full:
            log_fail("customer_support", name, email, "Could not fetch person {}: {}".format(
                pid, full.get("detail", "")))
            continue

        current_roles = full.get(ROLES_KEY) or []
        if not isinstance(current_roles, list):
            current_roles = []

        # Skip if already present
        if any(r.get("id") == cs_role_id for r in current_roles if isinstance(r, dict)):
            print("  ⏭️  Skipped {} — '{}' already present".format(name, NEW_ROLE))
            continue

        new_entry = {"id": cs_role_id, "value": NEW_ROLE}
        updated_roles = current_roles + [new_entry]

        time.sleep(RATE_LIMIT_SLEEP)
        result = client.patch("people/{}".format(pid), {
            "person": {ROLES_KEY: updated_roles}
        })
        if "_error" in result:
            log_fail("customer_support", name, email, "PATCH {}: {}".format(
                result["_error"], result.get("detail", "")))
        else:
            old_labels = [r.get("value", "") for r in current_roles if isinstance(r, dict)]
            print("  ✅ Updated {} — appended '{}' (had {} role(s))".format(
                name, NEW_ROLE, len(old_labels)))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Update Loxo people records from CSVs.")
    parser.add_argument("--discover", action="store_true",
                        help="Print non-empty custom_* fields for sample people and exit")
    parser.add_argument("--debug", metavar="EMAIL",
                        help="Inspect search result and current field values for one email")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without making any API calls")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Process only first N rows of each CSV")
    args = parser.parse_args()

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print("❌ Missing environment variable: {}".format(API_KEY_ENV))
        print("   Add it to a .env file in the project root or export it in your shell.")
        sys.exit(1)

    client = LoxoStaffingClient(api_key)

    if args.discover:
        run_discover(client)
        return

    if args.debug:
        run_debug(client, args.debug)
        return

    if args.dry_run:
        print("\n🔎 DRY RUN MODE — no API calls will be made\n")

    # Resolve "Customer Support/Customer Care" hierarchy id once up front
    cs_role_id = None
    if not args.dry_run:
        print("🔍 Looking up hierarchy id for '{}' ...".format(NEW_ROLE), flush=True)
        cs_role_id = find_cs_role_id(client)
        if cs_role_id is None:
            print("  ⚠️  Could not resolve hierarchy id — Task 2 rows will be logged as failures.")
        else:
            print("  ✅ Found id={} for '{}'\n".format(cs_role_id, NEW_ROLE))

    run_willo_updates(client, dry_run=args.dry_run, limit=args.limit)
    run_cs_updates(client, dry_run=args.dry_run, limit=args.limit, cs_role_id=cs_role_id)

    if not args.dry_run:
        write_failures()

    print("\nDone.\n")


if __name__ == "__main__":
    main()
