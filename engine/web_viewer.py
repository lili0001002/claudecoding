"""
轻量 HTTP 服务，提供题材详情查询页面
运行：python -m engine.web_viewer
访问：http://10.0.0.2:8888/

架构说明（AI-Native 设计）：
  本文件职责：HTTP 路由 + HTML 页面渲染
  engine/ag_service.py  : AI图谱行情/历史K线缓存（后台线程）
  engine/static/*.css/js: 前端样式与逻辑（按模块拆分，独立可维护）

迁移清单：
  1. 复制整个项目目录
  2. 安装依赖：pip install -r requirements.txt
  3. 配置 TXCFGL_TOKEN（启动参数 --token 或环境变量）
  4. 启动：python -m engine.web_viewer --token <token>
"""
import gzip
import json
import os
import re
import requests
import socketserver as _socketserver
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime


class _ThreadingHTTPServer(_socketserver.ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器：每个请求在独立线程处理，避免慢请求阻塞整个服务"""
    daemon_threads = True  # 子线程随主进程退出，无需手动 join

# market 模块（可选，失败时降级）
try:
    from engine.txcfgl.market import (
        fetch_index_snapshot, fetch_market_amount, fetch_up_down,
        fetch_stock_daily, fetch_stock_main_business,
        fetch_subject_lead, fetch_index_history,
        fetch_stock_subject_tree,
    )
    _MARKET_OK = True
except ImportError:
    _MARKET_OK = False

TXCFGL_TOKEN = None  # 由启动参数或环境变量注入


def _market_session():
    from engine.txcfgl.market import _session
    token = TXCFGL_TOKEN or os.environ.get("TXCFGL_TOKEN", "")
    return _session(token) if token else None

# 行情工具函数委托给 ag_service（保留 fetch_quotes 供页面级别的上涨/下跌统计使用）
from engine.ag_service import fetch_quotes

DATA_FILE        = os.path.join(os.path.dirname(__file__), "..", "data", "subject_stocks_result.json")
FULL_CACHE_FILE  = os.path.join(os.path.dirname(__file__), "..", "data", "subject_stock_full.json")
KUAKE_EVENTS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "kuake_events.json")
AI_GRAPH_FILE    = os.path.join(os.path.dirname(__file__), "..", "data", "ai_graph.json")
PORT = 8888

# QMT 推送过来的实时行情缓存 {stockId: {pctChg, price, time}}（POST /api/ai-quotes-push）
import threading as _threading
_qmt_quotes: dict = {}
_qmt_quotes_lock  = _threading.Lock()


def _build_ag_history_from_kline():  # 已迁移至 ag_service
    pass


def _refresh_ag_history_cache():  # 已迁移，保留空壳
    pass

def _refresh_ag_quote_cache():  # 已迁移，保留空壳
    pass

# selectedId -> {stockId, stockName} 索引（从完整缓存建立，按 mtime 热重载）
_sel_id_index: dict = {}
_sel_id_mtime: float = 0.0

def _load_sel_id_index():
    global _sel_id_index, _sel_id_mtime
    try:
        mt = os.path.getmtime(FULL_CACHE_FILE)
    except OSError:
        return
    if _sel_id_index and mt == _sel_id_mtime:
        return
    try:
        with open(FULL_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        new_idx = {}
        for item in data:
            for s in (item.get("stocks") or []):
                sel_id = s.get("selectedId")
                sid    = s.get("stockId",   "")
                sname  = s.get("stockName", "")
                if sel_id and sid and sid != "111" and sname and sname != "****":
                    new_idx[int(sel_id)] = {"stockId": sid, "stockName": sname}
        _sel_id_index = new_idx
        _sel_id_mtime = mt
    except Exception:
        pass


def _unmask_subject(subject_id):
    """
    对指定题材直接调夸克 /ticaitupu 补全，更新缓存和 result.json。

    不走 kuake_run（它依赖 /subject/list 行业分类，热门题材不在里面），
    而是直接请求夸克 API → 提取 selectedId→stockId 映射 → 写入缓存 → 重新处理。
    """
    result = {"ok": False, "fixed": 0, "still_masked": 0, "error": ""}
    try:
        sid_int = int(subject_id)

        # 1. 登录夸克获取 token，再请求 API
        from engine.kuake.incremental import (
            kuake_login, fetch_kuake_stocks, extract_stocks as kuake_extract,
            load_cache as kuake_load_cache, save_cache as kuake_save_cache,
        )
        print(f"[unmask] 先登录夸克...")
        kuake_login()
        print(f"[unmask] 请求夸克 /ticaitupu?id={sid_int}")
        nodes = fetch_kuake_stocks(sid_int)
        kuake_stocks = kuake_extract(nodes)
        print(f"[unmask] 夸克返回 {len(kuake_stocks)} 只股票")

        if not kuake_stocks:
            result["error"] = "夸克服务器未返回该题材数据"
            result["ok"] = True
            return result

        # 2. 更新 subject_stock_full.json 缓存
        local_cache = kuake_load_cache()
        local_cache[sid_int] = {
            "subjectId":   sid_int,
            "subjectName": f"topic_{sid_int}",
            "level":       None,
            "parentId":    None,
            "pctChg":      None,
            "updateTime":  "",
            "stocks":      kuake_stocks,
        }
        kuake_save_cache(local_cache)
        print(f"[unmask] 缓存已更新，共 {len(local_cache)} 个题材")

        # 3. 从新抓取的数据构建 selectedId → stockId 映射
        new_mappings = {}
        for s in kuake_stocks:
            sel = s.get("selectedId")
            sid_v = s.get("stockId", "")
            sname = s.get("stockName", "")
            if sel and sid_v and len(sid_v) == 6 and sid_v.isdigit():
                new_mappings[int(sel)] = {"stockId": sid_v, "stockName": sname}
        print(f"[unmask] 新增 selectedId 映射 {len(new_mappings)} 条")

        # 4. 强制重载全局 selectedId 索引
        global _sel_id_index, _sel_id_mtime
        _sel_id_mtime = 0.0
        _load_sel_id_index()
        _sel_id_index.update(new_mappings)

        from engine.txcfgl.incremental import (
            load_kuake_cache as _reload_inc_cache,
            fetch_subject_mapping, extract_stocks_from_mapping,
        )
        _reload_inc_cache()

        # 5. 用新索引重新处理 result.json 中该题材的股票
        if not os.path.isfile(DATA_FILE):
            result["error"] = "result.json 不存在"
            return result

        with open(DATA_FILE, encoding="utf-8") as f:
            all_records = json.load(f)

        fixed_total = 0
        still_masked_total = 0
        for rec in all_records:
            if rec.get("subjectId") != sid_int:
                continue
            rank_date = rec.get("rankDate", "")
            mapping = fetch_subject_mapping(sid_int, query_date=rank_date)
            new_stocks = extract_stocks_from_mapping(mapping)
            old_masked = sum(1 for s in rec.get("stocks", []) if s.get("source") == "masked")
            new_masked = sum(1 for s in new_stocks if s.get("source") == "masked")
            rec["stocks"] = new_stocks
            fixed_total += max(0, old_masked - new_masked)
            still_masked_total += new_masked

        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(all_records, f, ensure_ascii=False, indent=2)

        global _data_cache, _data_cache_mtime
        _data_cache = None
        _data_cache_mtime = 0.0

        result["ok"] = True
        result["fixed"] = fixed_total
        result["still_masked"] = still_masked_total
        print(f"[unmask] 完成: 修复 {fixed_total}, 仍屏蔽 {still_masked_total}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        result["error"] = f"{type(e).__name__}: {e}"
    return result

def _load_static(filename):
    """从 engine/static/ 加载静态文件内容，文件不存在时返回空字符串"""
    path = os.path.join(os.path.dirname(__file__), "static", filename)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""

CSS = _load_static("main.css") + _load_static("aigraph.css")

JS = _load_static("main.js") + _load_static("aigraph.js")


def pct_str(pct, cls=True):
    try:
        v = float(pct)
        s = f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"
        if not cls:
            return s
        c = "pct-green" if v >= 0 else "pct-red"
        return f'<span class="{c}">{s}</span>'
    except (TypeError, ValueError):
        return '<span class="pct-gray">—</span>'


_data_cache: dict | None = None
_data_cache_mtime: float = 0.0

def load_data() -> dict:
    """读取题材数据文件，文件未变化时返回内存缓存，避免每次请求都做磁盘 I/O"""
    global _data_cache, _data_cache_mtime
    try:
        mtime = os.path.getmtime(DATA_FILE)
    except OSError:
        mtime = 0.0
    if _data_cache is not None and mtime == _data_cache_mtime:
        return _data_cache
    with open(DATA_FILE, encoding="utf-8") as f:
        _data_cache = json.load(f)
    _data_cache_mtime = mtime
    return _data_cache


_kuake_events_cache: list | None = None
_kuake_events_mtime: float = 0.0
_kuake_stocks_index_cache: dict | None = None
_kuake_stocks_index_mtime: float = 0.0


def _load_kuake_events_raw() -> list:
    """读取 data/kuake_events.json 原始 rows（按 mtime 缓存）"""
    global _kuake_events_cache, _kuake_events_mtime
    try:
        mtime = os.path.getmtime(KUAKE_EVENTS_FILE)
    except OSError:
        return []
    if _kuake_events_cache is not None and mtime == _kuake_events_mtime:
        return _kuake_events_cache
    try:
        with open(KUAKE_EVENTS_FILE, encoding="utf-8") as f:
            _kuake_events_cache = json.load(f) or []
    except Exception:
        _kuake_events_cache = []
    _kuake_events_mtime = mtime
    return _kuake_events_cache


def _load_kuake_stocks_index() -> dict:
    """构建 subjectId(int) -> stocks list 的查找表（来源 subject_stock_full.json，按 mtime 缓存）"""
    global _kuake_stocks_index_cache, _kuake_stocks_index_mtime
    try:
        mtime = os.path.getmtime(FULL_CACHE_FILE)
    except OSError:
        return {}
    if _kuake_stocks_index_cache is not None and mtime == _kuake_stocks_index_mtime:
        return _kuake_stocks_index_cache
    idx: dict = {}
    try:
        with open(FULL_CACHE_FILE, encoding="utf-8") as f:
            for item in json.load(f) or []:
                sid = item.get("subjectId")
                if sid is None:
                    continue
                idx[int(sid)] = item.get("stocks") or []
    except Exception:
        idx = {}
    _kuake_stocks_index_cache = idx
    _kuake_stocks_index_mtime = mtime
    return idx


def _kuake_event_to_record(ev: dict, stocks_index: dict) -> dict:
    """把 kuake /ticaiwajue 一条 row 转成 _render_cards 期望的题材记录形态。
    headline 取 description 首段【...】内容，由 _clean_subject_name 在渲染时再剥壳。"""
    desc = ev.get("description") or ""
    head = desc.lstrip()
    end = head.find("】")
    if head.startswith("【") and end > 0:
        headline = head[: end + 1]
    else:
        headline = ev.get("subjectName") or "未知"
    sid = ev.get("subjectId")
    stocks = list(stocks_index.get(int(sid), [])) if sid is not None else []
    ct = ev.get("createTime") or ""
    return {
        "type": ev.get("type"),
        "subjectId": sid,
        "subjectName": headline,
        "rankDate": ct[:10] if ct else "",
        "createTime": ct,
        "pctChg": ev.get("pctChg"),
        "stocks": stocks,
        "detail": desc,
    }


def load_kuake_events_for_tab(event_type: int = 3) -> list:
    """按 type 过滤并转成卡片记录格式（默认 type=3 驱动事件）。"""
    rows = _load_kuake_events_raw()
    idx = _load_kuake_stocks_index()
    return [_kuake_event_to_record(r, idx) for r in rows if r.get("type") == event_type]


def load_event_records_merged() -> list:
    """合并历史 result.json 中 type=3 与 kuake_events.json type=3，按 createTime/rankDate 倒序。
    历史记录走原 detail/subjectName，新记录从 description 剥壳。"""
    out = []
    # 历史 type=3（来自 result.json，2026-02 ~ 2026-03 期间生效，之后 txcfgl 被限制）
    try:
        for r in load_data():
            if r.get("type") == 3:
                out.append(r)
    except Exception:
        pass
    # kuake 新增 type=3
    out.extend(load_kuake_events_for_tab(event_type=3))
    return out


_subject_index_cache: dict | None = None
_subject_index_mtime: float = 0.0


def _build_subject_index() -> dict:
    """subjectId(str) -> 最新一条 result.json 记录（用于 /api/subject-query 兜底）。"""
    global _subject_index_cache, _subject_index_mtime
    try:
        mtime = os.path.getmtime(DATA_FILE)
    except OSError:
        return {}
    if _subject_index_cache is not None and mtime == _subject_index_mtime:
        return _subject_index_cache
    idx: dict = {}
    try:
        for r in load_data():
            sid = r.get("subjectId")
            if sid is None:
                continue
            key = str(sid)
            prev = idx.get(key)
            if prev is None or (r.get("rankDate") or "") > (prev.get("rankDate") or ""):
                idx[key] = r
    except Exception:
        idx = {}
    _subject_index_cache = idx
    _subject_index_mtime = mtime
    return idx


def lookup_subject_local(sub_id) -> dict:
    """txcfgl 401 时从 result.json 取 reason/detail 兜底。"""
    rec = _build_subject_index().get(str(sub_id)) or {}
    return {
        "reason": rec.get("reason") or "",
        "detail": rec.get("detail") or "",
    }


def _num_or_none(value):
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    try:
        if value in ("", None):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_subject_cycle_local(max_days=20, max_rows=50):
    """
    Build /api/subject-cycle compatible data from local subject history.

    The upstream history-cycle endpoint can return an empty list when the
    external session/token is stale. The rotation page should still be useful
    from our persisted subject_stocks_result.json, so this local fallback keeps
    the UI available without another network dependency.
    """
    try:
        data = load_data()
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    date_pat = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    by_date = {}
    seen = set()
    seq = 0
    for rec in data:
        if not isinstance(rec, dict):
            continue
        date_s = str(rec.get("rankDate") or rec.get("date") or "")[:10]
        if not date_pat.match(date_s):
            continue
        subject_id = rec.get("subjectId")
        name = rec.get("subjectName") or rec.get("name") or ""
        if not subject_id and not name:
            continue
        key = (date_s, str(subject_id or name))
        if key in seen:
            continue
        seen.add(key)

        stocks = rec.get("stocks") if isinstance(rec.get("stocks"), list) else []
        pct = _num_or_none(rec.get("pctChg"))
        limit_up = _int_or_none(rec.get("limitUpTimes"))
        by_date.setdefault(date_s, []).append({
            "subjectId": subject_id,
            "name": _clean_subject_name(str(name)) if name else str(subject_id),
            "pctChg": pct,
            "limitUpTimes": limit_up,
            "type": rec.get("type"),
            "stockCount": len(stocks),
            "_seq": seq,
        })
        seq += 1

    result = []
    for date_s in sorted(by_date.keys(), reverse=True)[:max_days]:
        rows = by_date[date_s]
        rows.sort(key=lambda r: (
            -(r.get("limitUpTimes") or 0),
            -(r.get("pctChg") or -9999),
            -r.get("stockCount", 0),
            r.get("_seq", 0),
        ))
        clean_rows = []
        for row in rows[:max_rows]:
            row.pop("_seq", None)
            clean_rows.append(row)
        result.append({"date": date_s, "rows": clean_rows})
    return result


def _pct_cls(pv):
    """A股配色：上涨红(up)，下跌绿(dn)"""
    if pv > 0:  return "up"
    if pv < 0:  return "dn"
    return "flat"


def _pct_s(pv):
    return f"+{pv:.2f}%" if pv >= 0 else f"{pv:.2f}%"


def _clean_subject_name(name):
    """清洁题材/事件名：去掉 "【驱动事件：" 等前缀和"】"后缀，折叠多余省略号"""
    if not name:
        return name or ""
    s = name.strip()
    if s.startswith("【"):
        for sep in ("：", ":"):
            idx = s.find(sep)
            if 0 < idx < 12:  # 前缀通常很短（"驱动事件" "新题材" 等）
                s = s[idx + 1:]
                break
    if s.endswith("】"):
        s = s[:-1]
    s = s.strip()
    # 折叠 4+ 连续点为 "…"，避免出现 "中国大陆......" 这种丑陋的多省略号
    s = re.sub(r'\.{3,}', '…', s)
    s = re.sub(r'…+', '…', s)
    return s or name


def _render_stock_grid(stocks, detail=False):
    """渲染股票网格，行情占位等待JS异步填充"""
    sorted_s = sorted(stocks, key=lambda x: -(x.get("importance") or 0))

    if detail:
        items = ""
        for i, s in enumerate(sorted_s):
            sname = s.get("stockName", "—")
            sid   = s.get("stockId") or ""
            top_cls = " top3" if i < 3 else ""
            sid_attr = f' data-sid="{sid}"' if sid else ""
            items += (f'<div class="dstk{top_cls}"{sid_attr}>'
                      f'<div class="dstk-name">{sname}</div>'
                      f'<div class="dstk-code">{sid or "—"}</div>'
                      f'<div class="dstk-pct flat"><span class="pct-val flat">—</span></div>'
                      f'<div class="dstk-price"><span class="price-val"></span></div>'
                      f'</div>')
        return f'<div class="detail-stocks-grid">{items}</div>'
    else:
        items = ""
        for s in sorted_s:
            sname = s.get("stockName", "—")
            sid   = s.get("stockId") or ""
            sid_attr = f' data-sid="{sid}"' if sid else ""
            items += (f'<div class="stk"{sid_attr}>'
                      f'<div class="stk-left">'
                      f'<div class="stk-name">{sname}</div>'
                      f'<div class="stk-code">{sid or "—"}</div>'
                      f'</div>'
                      f'<div class="stk-right">'
                      f'<div class="stk-pct flat"><span class="pct-val flat">—</span></div>'
                      f'<div class="stk-price"><span class="price-val"></span></div>'
                      f'</div>'
                      f'</div>')
        return f'<div class="stocks-grid">{items}</div>'


def _render_cards(items, sort_by="pct"):
    """渲染题材卡片网格，行情由JS异步填充"""
    if sort_by == "time":
        # 按 createTime 倒序（事件发生时间，新→旧）
        def sort_key(x):
            return x[1].get("createTime") or x[1].get("rankDate") or ""
        items = sorted(items, key=sort_key, reverse=True)
    else:
        # 按涨跌幅排序（涨幅高→低）
        def sort_key(x):
            try: return float(x[1].get("pctChg") or 0)
            except: return 0
        items = sorted(items, key=sort_key, reverse=True)

    html = '<div class="cards-grid">'
    for rank, (orig_idx, r) in enumerate(items):
        is_new  = r.get("type") == 2
        tag_txt = "新题材" if is_new else "驱动事件"
        tag_cls = "tag-new" if is_new else "tag-event"
        name    = _clean_subject_name(r.get("subjectName", "未知"))
        date    = r.get("rankDate", "")
        ct      = r.get("createTime", "") or ""
        date_display = ct[11:19] if (not is_new and len(ct) >= 19) else date
        stocks  = r.get("stocks", [])
        cnt     = len(stocks)
        try:
            pv    = float(r.get("pctChg"))
            ps    = _pct_s(pv)
            pc    = _pct_cls(pv)
        except (TypeError, ValueError):
            ps, pc = "—", "flat"
        num_cls = ["rank-1","rank-2","rank-3"][rank] if rank < 3 else "rank-n"
        stock_grid = _render_stock_grid(stocks, detail=False)

        subj_id = r.get("subjectId", "")
        html += f"""<details class="card" data-subid="{subj_id}" data-date="{date}">
<summary class="card-sum">
  <div class="rank-num {num_cls}">{rank+1}</div>
  <div class="card-info">
    <div class="card-name" data-full="{name}">{name}</div>
    <div class="card-sub"><span class="{tag_cls}">{tag_txt}</span> {cnt}只 · {date_display}</div>
  </div>
  <div class="card-pct {pc}">{ps}</div>
  <span class="card-arrow">&#9654;</span>
</summary>
<div class="lead-bar" id="lead-{subj_id}"></div>
<div class="stocks-panel">{stock_grid}</div>
</details>"""
    html += '</div>'
    return html


def _render_tab_pane(data, tab_key):
    """渲染单个 tab 的内容 pane"""
    if tab_key == "new":
        filtered = [(i, r) for i, r in enumerate(data) if r.get("type") == 2]
    elif tab_key == "event":
        # 平台已限制 txcfgl 端事件源：历史 type=3 来自 result.json（2026-03-20 前），
        # 之后由 kuake_events.json 持续供给。两者合并按时间倒序展示。
        merged = load_event_records_merged()
        filtered = list(enumerate(merged))
    else:
        filtered = list(enumerate(data))

    groups = {}
    for orig_idx, r in filtered:
        d = r.get("rankDate", "未知")
        groups.setdefault(d, []).append((orig_idx, r))

    body_html = ""
    sort_by = "time" if tab_key == "event" else "pct"
    for date in sorted(groups.keys(), reverse=True):
        body_html += f'<div class="date-group">{date}</div>'
        body_html += _render_cards(groups[date], sort_by=sort_by)
    return body_html, len(filtered)


def render_market_bar():
    """渲染大盘看板 HTML，失败时返回空字符串"""
    if not _MARKET_OK:
        return ""
    try:
        ms = _market_session()
        if not ms:
            return ""
        idx_data    = fetch_index_snapshot(ms)
        amount_data = fetch_market_amount(ms)
        today = datetime.now().strftime("%Y-%m-%d")
        up_down = fetch_up_down(ms, today)
        ud = up_down[0] if up_down else {}
    except Exception:
        return ""

    # 拉取四指数历史走势（用于 sparkline）
    spark_data = {}
    for code in ["000001.SH", "399001.SZ", "399006.SZ", "399106.SZ"]:
        try:
            hist = fetch_index_history(ms, code, days=60)
            spark_data[code] = [float(d.get("close", 0)) for d in hist if d.get("close")]
        except Exception:
            spark_data[code] = []

    cards = ""
    order = ["000001.SH", "399001.SZ", "399006.SZ", "399106.SZ"]
    for code in order:
        v = idx_data.get(code, {})
        if not v:
            continue
        pct  = v.get("pctChg", 0)
        dire = "up" if pct > 0 else ("dn" if pct < 0 else "")
        cls  = "mkt-up" if pct > 0 else ("mkt-dn" if pct < 0 else "mkt-flat")
        ps   = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"
        hi   = v.get("high") or 0
        lo   = v.get("low")  or 0
        spark_json = json.dumps(spark_data.get(code, []))
        cards += (f'<div class="mkt-card {dire}">'
                  f'<div class="mkt-name">{v["name"]}</div>'
                  f'<div class="mkt-val {cls}">{v["close"]:.2f}</div>'
                  f'<div class="mkt-row">'
                  f'<span class="mkt-pct {cls}">{ps}</span>'
                  f'<span class="mkt-hl">H {hi:.2f} L {lo:.2f}</span>'
                  f'</div>'
                  f'<canvas class="mkt-spark" data-spark=\'{spark_json}\' data-cls="{dire}" width="200" height="28"></canvas>'
                  f'</div>')

    up      = ud.get("upCount",    "—")
    dn      = ud.get("downCount",  "—")
    st      = ud.get("stayCount",  "—")
    amt     = amount_data.get("amount") or 0
    add_amt = amount_data.get("addAmount") or 0
    amt_str = f"{amt/1e8:.0f}亿" if amt else "—"
    add_str = (f'+{add_amt/1e8:.0f}亿' if add_amt >= 0 else f'{add_amt/1e8:.0f}亿') if add_amt else ""
    stats = (f'<div class="mkt-stats-bar">'
             f'<span class="mkt-up">↑ 涨 {up}</span>'
             f'<span class="mkt-flat">— 平 {st}</span>'
             f'<span class="mkt-dn">↓ 跌 {dn}</span>'
             f'<span class="mkt-amount">成交额 {amt_str}'
             + (f' <span style="color:#aaa">({add_str})</span>' if add_str else "") +
             f'</span></div>')
    return f'<div class="market-panel"><div class="market-grid">{cards}</div>{stats}</div>'


def render_index(data, tab="new"):
    now = datetime.now().strftime("%m-%d %H:%M:%S")

    panes_html = ""
    counts = {}

    # 新题材 tab —— 左列列表 + 右侧子树（内容由 JS 异步加载）
    new_display = "" if tab == "new" else ' style="display:none"'
    cnt_new = sum(1 for r in data if r.get("type") == 2)
    counts["new"] = cnt_new
    panes_html += (f'<div class="tab-pane treepage" data-tab="new"{new_display}>'
                   f'<div class="tree-list" id="newList"><div class="tree-loading">加载中…</div></div>'
                   f'<div class="tree-detail" id="newDetail">'
                   f'<div class="tree-detail-hd"><div class="tree-detail-name" id="ndName">—</div>'
                   f'<div class="tree-detail-pct" id="ndPct"></div>'
                   f'<div class="tree-detail-date" id="ndDate"></div></div>'
                   f'<div class="tree-detail-body" id="ndBody"><div class="tree-detail-loading">点击左侧题材</div></div>'
                   f'</div>'
                   f'<div class="tree-chart" id="newChart"><div class="tree-loading">点击左侧题材查看子树</div></div>'
                   f'</div>')

    # 驱动事件 tab —— 内容由 JS 懒加载（/api/events），避免首屏渲染大量卡片
    # 数据源：result.json 历史 type=3 + data/kuake_events.json（type=3）合并
    cnt_event = (
        sum(1 for r in data if r.get("type") == 3)
        + sum(1 for r in _load_kuake_events_raw() if r.get("type") == 3)
    )
    event_display = "" if tab == "event" else ' style="display:none"'
    panes_html += (f'<div class="tab-pane" data-tab="event"{event_display}>'
                   f'<div id="eventBody" class="cards-loading">加载中…</div>'
                   f'</div>')
    counts["event"] = cnt_event

    # 题材轮动 tab
    cycle_display = "" if tab == "cycle" else ' style="display:none"'
    panes_html += (f'<div class="tab-pane cycle-wrap" data-tab="cycle"{cycle_display}>'
                   f'<div class="cycle-loading" id="cycleBody">加载中…</div>'
                   f'</div>')

    # AI 图谱 Tab5
    ag_display = "" if tab == "aigraph" else ' style="display:none"'
    panes_html += (f'<div class="tab-pane aigraph-wrap" id="aigraphWrap" data-tab="aigraph"{ag_display}>'
                   f'<div class="aigraph-loading">图谱加载中…</div>'
                   f'</div>')

    # 研报分析 Tab6
    ime_display = "" if tab == "ime" else ' style="display:none"'
    panes_html += (f'<div class="tab-pane ime-wrap" id="imeWrap" data-tab="ime"{ime_display}>'
                   f'<div id="imeBody" class="ime-loading">加载中…</div>'
                   f'</div>')

    tabs_html = ""
    labels = {"new": "新题材", "event": "驱动事件", "cycle": "题材轮动", "aigraph": "AI图谱", "ime": "研报"}
    for t, label in labels.items():
        cls = "tab active" if t == tab else "tab"
        cnt_badge = f'<span class="tab-cnt">{counts[t]}</span>' if t in counts else ""
        tabs_html += f'<span class="{cls}" data-tab="{t}">{label}{cnt_badge}</span>'

    return f"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>久赢恒丰</title>
<style>{CSS}</style>
</head><body>
<div class="topbar">
  <div class="topbar-row">
    <span class="topbar-title">题材挖掘</span>
    <span class="topbar-meta">{now}</span>
  </div>
  <div class="tabs">{tabs_html}</div>
</div>
<div class="main">{panes_html}</div>
<div class="fulltext-mask" id="ftMask">
  <div class="fulltext-box">
    <span class="fulltext-close" id="ftClose">&#10005;</span>
    <div class="fulltext-title" id="ftTitle"></div>
    <div class="fulltext-meta" id="ftMeta"></div>
    <div id="ftExtra"></div>
  </div>
</div>
<script>{JS}</script>
</body></html>"""


def render_detail(data, idx):
    if idx < 0 or idx >= len(data):
        return "<h2>未找到</h2>"
    r       = data[idx]
    is_new  = r.get("type") == 2
    tag_cls = "tag-new" if is_new else "tag-event"
    tag_txt = "新题材" if is_new else "驱动事件"
    name    = _clean_subject_name(r.get("subjectName", "未知"))
    date    = r.get("rankDate", "")
    stocks  = r.get("stocks", [])
    sorted_s = sorted(stocks, key=lambda x: -(x.get("importance") or 0))
    cnt     = len(stocks)
    pct     = r.get("pctChg")

    # 涨跌色
    try:
        pv = float(pct)
        pct_cls = _pct_cls(pv)
        pct_s   = _pct_s(pv)
    except (TypeError, ValueError):
        pct_cls, pct_s = "flat", "—"

    stock_grid = _render_stock_grid(sorted_s, detail=True)

    # 题材详情文本（HTML，需清洗）
    detail_html = r.get("detail") or ""
    reason_txt  = r.get("reason") or ""
    luc         = r.get("limitUpCount")
    sc          = r.get("stockCount")
    stat3 = ""
    if luc is not None:
        stat3 += f'<div class="stat-item"><div class="stat-val up">{luc}</div><div class="stat-label">涨停数</div></div>'
    if sc is not None:
        stat3 += f'<div class="stat-item"><div class="stat-val">{sc}</div><div class="stat-label">覆盖股票</div></div>'

    # 首只有 stockId 的股票，用于日K/主营（取 importance 最高的那只）
    first_sid = next((s["stockId"] for s in sorted_s if s.get("stockId")), "")
    body_sid_attr = f' data-sid="{first_sid}"' if first_sid else ""

    reason_block = ""
    if reason_txt:
        reason_block = f'<div class="section-hd">驱动原因</div><div style="padding:12px 20px 12px;background:#fff;font-size:13px;color:#333;line-height:1.7">{reason_txt}</div>'

    detail_block = ""
    if detail_html and detail_html.strip() not in ("", "<p><br></p>", "<p></p>"):
        detail_block = f'<div class="section-hd">题材详情</div><div style="padding:12px 20px 16px;background:#fff;font-size:13px;color:#444;line-height:1.8">{detail_html}</div>'

    kline_block = ""
    biz_block   = ""
    if first_sid:
        kline_block = (f'<div class="section-hd">日K走势（近60日）— {sorted_s[0].get("stockName",first_sid) if sorted_s else first_sid}</div>'
                       f'<div class="kline-wrap">'
                       f'<div class="kline-canvas-wrap"><canvas class="kline" id="klineCanvas"></canvas></div>'
                       f'</div>')
        biz_block   = (f'<div class="section-hd">主营业务构成</div>'
                       f'<div class="biz-wrap"><div class="biz-bars" id="bizBars"><div class="kline-empty">加载中…</div></div></div>')

    return f"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>{name}</title>
<style>{CSS}</style>
</head><body{body_sid_attr}>
<script>{JS}</script>
<div class="detail-nav">
  <a href="/">&#8592;</a>
  <div class="detail-nav-title">题材详情</div>
</div>
<div class="detail-hero">
  <div class="detail-hero-top">
    <div class="detail-hero-name">{name}</div>
    <div class="detail-hero-pct {pct_cls}">{pct_s}</div>
  </div>
  <div class="detail-hero-meta">{date} &nbsp;·&nbsp; <span class="{tag_cls}">{tag_txt}</span></div>
  <div class="detail-stats">
    <div class="stat-item"><div class="stat-val">{cnt}</div><div class="stat-label">相关股票</div></div>
    <div class="stat-item"><div class="stat-val {pct_cls}">{pct_s}</div><div class="stat-label">当日涨幅</div></div>
    <div class="stat-item"><div class="stat-val">{date}</div><div class="stat-label">更新日期</div></div>
    {stat3}
  </div>
</div>
{reason_block}
{detail_block}
<div class="section-hd">相关股票（按实时涨幅排序）</div>
{stock_grid}
{kline_block}
{biz_block}
<div class="section-hd">关联题材</div>
<div class="subj-list" id="subjList"><div class="kline-empty">加载中…</div></div>
</body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
# 管理层辅助函数（供 /api/mgr/* 接口调用）
# ─────────────────────────────────────────────────────────────────────────────

_MGR_SERVICES = ["jiuying-web", "jiuying-tunnel", "cron"]


def _mgr_run_cmd(cmd: list, timeout: int = 10) -> dict:
    """执行系统命令并返回结果 dict"""
    import subprocess as _sp
    try:
        ret = _sp.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": ret.returncode == 0, "output": (ret.stdout + ret.stderr).strip()}
    except _sp.TimeoutExpired:
        return {"ok": False, "output": "命令超时"}
    except Exception as e:
        return {"ok": False, "output": str(e)}


def _mgr_service_status(name: str) -> dict:
    """查询 systemd 服务状态"""
    import subprocess as _sp
    try:
        out = _sp.check_output(
            ["systemctl", "is-active", name],
            text=True, stderr=_sp.DEVNULL, timeout=5,
        ).strip()
        return {"service": name, "active": out == "active", "state": out}
    except _sp.TimeoutExpired:
        return {"service": name, "active": False, "state": "timeout"}
    except Exception:
        return {"service": name, "active": False, "state": "unknown"}


def _build_mgr_health_response() -> bytes:
    """构建 /api/mgr/health 响应体"""
    result: dict = {"ok": True, "project": "久赢恒丰", "services": [], "system": {}}
    # systemd 服务状态
    for svc in _MGR_SERVICES:
        result["services"].append(_mgr_service_status(svc))
    # 系统基本信息
    try:
        import socket
        result["system"]["hostname"] = socket.gethostname()
    except Exception:
        pass
    try:
        import subprocess as _sp
        uptime_out = _sp.check_output(["uptime", "-p"], text=True, timeout=5).strip()
        result["system"]["uptime"] = uptime_out
    except Exception:
        pass
    try:
        import subprocess as _sp
        df_out = _sp.check_output(
            ["df", "-h", "--output=avail,pcent", "/"], text=True, timeout=5,
        ).strip().splitlines()
        if len(df_out) > 1:
            parts = df_out[1].split()
            result["system"]["disk_avail"] = parts[0] if parts else "—"
            result["system"]["disk_used_pct"] = parts[1] if len(parts) > 1 else "—"
    except Exception:
        pass
    return json.dumps(result, ensure_ascii=False).encode("utf-8")


def _build_mgr_local_stats_response() -> bytes:
    """构建 /api/mgr/local-stats 响应体（调用 local_data_stats.scan_all）"""
    try:
        from engine.local_data_stats import scan_all
        data = scan_all()
        return json.dumps({"ok": True, "data": data}, ensure_ascii=False).encode("utf-8")
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8")


_MGR_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_MGR_LOG_PATHS = {
    "jiuying-web": os.path.join(_MGR_LOG_DIR, "cron.log"),
    "tunnel":      os.path.join(_MGR_LOG_DIR, "cf_tunnel.log"),
    "health":      os.path.join(_MGR_LOG_DIR, "health.log"),
    "cron":        os.path.join(_MGR_LOG_DIR, "cron.log"),
}

def _build_mgr_log_tail_response(qs: dict) -> bytes:
    """构建 /api/mgr/log-tail 响应体，读取指定日志文件末尾 N 行"""
    key   = (qs.get("key", ["jiuying-web"]) or ["jiuying-web"])[0]
    lines = int((qs.get("lines", ["100"]) or ["100"])[0])
    lines = min(lines, 500)
    path  = _MGR_LOG_PATHS.get(key, _MGR_LOG_PATHS["jiuying-web"])
    try:
        with open(path, encoding="utf-8", errors="replace") as _f:
            tail = _f.readlines()[-lines:]
        return json.dumps({
            "ok": True, "key": key, "file": path,
            "lines": [l.rstrip("\n") for l in tail],
        }, ensure_ascii=False).encode("utf-8")
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e), "lines": []},
                          ensure_ascii=False).encode("utf-8")


