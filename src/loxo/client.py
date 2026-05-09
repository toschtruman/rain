import os
import requests
from typing import Optional


INSTANCES = {
    "staffing": {
        "base_url": "https://rain-global.app.loxo.co/api/rain-global/",
        "api_key_env": "LOXO_RAIN_STAFFING_API_KEY",
    },
    "leadership": {
        "base_url": "https://rain.app.loxo.co/api/rain/",
        "api_key_env": "LOXO_RAIN_LEADERSHIP_API_KEY",
    },
}

# Module-level stage cache: {instance: {stage_id: stage_name}}
_stage_cache: dict = {}


class LoxoClient:
    def __init__(self, instance: str = "staffing"):
        if instance not in INSTANCES:
            raise ValueError(f"Unknown instance '{instance}'. Choose from: {list(INSTANCES)}")
        config = INSTANCES[instance]
        api_key = os.environ.get(config["api_key_env"])
        if not api_key:
            raise RuntimeError(f"Missing env var: {config['api_key_env']}")
        self.instance = instance
        self.base_url = config["base_url"]
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.base_url + path.lstrip("/")
        resp = self.session.get(url, params=params or {})
        if not resp.ok:
            return {"_http_error": resp.status_code, "detail": resp.text[:200], "url": url}
        return resp.json()

    def _stage_map(self, job_id: int) -> dict:
        """Return {stage_id: stage_name}, fetched once per instance and cached."""
        if self.instance in _stage_cache:
            return _stage_cache[self.instance]

        raw = self._get("workflow_stages")
        if "_http_error" not in raw:
            stages = raw if isinstance(raw, list) else raw.get("workflow_stages", raw.get("data", []))
            _stage_cache[self.instance] = {
                str(s.get("id", "")): s.get("name", str(s.get("id", "")))
                for s in stages if isinstance(s, dict)
            }
            print(f"[stages] loaded {len(_stage_cache[self.instance])} stages for {self.instance}", flush=True)
        else:
            print(f"[stages] workflow_stages failed: {raw}", flush=True)
            _stage_cache[self.instance] = {}

        return _stage_cache[self.instance]

    def get_candidates(self, page: int = 1, per_page: int = 25, **filters) -> dict:
        return self._get("people", {"page": page, "per_page": per_page, **filters})

    def get_candidate(self, candidate_id: int) -> dict:
        return self._get(f"people/{candidate_id}")

    def get_jobs(self, page: int = 1, per_page: int = 25, **filters) -> dict:
        for path in ("jobs", "job_orders", "searches"):
            result = self._get(path, {"page": page, "per_page": per_page, **filters})
            if "_http_error" not in result:
                return result
        return result

    def get_job(self, job_id: int) -> dict:
        return self._get(f"jobs/{job_id}")

    def get_job_candidates(self, job_id: int) -> dict:
        raw = self._get(f"jobs/{job_id}/candidates")
        if "_http_error" in raw:
            return raw

        stage_map = self._stage_map(job_id)
        candidates = raw if isinstance(raw, list) else raw.get("candidates", raw.get("data", []))

        # Enrich each candidate with a human-readable stage name
        for c in candidates:
            if isinstance(c, dict):
                sid = str(c.get("stage_id", c.get("workflow_stage_id", "")))
                if sid and stage_map:
                    c["stage_name"] = stage_map.get(sid, sid)

        return {"candidates": candidates, "total": len(candidates)}

    def search_candidates(self, query: str, page: int = 1, per_page: int = 25) -> dict:
        return self._get("people", {"q": query, "page": page, "per_page": per_page})
