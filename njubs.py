#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
njubs.py
监控 https://nubs.nju.edu.cn/main.htm 的四个模块。
当模块内容发生变化时输出变化摘要、发送邮件通知，并自动 commit + push 快照到 GitHub。
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import hashlib
import os
import smtplib
import subprocess
from email.mime.text import MIMEText
from email.header import Header
from typing import Dict, List, Tuple
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

# --------------------------
# 配置区
# --------------------------
URL = "http://nubs.nju.edu.cn/main.htm"
MODULE_IDS = {
    "latest_updates": "wp_news_w46",   # 最新动态
    "notices": "wp_news_w47",          # 通知公告
    "events": "wp_news_w48",           # 活动预告
    "procurement": "wp_news_w100",     # 招标采购
}

SNAPSHOT_FILE = "nubs_snapshot.json"
USER_AGENT = "Mozilla/5.0 (compatible; nubs-watcher/1.0; +https://github.com/)"

# 读取 GitHub Secrets（在 workflow 中注入）
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_TO = os.environ.get("EMAIL_TO", "").split(",")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # 自动 push 用

# --------------------------
# 请求和解析
# --------------------------
class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

def get_page(url: str, timeout: int = 15) -> str:
    headers = {"User-Agent": USER_AGENT}
    s = requests.Session()
    s.mount("http://", HTTPAdapter(max_retries=3))
    r = s.get(url, headers=headers, timeout=timeout, verify=False, allow_redirects=False)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text

def parse_module(html: str, module_id: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find(id=module_id)
    items = []
    if not container:
        return items
    lis = container.select("ul.news_list li.news") or container.select("li")
    for li in lis:
        a = li.find("a")
        if not a:
            continue
        title = (a.get("title") or a.get_text() or "").strip()
        href = a.get("href", "").strip()
        if href.startswith("/"):
            href = requests.compat.urljoin(URL, href)
        date_span = li.find(class_="news-time2")
        date = date_span.get_text().strip() if date_span else ""
        key_str = f"{title}||{href}||{date}"
        item_hash = hashlib.sha256(key_str.encode("utf-8")).hexdigest()
        items.append({"title": title, "href": href, "date": date, "hash": item_hash})
    return items

def fetch_all_modules() -> Dict[str, List[Dict]]:
    html = get_page(URL)
    results = {}
    for name, mid in MODULE_IDS.items():
        results[name] = parse_module(html, mid)
    return results

def load_snapshot(path: str) -> Dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        return {}

def save_snapshot(path: str, data: Dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def diff_snapshots(old: Dict, new: Dict) -> Dict[str, Dict]:
    diffs = {}
    for mod in MODULE_IDS.keys():
        old_list = old.get(mod, [])
        new_list = new.get(mod, [])
        old_map = {it['hash']: it for it in old_list}
        new_map = {it['hash']: it for it in new_list}
        old_hashes = set(old_map.keys())
        new_hashes = set(new_map.keys())
        added = [new_map[h] for h in sorted(new_hashes - old_hashes)]
        removed = [old_map[h] for h in sorted(old_hashes - new_hashes)]
        changed = []
        old_by_href = {it['href']: it for it in old_list}
        for it in new_list:
            oh = old_by_href.get(it['href'])
            if oh and (oh['title'] != it['title'] or oh.get('date') != it.get('date')):
                changed.append((oh, it))
        diffs[mod] = {"added": added, "removed": removed, "changed": changed}
    return diffs

# --------------------------
# 通知：邮件
# --------------------------
def send_email(subject: str, body: str):
    if not (SMTP_USER and SMTP_PASS and EMAIL_FROM and EMAIL_TO):
        print("❌ 邮件配置缺失，无法发送")
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = Header(EMAIL_FROM)
    msg["To"] = Header(", ".join(EMAIL_TO))
    msg["Subject"] = Header(subject, "utf-8")

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print("✅ 邮件发送成功")
    except Exception as e:
        print("❌ 邮件发送失败:", e)

# --------------------------
# 自动 git commit + push
# --------------------------
def git_commit_and_push(file_path: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        subprocess.run(["git", "add", file_path], check=True)
        subprocess.run(["git", "commit", "-m", f"update snapshot {ts}"], check=True)
        if GITHUB_TOKEN:
            subprocess.run([
                "git", "push",
                f"https://x-access-token:{GITHUB_TOKEN}@github.com/Xipingo/NJUBSwatcher.git",
                "main"
            ], check=True)
        else:
            subprocess.run(["git", "push"], check=True)
        print("✅ 自动 git push 成功")
    except subprocess.CalledProcessError as e:
        print("❌ git 操作失败:", e)

# --------------------------
# 辅助
# --------------------------
def summarize_diffs(diffs: Dict[str, Dict]) -> Tuple[bool, str]:
    lines = []
    has_change = False
    for mod, info in diffs.items():
        added, removed, changed = info['added'], info['removed'], info['changed']
        if not (added or removed or changed):
            continue
        has_change = True
        lines.append(f"模块: {mod}")
        if added:
            lines.append(f"  新增 {len(added)} 条：")
            for it in added[:5]:
                lines.append(f"    + {it['title']} ({it.get('date','')})\n        {it['href']}")
        if removed:
            lines.append(f"  删除 {len(removed)} 条")
        if changed:
            lines.append(f"  变更 {len(changed)} 条")
        lines.append("")
    return has_change, "\n".join(lines)

# --------------------------
# 主流程
# --------------------------
def main():
    try:
        new_snapshot = fetch_all_modules()
    except Exception as e:
        print("抓取失败：", e)
        return

    old_snapshot = load_snapshot(SNAPSHOT_FILE)
    diffs = diff_snapshots(old_snapshot, new_snapshot)
    changed, summary = summarize_diffs(diffs)

    if changed:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        subject = f"[NUBS] 公告更新 ({ts})"
        body = f"{subject}\n\n{summary}"
        print(body)
        save_snapshot(SNAPSHOT_FILE, new_snapshot)
        git_commit_and_push(SNAPSHOT_FILE)
        send_email(subject, body)
    else:
        if not old_snapshot:
            save_snapshot(SNAPSHOT_FILE, new_snapshot)
            git_commit_and_push(SNAPSHOT_FILE)
            print("首次抓取并保存快照。")
        else:
            print("未检测到变化。")

if __name__ == "__main__":
    main()