def _build_mgr_request_stats_response() -> bytes:
    """
    构建 /api/mgr/request-stats 响应体。
    解析 logs/kuake_*.log 和 logs/txcfgl_*.log，汇总各端点请求统计。
    返回格式：
      {
        "ok": true,
        "endpoints": [
          {
            "name": "夸克服务器",
            "host": "111.170.164.89:600",
            "log_prefix": "kuake",
            "total_requests": 120,
            "success": 115,
            "failed": 5,
            "total_stocks": 3600,
            "avg_interval_s": 9.4,
            "min_interval_s": 5.1,
            "max_interval_s": 15.2,
            "last_run": "2026-03-06 15:41",
            "last_mode": "incremental",
            "run_count": 8
          },
          ...
        ]
      }
    """
    import re as _re
    import glob as _glob
    from datetime import datetime as _dt

    def _parse_block(content: str) -> dict:
        """解析单次运行的统计文本块，返回各字段数值。
        日志格式示例（kuake/incremental _save_stats_log 生成）:
          请求总数: 6
            成功(有数据): 6
            成功(无数据): 0
            失败: 0
          获取股票总数: 6
          请求间隔: avg=14.49s  min=7.10s  max=34.13s
          ===== 间隔明细 =====
            #1: 13.04s  #2: 10.36s  ...
        """
        def _i(pat):
            m = _re.search(pat, content)
            return int(m.group(1)) if m else 0

        def _f(pat):
            m = _re.search(pat, content)
            return float(m.group(1)) if m else None

        req    = _i(r"请求总数[:：]\s*(\d+)")
        succ   = _i(r"成功\(有数据\)[:：]\s*(\d+)")
        fail   = _i(r"失败[:：]\s*(\d+)")
        stocks = _i(r"获取股票总数[:：]\s*(\d+)")

        # 优先从汇总行解析 avg/min/max
        avg_i = _f(r"avg=([\d.]+)s")
        min_i = _f(r"min=([\d.]+)s")
        max_i = _f(r"max=([\d.]+)s")

        # 退化到明细行
        if avg_i is None:
            ivs = [float(v) for v in _re.findall(r"#\d+:\s*([\d.]+)s", content)]
            if ivs:
                avg_i = round(sum(ivs) / len(ivs), 2)
                min_i = round(min(ivs), 2)
                max_i = round(max(ivs), 2)

        return {"req": req, "succ": succ, "fail": fail,
                "stocks": stocks, "avg": avg_i, "min": min_i, "max": max_i}

    endpoints_cfg = [
        {"name": "夸克服务器",   "host": "111.170.164.89:600",
         "log_prefix": "kuake",  "use_cron": False},
        {"name": "久赢恒丰 App", "host": "app.txcfgl.com",
         "log_prefix": "txcfgl", "use_cron": True},
    ]

    results = []
    for ep in endpoints_cfg:
        prefix   = ep["log_prefix"]
        use_cron = ep.get("use_cron", False)

        blocks:    list[str] = []
        run_count: int       = 0
        last_ts:   "_dt | None" = None
        last_mode: "str | None" = None

        if use_cron:
            # txcfgl 运行结果写进 cron.log，按段落拆分
            cron_path = os.path.join(_MGR_LOG_DIR, "cron.log")
            if os.path.exists(cron_path):
                try:
                    with open(cron_path, encoding="utf-8", errors="replace") as fh:
                        raw = fh.read()
                    # 以分割线或"完成"标志划分运行段
                    chunks = _re.split(r"(?m)^={10,}", raw)
                    blocks = [c for c in chunks if "请求总数" in c]
                    run_count = len(blocks)
                    ts_list = _re.findall(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", raw)
                    if ts_list:
                        try:
                            last_ts = _dt.strptime(ts_list[-1], "%Y-%m-%d %H:%M:%S")
                            last_mode = "cron"
                        except ValueError:
                            pass
                except Exception:
                    pass
        else:
            pattern   = os.path.join(_MGR_LOG_DIR, f"{prefix}_*.log")
            log_files = sorted(_glob.glob(pattern))
            run_count = len(log_files)
            for fpath in log_files:
                try:
                    with open(fpath, encoding="utf-8", errors="replace") as fh:
                        blocks.append(fh.read())
                    ts_m = _re.search(r"(\d{8}_\d{6})", fpath)
                    if ts_m:
                        run_ts = _dt.strptime(ts_m.group(1), "%Y%m%d_%H%M%S")
                        if last_ts is None or run_ts > last_ts:
                            last_ts = run_ts
                            bn = os.path.basename(fpath)
                            last_mode = "incremental" if "incremental" in bn else "full"
                except Exception:
                    continue

        parsed = [_parse_block(b) for b in blocks]

        total_req = sum(d["req"]    for d in parsed)
        total_suc = sum(d["succ"]   for d in parsed)
        total_fal = sum(d["fail"]   for d in parsed)
        total_stk = sum(d["stocks"] for d in parsed)

        avgs = [d["avg"] for d in parsed if d["avg"] is not None]
        mins = [d["min"] for d in parsed if d["min"] is not None]
        maxs = [d["max"] for d in parsed if d["max"] is not None]
        avg_i = round(sum(avgs) / len(avgs), 1) if avgs else None
        min_i = round(min(mins), 1)              if mins else None
        max_i = round(max(maxs), 1)              if maxs else None

        results.append({
            "name":           ep["name"],
            "host":           ep["host"],
            "total_requests": total_req,
            "success":        total_suc,
            "failed":         total_fal,
            "total_stocks":   total_stk,
            "avg_interval_s": avg_i,
            "min_interval_s": min_i,
            "max_interval_s": max_i,
            "last_run":       last_ts.strftime("%Y-%m-%d %H:%M") if last_ts else None,
            "last_mode":      last_mode,
            "run_count":      run_count,
        })

    return json.dumps({"ok": True, "endpoints": results},
                      ensure_ascii=False).encode("utf-8")


def _build_mgr_tasks_get_response(qs: dict) -> bytes:
    """构建 GET /api/mgr/tasks 响应体（服务状态列表）"""
    services = qs.get("service", _MGR_SERVICES)
    if isinstance(services, str):
        services = [services]
    result = {"ok": True, "tasks": [_mgr_service_status(s) for s in services]}
    return json.dumps(result, ensure_ascii=False).encode("utf-8")


def _handle_mgr_tasks_post(body_bytes: bytes) -> bytes:
    """处理 POST /api/mgr/tasks（控制 systemd 服务）"""
    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        return json.dumps({"ok": False, "error": "JSON 解析失败"}).encode("utf-8")
    service = data.get("service", "")
    action  = data.get("action", "")
    if not service or action not in ("start", "stop", "restart"):
        return json.dumps({"ok": False, "error": "需要 service 和 action(start|stop|restart)"}).encode("utf-8")
    if service not in _MGR_SERVICES:
        return json.dumps({"ok": False, "error": f"未知服务: {service}"}).encode("utf-8")
    ret = _mgr_run_cmd(["sudo", "systemctl", action, service], timeout=15)
    ret["service"] = service
    ret["action"]  = action
    return json.dumps(ret, ensure_ascii=False).encode("utf-8")


def _send_json(handler, body: bytes, status: int = 200):
    """向客户端发送 JSON 响应的快捷方法"""
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


# ─────────────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _proxy_tokens(self, method="GET", body=None):
        """反向代理 /tokens* 请求到 Token Manager (port 8889)"""
        import urllib.request, urllib.error
        parsed = urlparse(self.path)
        # 重写路径: /tokens → /, /tokens/api/xxx → /api/xxx
        target_path = parsed.path[len("/tokens"):] or "/"
        url = f"http://127.0.0.1:8889{target_path}"
        if parsed.query:
            url += f"?{parsed.query}"
        req = urllib.request.Request(url, method=method, data=body)
        auth = self.headers.get("Authorization")
        if auth:
            req.add_header("Authorization", auth)
        ct = self.headers.get("Content-Type")
        if ct:
            req.add_header("Content-Type", ct)
        # 提取操作耗时较长，增加超时
        _timeout = 180 if "token-extract" in target_path else 30
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as resp:
                rbody = resp.read()
                self.send_response(resp.status)
                for h in ("Content-Type", "WWW-Authenticate"):
                    v = resp.getheader(h)
                    if v:
                        self.send_header(h, v)
                self.send_header("Content-Length", str(len(rbody)))
                self.end_headers()
                self.wfile.write(rbody)
        except urllib.error.HTTPError as e:
            rbody = e.read()
            self.send_response(e.code)
            for h in ("Content-Type", "WWW-Authenticate"):
                v = e.headers.get(h)
                if v:
                    self.send_header(h, v)
            self.send_header("Content-Length", str(len(rbody)))
            self.end_headers()
            self.wfile.write(rbody)
        except Exception:
            self.send_response(502)
            self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path.startswith("/tokens"):
            self._proxy_tokens("GET")
            return

        data   = load_data()

        if parsed.path == "/":
            qs  = parse_qs(parsed.query)
            tab = qs.get("tab", ["new"])[0]
            body = render_index(data, tab).encode("utf-8")
        elif parsed.path == "/detail":
            qs  = parse_qs(parsed.query)
            idx = int(qs.get("id", [0])[0])
            body = render_detail(data, idx).encode("utf-8")
        elif parsed.path == "/api/ime-reports":
            qs    = parse_qs(parsed.query)
            page  = max(1, int(qs.get("page", ["1"])[0]))
            cat   = qs.get("category", [""])[0]
            q     = qs.get("q", [""])[0].strip().lower()
            limit = max(0, int(qs.get("limit", ["0"])[0]))   # 0=paginate, >0=return N items
            page_size = 500
            try:
                ime_path = os.path.join(os.path.dirname(__file__),
                                         "..", "data", "ime_reports.json")
                with open(ime_path, encoding="utf-8") as f:
                    payload = json.load(f)
                items = payload.get("items", [])
                if cat:
                    items = [r for r in items if r.get("category", "") == cat]
                if q:
                    items = [r for r in items if
                             q in (r.get("file_name", "") or "").lower() or
                             q in (r.get("s_one_liner", "") or "").lower() or
                             q in (r.get("s_catalyst", "") or "").lower()]
                total = len(items)
                if limit > 0:
                    result = {"total": total, "page": 1, "page_size": total,
                              "updated_at": payload.get("updated_at", ""),
                              "items": items[:limit]}
                else:
                    start  = (page - 1) * page_size
                    result = {"total": total, "page": page, "page_size": page_size,
                              "updated_at": payload.get("updated_at", ""),
                              "items": items[start:start + page_size]}
            except FileNotFoundError:
                result = {"total": 0, "page": 1, "page_size": page_size,
                          "updated_at": "", "items": [], "error": "数据文件未找到"}
            except Exception as e:
                result = {"total": 0, "page": 1, "page_size": page_size,
                          "updated_at": "", "items": [], "error": str(e)}
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/ime-posts":
            qs    = parse_qs(parsed.query)
            limit = min(500, int(qs.get("limit", ["300"])[0]))
            ht    = qs.get("hashtag", [""])[0]
            try:
                ime_path = os.path.join(os.path.dirname(__file__),
                                         "..", "data", "ime_posts.json")
                with open(ime_path, encoding="utf-8") as f:
                    payload = json.load(f)
                items = payload.get("items", [])
                if ht:
                    items = [p for p in items if p.get("hashtag", "") == ht]
                result = {"total": len(items), "updated_at": payload.get("updated_at", ""),
                          "items": items[:limit]}
            except FileNotFoundError:
                result = {"total": 0, "updated_at": "", "items": [], "error": "数据文件未找到"}
            except Exception as e:
                result = {"total": 0, "updated_at": "", "items": [], "error": str(e)}
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/events":
            data = load_data()
            html, _ = _render_tab_pane(data, "event")
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/quotes":
            qs  = parse_qs(parsed.query)
            ids = [i for i in qs.get("ids", [""])[0].split(",") if i]
            qt  = fetch_quotes(ids)
            body = json.dumps(qt, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/ai-graph":
            # 返回 ai_graph.json 静态文件
            try:
                with open(AI_GRAPH_FILE, encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
            except Exception:
                body = b'{"nodes":[],"edges":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/ai-quotes":
            # 返回图谱行情缓存（新浪20分钟全量，QMT推送优先覆盖）
            from engine import ag_service as _ags
            cached = _ags.get_quote_cache()
            with _qmt_quotes_lock:
                for k, v in _qmt_quotes.items():
                    cached[k] = v  # QMT实时数据覆盖
            body = json.dumps(cached, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/ag-history":
            # 返回历史行情（从QMT K线拉取）{date: {stockId: pct}}
            from engine import ag_service as _ags
            hist = _ags.get_history_cache()
            body = json.dumps(hist, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/subject-cycle":
            result = []
            if _MARKET_OK:
                try:
                    ms = _market_session()
                    if ms:
                        from engine.txcfgl.market import fetch_subject_history_cycle
                        result = fetch_subject_history_cycle(ms, cycle=1)
                except Exception:
                    import traceback; traceback.print_exc()
            if not result:
                result = _build_subject_cycle_local()
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/subject-tree":
            # 从 result.json 构建树：日期 → [新题材/驱动事件] → 题材节点(含stocks)
            from collections import OrderedDict
            date_map = OrderedDict()
            for r in sorted(data, key=lambda x: x.get("rankDate",""), reverse=True):
                d     = r.get("rankDate", "未知")
                ttype = "新题材" if r.get("type") == 2 else "驱动事件"
                date_map.setdefault(d, {"新题材": [], "驱动事件": []})
                stocks_brief = [
                    {"stockId": s.get("stockId",""), "stockName": s.get("stockName",""),
                     "pctChg": s.get("pctChg"), "importance": s.get("importance"),
                     "reason": s.get("reason") or "", "group": s.get("group") or ""}
                    for s in sorted(r.get("stocks",[]), key=lambda x: -(x.get("importance") or 0))
                ]
                ct = r.get("createTime","") or ""
                date_map[d][ttype].append({
                    "name":       r.get("subjectName",""),
                    "subjectId":  r.get("subjectId"),
                    "pctChg":     r.get("pctChg"),
                    "rankDate":   r.get("rankDate",""),
                    "createTime": ct,
                    "timeStr":    ct[11:19] if len(ct) >= 19 else "",
                    "stocks":     stocks_brief,
                    "children":   [],
                })
            # 转为树形列表
            tree = []
            for d, types in date_map.items():
                children = []
                for tname, items in types.items():
                    if items:
                        children.append({"name": tname, "pctChg": None,
                                         "children": items, "stocks": []})
                tree.append({"name": d, "pctChg": None,
                             "children": children, "stocks": []})
            body = json.dumps(tree, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/subject-child-tree":
            qs     = parse_qs(parsed.query)
            sub_id = qs.get("id", [""])[0].strip()
            result = []
            if sub_id and _MARKET_OK:
                try:
                    ms = _market_session()
                    if ms:
                        from engine.txcfgl.market import fetch_subject_child_stock_tree
                        raw_tree = fetch_subject_child_stock_tree(ms, sub_id)

                        # 从本地 result.json 找到该题材的股票列表，按 importance 排序
                        # 作为补全被屏蔽 stockId/stockName 的来源
                        local_stocks = []
                        for r in data:
                            if str(r.get("subjectId","")) == str(sub_id):
                                local_stocks = sorted(
                                    r.get("stocks", []),
                                    key=lambda x: -(x.get("importance") or 0)
                                )
                                break

                        # 构建 stockId → {stockId, stockName} 映射（仅有效的）
                        local_map = {}
                        for s in local_stocks:
                            sid_v = s.get("stockId","")
                            if sid_v and sid_v != "111":
                                local_map[sid_v] = s

                        # child-stock-tree 结构：
                        # [root节点] -> children=[首批定价矿产,潜在扩展矿产]
                        #                          -> children=[锗,镓,钨,锑]
                        #                                       -> stocks=[{stockId:111,reason:...}]
                        # 目标：取第二层(首批/潜在)为分组，第三层(锗/镓/钨)为子分组含stocks

                        def flatten_tree_to_groups(nodes):
                            """
                            跳过 root 节点，提取所有叶子分组（含stocks的节点）
                            如果中间层有名字，保留为分组容器
                            """
                            result_groups = []
                            for n in (nodes or []):
                                name     = (n.get("name") or "").strip()
                                children = n.get("children") or []
                                stocks   = n.get("stocks") or []
                                # 叶子节点：有名字有股票
                                if stocks and name:
                                    result_groups.append({
                                        "name": name, "pctChg": n.get("pctChg"),
                                        "stocks": stocks, "children": []
                                    })
                                # 中间节点：有名字有子节点
                                elif children and name:
                                    sub = flatten_tree_to_groups(children)
                                    if sub:
                                        result_groups.append({
                                            "name": name, "pctChg": n.get("pctChg"),
                                            "stocks": [], "children": sub
                                        })
                                    else:
                                        # 子节点没有股票，跳过
                                        pass
                                # root 节点（无名或与查询相同）：穿透
                                elif children:
                                    result_groups.extend(flatten_tree_to_groups(children))
                            return result_groups

                        groups = flatten_tree_to_groups(raw_tree)

                        # 补全被屏蔽的 stockId/stockName
                        # 优先用 selectedId 精确匹配完整缓存索引
                        _load_sel_id_index()
                        # 同时建立 result.json 本题材的 selectedId -> stock 索引
                        local_sel_map = {}
                        local_sid_map = {}
                        local_reason_map = {}  # reason -> stock，作为 selectedId 缺失时的 fallback
                        local_group_map = {}   # group_name -> [stocks]，整组替换 fallback
                        for s in local_stocks:
                            sel = s.get("selectedId")
                            sid_v = s.get("stockId","")
                            rs    = (s.get("reason") or "").strip()
                            grp   = (s.get("group") or "").strip()
                            if sel:
                                local_sel_map[int(sel)] = s
                            if sid_v and sid_v != "111":
                                local_sid_map[sid_v] = s
                            if rs and sid_v and sid_v != "111":
                                local_reason_map[rs] = s
                            if grp and sid_v and sid_v != "111":
                                local_group_map.setdefault(grp, []).append(s)
                        # 同时按 group 末段（"火电/火电装机量前五" -> "火电装机量前五"）建索引
                        local_group_tail_map = {}
                        for grp, stks in local_group_map.items():
                            tail = grp.split("/")[-1]
                            local_group_tail_map.setdefault(tail, []).extend(stks)

                        # 扩展：把夸克 cache (subject_stock_full.json) 同 subjectId 的 stocks 也加入索引
                        # 夸克 cache 的 stocks 有 reason 字段（child-stock-tree 用 reason 关联），是 result.json 的补充
                        try:
                            with open(FULL_CACHE_FILE, encoding="utf-8") as _kf:
                                _kuake_cache = json.load(_kf)
                            for _item in _kuake_cache:
                                if _item.get("subjectId") == int(sub_id):
                                    for _ks in (_item.get("stocks") or []):
                                        _ksid = _ks.get("stockId") or ""
                                        _ksn  = _ks.get("stockName") or ""
                                        _krs  = (_ks.get("reason") or "").strip()
                                        _kgrp = (_ks.get("group") or "").strip()
                                        # 排除污染数据
                                        if not _ksid or _ksid == "111":
                                            continue
                                        if _ksn in ("****", "QQ群", "邮箱", ""):
                                            continue
                                        # reason 索引（关键关联键）
                                        if _krs and _krs not in local_reason_map:
                                            local_reason_map[_krs] = {
                                                "stockId": _ksid, "stockName": _ksn,
                                                "pctChg": _ks.get("pctChg"),
                                                "importance": _ks.get("importance"),
                                            }
                                        # group 末段索引（candidate 池 fallback）
                                        if _kgrp:
                                            _tail = _kgrp.split("/")[-1]
                                            local_group_tail_map.setdefault(_tail, []).append({
                                                "stockId": _ksid, "stockName": _ksn,
                                                "pctChg": _ks.get("pctChg"),
                                                "importance": _ks.get("importance"),
                                            })
                                    break
                        except Exception:
                            pass

                        def fill_node_stocks(groups_list):
                            for g in groups_list:
                                if g.get("children"):
                                    fill_node_stocks(g["children"])
                                    continue
                                raw_stocks = g.get("stocks") or []
                                if not raw_stocks:
                                    continue
                                g_name = (g.get("name") or "").strip()

                                # 收集本 group 已经合法的 stockIds（避免重复填充）
                                api_legit_sids = set()
                                for s in raw_stocks:
                                    sid0 = str(s.get("stockId") or "")
                                    sname0 = s.get("name") or s.get("stockName") or ""
                                    if sid0 and sid0 != "111" and sname0 and sname0 != "****":
                                        api_legit_sids.add(sid0)

                                # 准备 group 内 candidate 池（result.json 同 group 的合法 stocks，按 importance 排序，去重已存在）
                                candidates = []
                                if g_name:
                                    local_same = local_group_tail_map.get(g_name) or []
                                    candidates = [ls for ls in local_same
                                                  if ls.get("stockId") and ls.get("stockId") not in api_legit_sids]
                                    candidates.sort(key=lambda x: -(x.get("importance") or 0))

                                ci = 0  # candidate 索引
                                filled = []
                                for s in raw_stocks:
                                    sid_v  = str(s.get("stockId") or "")
                                    sname  = s.get("name") or s.get("stockName") or ""
                                    reason = s.get("reason") or ""
                                    pct    = s.get("pctChg")
                                    sel_id = s.get("selectedId")

                                    # 1) selectedId 反查（result 本题材 + 完整缓存）
                                    if (sid_v in ("111", "") or sname in ("****", "")) and sel_id:
                                        sel_key = int(sel_id)
                                        ls = local_sel_map.get(sel_key)
                                        if not ls:
                                            fc = _sel_id_index.get(sel_key)
                                            if fc:
                                                ls = fc
                                        if ls:
                                            sid_v = ls.get("stockId","") or sid_v
                                            sname = ls.get("stockName","") or sname
                                            if pct is None:
                                                pct = ls.get("pctChg")

                                    # 2) reason 反查（result 本题材）
                                    if (sid_v in ("111", "") or sname in ("****", "")) and reason:
                                        ls = local_reason_map.get(reason.strip())
                                        if ls:
                                            sid_v = ls.get("stockId","") or sid_v
                                            sname = ls.get("stockName","") or sname
                                            if pct is None:
                                                pct = ls.get("pctChg")

                                    # 3) group 内 candidate 池按位置填充（mixed 情况也覆盖）
                                    if (sid_v in ("111", "") or sname in ("****", "")) and ci < len(candidates):
                                        cand = candidates[ci]
                                        ci += 1
                                        sid_v = cand.get("stockId","") or ""
                                        sname = cand.get("stockName","") or ""
                                        if pct is None:
                                            pct = cand.get("pctChg")

                                    # 4) 最终 fallback：清空字段，前端显示 🔒 受限
                                    if sid_v == "111" or sname == "****":
                                        sid_v = ""
                                        sname = ""
                                    filled.append({"stockId": sid_v, "stockName": sname,
                                                   "reason": reason, "pctChg": pct})
                                g["stocks"] = filled

                        fill_node_stocks(groups)
                        result = groups

                        # txcfgl 401 / 空响应时，从 kuake 缓存 + result.json 双源构建分组
                        # kuake cache 字段更全（带 reason），result.json 兜底
                        if not result:
                            kuake_idx = _load_kuake_stocks_index()
                            try:
                                sid_int = int(sub_id)
                            except (TypeError, ValueError):
                                sid_int = None
                            kuake_stocks = kuake_idx.get(sid_int) or [] if sid_int is not None else []

                            # reason 索引（kuake stockId -> reason），用来补 result.json 缺失的 reason
                            kuake_reason_by_sid = {}
                            for ks in kuake_stocks:
                                ksid = ks.get("stockId") or ""
                                if ksid and ksid != "111":
                                    kuake_reason_by_sid[ksid] = ks.get("reason") or ""

                            # 选用更丰富的源：哪边带 reason 多就用哪边
                            ks_with_reason = sum(1 for s in kuake_stocks if (s.get("reason") or "").strip())
                            ls_with_reason = sum(1 for s in local_stocks if (s.get("reason") or "").strip())
                            primary = kuake_stocks if ks_with_reason >= ls_with_reason and kuake_stocks else local_stocks

                            grouped: dict = {}
                            for s in primary:
                                sid_v = s.get("stockId") or ""
                                snm   = s.get("stockName") or s.get("name") or ""
                                if not sid_v or sid_v == "111":
                                    continue
                                if not snm or snm in ("****", "QQ群", "邮箱"):
                                    continue
                                # reason: 优先本条 → kuake 缓存按 stockId 反查
                                reason = (s.get("reason") or "").strip() or kuake_reason_by_sid.get(sid_v, "")
                                full = (s.get("group") or "").strip()
                                # group 形如 "题材名/分支1/分支2"，剥离题材名前缀
                                segs = [seg for seg in full.split("/") if seg]
                                gname = "/".join(segs[1:]) if len(segs) > 1 else (segs[0] if segs else "全部")
                                grouped.setdefault(gname, []).append({
                                    "stockId":   sid_v,
                                    "stockName": snm,
                                    "reason":    reason,
                                    "pctChg":    s.get("pctChg"),
                                })
                            result = []
                            for gname in grouped:
                                stks = grouped[gname]
                                stks.sort(key=lambda x: -((x.get("pctChg") or 0)))
                                result.append({"name": gname, "pctChg": None,
                                               "stocks": stks, "children": []})
                except Exception:
                    import traceback; traceback.print_exc()
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/subject-query":
            qs     = parse_qs(parsed.query)
            sub_id = qs.get("id", [""])[0].strip()
            result = {}
            if sub_id and _MARKET_OK:
                try:
                    ms = _market_session()
                    if ms:
                        from engine.txcfgl.market import fetch_subject_query
                        d = fetch_subject_query(ms, sub_id) or {}
                        result = {
                            "reason": d.get("reason") or "",
                            "detail": d.get("detail") or "",
                        }
                except Exception:
                    pass
            # txcfgl 401 / 空响应 时回落到 result.json 本地缓存
            if sub_id and not (result.get("reason") or result.get("detail")):
                result = lookup_subject_local(sub_id)
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/subject-lead":
            qs     = parse_qs(parsed.query)
            sub_id = qs.get("id",   [""])[0].strip()
            date_s = qs.get("date", [""])[0].strip()
            result = []
            if sub_id and date_s and _MARKET_OK:
                try:
                    ms = _market_session()
                    if ms:
                        result = fetch_subject_lead(ms, sub_id, date_s)
                except Exception:
                    pass
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/stock-subjects":
            qs  = parse_qs(parsed.query)
            sid = qs.get("id", [""])[0].strip()
            result = []
            if sid and _MARKET_OK:
                try:
                    ms = _market_session()
                    if ms:
                        tree = fetch_stock_subject_tree(ms, sid)
                        # 扁平化：取每个节点的 name/subjectId/pctChg
                        def _flatten(nodes, out):
                            for n in (nodes or []):
                                if n.get("name"):
                                    out.append({
                                        "subjectId":   n.get("subjectId"),
                                        "name":        n.get("name"),
                                        "pctChg":      n.get("pctChg"),
                                    })
                                _flatten(n.get("children") or [], out)
                        _flatten(tree, result)
                except Exception:
                    pass
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/stock-detail":
            qs  = parse_qs(parsed.query)
            sid = qs.get("id", [""])[0].strip()
            result = {"kline": None, "biz": None}
            if sid and _MARKET_OK:
                try:
                    ms = _market_session()
                    if ms:
                        kline = fetch_stock_daily(ms, sid, days=60)
                        raw_biz = fetch_stock_main_business(ms, sid)
                        # 取大类（level 1），转换为 [{label, amount}] 并按金额降序
                        biz_level1 = raw_biz.get("1") or {}
                        biz_list = sorted(
                            [{"label": k, "amount": v} for k, v in biz_level1.items()],
                            key=lambda x: -x["amount"]
                        )
                        result["kline"] = kline
                        result["biz"]   = biz_list[:10]
                except Exception:
                    pass
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/unmask-subject":
            qs     = parse_qs(parsed.query)
            sub_id = qs.get("id", [""])[0].strip()
            if not sub_id:
                body = json.dumps({"ok": False, "error": "缺少 id 参数"}).encode("utf-8")
            else:
                import threading as _th
                _unmask_key = f"_unmask_{sub_id}"
                if getattr(Handler, _unmask_key, False):
                    body = json.dumps({"ok": False, "error": "该题材正在补全中"}).encode("utf-8")
                else:
                    setattr(Handler, _unmask_key, True)
                    result_holder = [None]
                    def _do_unmask():
                        try:
                            result_holder[0] = _unmask_subject(sub_id)
                        finally:
                            setattr(Handler, _unmask_key, False)
                    t = _th.Thread(target=_do_unmask, daemon=True)
                    t.start()
                    t.join(timeout=120)
                    if result_holder[0] is None:
                        body = json.dumps({"ok": False, "error": "补全超时（120s）"}).encode("utf-8")
                    else:
                        body = json.dumps(result_holder[0], ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/trigger-refresh":
            import threading as _th, time as _time
            _now = _time.time()
            if hasattr(Handler, '_last_refresh') and _now - Handler._last_refresh < 300:
                body = json.dumps({"ok": False, "reason": "cooldown"}).encode("utf-8")
            else:
                Handler._last_refresh = _now
                def _do_refresh():
                    try:
                        from engine.txcfgl.incremental import run
                        run()
                    except Exception:
                        try:
                            import subprocess, sys
                            subprocess.run(
                                [sys.executable, "-m", "engine.txcfgl.incremental"],
                                cwd=os.path.dirname(os.path.dirname(__file__)),
                                timeout=120
                            )
                        except Exception:
                            pass
                _th.Thread(target=_do_refresh, daemon=True).start()
                body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif parsed.path == "/api/mgr/health":
            # 管理层：久赢恒丰 服务健康状态
            _send_json(self, _build_mgr_health_response())
            return
        elif parsed.path == "/api/mgr/local-stats":
            # 管理层：F 盘数据源统计
            _send_json(self, _build_mgr_local_stats_response())
            return
        elif parsed.path == "/api/mgr/tasks":
            # 管理层：查询 systemd 服务状态
            _send_json(self, _build_mgr_tasks_get_response(parse_qs(parsed.query)))
            return
        elif parsed.path == "/api/mgr/log-tail":
            # 管理层：读取日志文件末尾
            _send_json(self, _build_mgr_log_tail_response(parse_qs(parsed.query)))
            return
        elif parsed.path == "/api/mgr/request-stats":
            # 管理层：各端点请求统计（总数 / 平均间隔）
            _send_json(self, _build_mgr_request_stats_response())
            return
        elif parsed.path.startswith("/reports/"):
            # 研报文件下载：/reports/{category}/{filename}
            from urllib.parse import unquote
            rel = unquote(parsed.path[len("/reports/"):])
            safe = os.path.normpath(rel)
            if ".." in safe or safe.startswith(os.sep):
                self.send_response(403)
                self.end_headers()
                return
            fpath = os.path.join(os.path.dirname(__file__), "..", "data", "reports", safe)
            if not os.path.isfile(fpath):
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"File not found")
                return
            ext = os.path.splitext(fpath)[1].lower()
            ct_map = {".pdf": "application/pdf", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".ppt": "application/vnd.ms-powerpoint"}
            ct = ct_map.get(ext, "application/octet-stream")
            with open(fpath, "rb") as fp:
                body = fp.read()
            fname = os.path.basename(fpath)
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'inline; filename="{fname}"')
            self.end_headers()
            self.wfile.write(body)
            return
        else:
            self.send_response(404)
            self.end_headers()
            return

        accept_enc = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept_enc:
            body = gzip.compress(body, compresslevel=6)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/tokens"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            self._proxy_tokens("POST", body)
            return
        if parsed.path == "/api/ai-quotes-push":
            # QMT 本机推送实时行情：{"stockId": {pctChg, price, time}, ...}
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                qt = json.loads(raw.decode("utf-8"))
                with _qmt_quotes_lock:
                    _qmt_quotes.update(qt)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception:
                self.send_response(400)
                self.end_headers()
        elif parsed.path == "/api/mgr/tasks":
            # 管理层：控制 systemd 服务（start / stop / restart）
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                _send_json(self, _handle_mgr_tasks_post(raw))
            except Exception as e:
                _send_json(self, json.dumps({"ok": False, "error": str(e)}).encode(), 500)
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser()
    _parser.add_argument("--token", default="", help="久赢恒丰 Bearer Token")
    _parser.add_argument("--port",  type=int, default=PORT)
    _args = _parser.parse_args()
    if _args.token:
        TXCFGL_TOKEN = _args.token
    elif not TXCFGL_TOKEN:
        # 自动从 incremental.py 读取 TOKEN
        try:
            from engine.txcfgl.incremental import TOKEN as _INC_TOKEN
            TXCFGL_TOKEN = _INC_TOKEN
        except Exception:
            pass
    PORT_ = _args.port
    server = _ThreadingHTTPServer(("0.0.0.0", PORT_), Handler)
    print(f"[Web] 运行在 http://0.0.0.0:{PORT_}  大盘看板: {'ON' if TXCFGL_TOKEN else 'OFF (--token 未指定)'}")
    from engine import ag_service as _ags
    _ags.start_background_tasks(_market_session)  # 启动行情+历史K线后台线程
    server.serve_forever()
