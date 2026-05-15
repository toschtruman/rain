#!/usr/bin/env python3
"""
restore_kristian.py

Restores Kristian Louie Odtojan (Loxo id=7791221) to his original
custom_hierarchy_13 roles:
  Client Success, Customer Support/Customer Care,
  Executive Assistant, Virtual Assistant

Run:
  python restore_kristian.py --export rain-global-staffing-people-export-920.csv
  python restore_kristian.py --export rain-global-staffing-people-export-920.csv --dry-run
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

STAFFING_BASE_URL = "https://rain-global.app.loxo.co/api/rain-global/"
API_KEY_ENV       = "LOXO_RAIN_STAFFING_API_KEY"

KRISTIAN_ID   = "7791221"
ROLES_KEY     = "custom_hierarchy_13"
ROLES_COL     = "Roles Experienced in - Registration"
TARGET_ROLES  = [
    "Client Success",
    "Customer Support/Customer Care",
    "Executive Assistant",
    "Virtual Assistant",
]


# ---------------------------------------------------------------------------
# Minimal client
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": "Bearer {}".format(api_key),
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return STAFFING_BASE_URL + path.lstrip("/")

    def get(self, path: str) -> dict:
        resp = self.session.get(self._url(path))
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

    def patch_raw(self, path: str, body: dict) -> tuple:
        """Return (status_code, response_text) without any processing."""
        resp = self.session.patch(self._url(path), json=body)
        return resp.status_code, resp.text


# ---------------------------------------------------------------------------
# Find role IDs from a donor person in the export
# ---------------------------------------------------------------------------

def find_role_ids(client: Client, export_path: Path) -> dict:
    """
    Scan the export for someone who has all TARGET_ROLES in their
    roles column, fetch their API record, and return a
    { role_label: id } dict for every role we need.
    """
    print("🔍 Scanning export for a person with all target roles ...", flush=True)
    donor_id = None

    with open(export_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cell = (row.get(ROLES_COL) or "").lower()
            if all(role.lower() in cell for role in TARGET_ROLES):
                donor_id = (row.get("Id") or "").strip()
                donor_email = (row.get("Email") or "").strip()
                print("  Found donor: id={}, email={!r}".format(donor_id, donor_email))
                break

    if not donor_id:
        print("  ⚠️  No single person has all 4 roles — trying partial matches ...")
        # Fall back: collect IDs from multiple donors, one per missing role
        role_to_id = {}
        with open(export_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            candidates = [r for r in reader
                          if any(role.lower() in (r.get(ROLES_COL) or "").lower()
                                 for role in TARGET_ROLES)]

        for role in TARGET_ROLES:
            if role in role_to_id:
                continue
            for row in candidates:
                if role.lower() in (row.get(ROLES_COL) or "").lower():
                    cid = (row.get("Id") or "").strip()
                    print("  Fetching partial-donor id={} for role {!r} ...".format(cid, role))
                    full = client.get("people/{}".format(cid))
                    time.sleep(0.5)
                    if "_error" in full:
                        continue
                    for entry in (full.get(ROLES_KEY) or []):
                        label = (entry.get("value") or "").strip()
                        if label in TARGET_ROLES and label not in role_to_id:
                            role_to_id[label] = entry["id"]
                    if role in role_to_id:
                        break
        return role_to_id

    # Fetch the donor's full record
    print("  Fetching full record for donor id={} ...".format(donor_id), flush=True)
    time.sleep(0.5)
    full = client.get("people/{}".format(donor_id))
    if "_error" in full:
        print("❌ Could not fetch donor: {}".format(full))
        sys.exit(1)

    role_to_id = {}
    for entry in (full.get(ROLES_KEY) or []):
        label = (entry.get("value") or "").strip()
        if label in TARGET_ROLES:
            role_to_id[label] = entry["id"]

    return role_to_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", required=True, metavar="FILE",
                        help="Path to the Loxo people export CSV")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the restore payload without making any API calls")
    parser.add_argument("--verify", action="store_true",
                        help="Fetch and print Kristian's current custom_hierarchy_13 from the API")
    parser.add_argument("--debug-patch", action="store_true",
                        help="Fetch current roles, PATCH with existing+CS role, print raw response, re-fetch to confirm")
    args = parser.parse_args()

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print("❌ Missing {}".format(API_KEY_ENV))
        sys.exit(1)

    client = Client(api_key)

    if args.verify:
        print("\n🔍 Fetching current {} for Kristian (id={}) ...\n".format(
            ROLES_KEY, KRISTIAN_ID), flush=True)
        full = client.get("people/{}".format(KRISTIAN_ID))
        if "_error" in full:
            print("❌ Could not fetch: {}".format(full))
            sys.exit(1)
        roles = full.get(ROLES_KEY)
        if not roles:
            print("  {} is empty or missing on this record.".format(ROLES_KEY))
        else:
            print("  {} entry(s) in {}:\n".format(len(roles), ROLES_KEY))
            for entry in roles:
                print("    {{'id': {}, 'value': {!r}}}".format(
                    entry.get("id"), entry.get("value")))
        print()
        return

    if args.debug_patch:
        import json

        def print_roles(label, full):
            roles = full.get(ROLES_KEY)
            if not roles:
                print("  {} → empty / missing".format(label))
            else:
                print("  {} → {} entry(s):".format(label, len(roles)))
                for e in roles:
                    print("    {{'id': {}, 'value': {!r}}}".format(e.get("id"), e.get("value")))

        print("\n🔍 DEBUG PATCH for Kristian (id={})\n".format(KRISTIAN_ID), flush=True)

        # Step 1: fetch current record
        print("── Step 1: current record ──────────────────────────────────────")
        full = client.get("people/{}".format(KRISTIAN_ID))
        if "_error" in full:
            print("❌ Fetch failed: {}".format(full))
            sys.exit(1)
        print_roles(ROLES_KEY, full)

        current_roles = full.get(ROLES_KEY) or []
        if not isinstance(current_roles, list):
            current_roles = []

        # Step 2: build and show the PATCH body
        new_entry = {"id": 43389, "value": "Customer Support/Customer Care"}
        already_present = any(r.get("id") == 43389 for r in current_roles if isinstance(r, dict))
        updated_roles = current_roles if already_present else current_roles + [new_entry]
        body = {"person": {ROLES_KEY: updated_roles}}

        print("\n── Step 2: PATCH body ──────────────────────────────────────────")
        print(json.dumps(body, indent=2))

        # Step 3: send PATCH and print full raw response
        print("\n── Step 3: raw PATCH response ──────────────────────────────────")
        time.sleep(0.5)
        status, text = client.patch_raw("people/{}".format(KRISTIAN_ID), body)
        print("  HTTP {}".format(status))
        try:
            print(json.dumps(json.loads(text), indent=2))
        except Exception:
            print(text[:1000])

        # Step 4: re-fetch and print
        print("\n── Step 4: record after PATCH ──────────────────────────────────")
        time.sleep(0.5)
        full2 = client.get("people/{}".format(KRISTIAN_ID))
        if "_error" in full2:
            print("❌ Re-fetch failed: {}".format(full2))
        else:
            print_roles(ROLES_KEY, full2)
        print()
        return

    role_to_id = find_role_ids(client, Path(args.export))

    print("\n  Resolved role IDs:")
    missing = []
    for role in TARGET_ROLES:
        rid = role_to_id.get(role)
        print("    {:45s} id={}".format(role, rid if rid else "❌ NOT FOUND"))
        if rid is None:
            missing.append(role)

    if missing:
        print("\n❌ Could not resolve IDs for: {}".format(missing))
        print("   Cannot safely restore — add those IDs manually and re-run.")
        sys.exit(1)

    restore_array = [
        {"id": role_to_id[role], "value": role}
        for role in TARGET_ROLES
    ]

    print("\n📋 Restore payload for Kristian Louie Odtojan (id={}):\n".format(KRISTIAN_ID))
    for entry in restore_array:
        print("   {{'id': {}, 'value': {!r}}}".format(entry["id"], entry["value"]))

    if args.dry_run:
        print("\n🔎 DRY RUN — no changes made.\n")
        return

    print("\n⏳ Patching ...", flush=True)
    time.sleep(0.5)
    result = client.patch("people/{}".format(KRISTIAN_ID), {
        "person": {ROLES_KEY: restore_array}
    })

    if "_error" in result:
        print("❌ PATCH failed: {}".format(result))
        sys.exit(1)

    print("✅ Restored Kristian Louie Odtojan — {} roles set.".format(len(restore_array)))


if __name__ == "__main__":
    main()
