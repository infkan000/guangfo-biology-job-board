#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Manage job listings and render the local dashboard.

Commands:
  add       Read jobs from --file (JSON array) or --jobs (inline JSON), dedup and append.
  render    Rebuild dashboard/index_v2.html from data/jobs_v2.jsonl + data/config_v2.json.

Job fields (canonical keys):
  title, company, city, salary, summary, source, url, posted_date, is_foreign, match_score
"""
from html import escape
from jobs_policy_v2 import eligible
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
JOBS_FILE = DATA_DIR / "jobs_v2.jsonl"
CONFIG_FILE = DATA_DIR / "config_v2.json"
DASHBOARD_DIR = ROOT / "dashboard"
INDEX_FILE = DASHBOARD_DIR / "index_v2.html"

FIELDS = [
    "title", "company", "city", "salary", "summary",
    "source", "url", "posted_date", "is_foreign", "match_score",
    "is_expired", "expired_reason",
]


def now_iso():
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def url_key(job):
    url = str(job.get("url") or "").strip()
    if not url:
        return ""
    return "url:" + re.sub(r"[?#].*$", "", url).rstrip("/")


def combo_key(job):
    return "combo:{}|{}|{}".format(
        norm(job.get("company")), norm(job.get("title")), norm(job.get("city"))
    )


def is_new_day(value):
    """Return True when the value is a timestamp for the local calendar day."""
    text = str(value or "").strip()
    if not text:
        return False
    try:
        dt = datetime.fromisoformat(text)
        return dt.date() == datetime.now().astimezone().date()
    except ValueError:
        return text[:10] == datetime.now().astimezone().strftime("%Y-%m-%d")


def load_jobs():
    jobs = []
    if JOBS_FILE.exists():
        with JOBS_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    jobs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return jobs


def save_jobs(jobs):
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with JOBS_FILE.open("w", encoding="utf-8") as f:
        for j in jobs:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")


def clean(raw):
    j = {}
    for f in FIELDS:
        v = raw.get(f, "")
        j[f] = "" if v is None else v
    j["title"] = str(j["title"]).strip()
    j["company"] = str(j["company"]).strip()
    j["city"] = str(j["city"]).strip()
    j["salary"] = str(j["salary"]).strip()
    j["summary"] = str(j["summary"]).strip()
    j["source"] = str(j["source"]).strip()
    j["url"] = str(j["url"]).strip()
    j["posted_date"] = str(j["posted_date"]).strip()
    if isinstance(j["is_foreign"], str):
        j["is_foreign"] = j["is_foreign"].strip().lower() in {"true", "1", "yes", "y", "是"}
    else:
        j["is_foreign"] = bool(j["is_foreign"])
    try:
        j["match_score"] = int(j["match_score"]) if str(j["match_score"]).strip() else None
    except (TypeError, ValueError):
        j["match_score"] = None
    if isinstance(j["is_expired"], str):
        j["is_expired"] = j["is_expired"].strip().lower() in {"true", "1", "yes", "y", "是"}
    else:
        j["is_expired"] = bool(j["is_expired"])
    j["expired_reason"] = str(j["expired_reason"]).strip()
    return j


def add(new_raw):
    config = load_config()
    accepted = [j for j in new_raw if isinstance(j, dict) and eligible(j, config)]
    print(f"模糊搜索筛选：接受 {len(accepted)} 条，排除 {len(new_raw)-len(accepted)} 条（城市 / 外企 / 生物岗位条件）。")
    new_raw = accepted
    jobs = load_jobs()
    for j in jobs:
        j["is_new"] = is_new_day(j.get("first_seen"))
    seen_urls = {url_key(x) for x in jobs if url_key(x)}
    seen_combos = {combo_key(x) for x in jobs}
    added = 0
    for raw in new_raw:
        if not isinstance(raw, dict) or not raw:
            continue
        j = clean(raw)
        if not j["title"] or not j["company"]:
            continue
        ukey = url_key(j)
        ckey = combo_key(j)
        if (ukey and ukey in seen_urls) or ckey in seen_combos:
            continue
        if ukey:
            seen_urls.add(ukey)
        seen_combos.add(ckey)
        j["first_seen"] = now_iso()
        j["is_new"] = True
        jobs.append(j)
        added += 1
    jobs.sort(key=lambda x: x.get("first_seen", ""), reverse=True)
    save_jobs(jobs)
    print(f"新增 {added} 个岗位，当前共 {len(jobs)} 个。")
    return added


def load_config():
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def render():
    jobs = load_jobs()
    config = load_config()
    jobs = [j for j in jobs if eligible(j, config)]
    cities = list(config.get("target", {}).get("cities", ["广州", "佛山"]))
    html = TEMPLATE
    html = html.replace("__JOBS_JSON__", json.dumps(jobs, ensure_ascii=False).replace("<", "\\u003c"))
    html = html.replace("__CONFIG_JSON__", json.dumps(config, ensure_ascii=False).replace("<", "\\u003c"))
    html = html.replace("__CITIES_JSON__", json.dumps(cities, ensure_ascii=False))
    html = html.replace("__UPDATED_AT__", now_iso())
    data_status = config.get("runtime", {}).get("collection_note") or ("模糊搜索已启用每日采集，请核对岗位原文。" if jobs else "模糊搜索岗位库暂无符合条件的结果。")
    html = html.replace("__DATA_STATUS__", escape(data_status))
    html = html.replace("__KEYWORDS_HTML__", " · ".join(escape(k) for k in config.get("keywords", [])))
    html = html.replace("__COMPANIES_HTML__", "；".join(escape(k) + "：" + " / ".join(escape(x) for x in v) for k,v in config.get("companies", {}).items()))
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(html, encoding="utf-8")
    print(f"已生成看板：{INDEX_FILE}（共 {len(jobs)} 个岗位）")


def mark_expired(urls, reason="平台已下架/链接失效"):
    jobs = load_jobs()
    targets = {re.sub(r"[?#].*$", "", u).rstrip("/") for u in urls if u}
    changed = 0
    for j in jobs:
        key = url_key(j)
        if key.startswith("url:"):
            key = key[4:]
        if key in targets:
            j["is_expired"] = True
            j["expired_reason"] = reason or "平台已下架/链接失效"
            changed += 1
    if changed:
        save_jobs(jobs)
    print(f"已标记 {changed} 个岗位为失效。")
    return changed


def main():
    ap = argparse.ArgumentParser(description="管理岗位库并生成本地看板")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="追加岗位（去重）")
    a.add_argument("--file", default=None, help="JSON 数组文件路径")
    a.add_argument("--jobs", default=None, help="内联 JSON 数组字符串")
    sub.add_parser("render", help="重新生成看板")
    exp = sub.add_parser("expired", help="按 URL 标记岗位为失效")
    exp.add_argument("--url", action="append", default=[], help="岗位 URL（可多次传入）")
    exp.add_argument("--reason", default="平台已下架/链接失效", help="失效原因")
    args = ap.parse_args()

    if args.cmd == "add":
        if args.jobs:
            raw = json.loads(args.jobs)
        elif args.file:
            raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        else:
            data = sys.stdin.read().strip()
            if not data:
                print("无输入数据。")
                return
            raw = json.loads(data) if data.startswith("[") else [
                json.loads(line) for line in data.splitlines() if line.strip()
            ]
        add(raw)
    elif args.cmd == "render":
        render()
    elif args.cmd == "expired":
        mark_expired(args.url, args.reason)


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>求职岗位看板 · 模糊搜索 · 广州/佛山外企 Biology</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--ink:#1f2430;--muted:#6b7280;--line:#e5e7eb;--accent:#2f6fed;--green:#15803d;--amber:#b45309}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);line-height:1.6}
.wrap{max-width:980px;margin:0 auto;padding:24px 16px 64px}
header h1{margin:0 0 4px;font-size:24px}
.sub{margin:0;color:var(--muted);font-size:14px}
.meta{margin:10px 0 0;font-size:13px;color:var(--muted)}
.meta b{color:var(--ink)}
.controls{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0 16px}
.controls input,.controls select{padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;font-size:14px;color:var(--ink)}
.controls input{flex:1;min-width:220px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:12px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.card .row1{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.card .title{font-size:16px;font-weight:600;color:#111827;text-decoration:none}
.card .title:hover{color:var(--accent)}
.card.expired{opacity:.58}
.card.expired .title{text-decoration:line-through;color:#9ca3af}
.card.expired .row2,.card.expired .summary{color:#9ca3af}
.badge{font-size:12px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.b-new{background:#dcfce7;color:var(--green)}
.b-expired{background:#e5e7eb;color:#6b7280}
.b-foreign{background:#e0edff;color:#1d4ed8}
.b-score{background:#fef3c7;color:var(--amber)}
.card .row2{margin:6px 0;color:var(--muted);font-size:13px;display:flex;gap:12px;flex-wrap:wrap}
.card .summary{font-size:13px;color:#374151;margin:6px 0 0;white-space:pre-wrap}
.empty{padding:40px;text-align:center;color:var(--muted)}
footer{margin-top:24px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:12px}
a{color:var(--accent)}
</style>
</head>
<body>
<div class="wrap">
<header>
<h1>求职岗位看板 · 模糊搜索</h1>
<p class="sub">广州 / 佛山外企 · Biology / Neuroscience / 生物技术 / 生物工程 · 1–3 年经验偏好</p>
<p class="meta">最后更新：<span id="updated"></span> ｜ 共 <b id="total">0</b> 个岗位 ｜ 今日新增 <b id="newToday">0</b> 个</p>
</header>
<section class="card" style="margin-top:20px;border-color:#f0c36d">
<strong>模糊搜索 · 每日 09:00 自动采集</strong>
<p class="summary">此页使用模糊搜索独立岗位库。__DATA_STATUS__</p>
<p class="summary">模糊筛选范围：实际工作地点包含广州或佛山，已确认外企属性；岗位名称或 JD 任一处出现 Biology、Neuroscience、生物技术、生物工程等相关词即可，不限制具体岗位名称或职能。</p>
<details><summary>查看模糊搜索关键词与候选公司</summary><p class="summary">__KEYWORDS_HTML__</p><p class="summary">__COMPANIES_HTML__</p><p class="summary">候选清单不代表当前有广州岗位，采集时需核实实际工作地点和职位要求。经验 1–3 年为偏好，需人工核对 JD。</p></details>
</section>
<div class="controls">
<input id="q" type="search" placeholder="搜索公司 / 岗位 / JD 关键词…">
<select id="city"><option value="">城市：全部</option></select>
<select id="type">
<option value="">岗位类型：全部</option>

</select>
<select id="foreign">
<option value="1">仅广州/佛山外企</option>
</select>
</div>
<main id="list"></main>
<footer>数据来源：综合平台 + 医药垂直平台 + 外企官网。模糊搜索与第一版独立运行；内容仅供信息整理，投递请以平台原文为准。</footer>
</div>
<script>
var JOBS = __JOBS_JSON__;
var CONFIG = __CONFIG_JSON__;
var CITIES = __CITIES_JSON__;
var UPDATED_AT = "__UPDATED_AT__";
function classify(j){
  var t = ((j.title || '') + ' ' + (j.summary || '')).toLowerCase();
  return Object.keys(CONFIG.role_groups || {}).filter(function(group){
    return CONFIG.role_groups[group].some(function(k){ return containsTerm(t, k); });
  });
}
function containsTerm(t,k){
  k = k.toLowerCase();
  if(/^[a-z0-9 +&-]+$/.test(k)) return new RegExp('(^|[^a-z0-9])' + k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&') + '($|[^a-z0-9])','i').test(t);
  return t.indexOf(k) !== -1;
}
function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
  });
}
function isNew(j){
  
  if(!j.first_seen) return false;
  var d = new Date(j.first_seen);
  if(isNaN(d.getTime())) return false;
  var now = new Date();
  return d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
}
function isExpired(j){
  if(j.is_expired === true || (j.expired_reason && String(j.expired_reason).trim())) return true;
  var pd = String(j.posted_date || '').trim();
  if(!pd) return false;
  var text = pd.length <= 10 ? pd + 'T00:00:00' : pd;
  var t = Date.parse(text);
  if(isNaN(t)) return false;
  var days = (CONFIG && CONFIG.expire_after_days) || 60;
  return (Date.now() - t) > days * 24 * 3600 * 1000;
}
function cityList(j){
  return String(j.city || '').replace(/\//g,' ').split(/[\s,，、]+/).filter(Boolean);
}
function matchCity(j, c){
  return c === '' || cityList(j).some(function(x){
    return x.indexOf(c) !== -1
      || (c === '广州' && /^(guangzhou|canton)$/i.test(x))
      || (c === '佛山' && /^(foshan|fatshan)$/i.test(x));
  });
}
function render(){
  document.getElementById('updated').textContent = UPDATED_AT.replace('T',' ').slice(0,16);
  document.getElementById('total').textContent = JOBS.length;
  document.getElementById('newToday').textContent = JOBS.filter(isNew).length;
  var sel = document.getElementById('city');
  CITIES.forEach(function(c){
    var o = document.createElement('option'); o.value = c; o.textContent = c; sel.appendChild(o);
  });
  Object.keys(CONFIG.role_groups || {}).forEach(function(group){
    var o = document.createElement('option'); o.value = group; o.textContent = group;
    document.getElementById('type').appendChild(o);
  });
  draw();
}
function draw(){
  var q = document.getElementById('q').value.trim().toLowerCase();
  var city = document.getElementById('city').value;
  var type = document.getElementById('type').value;
  var foreign = document.getElementById('foreign').value;
  var rows = JOBS.filter(function(j){
    if(city && !matchCity(j, city)) return false;
    if(type && classify(j).indexOf(type) === -1) return false;
    if(foreign !== '' && (j.is_foreign ? '1' : '0') !== foreign) return false;
    if(q){
      var hay = ((j.title||'')+' '+(j.company||'')+' '+(j.summary||'')).toLowerCase();
      if(hay.indexOf(q) === -1) return false;
    }
    return true;
  }).sort(function(a,b){
    var da = a.posted_date || ''; var db = b.posted_date || '';
    if(da !== db) return da < db ? 1 : -1;
    return (b.first_seen || '').localeCompare(a.first_seen || '');
  });
  var list = document.getElementById('list');
  if(!rows.length){
    list.innerHTML = '<div class="empty">模糊搜索暂无符合条件的岗位。</div>';
    return;
  }
  list.innerHTML = rows.map(function(j){
    var badges = '';
    var expired = isExpired(j);
    if(isNew(j)) badges += '<span class="badge b-new">新增</span>';
    if(expired){
      var reason = (j.expired_reason && String(j.expired_reason).trim()) || '';
      badges += '<span class="badge b-expired" title="' + esc(reason) + '">失效</span>';
    }
    if(j.is_foreign) badges += '<span class="badge b-foreign">外企</span>';
    if(j.match_score != null) badges += '<span class="badge b-score">匹配 ' + j.match_score + '</span>';
    var title = j.url ? '<a class="title" href="' + esc(j.url) + '" target="_blank" rel="noopener">' + esc(j.title) + '</a>' : '<span class="title">' + esc(j.title) + '</span>';
    var meta = esc(j.company) + (j.city ? ' · ' + esc(j.city) : '') + (j.salary ? ' · ' + esc(j.salary) : '') + (j.source ? ' · ' + esc(j.source) : '') + (j.posted_date ? ' · ' + esc(j.posted_date) : '');
    var summary = j.summary ? '<div class="summary">' + esc(j.summary) + '</div>' : '';
    var cardCls = expired ? 'card expired' : 'card';
    return '<div class="' + cardCls + '"><div class="row1">' + title + badges + '</div><div class="row2">' + meta + '</div>' + summary + '</div>';
  }).join('');
}
document.addEventListener('DOMContentLoaded', function(){
  render();
  ['q','city','type','foreign'].forEach(function(id){
    document.getElementById(id).addEventListener('input', draw);
    document.getElementById(id).addEventListener('change', draw);
  });
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()

