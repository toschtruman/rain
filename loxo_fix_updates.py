#!/usr/bin/env python3
"""
loxo_fix_updates.py

Updates Loxo people records (staffing instance: rain-global) from two CSVs:
  1. willo-fixes.csv            → patches custom_text_1 (Willo URL)
  2. customer-support-fixes.csv → appends "Customer Support/Customer Care"
                                   to custom_hierarchy_13 (Roles Experienced
                                   in - Registration)

Person lookup uses a local Loxo export CSV (not the search API, which
returns email=None and can't be matched). The export is matched against
the Email, Personal Email, and Work Email columns.

Usage:
  python loxo_fix_updates.py --export people.csv --discover
  python loxo_fix_updates.py --export people.csv --debug EMAIL
  python loxo_fix_updates.py --export people.csv --find-hierarchy-id
  python loxo_fix_updates.py --export people.csv --dry-run --limit 3
  python loxo_fix_updates.py --export people.csv --limit 3
  python loxo_fix_updates.py --export people.csv

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
WILLO_CSV    = HERE / "willo-fixes.csv"
CS_CSV       = HERE / "customer-support-fixes.csv"
FAILURES_CSV = HERE / "failures.csv"

RATE_LIMIT_SLEEP = 0.5  # seconds between API calls

WILLO_KEY = "custom_text_1"        # Willo interview URL
ROLES_KEY = "custom_hierarchy_13"  # Roles Experienced in - Registration
NEW_ROLE  = "Customer Support/Customer Care"

# Columns in the Loxo export that may contain an email address
EXPORT_EMAIL_COLS = ["Email", "Personal Email", "Work Email"]


# ---------------------------------------------------------------------------
# Export CSV → email-to-id lookup
# ---------------------------------------------------------------------------

def load_export(export_path: Path) -> Dict[str, str]:
    """
    Read the Loxo people export CSV and return a dict of
    { normalised_email: loxo_person_id } built from all three
    email columns (Email, Personal Email, Work Email).
    Later rows win on collision so the most recent export row is used.
    """
    if not export_path.exists():
        print("❌ Export CSV not found: {}".format(export_path))
        sys.exit(1)

    lookup = {}  # type: Dict[str, str]
    with open(export_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = (row.get("Id") or "").strip()
            if not pid:
                continue
            for col in EXPORT_EMAIL_COLS:
                email = (row.get(col) or "").strip().lower()
                if email:
                    lookup[email] = pid

    print("📋 Loaded export: {} unique email→id mappings from {}".format(
        len(lookup), export_path.name))
    return lookup


def lookup_id(email: str, export: Dict[str, str]) -> Optional[str]:
    return export.get(email.strip().lower())


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class LoxoStaffingClient:
    def __init__(self, api_key: str):
        self.base_url = STAFFING_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": "Bearer {}".format(api_key),
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

    def get_person(self, person_id) -> dict:
        time.sleep(RATE_LIMIT_SLEEP)
        return self.get("people/{}".format(person_id))

    def get_hierarchy_options(self, hierarchy_id: int) -> dict:
        time.sleep(RATE_LIMIT_SLEEP)
        for path in (
            "hierarchies/{}".format(hierarchy_id),
            "hierarchy_items?hierarchy_id={}".format(hierarchy_id),
            "custom_hierarchies/{}".format(hierarchy_id),
        ):
            result = self.get(path)
            if "_error" not in result:
                return result
        return {"_error": "not_found",
                "detail": "No hierarchy endpoint responded for id={}".format(hierarchy_id)}


# ---------------------------------------------------------------------------
# Hierarchy helpers
# ---------------------------------------------------------------------------

def find_hierarchy_id_from_export(client: LoxoStaffingClient,
                                   export_path: Path) -> Optional[int]:
    """
    Scan the export CSV for anyone whose 'Roles Experienced in - Registration'
    column contains NEW_ROLE. Fetch the first match from the API and print
    their full custom_hierarchy_13 array so the correct id can be read off.
    Returns the id if found, else None.
    """
    print("\n🔍 --find-hierarchy-id: scanning export for someone with '{}' ...\n".format(NEW_ROLE))

    ROLES_EXPORT_COL = "Roles Experienced in - Registration"
    candidate_id = None

    with open(export_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        # Warn clearly if the column isn't present at all
        if ROLES_EXPORT_COL not in (reader.fieldnames or []):
            print("  ⚠️  Column {!r} not found in export. Available columns:".format(ROLES_EXPORT_COL))
            print("  ", reader.fieldnames)
            print("\n  Tip: pass the exact column name that holds role data.")
            return None

        for row in reader:
            cell = (row.get(ROLES_EXPORT_COL) or "").strip()
            if NEW_ROLE.lower() in cell.lower():
                candidate_id = (row.get("Id") or "").strip()
                candidate_email = (row.get("Email") or "").strip()
                print("  Found in export: id={}, email={!r}".format(candidate_id, candidate_email))
                print("  Export cell value: {!r}\n".format(cell))
                break

    if not candidate_id:
        print("  ❌ No one in the export has '{}' in column {!r}.".format(
            NEW_ROLE, ROLES_EXPORT_COL))
        print("  Cannot determine hierarchy id this way.")
        return None

    print("  Fetching full API record for id={} ...".format(candidate_id))
    full = client.get_person(candidate_id)
    if "_error" in full:
        print("  ❌ Could not fetch person {}: {}".format(candidate_id, full))
        return None

    roles_array = full.get(ROLES_KEY)
    print("  {} on API record:".format(ROLES_KEY))
    if not roles_array:
        print("  ❌ Field is empty or missing on this person's API record.")
        return None

    found_id = None
    for entry in roles_array:
        marker = " ← this one" if (entry.get("value") or "").strip().lower() == NEW_ROLE.lower() else ""
        print("    {{'id': {}, 'value': {!r}}}{}".format(
            entry.get("id"), entry.get("value"), marker))
        if (entry.get("value") or "").strip().lower() == NEW_ROLE.lower():
            found_id = entry.get("id")

    if found_id is not None:
        print("\n  ✅ Hierarchy id for '{}' = {}\n".format(NEW_ROLE, found_id))
    else:
        print("\n  ⚠️  '{}' not found in the API array despite being in the export cell.".format(NEW_ROLE))

    return found_id


def find_cs_role_id(client: LoxoStaffingClient) -> Optional[int]:
    """Return the hierarchy item id for NEW_ROLE, or None if not found."""
    hierarchy_id = int(ROLES_KEY.split("_")[-1])
    raw = client.get_hierarchy_options(hierarchy_id)
    if "_error" in raw:
        print("  ⚠️  Could not fetch hierarchy options: {}".format(raw))
        return None

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
        label = (item.get("value") or item.get("name") or item.get("label") or "").strip()
        if label.lower() == NEW_ROLE.lower():
            return item.get("id")

    print("  ⚠️  '{}' not found in hierarchy options. Sample: {}".format(
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
# --debug mode
# ---------------------------------------------------------------------------

def run_debug(client: LoxoStaffingClient, export: Dict[str, str], email: str):
    print("\n🔍 DEBUG: {!r}\n".format(email))

    pid = lookup_id(email, export)
    print("  1. Search email : {!r}".format(email))
    print("  2. Export lookup: {}".format(
        "id={}".format(pid) if pid else "❌ NOT FOUND in export CSV"))

    if not pid:
        print("\n  ⚠️  Email not in export — check spelling or try Personal/Work Email columns.")
        return

    print("  3. Fetching /people/{} ...".format(pid))
    full = client.get_person(pid)
    if "_error" in full:
        print("     ❌ Could not fetch: {}".format(full))
        return

    print("     id={}, name={!r}, loxo email={!r}".format(
        full.get("id"),
        "{} {}".format(full.get("first_name", ""), full.get("last_name", "")).strip(),
        full.get("email"),
    ))
    print("  4. Current {}: {!r}".format(WILLO_KEY, full.get(WILLO_KEY)))
    print()


# ---------------------------------------------------------------------------
# --discover mode
# ---------------------------------------------------------------------------

def run_discover(client: LoxoStaffingClient, export: Dict[str, str]):
    """Print all non-empty custom_* keys for a sample person."""

    def print_custom(person: dict):
        name = "{} {}".format(
            person.get("first_name", ""), person.get("last_name", "")).strip()
        print("\n" + "=" * 70)
        print("Person: {}  (id={})".format(name, person.get("id", "?")))
        print("=" * 70)
        custom = {k: v for k, v in person.items()
                  if k.startswith("custom_") and v not in (None, "", [], {})}
        if not custom:
            print("  ⚠️  No non-empty custom_* keys found.")
            print("  All keys:", list(person.keys()))
            return
        for key in sorted(custom):
            print("  {}: {!r}".format(key, custom[key]))

    print("\n🔍 DISCOVER MODE\n", flush=True)

    # Pick the first id in the export and fetch it
    if not export:
        print("❌ Export is empty.")
        return
    sample_id = next(iter(export.values()))
    full = client.get_person(sample_id)
    if "_error" in full:
        print("❌ Could not fetch person {}: {}".format(sample_id, full))
        return
    print_custom(full)

    # Also fetch the first person from willo-fixes.csv
    if WILLO_CSV.exists():
        with open(WILLO_CSV, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if rows:
            sample_email = (rows[0].get("Email") or "").strip()
            pid = lookup_id(sample_email, export)
            if pid:
                print("\n  Fetching Willo CSV sample ({}) ...".format(sample_email))
                full2 = client.get_person(pid)
                if "_error" not in full2:
                    print_custom(full2)
                else:
                    print("  ⚠️  Could not fetch: {}".format(full2))
            else:
                print("\n  ⚠️  {} not found in export".format(sample_email))

    print("\n✅ Discovery complete.\n")


# ---------------------------------------------------------------------------
# Task 1: Willo URL updates  (custom_text_1)
# ---------------------------------------------------------------------------

def run_willo_updates(client: LoxoStaffingClient, export: Dict[str, str],
                      dry_run: bool, limit: Optional[int]):
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

        pid = lookup_id(email, export)
        if not pid:
            log_fail("willo", name, email, "Email not found in export CSV")
            continue

        if dry_run:
            print("  🔎 [DRY RUN] {} ({}) id={}".format(name, email, pid))
            print("           → {} = {!r}".format(WILLO_KEY, willo_url))
            continue

        time.sleep(RATE_LIMIT_SLEEP)
        result = client.patch("people/{}".format(pid), {
            "person": {WILLO_KEY: willo_url}
        })
        if "_error" in result:
            log_fail("willo", name, email, "PATCH {}: {}".format(
                result["_error"], result.get("detail", "")))
        else:
            print("  ✅ Updated {} (id={}) — {} = {!r}".format(
                name, pid, WILLO_KEY, willo_url))


# ---------------------------------------------------------------------------
# Task 2: Customer Support role additions  (custom_hierarchy_13)
# ---------------------------------------------------------------------------

def run_cs_updates(client: LoxoStaffingClient, export: Dict[str, str],
                   dry_run: bool, limit: Optional[int], cs_role_id: Optional[int]):
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
        print("  ❌ Cannot run Task 2: hierarchy id for '{}' not resolved.".format(NEW_ROLE))
        for row in rows:
            email = (row.get("Email") or "").strip()
            name  = "{} {}".format(
                (row.get("First Name") or "").strip(),
                (row.get("Last Name") or "").strip()).strip()
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

        pid = lookup_id(email, export)
        if not pid:
            log_fail("customer_support", name, email, "Email not found in export CSV")
            continue

        if dry_run:
            print("  🔎 [DRY RUN] {} ({}) id={}".format(name, email, pid))
            print("           → append {{'id': {}, 'value': {!r}}} to {}".format(
                cs_role_id or "<??>", NEW_ROLE, ROLES_KEY))
            continue

        # Fetch full record to get current roles
        full = client.get_person(pid)
        if "_error" in full:
            log_fail("customer_support", name, email,
                     "Could not fetch person {}: {}".format(pid, full.get("detail", "")))
            continue

        current_roles = full.get(ROLES_KEY) or []
        if not isinstance(current_roles, list):
            current_roles = []

        if any(r.get("id") == cs_role_id for r in current_roles if isinstance(r, dict)):
            print("  ⏭️  Skipped {} — '{}' already present".format(name, NEW_ROLE))
            continue

        updated_roles = current_roles + [{"id": cs_role_id, "value": NEW_ROLE}]

        time.sleep(RATE_LIMIT_SLEEP)
        result = client.patch("people/{}".format(pid), {
            "person": {ROLES_KEY: updated_roles}
        })
        if "_error" in result:
            log_fail("customer_support", name, email, "PATCH {}: {}".format(
                result["_error"], result.get("detail", "")))
        else:
            old_labels = [r.get("value", "") for r in current_roles if isinstance(r, dict)]
            print("  ✅ Updated {} (id={}) — appended '{}' (had {} role(s))".format(
                name, pid, NEW_ROLE, len(old_labels)))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Update Loxo people records from CSVs.")
    parser.add_argument("--export", required=True, metavar="FILE",
                        help="Path to the Loxo people export CSV (used for email→id lookup)")
    parser.add_argument("--discover", action="store_true",
                        help="Print non-empty custom_* fields for sample people and exit")
    parser.add_argument("--debug", metavar="EMAIL",
                        help="Inspect export lookup and current field values for one email")
    parser.add_argument("--find-hierarchy-id", action="store_true",
                        help="Scan export for someone with the CS role and print their hierarchy array")
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

    export = load_export(Path(args.export))
    client = LoxoStaffingClient(api_key)

    if args.discover:
        run_discover(client, export)
        return

    if args.debug:
        run_debug(client, export, args.debug)
        return

    if args.find_hierarchy_id:
        find_hierarchy_id_from_export(client, Path(args.export))
        return

    if args.dry_run:
        print("\n🔎 DRY RUN MODE — no API calls will be made\n")

    # Resolve CS role hierarchy id once up front
    cs_role_id = None
    if not args.dry_run:
        print("🔍 Looking up hierarchy id for '{}' ...".format(NEW_ROLE), flush=True)
        cs_role_id = find_cs_role_id(client)
        if cs_role_id is None:
            print("  ⚠️  Could not resolve — Task 2 rows will be logged as failures.")
        else:
            print("  ✅ Found id={} for '{}'\n".format(cs_role_id, NEW_ROLE))

    run_willo_updates(client, export, dry_run=args.dry_run, limit=args.limit)
    run_cs_updates(client, export, dry_run=args.dry_run, limit=args.limit,
                   cs_role_id=cs_role_id)

    if not args.dry_run:
        write_failures()

    print("\nDone.\n")


if __name__ == "__main__":
    main()
