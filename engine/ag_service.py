"""
AI图谱后端服务（ag_service.py）
职责：
  - 管理图谱行情缓存（每20分钟从新浪拉取实时报价）
  - 管理历史K线缓存（每6小时从QMT拉取过去20日日K线）
  - 提供读取接口供 HTTP Handler 使用

设计原则（AI-Native）：
  - 单文件、职责单一：只负责"图谱数据源"，不含任何 HTTP/HTML 逻辑
  - 无副作用导入：通过 start_background_tasks() 显式启动后台线程
  - 降级友好：QMT 不可用时历史缓存返回空字典，行情缓存返回空字典
  - 线程安全：所有缓存读写均通过 threading.Lock 保护
"""
import json
import os
import re
import subprocess
import threading
import time

import requests

# ── 路径常量 ──────────────────────────────────────────────
_BASE_DIR     = os.path.dirname(__file__)
AI_GRAPH_FILE = os.path.join(_BASE_DIR, "..", "data", "ai_graph.json")

# ── 新浪行情 ──────────────────────────────────────────────
_SINA_URL = "https://hq.sinajs.cn/list={codes}"
_SINA_HDR = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
_AG_QUOTE_INTERVAL = 20 * 60  # 20分钟

# ── 实时行情缓存  {stockId: (pctChg, price)} ──────────────
_quote_cache: dict = {}
_quote_lock        = threading.Lock()
_quote_ts: float   = 0.0

# ── 历史K线缓存  {date: {stockId: pct}} ──────────────────
_history_cache: dict = {}
_history_lock        = threading.Lock()


# ──────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────

def _sina_code(stock_id: str) -> str:
    """600xxx→sh600xxx，000/002/300xxx→sz，688xxx→sh"""
    s = stock_id.strip()
    return ("sh" if s.startswith("6") else "sz") + s


def _graph_a_share_ids() -> list[str]:
    """从 ai_graph.json 收集所有 A 股 stockId（stock节点 + L2精选龙头）"""
    try:
        with open(AI_GRAPH_FILE, encoding="utf-8") as f:
            g = json.load(f)
    except Exception:
        return []

    ids: list[str] = []
    for n in g.get("nodes", []):
        if n.get("type") == "stock" and n.get("stockId"):
            sid = n["stockId"]
            if sid not in ids:
                ids.append(sid)
    for n in g.get("nodes", []):
        if n.get("type") == "l2":
            for s in (n.get("curatedStocks") or []):
                if s.get("market") == "A" and s.get("code") and s["code"] not in ids:
                    ids.append(s["code"])
    return ids


# ──────────────────────────────────────────────────────────
# 实时行情
# ──────────────────────────────────────────────────────────

def fetch_quotes(stock_ids: list[str]) -> dict:
    """批量查新浪行情，返回 {stockId: (pctChg, price)}"""
    if not stock_ids:
        return {}
    codes = ",".join(_sina_code(s) for s in stock_ids)
    try:
        r = requests.get(_SINA_URL.format(codes=codes), headers=_SINA_HDR, timeout=6)
        r.encoding = "gbk"
        result = {}
        for line in r.text.splitlines():
            m = re.match(r'var hq_str_(\w+)="([^"]*)"', line)
            if not m:
                continue
            parts = m.group(2).split(",")
            if len(parts) < 10:
                continue
            code = m.group(1)[2:]
            try:
                close_prev = float(parts[2])
                close_now  = float(parts[3])
                if close_prev > 0:
                    pct = (close_now - close_prev) / close_prev * 100
                    result[code] = (pct, close_now)
            except (ValueError, IndexError):
                pass
        return result
    except Exception:
        return {}


def get_quote_cache() -> dict:
    """线程安全读取实时行情缓存快照"""
    with _quote_lock:
        return dict(_quote_cache)


def _refresh_quotes_loop():
    global _quote_cache, _quote_ts
    while True:
        try:
            ids = _graph_a_share_ids()
            if ids:
                qt = fetch_quotes(ids)
                with _quote_lock:
                    _quote_cache = qt
                    _quote_ts = time.time()
        except Exception:
            pass
        time.sleep(_AG_QUOTE_INTERVAL)


