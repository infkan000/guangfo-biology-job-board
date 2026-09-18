#!/usr/bin/env python3
"""Refresh public job feeds and rebuild the static dashboard.

Designed for GitHub Actions: standard-library only, no local cookies or secrets.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
CONFIG_FILE = DATA / "config_v2.json"

sys.path.insert(0, str(ROOT / "scripts"))
import manage_jobs_v2 as manage_jobs  # noqa: E402


FEEDS = [
    {
        "kind": "greenhouse",
        "board": "angitiaincorporatedlimited",
        "company": "Angitia Biopharmaceuticals（安济盛生物）",
        "source": "Angitia Greenhouse 官方招聘",
    }
]

ROLE_TITLE_TERMS = (
    "scientist", "research", "biology", "biologist", "biotech", "biological",
    "medical", "clinical", "laboratory", "lab ", "quality", "application",
    "technical", "process", "cmc", "pharmacology", "toxicology", "assay",
    "cell", "molecular", "protein", "microbiology", "bioinformatics",
    "科学", "研究", "研发", "医学", "临床", "实验", "生物", "质量",
    "技术", "工艺", "药理", "毒理", "细胞", "分子", "蛋白", "微生物",
)


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GuangfoBiologyJobBoard/1.0 (+GitHub Actions)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def plain_text(value: str) -> str:
    text = html.unescape(html.unescape(str(value or "")))
    text = re.split(r"\bAbout Angitia\b", text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def iso_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10]


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    low = text.lower()
    return [term for term in keywords if str(term).lower() in low]


def collect_greenhouse(feed: dict, config: dict) -> tuple[list[dict], set[str]]:
    board = feed["board"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    payload = fetch_json(url)
    jobs = []
    active_urls = set()
    keywords = [str(item) for item in config.get("keywords", [])]
    for item in payload.get("jobs", []):
        job_url = str(item.get("absolute_url") or "").strip()
        if job_url:
            active_urls.add(job_url.rstrip("/"))
        location = str((item.get("location") or {}).get("name") or "").strip()
        if not re.search(r"广州|佛山|Guangzhou|Foshan|Canton", location, re.I):
            continue
        body = plain_text(item.get("content"))
        title = str(item.get("title") or "").strip()
        if not any(term in title.lower() for term in ROLE_TITLE_TERMS):
            continue
        hits = keyword_hits(f"{title} {body}", keywords)
        if not hits:
            continue
        summary = body[:680].rstrip()
        if len(body) > 680:
            summary += "…"
        jobs.append(
            {
                "title": title,
                "company": feed["company"],
                "city": "广州（黄埔）" if "Guangzhou" in location else location,
                "salary": "未披露",
                "summary": summary,
                "source": feed["source"],
                "url": job_url,
                "posted_date": iso_date(item.get("first_published")),
                "is_foreign": True,
                "match_score": min(98, 70 + len(set(hits)) * 3),
                "is_expired": False,
                "expired_reason": "",
            }
        )
    return jobs, active_urls


def expire_missing_greenhouse(active_urls: set[str]) -> int:
    jobs = manage_jobs.load_jobs()
    changed = 0
    prefix = "https://job-boards.greenhouse.io/angitiaincorporatedlimited/jobs/"
    for job in jobs:
        url = str(job.get("url") or "").split("?", 1)[0].rstrip("/")
        if url.startswith(prefix) and url not in active_urls and not job.get("is_expired"):
            job["is_expired"] = True
            job["expired_reason"] = "企业官方职位列表已下架"
            changed += 1
    if changed:
        manage_jobs.save_jobs(jobs)
    return changed


def update_runtime(config: dict, added: int, expired: int, feed_errors: list[str]) -> None:
    now = datetime.now(timezone.utc).astimezone()
    runtime = config.setdefault("runtime", {})
    runtime["last_run"] = now.replace(microsecond=0).isoformat()
    status = f"{now:%Y-%m-%d} 云端采集完成：新增 {added} 条，失效 {expired} 条。"
    if feed_errors:
        status += " 部分公开来源暂时不可用，已保留原数据。"
    runtime["collection_note"] = status
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    collected = []
    active_greenhouse = set()
    errors = []
    for feed in FEEDS:
        try:
            if feed["kind"] == "greenhouse":
                jobs, active = collect_greenhouse(feed, config)
                collected.extend(jobs)
                active_greenhouse.update(active)
        except Exception as exc:  # keep the last valid board when one source is down
            errors.append(f"{feed.get('board', feed['kind'])}: {exc}")

    added = manage_jobs.add(collected) if collected else 0
    expired = expire_missing_greenhouse(active_greenhouse) if active_greenhouse else 0
    update_runtime(config, added, expired, errors)
    manage_jobs.render()

    PUBLIC.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "dashboard" / "index_v2.html", PUBLIC / "index.html")
    print(json.dumps({"added": added, "expired": expired, "errors": errors}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

