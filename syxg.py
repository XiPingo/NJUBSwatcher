#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
syxg.py
监控 http://syxg.nju.edu.cn/ 四个模块（通知公告、就业招聘、规则流程、风采展示）
当模块内容发生变化时输出变化摘要并发送通知。
保存上次快照到 snapshot.json。
（坏消息，这个网站只能校内ip访问，因此无法抓取）
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import hashlib
import os
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from typing import Dict, List, Tuple

# --------------------------
# 配置
# --------------------------
URL = "http://syxg.nju.edu.cn/"
SNAPSHOT_FILE = "syxg_snapshot.json"
USER_AGENT = "Mozilla/5.0 (compatible; syxg-watcher/1.0; +https://github.com/)"

MODULE_NAMES = ["通知公告", "就业招聘", "规则流程", "风采展示"]

# 通知选项
ENABLE_EMAIL = True

# 邮件配置（全部从环境变量读，避免泄露）
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.environ.get("EMAIL_TO", "").split(",")  # 多个收件人用逗号分隔

# --------------------------
# 抓取与解析
# --------------------------
def get_page(url: str, timeout: int = 15) -> str:
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text

def parse_modules(html: str) -> Dict[str, List[Dict]]:
    soup = BeautifulSoup(html, "html.parser")
    results = {}

    for box in soup.select("div.two div.box"):
        title_div = box.find("div", class_="box-title")
        if not title_div:
            continue
        module_name = title_div.get_text(strip=True).replace("+more", "").strip()
        if module_name not in MODULE_NAMES:
            continue

        items = []
        for li in box.select("div.box-content ul li"):
            date = li.find("span").get_text(strip=True) if li.find("span") else ""
            a_tags = li.find_all("a")
            if not a_tags:
                continue
            link_a = a_tags[-1]
            title = link_a.get("title") or link_a.get_text(strip=True)
            href = link_a.get("href", "").strip()
            if href.startswith("/"):
                href = requests.compat.urljoin(URL, href)

            key_str = f"{title}||{href}||{date}"
            item_hash = hashlib.sha256(key_str.encode("utf-8")).hexdigest()
            items.append({"title": title, "href": href, "date": date, "hash": item_hash})

        results[module_name] = items
    return results

# --------------------------
# 快照工具
# --------------------------
def load_snapshot(path: str) -> Dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_snapshot(path: str, data: Dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def diff_snapshots(old: Dict, new: Dict) -> Dict[str, Dict]:
    diffs = {}
    for mod in MODULE_NAMES:
        old_list = old.get(mod, [])
        new_list = new.get(mod, [])
        old_map = {it['hash']: it for it in old_list}
        new_map = {it['hash']: it for it in new_list}
        added = [new_map[h] for h in (set(new_map) - set(old_map))]
        removed = [old_map[h] for h in (set(old_map) - set(new_map))]
        changed = []
        old_by_href = {it['href']: it for it in old_list}
        for it in new_list:
            oh = old_by_href.get(it['href'])
            if oh and (oh['title'] != it['title'] or oh.get('date') != it.get('date')):
                changed.append((oh, it))
        diffs[mod] = {"added": added, "removed": removed, "changed": changed}
    return diffs

# --------------------------
# 通知函数
# --------------------------
def send_email(subject: str, body: str):
    if not ENABLE_EMAIL or not SMTP_USER or not SMTP_PASS or not EMAIL_TO:
        print("⚠️ 邮件配置不完整，跳过邮件发送")
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = Header(EMAIL_FROM)
    msg["To"] = Header(", ".join(EMAIL_TO))
    msg["Subject"] = Header(subject, "utf-8")
    s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    s.login(SMTP_USER, SMTP_PASS)
    s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    s.quit()
    print("📧 邮件发送成功")

# --------------------------
# 辅助
# --------------------------
def summarize_diffs(diffs: Dict[str, Dict]) -> Tuple[bool, str]:
    lines = []
    has_change = False
    for mod, info in diffs.items():
        added = info['added']
        removed = info['removed']
        changed = info['changed']
        if not (added or removed or changed):
            continue
        has_change = True
        lines.append(f"模块: {mod}")
        if added:
            lines.append(f"  新增 {len(added)} 条：")
            for it in added:
                lines.append(f"    + {it['title']} ({it['date']})\n        {it['href']}")
        if removed:
            lines.append(f"  删除 {len(removed)} 条：")
            for it in removed:
                lines.append(f"    - {it['title']} ({it['date']})")
        if changed:
            lines.append(f"  变更 {len(changed)} 条：")
            for old, new in changed:
                lines.append(f"    * {old['title']} -> {new['title']} ({old['date']} -> {new['date']})")
        lines.append("")
    return has_change, "\n".join(lines)

# --------------------------
# 主流程
# --------------------------
def main():
    try:
        new_snapshot = parse_modules(get_page(URL))
    except Exception as e:
        print("抓取失败：", e)
        return

    old_snapshot = load_snapshot(SNAPSHOT_FILE)
    diffs = diff_snapshots(old_snapshot, new_snapshot)
    changed, summary = summarize_diffs(diffs)
    if changed:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        subject = f"[SYXG] 公告更新检测到 ({ts})"
        body = f"{subject}\n\n{summary}"
        print(body)
        save_snapshot(SNAPSHOT_FILE, new_snapshot)
        try:
            send_email(subject, body)
        except Exception as e:
            print("邮件发送失败：", e)
    else:
        if not old_snapshot:
            save_snapshot(SNAPSHOT_FILE, new_snapshot)
            print("首次抓取并保存快照。")
        else:
            print("未检测到变化。")

if __name__ == "__main__":
    main()
