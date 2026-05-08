import os
import requests
from typing import Optional

MONDAY_API_URL = "https://api.monday.com/v2"

BOARD_IDS = {
    "deals": 18411151566,
    "contacts": 18411151569,
    "accounts": 18411151567,
    "projects": 18407250077,
}

# Only fetch the columns we actually need to stay under complexity limits
DEAL_COLUMNS = ["status", "text", "date", "person", "numeric"]


class MondayClient:
    def __init__(self):
        api_key = os.environ.get("MONDAY_API_KEY")
        if not api_key:
            raise RuntimeError("Missing env var: MONDAY_API_KEY")
        self.headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
            "API-Version": "2026-07",
        }

    def query(self, gql: str, variables: Optional[dict] = None) -> dict:
        payload = {"query": gql}
        if variables:
            payload["variables"] = variables
        resp = requests.post(MONDAY_API_URL, json=payload, headers=self.headers)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Monday GraphQL errors: {data['errors']}")
        return data.get("data", {})

    def get_board_items(self, board_name: str, limit: int = 25, cursor: Optional[str] = None) -> dict:
        board_id = BOARD_IDS[board_name]
        cursor_arg = f', cursor: "{cursor}"' if cursor else ""
        gql = f"""
        query {{
          boards(ids: [{board_id}]) {{
            name
            items_page(limit: {limit}{cursor_arg}) {{
              cursor
              items {{
                id
                name
                column_values {{
                  id
                  text
                }}
              }}
            }}
          }}
        }}
        """
        return self.query(gql)

    def get_deals_by_stage(self) -> dict:
        """Fetch deals grouped by pipeline stage, paginated to stay under complexity cap."""
        # First get group names only
        groups_gql = f"""
        query {{
          boards(ids: [{BOARD_IDS['deals']}]) {{
            groups {{
              id
              title
            }}
          }}
        }}
        """
        groups_data = self.query(groups_gql)
        groups = groups_data.get("boards", [{}])[0].get("groups", [])

        result = {"boards": [{"groups": []}]}
        for group in groups:
            items_gql = f"""
            query {{
              boards(ids: [{BOARD_IDS['deals']}]) {{
                groups(ids: ["{group['id']}"]) {{
                  id
                  title
                  items_page(limit: 25) {{
                    items {{
                      id
                      name
                      column_values {{
                        id
                        text
                      }}
                    }}
                  }}
                }}
              }}
            }}
            """
            data = self.query(items_gql)
            fetched = data.get("boards", [{}])[0].get("groups", [])
            result["boards"][0]["groups"].extend(fetched)

        return result

    def get_stale_deals(self, days_stale: int = 14) -> dict:
        return self.get_board_items("deals", limit=25)

    def get_contacts(self, limit: int = 25) -> dict:
        return self.get_board_items("contacts", limit=limit)

    def get_accounts(self, limit: int = 25) -> dict:
        return self.get_board_items("accounts", limit=limit)
