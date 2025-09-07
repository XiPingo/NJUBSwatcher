#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
南京大学商学院官网更新检测脚本
- 支持模块：最新动态、通知公告、活动预告、招标采购、商院视点、公示信息
- 每个模块可独立订阅邮件
"""

import os
import re
import json
import time
import smtplib
import requests
from email.mime.text import MIMEText
from email.header import Header
from bs4 import BeautifulSoup
from subprocess import run
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --------------------------
# 配置区
# --------------------------
URL = "https://nubs.nju.edu.cn/main.htm"
MODULE_IDS = {
    "latest_updates": "wp_news_w46",   # 最新动态
    "notices": "wp_news_w47",          # 通知公告
    "events": "wp_news_w48",           # 活动预告
    "procurement": "wp_news_w100",     # 招标采购
    "viewpoints": "wp_news_w49",       # 商院视点
    "announcements": "wp_news_w110",   # 公示信息
}

SNAPSHOT_FILE = "nubs_snapshot.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"

# 邮件配置（全局发件人）
SMTP_HOST = "smtp.qq.com"
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()

# 模块订阅配置（每个模块可独立订阅）
MODULE_SUBSCRIPTIONS = {
    "latest_updates": os.getenv("EMAIL_TO_UPDATES", ""),
    "notices": os.getenv("EMAIL_TO_NOTICES", ""),
    "events": os.getenv("EMAIL_TO_EVENTS", ""),
    "procurement": os.getenv("EMAIL_TO_PROCUREMENT", ""),
    "viewpoints": os.getenv("EMAIL_TO_VIEWPOINTS", ""),
    "announcements": os.getenv("EMAIL_TO_ANNOUNCEMENTS", ""),
}

# 转换为 {模块: [邮箱列表]}
for k, v in MODULE_SUBSCRIPTIONS.items():
    MODULE_SUBSCRIPTIONS[k] = [addr.strip() for addr in v.split(",") if addr.strip()]

# --------------------------
# 工具函数
# --------------------------

def fetch_module(module_id):
    """抓取单个模块的文章列表"""
    resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=30, verify=False)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    module = soup.find("div", id=module_id)
    if not module:
        return []
    links = module.find_all("a", href=True)
    results = []
    for a in links:
        title = a.get_text(strip=True)
        href = a["href"]
        if not href.startswith("http"):
            href = "http://nubs.nju.edu.cn/" + href.lstrip("/")
        if title:
            results.append({"title": title, "url": href})
    return results

def fetch_all_modules():
    """抓取所有模块"""
    all_data = {}
    for name, module_id in MODULE_IDS.items():
        all_data[name] = fetch_module(module_id)
    return all_data

def load_snapshot(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_snapshot(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def diff_snapshots(old, new):
    diffs = {}
    for module in MODULE_IDS:
        old_items = {item["url"]: item for item in old.get(module, [])}
        new_items = {item["url"]: item for item in new.get(module, [])}
        added = [v for k, v in new_items.items() if k not in old_items]
        removed = [v for k, v in old_items.items() if k not in new_items]
        changed = [
            {"old": old_items[k], "new": new_items[k]}
            for k in new_items
            if k in old_items and old_items[k]["title"] != new_items[k]["title"]
        ]
        diffs[module] = {"added": added, "removed": removed, "changed": changed}
    return diffs

def summarize_diffs(diffs):
    lines = []
    for module, info in diffs.items():
        added, removed, changed = info["added"], info["removed"], info["changed"]
        if not (added or removed or changed):
            continue
        lines.append(f"\n### {module} ###")
        for item in added:
            lines.append(f"+ {item['title']} {item['url']}")
        for item in removed:
            lines.append(f"- {item['title']} {item['url']}")
        for item in changed:
            lines.append(f"* {item['old']['title']} -> {item['new']['title']} {item['new']['url']}")
    return "\n".join(lines)

def send_email(module: str, subject: str, body: str):
    recipients = MODULE_SUBSCRIPTIONS.get(module, [])
    if not (recipients and SMTP_USER and SMTP_PASS):
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = Header(EMAIL_FROM)
    msg["To"] = Header(", ".join(recipients))
    msg["Subject"] = Header(subject, "utf-8")
    s = smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=30)
    s.login(SMTP_USER, SMTP_PASS)
    s.sendmail(EMAIL_FROM, recipients, msg.as_string())
    s.quit()

def git_commit_and_push(filepath):
    try:
        run(["git", "config", "--global", "user.email", "actions@github.com"])
        run(["git", "config", "--global", "user.name", "GitHub Actions"])
        run(["git", "add", filepath], check=True)
        run(["git", "commit", "-m", f"update snapshot {time.strftime('%Y-%m-%d %H:%M:%S')}"], check=True)
        run(["git", "push", "origin", "main"], check=True)
    except Exception as e:
        print("Git 推送失败：", e)

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

    has_change = False
    for mod, info in diffs.items():
        added, removed, changed = info['added'], info['removed'], info['changed']
        if not (added or removed or changed):
            continue
        has_change = True
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        subject = f"[NUBS] {mod} 更新 ({ts})"
        body = f"{subject}\n\n{summarize_diffs({mod: info})}"
        print(body)
        try:
            send_email(mod, subject, body)
        except Exception as e:
            print(f"模块 {mod} 邮件发送失败：", e)

    if has_change:
        save_snapshot(SNAPSHOT_FILE, new_snapshot)
        git_commit_and_push(SNAPSHOT_FILE)
    else:
        if not old_snapshot:
            save_snapshot(SNAPSHOT_FILE, new_snapshot)
            git_commit_and_push(SNAPSHOT_FILE)
            print("首次抓取并保存快照。")
        else:
            print("未检测到变化。")

if __name__ == "__main__":
    main()