# ──────────────────────────────────────────────────────────
# 历史K线缓存（依赖 QMT，不可用时静默降级）
# ──────────────────────────────────────────────────────────

def _build_history_from_kline(market_session) -> dict:
    """
    从 QMT 批量拉取图谱所有 A 股过去 25 日日 K 线，
    构建并返回 {date_str: {stockId: pct}}（最近20个交易日）
    """
    ids = _graph_a_share_ids()
    if not ids:
        return {}

    try:
        from engine.txcfgl.market import fetch_stock_daily as _fsd
    except ImportError:
        return {}

    hist: dict = {}
    for sid in ids:
        try:
            raw    = _fsd(market_session, sid, days=25)
            fields = raw.get("fields") or []
            items  = raw.get("items")  or []
            try:
                di = fields.index("trade_date")
                pi = fields.index("pct_chg")
            except ValueError:
                continue
            for row in items:
                date_raw = str(row[di])
                date_str = (date_raw[:4] + "-" + date_raw[4:6] + "-" + date_raw[6:]
                            if len(date_raw) == 8 else date_raw[:10])
                pct = row[pi]
                if pct is None:
                    continue
                try:
                    pct = float(pct)
                except (TypeError, ValueError):
                    continue
                hist.setdefault(date_str, {})[sid] = pct
        except Exception:
            continue

    keys = sorted(hist.keys(), reverse=True)[:20]
    return {k: hist[k] for k in keys}


def _build_history_from_clickhouse() -> dict:
    """Build recent daily pct history from local qmt_tick.bars_daily_a."""
    ids = _graph_a_share_ids()
    if not ids:
        return {}

    quoted = ",".join("'" + sid.replace("'", "") + "'" for sid in ids)
    query = f"""
    SELECT
        trade_date,
        code,
        if(prev_close > 0, (close - prev_close) / prev_close * 100, 0) AS pct
    FROM
    (
        SELECT
            trade_date,
            code,
            close,
            lagInFrame(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close
        FROM qmt_tick.bars_daily_a
        WHERE code IN ({quoted})
          AND trade_date >= today() - 90
        ORDER BY code, trade_date
    )
    WHERE prev_close > 0
    ORDER BY trade_date DESC, code
    FORMAT TabSeparated
    """
    try:
        proc = subprocess.run(
            ["clickhouse-client", "--query", query],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}

    hist: dict = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        date_str, sid, pct_raw = parts
        try:
            pct = float(pct_raw)
        except (TypeError, ValueError):
            continue
        hist.setdefault(date_str, {})[sid] = pct

    keys = sorted(hist.keys(), reverse=True)[:20]
    return {k: hist[k] for k in keys}


def get_history_cache() -> dict:
    """线程安全读取历史K线缓存快照（深拷贝，内层dict独立）"""
    with _history_lock:
        return {date: dict(stocks) for date, stocks in _history_cache.items()}


def _refresh_history_loop(get_session_fn):
    global _history_cache
    while True:
        try:
            # Fill from the local QMT data layer first so /api/ag-history is
            # available shortly after process start and does not depend on an
            # external token/API.
            result = _build_history_from_clickhouse()
            if result:
                with _history_lock:
                    _history_cache = result

            # External kline data is optional enhancement; failures keep the
            # durable local cache intact.
            ms = get_session_fn()
            if ms:
                ext = _build_history_from_kline(ms)
                if ext:
                    with _history_lock:
                        _history_cache = ext
        except Exception:
            pass
        time.sleep(6 * 3600)  # 6??????


def start_background_tasks(get_session_fn):
    """
    启动两个后台守护线程：
      - 实时行情刷新（每20分钟）
      - 历史K线刷新（每6小时，启动时立即执行一次）

    参数：
      get_session_fn: 无参可调用，返回 QMT market session 或 None
    """
    threading.Thread(target=_refresh_quotes_loop, daemon=True, name="ag-quotes").start()
    threading.Thread(target=_refresh_history_loop, args=(get_session_fn,),
                     daemon=True, name="ag-history").start()
