import os
import requests
from typing import Optional


LOXO_BASE_URL = "https://app.loxo.co/api/{slug}/"

INSTANCES = {
    "staffing": {
        "slug": "rain-staffing",
        "api_key_env": "LOXO_RAIN_STAFFING_API_KEY",
    },
    "leadership": {
        "slug": "rain-leadership",
        "api_key_env": "LOXO_RAIN_LEADERSHIP_API_KEY",
    },
}


class LoxoClient:
    def __init__(self, instance: str = "staffing"):
        if instance not in INSTANCES:
            raise ValueError(f"Unknown instance '{instance}'. Choose from: {list(INSTANCES)}")
        config = INSTANCES[instance]
        api_key = os.environ.get(config["api_key_env"])
        if not api_key:
            raise RuntimeError(f"Missing env var: {config['api_key_env']}")
        self.instance = instance
        self.slug = config["slug"]
        self.base_url = LOXO_BASE_URL.format(slug=self.slug)
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.base_url + path.lstrip("/")
        resp = self.session.get(url, params=params or {})
        if not resp.ok:
            return {"error": resp.status_code, "detail": resp.text, "url": url}
        return resp.json()

    def get_candidates(self, page: int = 1, per_page: int = 25, **filters) -> dict:
        return self._get("candidates", {"page": page, "per_page": per_page, **filters})

    def get_candidate(self, candidate_id: int) -> dict:
        return self._get(f"candidates/{candidate_id}")

    def get_jobs(self, page: int = 1, per_page: int = 25, **filters) -> dict:
        # Loxo uses job_orders as the primary endpoint
        for path in ("job_orders", "jobs", "searches"):
            result = self._get(path, {"page": page, "per_page": per_page, **filters})
            if "error" not in result:
                return result
        return result

    def get_job(self, job_id: int) -> dict:
        return self._get(f"job_orders/{job_id}")

    def get_job_candidates(self, job_id: int, page: int = 1, per_page: int = 25) -> dict:
        for path in (f"job_orders/{job_id}/candidates", f"jobs/{job_id}/candidates"):
            result = self._get(path, {"page": page, "per_page": per_page})
            if "error" not in result:
                return result
        return result

    def search_candidates(self, query: str, page: int = 1, per_page: int = 25) -> dict:
        return self._get("candidates", {"q": query, "page": page, "per_page": per_page})
