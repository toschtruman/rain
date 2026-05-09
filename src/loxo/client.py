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

    def get_job_candidates(self, job_id: int, stage_name: str = None) -> dict:
        stage_map = self._stage_map(job_id)

        target_stage_id = None
        if stage_name and stage_map:
            target_stage_id = next(
                (sid for sid, sname in stage_map.items()
                 if stage_name.lower() in sname.lower()),
                None
            )

        # Try different params to include placed/archived candidates
        all_candidates = []
        for params in [{}, {"status": "all"}, {"include_archived": True}, {"active": False}]:
            raw = self._get(f"jobs/{job_id}/candidates", params)
            if "_http_error" in raw:
                continue
            batch = raw if isinstance(raw, list) else raw.get("candidates", raw.get("data", []))
            for c in batch:
                if isinstance(c, dict) and c.get("id") not in {x.get("id") for x in all_candidates}:
                    sid = str(c.get("workflow_stage_id", ""))
                    c["stage_name"] = stage_map.get(sid, sid) if stage_map else sid
                    all_candidates.append(c)

        print(f"[candidates] job {job_id} total unique={len(all_candidates)}, stages={set(c.get('stage_name') for c in all_candidates)}", flush=True)

        filtered = (
            [c for c in all_candidates if str(c.get("workflow_stage_id", "")) == target_stage_id]
            if target_stage_id else all_candidates
        )
        return {"candidates": filtered, "total": len(filtered), "total_all": len(all_candidates)}

    def get_placements(self, job_id: int = None) -> dict:
        all_placements = []
        scroll_id = None

        while True:
            params = {}
            if scroll_id:
                params["scroll_id"] = scroll_id

            raw = self._get("placements", params)
            if "_http_error" in raw:
                return raw

            batch = raw if isinstance(raw, list) else raw.get("placements", raw.get("data", []))
            if not batch:
                break

            all_placements.extend(batch)

            total_count = raw.get("total_count") if isinstance(raw, dict) else None
            scroll_id = raw.get("scroll_id") if isinstance(raw, dict) else None

            print(f"[placements] fetched {len(all_placements)}/{total_count} (scroll_id={'yes' if scroll_id else 'none'})", flush=True)

            # Stop if no more pages or we've got everything
            if not scroll_id or (total_count is not None and len(all_placements) >= total_count):
                break

        print(f"[placements] total fetched: {len(all_placements)}", flush=True)

        if job_id and all_placements:
            filtered = []
            for p in all_placements:
                p_job_id = (
                    p.get("job_id")
                    or p.get("job_order_id")
                    or (p.get("job") or {}).get("id")
                    or (p.get("job_order") or {}).get("id")
                )
                if str(p_job_id) == str(job_id):
                    filtered.append(p)
            print(f"[placements] after filter job_id={job_id}: {len(filtered)}", flush=True)
            all_placements = filtered

        return {"placements": all_placements, "total": len(all_placements)}
