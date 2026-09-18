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


ROLE_TITLE_TERMS = (
    "scientist", "research", "biology", "biologist", "biotech", "biological",
    "medical", "clinical", "laboratory", "lab ", "quality", "application",
    "technical", "process", "cmc", "pharmacology", "toxicology", "assay",
    "cell", "molecular", "protein", "microbiology", "bioinformatics",
    "科学", "研究", "研发", "医学", "临床", "实验", "生物", "质量",
    "技术", "工艺", "药理", "毒理", "细胞", "分子", "蛋白", "微生物",
)


def fetch_json(url: str, payload: dict | None = None) -> dict:
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "GuangfoBiologyJobBoard/1.0 (+GitHub Actions)",
            "Accept": "application/json",
            "Content-Type": "application/json",
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


def make_summary(body: str, hits: list[str]) -> str:
    summary = body[:680].rstrip()
    if len(body) > 680:
        summary += "…"
    unique_hits = list(dict.fromkeys(str(hit) for hit in hits))
    if unique_hits:
        summary += "\n命中关键词：" + " / ".join(unique_hits[:8]) + "。"
    return summary


def target_city(text: str) -> str:
    cities = []
    if re.search(r"广州|Guangzhou|Canton", text, re.I):
        cities.append("广州")
    if re.search(r"佛山|Foshan|Fatshan", text, re.I):
        cities.append("佛山")
    return " / ".join(cities)


def source_key(feed: dict) -> str:
    return str(feed.get("id") or feed.get("board") or feed.get("tenant") or feed["kind"])


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
        jobs.append(
            {
                "title": title,
                "company": feed["company"],
                "city": "广州（黄埔）" if "Guangzhou" in location else location,
                "salary": "未披露",
                "summary": make_summary(body, hits),
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


def workday_company(feed: dict, item: dict, detail: dict) -> str:
    labels = feed.get("company_labels", {})
    bullet_fields = item.get("bulletFields") or []
    organization = str((detail.get("hiringOrganization") or {}).get("name") or "")
    candidates = [str(value) for value in bullet_fields[1:]] + [organization]
    for candidate in candidates:
        low = candidate.lower()
        for needle, company in labels.items():
            if str(needle).lower() in low:
                return str(company)
    return str(feed["company"])


def collect_workday(feed: dict, config: dict) -> tuple[list[dict], set[str]]:
    host = str(feed["host"]).rstrip("/")
    tenant = str(feed["tenant"])
    site = str(feed["site"])
    api_base = f"{host}/wday/cxs/{tenant}/{site}"
    public_base = str(feed.get("public_base") or f"{host}/{site}").rstrip("/")
    queries = feed.get("queries") or ["Guangzhou", "Foshan"]
    keywords = [str(item) for item in config.get("keywords", [])]
    listings = {}

    for query in queries:
        offset = 0
        while True:
            payload = fetch_json(
                f"{api_base}/jobs",
                {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": query},
            )
            page = payload.get("jobPostings") or []
            for item in page:
                path = str(item.get("externalPath") or "").strip()
                if path:
                    listings[path] = item
            offset += len(page)
            if not page or offset >= int(payload.get("total") or 0):
                break

    jobs = []
    active_urls = set()
    for path, item in listings.items():
        job_url = f"{public_base}{path}"
        active_urls.add(job_url.rstrip("/"))
        detail = fetch_json(f"{api_base}{path}")
        info = detail.get("jobPostingInfo") or {}
        title = str(info.get("title") or item.get("title") or "").strip()
        body = plain_text(info.get("jobDescription"))
        location = str(info.get("location") or item.get("locationsText") or "").strip()
        city = target_city(" ".join((location, path, body)))
        if not city:
            continue
        if not any(term in title.lower() for term in ROLE_TITLE_TERMS):
            continue
        hits = keyword_hits(f"{title} {body}", keywords)
        if not hits:
            continue
        jobs.append(
            {
                "title": title,
                "company": workday_company(feed, item, detail),
                "city": city,
                "salary": "未披露",
                "summary": make_summary(body, hits),
                "source": feed["source"],
                "url": job_url,
                "posted_date": iso_date(info.get("startDate")),
                "is_foreign": True,
                "match_score": min(98, 70 + len(set(hits)) * 3),
                "is_expired": False,
                "expired_reason": "",
            }
        )
    return jobs, active_urls


def expire_missing(feed: dict, active_urls: set[str]) -> int:
    jobs = manage_jobs.load_jobs()
    changed = 0
    dirty = False
    prefix = str(feed.get("url_prefix") or "").rstrip("/") + "/"
    if prefix == "/":
        return 0
    active_keys = {manage_jobs.url_key({"url": url}) for url in active_urls}
    for job in jobs:
        url = str(job.get("url") or "").split("?", 1)[0].rstrip("/")
        if not url.startswith(prefix):
            continue
        is_active = manage_jobs.url_key(job) in active_keys
        if is_active and job.get("is_expired"):
            job["is_expired"] = False
            job["expired_reason"] = ""
            dirty = True
        elif not is_active and not job.get("is_expired"):
            job["is_expired"] = True
            job["expired_reason"] = "企业官方职位列表已下架"
            changed += 1
            dirty = True
    if dirty:
        manage_jobs.save_jobs(jobs)
    return changed


def update_runtime(
    config: dict, added: int, expired: int, successful: int, total: int, feed_errors: list[str]
) -> None:
    now = datetime.now(timezone.utc).astimezone()
    runtime = config.setdefault("runtime", {})
    runtime["last_run"] = now.replace(microsecond=0).isoformat()
    status = (
        f"{now:%Y-%m-%d} 云端采集完成：{successful}/{total} 个官方来源成功，"
        f"新增 {added} 条，失效 {expired} 条。"
    )
    if feed_errors:
        status += " 部分公开来源暂时不可用，已保留原数据。"
    runtime["collection_note"] = status
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    collected = []
    active_by_feed = {}
    errors = []
    feeds = config.get("job_sources") or []
    successful = 0
    for feed in feeds:
        if not feed.get("enabled", True):
            continue
        try:
            if feed["kind"] == "greenhouse":
                jobs, active = collect_greenhouse(feed, config)
            elif feed["kind"] == "workday":
                jobs, active = collect_workday(feed, config)
            else:
                raise ValueError(f"unsupported source kind: {feed['kind']}")
            collected.extend(jobs)
            active_by_feed[source_key(feed)] = (feed, active)
            successful += 1
        except Exception as exc:  # keep the last valid board when one source is down
            errors.append(f"{source_key(feed)}: {exc}")

    added = manage_jobs.add(collected) if collected else 0
    expired = sum(
        expire_missing(feed, active)
        for feed, active in active_by_feed.values()
        if active
    )
    enabled_total = sum(1 for feed in feeds if feed.get("enabled", True))
    update_runtime(config, added, expired, successful, enabled_total, errors)
    manage_jobs.render()

    PUBLIC.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "dashboard" / "index_v2.html", PUBLIC / "index.html")
    print(json.dumps({"added": added, "expired": expired, "errors": errors}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

