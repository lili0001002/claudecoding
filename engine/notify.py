"""
钉钉推送模块
用法：
  from engine.notify import send_dingtalk
  send_dingtalk("标题", "内容")

凭证加载优先级（高 → 低）：
  1. 环境变量  DINGTALK_WEBHOOK / DINGTALK_SECRET / PUBLIC_URL
  2. 项目根目录 notify_secrets.json
"""
import requests
import json
import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import re
from datetime import datetime


def _load_secrets() -> dict:
    """按优先级加载钉钉凭证（环境变量 > notify_secrets.json）"""
    secrets: dict = {}
    _secrets_path = os.path.join(os.path.dirname(__file__), "..", "notify_secrets.json")
    if os.path.exists(_secrets_path):
        try:
            with open(_secrets_path, encoding="utf-8") as _f:
                secrets = json.load(_f)
        except Exception as _e:
            print(f"[notify] 读取 notify_secrets.json 失败: {_e}")
    secrets["webhook"]    = os.environ.get("DINGTALK_WEBHOOK",  secrets.get("webhook", ""))
    secrets["secret"]     = os.environ.get("DINGTALK_SECRET",   secrets.get("secret", ""))
    secrets["public_url"] = os.environ.get("PUBLIC_URL",        secrets.get("public_url", "http://localhost:8888"))
    return secrets


_SECRETS = _load_secrets()
WEBHOOK    = _SECRETS["webhook"]
SECRET     = _SECRETS["secret"]
PUBLIC_URL = _SECRETS["public_url"]


def _get_public_url():
    """返回当前公网访问地址（优先读环境变量 PUBLIC_URL）"""
    return os.environ.get("PUBLIC_URL", PUBLIC_URL)


def _sign_url():
    if not SECRET:
        return WEBHOOK
    ts = str(round(time.time() * 1000))
    s  = f"{ts}\n{SECRET}"
    sign = base64.b64encode(
        hmac.new(SECRET.encode("utf-8"), s.encode("utf-8"), digestmod=hashlib.sha256).digest()
    ).decode("utf-8")
    return f"{WEBHOOK}&timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"


def send_dingtalk(title: str, content: str) -> bool:
    """发送 Markdown 消息到钉钉群"""
    url  = _sign_url()
    body = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text":  content,
        },
        "at": {"isAtAll": False},
    }
    try:
        r = requests.post(url, json=body, timeout=10)
        result = r.json()
        if result.get("errcode") == 0:
            return True
        else:
            print(f"[钉钉] 推送失败: {result}")
            return False
    except Exception as e:
        print(f"[钉钉] 推送异常: {e}")
        return False


def _pct_str(pct):
    try:
        v = float(pct)
        return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _top_stocks(stocks, n=8):
    """按 importance 排序，取前 n 只，返回 '名称 代码' 列表"""
    sorted_s = sorted(
        [s for s in stocks if s.get("stockName") and s.get("stockId")],
        key=lambda x: -(x.get("importance") or 0)
    )
    return sorted_s[:n]


def send_alert(title: str, detail: str) -> bool:
    """发送异常告警到钉钉群"""
    now  = datetime.now().strftime("%m-%d %H:%M:%S")
    body = f"## [告警] {title}\n\n> 时间：{now}\n\n```\n{detail[:800]}\n```"
    return send_dingtalk(f"[告警] {title}", body)


def format_summary_message(new_count: int, update_count: int,
                            total: int, total_stocks: int,
                            new_records: list) -> tuple:
    now   = datetime.now().strftime("%m-%d %H:%M")
    title = f"久赢恒丰 {now} 新增{new_count}条"

    lines = [
        f"## 久赢恒丰 {now}",
        f"新增 **{new_count}** 条 / 更新 {update_count} 条",
        f"",
    ]

    if new_records:
        for r in new_records:
            tag    = "[新题材]" if r.get("type") == 2 else "[驱动事件]"
            name   = r.get("subjectName", "未知")
            pct    = _pct_str(r.get("pctChg"))
            stocks = r.get("stocks", [])
            top    = _top_stocks(stocks, 6)
            names  = " / ".join(s["stockName"] for s in top)
            more   = f"+{len(stocks)-6}只" if len(stocks) > 6 else ""

            short_name = name if len(name) <= 24 else name[:24] + "…"

            lines.append(f"**{tag} {short_name}** {pct} 共{len(stocks)}只")
            lines.append(f"{names} {more}")
            lines.append("")
    else:
        lines.append(f"无新增，已更新 {update_count} 条历史数据")

    public_url = _get_public_url()
    lines.append(f"[查看全部详情]({public_url})")

    return title, "\n".join(lines)


def format_record_message(record: dict) -> tuple:
    """兼容旧调用，实际不再单独推送每条"""
    return format_summary_message(1, 0, 1, len(record.get("stocks", [])), [record])
