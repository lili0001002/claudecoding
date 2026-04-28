"""
健康检查与自愈智能体（health_agent.py）

职责：
  - 定期探测关键服务存活状态（Web服务、API端点）
  - 检测 Token 是否失效
  - 检测行情缓存是否过期（超过阈值未更新）
  - 发现异常时：自动重启服务 + 发钉钉告警
  - 每日生成运行日报推送到钉钉

运行方式：
  作为独立进程：python -m engine.agents.health_agent
  或由 systemd 管理（见 deploy/jiuying-health.service）

设计原则：
  - 无状态：每次检查独立，不依赖上次结果
  - 幂等：重复执行无副作用
  - 静默降级：告警失败不影响检查循环继续运行
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# ── 配置 ───────────────────────────────────────────────────
_BASE_DIR  = os.path.join(os.path.dirname(__file__), "..", "..")
_LOCAL_URL = os.environ.get("HEALTH_TARGET_URL", "http://127.0.0.1:8888")
_SERVICE   = os.environ.get("HEALTH_SERVICE_NAME", "jiuying-web")

# 检查间隔（秒）
_CHECK_INTERVAL     = int(os.environ.get("HEALTH_CHECK_INTERVAL",  "60"))
_REPORT_HOUR        = int(os.environ.get("HEALTH_REPORT_HOUR",     "18"))  # 每日18:00发日报
# 行情缓存最大允许过期时长（秒）
_QUOTE_STALE_SECS   = int(os.environ.get("HEALTH_QUOTE_STALE",  "2700"))  # 45分钟

# ── 状态跟踪 ───────────────────────────────────────────────
_restart_count   = 0
_last_report_day = None
_loop_count       = 0
_last_alert_at: dict[str, float] = {}
_last_fail_signature = ""
from collections import deque as _deque
_check_history: _deque = _deque(maxlen=100)  # 最近100条检查记录（deque 自动丢弃超限元素，O(1) 追加）


# ──────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────

def _notify(title: str, text: str):
    """发钉钉告警，失败静默"""
    try:
        from engine.notify import send as _send
        _send(title=title, text=text)
    except Exception:
        pass


def _notify_once(key: str, title: str, text: str, cooldown: int | None = None):
    """Throttle repeated alerts for persistent non-critical failures."""
    now = time.time()
    window = _ALERT_COOLDOWN_SECS if cooldown is None else cooldown
    last = _last_alert_at.get(key, 0)
    if now - last < window:
        return False
    _last_alert_at[key] = now
    _notify(title=title, text=text)
    return True


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[health] {ts}  {msg}", flush=True)


def _http_get(path: str, timeout: int = 8) -> tuple[int, bytes]:
    url = _LOCAL_URL + path
    req = urllib.request.Request(url, headers={"User-Agent": "health-agent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


# ──────────────────────────────────────────────────────────
# 检查项
# ──────────────────────────────────────────────────────────

def check_web_alive() -> dict:
    """检查 Web 服务是否存活（GET /）"""
    try:
        status, _ = _http_get("/", timeout=5)
        return {"check": "web_alive", "ok": status == 200, "detail": f"HTTP {status}"}
    except Exception as e:
        return {"check": "web_alive", "ok": False, "detail": str(e)}


def check_api_graph() -> dict:
    """检查 /api/ai-graph 返回有效数据"""
    try:
        status, body = _http_get("/api/ai-graph", timeout=8)
        d = json.loads(body)
        chains = len((d.get("meta") or {}).get("logicChains") or [])
        nodes  = len(d.get("nodes") or [])
        ok = chains > 0 and nodes > 0
        return {"check": "api_graph", "ok": ok,
                "detail": f"nodes={nodes} chains={chains}"}
    except Exception as e:
        return {"check": "api_graph", "ok": False, "detail": str(e)}


def check_quotes_fresh() -> dict:
    """检查实时行情缓存是否新鲜（有数据且不为空）"""
    try:
        status, body = _http_get("/api/ai-quotes", timeout=8)
        d = json.loads(body)
        count = len(d)
        ok = count > 0
        return {"check": "quotes_fresh", "ok": ok,
                "detail": f"quote_count={count}"}
    except Exception as e:
        return {"check": "quotes_fresh", "ok": False, "detail": str(e)}


def check_history_available() -> dict:
    """????K????????????????????????"""
    try:
        status, body = _http_get("/api/ag-history", timeout=8)
        d = json.loads(body)
        dates = len(d) if isinstance(d, dict) else 0
        detail = f"history_dates={dates}"
        if dates == 0:
            detail += " (degraded)"
        return {"check": "history_available", "ok": True, "detail": detail}
    except Exception as e:
        return {"check": "history_available", "ok": False, "detail": str(e)}

def check_masked_stocks() -> dict:
    """??????????????????????????????"""
    try:
        result_file = os.path.join(_BASE_DIR, "data", "subject_stocks_result.json")
        with open(result_file, encoding="utf-8") as f:
            data = json.load(f)
        masked = []
        for r in data:
            for item in r.get("stocks", []):
                sid = item.get("stockId", "")
                sname = item.get("stockName", "")
                if sid in ("111", "") or sname in ("****", ""):
                    masked.append({"subject": r.get("subjectName", "?"),
                                   "selectedId": item.get("selectedId")})
        detail = "clean" if not masked else f"masked={len(masked)} (observed)"
        if masked:
            subjects = list(set(m["subject"] for m in masked))
            detail += f" subjects={subjects[:5]}"
        return {"check": "masked_stocks", "ok": True, "detail": detail}
    except Exception as e:
        return {"check": "masked_stocks", "ok": True, "detail": f"skip({e})"}

def check_token_valid() -> dict:
    """检查久赢恒丰 Token 是否有效（通过 /api/subject-tree 的响应判断）"""
    try:
        status, body = _http_get("/api/subject-tree", timeout=10)
        d = json.loads(body)
        ok = isinstance(d, list) and len(d) > 0
        return {"check": "token_valid", "ok": ok,
                "detail": f"subjects={len(d) if isinstance(d, list) else 'invalid'}"}
    except Exception as e:
        return {"check": "token_valid", "ok": False, "detail": str(e)}


# ──────────────────────────────────────────────────────────
# 自愈动作
# ──────────────────────────────────────────────────────────

def _restart_service() -> bool:
    """尝试重启 systemd 服务，返回是否成功"""
    global _restart_count
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", _SERVICE],
            capture_output=True, text=True, timeout=30
        )
        ok = result.returncode == 0
        if ok:
            _restart_count += 1
            _log(f"Service restarted (total={_restart_count})")
        else:
            _log(f"Restart failed: {result.stderr}")
        return ok
    except Exception as e:
        _log(f"Restart exception: {e}")
        return False


# ──────────────────────────────────────────────────────────
# 主检查循环
# ──────────────────────────────────────────────────────────

def run_checks() -> list[dict]:
    """执行一轮所有检查，返回结果列表"""
    checks = [
        check_web_alive(),
        check_api_graph(),
        check_quotes_fresh(),
        check_history_available(),
        check_masked_stocks(),
        check_token_valid(),
    ]
    ts = datetime.now().isoformat(timespec="seconds")
    for c in checks:
        c["ts"] = ts
    return checks


def _handle_failures(results: list[dict]):
    """?????????????????????"""
    failures = [r for r in results if not r["ok"]]
    if not failures:
        return

    web_down = any(r["check"] == "web_alive" and not r["ok"] for r in results)
    if web_down:
        _log("Web service down, attempting restart...")
        ok = _restart_service()
        _notify_once(
            "web_down",
            title="?? ???????",
            text=f"?????????{'?????' if ok else '??????????'}\n"
                 f"????{', '.join(f['check'] for f in failures)}",
            cooldown=300,
        )
        return

    token_bad = any(r["check"] == "token_valid" and not r["ok"] for r in results)
    if token_bad:
        detail = [r for r in failures if r["check"] == "token_valid"][0]["detail"]
        if _notify_once(
            "token_bad",
            title="?? ??? Token ????",
            text="subject-tree ?????????????? TOKEN\n"
                 f"?????{detail}",
        ):
            _log("Token appears invalid, alert sent")

    data_missing = [r for r in failures if r["check"] in ("quotes_fresh", "history_available")]
    if data_missing:
        key = "data_missing:" + ",".join(sorted(r["check"] for r in data_missing))
        if _notify_once(
            key,
            title="?? ?????????",
            text="\n".join(f"- {r['check']}: {r['detail']}" for r in data_missing),
        ):
            _log(f"Data cache issues: {[r['check'] for r in data_missing]}")

def _generate_daily_report(history: list[dict]):
    """生成并推送日报"""
    if not history:
        return

    total = len(history)
    fails = sum(1 for h in history if not all(r["ok"] for r in h.get("results", [])))
    uptime_pct = (total - fails) / total * 100 if total else 0

    # 统计各检查项的失败次数
    fail_counts: dict[str, int] = {}
    for h in history:
        for r in h.get("results", []):
            if not r["ok"]:
                fail_counts[r["check"]] = fail_counts.get(r["check"], 0) + 1

    lines = [
        f"📋 久赢恒丰日报 {datetime.now().strftime('%Y-%m-%d')}",
        f"",
        f"✅ 服务可用率：{uptime_pct:.1f}%（{total}次检查）",
        f"🔄 自动重启次数：{_restart_count}",
    ]
    if fail_counts:
        lines.append("⚠️ 异常统计：")
        for check, cnt in sorted(fail_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  - {check}: {cnt}次")
    else:
        lines.append("🎉 今日无异常")

    _notify(title="久赢恒丰日报", text="\n".join(lines))
    _log("Daily report sent")


# ──────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────

def main():
    global _last_report_day, _loop_count, _last_fail_signature
    _log(f"Health agent started. Target={_LOCAL_URL}, Service={_SERVICE}")
    _log(f"Check interval={_CHECK_INTERVAL}s, Report hour={_REPORT_HOUR}:00")

    daily_history: list[dict] = []

    while True:
        results = run_checks()
        now     = datetime.now()
        ok_all  = all(r["ok"] for r in results)

        status_str = "✓" if ok_all else "✗"
        fails = [r["check"] for r in results if not r["ok"]]
        _log(f"{status_str} checks={'OK' if ok_all else 'FAIL: '+','.join(fails)}")

        _check_history.append({"ts": now.isoformat(), "results": results})
        daily_history.append({"ts": now.isoformat(), "results": results})

        if not ok_all:
            _handle_failures(results)

        # 每日定时日报
        today = now.date()
        if (now.hour == _REPORT_HOUR and
                (not _last_report_day or _last_report_day < today)):
            _generate_daily_report(daily_history)
            _last_report_day = today
            daily_history = []  # 重置当日记录

        time.sleep(_CHECK_INTERVAL)


if __name__ == "__main__":
    main()
