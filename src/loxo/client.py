import os
import requests
from collections import defaultdict
from datetime import datetime, timezone
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

        all_candidates = []
        scroll_id = None
        while True:
            params = {"scroll_id": scroll_id} if scroll_id else {}
            raw = self._get(f"jobs/{job_id}/candidates", params)
            if "_http_error" in raw:
                break

            batch = raw if isinstance(raw, list) else raw.get("candidates", raw.get("data", []))
            if not batch:
                break

            for c in batch:
                if isinstance(c, dict):
                    sid = str(c.get("workflow_stage_id", ""))
                    c["stage_name"] = stage_map.get(sid, sid) if stage_map else sid
                    all_candidates.append(c)

            total_count = raw.get("total_count") if isinstance(raw, dict) else None
            scroll_id = raw.get("scroll_id") if isinstance(raw, dict) else None
            if not scroll_id or (total_count is not None and len(all_candidates) >= total_count):
                break

        print(f"[candidates] job {job_id} total={len(all_candidates)}, stages={set(c.get('stage_name') for c in all_candidates)}", flush=True)

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

    def get_leaderboard(self, since: str = None) -> dict:
        """
        Aggregate placements by recruiter/owner and compute revenue.
        since: ISO date string e.g. '2026-01-01'. Defaults to current year.
        Returns per-person placement count and estimated revenue.
        """
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        if since_dt is None:
            since_dt = datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc)

        all_placements = self.get_placements().get("placements", [])

        stats: dict = defaultdict(lambda: {"placements": 0, "revenue": 0.0, "roles": []})

        for p in all_placements:
            created_raw = p.get("created_at") or p.get("start_date") or ""
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                created_dt = None

            if created_dt and created_dt < since_dt:
                continue

            # Revenue: salary * fee% or flat fee
            salary = float(p.get("salary") or 0)
            fee = float(p.get("fee") or 0)
            fee_key = (p.get("fee_type") or {}).get("key", "percentage")
            revenue = (salary * fee / 100) if fee_key == "percentage" else fee

            job_title = (p.get("job") or {}).get("title", "Unknown role")

            # Credit splits if present, otherwise credit created_by
            splits = p.get("splits") or []
            if splits:
                for s in splits:
                    user = s.get("user") or {}
                    name = user.get("name") or user.get("email") or "Unknown"
                    pct = float(s.get("percentage") or 100) / 100
                    stats[name]["placements"] += 1
                    stats[name]["revenue"] += round(revenue * pct, 2)
                    if job_title not in stats[name]["roles"]:
                        stats[name]["roles"].append(job_title)
            else:
                creator = p.get("created_by") or {}
                name = creator.get("name") or creator.get("email") or "Unknown"
                stats[name]["placements"] += 1
                stats[name]["revenue"] += round(revenue, 2)
                if job_title not in stats[name]["roles"]:
                    stats[name]["roles"].append(job_title)

        leaderboard = sorted(
            [{"name": k, **v} for k, v in stats.items()],
            key=lambda x: x["placements"],
            reverse=True,
        )
        return {"since": since_dt.date().isoformat(), "leaderboard": leaderboard}

    def get_team_activity(self, since: str = None) -> dict:
        """
        Aggregate person_events (calls, emails, notes, interviews) by user.
        since: ISO date string. Defaults to last 30 days.
        """
        if since:
            try:
                since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
            except ValueError:
                since_dt = None
        else:
            since_dt = None

        # Fetch person_events with scroll pagination
        all_events = []
        scroll_id = None
        while True:
            params = {"scroll_id": scroll_id} if scroll_id else {}
            raw = self._get("person_events", params)
            if "_http_error" in raw:
                break
            batch = raw if isinstance(raw, list) else raw.get("person_events", raw.get("data", []))
            if not batch:
                break
            all_events.extend(batch)
            total_count = raw.get("total_count") if isinstance(raw, dict) else None
            scroll_id = raw.get("scroll_id") if isinstance(raw, dict) else None
            if not scroll_id or (total_count is not None and len(all_events) >= total_count):
                break

        activity: dict = defaultdict(lambda: {"total": 0, "by_type": defaultdict(int)})

        for e in all_events:
            created_raw = e.get("created_at") or ""
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                created_dt = None

            if since_dt and created_dt and created_dt < since_dt:
                continue

            user = e.get("user") or e.get("created_by") or {}
            name = user.get("name") or user.get("email") or "Unknown"
            activity_type = (e.get("activity_type") or {}).get("name", "Activity")

            activity[name]["total"] += 1
            activity[name]["by_type"][activity_type] += 1

        ranked = sorted(
            [{"name": k, "total": v["total"], "by_type": dict(v["by_type"])}
             for k, v in activity.items()],
            key=lambda x: x["total"],
            reverse=True,
        )
        since_label = since_dt.date().isoformat() if since_dt else "all time"
        return {"since": since_label, "activity": ranked}
