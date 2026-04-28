"""
久赢恒丰 - 大盘行情 / 交易日历 / 市场宽度模块
提供给 incremental.py 和 web_viewer.py 调用
"""
import requests

BASE = "https://app.txcfgl.com/api/app"


def _session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "user-agent":    "Dart/3.4 (dart:io)",
        "appversion":    "10407",
        "appplatform":   "ANDROID",
        "accept-encoding": "gzip",
        "host":          "app.txcfgl.com",
        "authorization": token,
    })
    return s


# ───────────────────────── 交易日历 ─────────────────────────

def fetch_recent_trade_dates(session, n=1):
    """返回最近 n 个交易日列表，最新在前，如 ['2026-02-25', ...]"""
    r = session.get(f"{BASE}/trade-date/recent/{n}", params={"platform": "app"}, timeout=6)
    d = r.json()
    return d.get("data") or []


def fetch_latest_trade_date(session):
    """返回最近一个交易日字符串，如 '2026-02-25'"""
    dates = fetch_recent_trade_dates(session, 1)
    return dates[0] if dates else None


def is_trade_date(session, date_str):
    """判断 date_str (YYYY-MM-DD) 是否是交易日，返回 bool"""
    r = session.get(f"{BASE}/trade-date/trade", params={"date": date_str}, timeout=6)
    d = r.json().get("data") or {}
    return bool(d.get("trade"))


# ───────────────────────── 大盘指数 ─────────────────────────

# 指数代码 -> 中文名
INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "399106.SZ": "深证综指",
}


def fetch_index_snapshot(session):
    """
    返回大盘四指数快照 dict，结构：
    {
      "000001.SH": {
        "name": "上证指数",
        "close": 4147.23,
        "preClose": 4117.41,
        "pctChg": 0.73,
        "high": 4167.84,
        "low":  4122.70,
        "open": 4123.78,
        "time": "153149",
        "trade_date": "20260225"
      }, ...
    }
    """
    try:
        r = session.get(f"{BASE}/realtime/index", timeout=6)
        raw = r.json().get("data") or {}
        result = {}
        for code, v in raw.items():
            if not isinstance(v, dict):
                continue
            pre = v.get("preClose") or 0
            close = v.get("close") or 0
            pct = round((close - pre) / pre * 100, 2) if pre else 0
            result[code] = {
                "name":       INDEX_NAMES.get(code, code),
                "close":      close,
                "preClose":   pre,
                "pctChg":     pct,
                "high":       v.get("high"),
                "low":        v.get("low"),
                "open":       v.get("open"),
                "time":       v.get("time", ""),
                "trade_date": v.get("trade_date", ""),
            }
        return result
    except Exception:
        return {}


def fetch_market_amount(session):
    """返回大盘成交额快照 {"amount": float, "addAmount": float}"""
    try:
        r = session.get(f"{BASE}/realtime/index-amount", timeout=6)
        return r.json().get("data") or {}
    except Exception:
        return {}


# ───────────────────────── 市场宽度（涨跌统计）─────────────────────────

def fetch_up_down(session, date_str):
    """
    返回指定日期涨跌统计，结构：
    [{"date":"2026-02-25","upCount":3540,"downCount":1529,"stayCount":121}]
    """
    try:
        r = session.get(f"{BASE}/stock/up-down-daily",
                        params={"fromDate": date_str, "toDate": date_str}, timeout=6)
        return r.json().get("data") or []
    except Exception:
        return []


# ───────────────────────── 个股详情 ─────────────────────────

def fetch_stock_daily(session, stock_id, days=60):
    """
    返回个股日K数据，结构：
    {
      "has_more": False,
      "fields": ["ts_code","trade_date","open","high","low","close",
                 "pre_close","change","pct_chg","vol","amount"],
      "items": [[...], ...]
    }
    """
    try:
        r = session.get(f"{BASE}/data/one-stock-daily",
                        params={"stockId": stock_id, "days": days}, timeout=8)
        return r.json().get("data") or {}
    except Exception:
        return {}


def fetch_stock_main_business(session, stock_id):
    """
    返回个股主营业务分类数据，结构：
    {"1": {"产品A": 金额, ...}, "2": {...}, "3": {...}}
    层级 1=大类 2=子类 3=地区
    """
    try:
        r = session.get(f"{BASE}/data/one-stock-main-business/v2",
                        params={"stockId": stock_id}, timeout=8)
        return r.json().get("data") or {}
    except Exception:
        return {}


def fetch_subject_query(session, subject_id):
    """
    返回题材详情元信息：
    {subjectId, name, detail, reason, bizKey, pctChg, level, ...}
    """
    try:
        r = session.get(f"{BASE}/subject/query/{subject_id}", timeout=6)
        return r.json().get("data") or {}
    except Exception:
        return {}


def fetch_subject_parents(session, subject_id):
    """返回题材父级分类列表"""
    try:
        r = session.get(f"{BASE}/subject/parents/{subject_id}", timeout=6)
        return r.json().get("data") or []
    except Exception:
        return []


def fetch_premarket(session, page=1, page_size=20):
    """
    返回盘前必读列表（top-history type=4）
    结构: {"total": N, "rows": [{subjectId, name, description, createTime, rankDate, pctChg}, ...]}
    """
    try:
        r = session.get(f"{BASE}/subject/top-history",
                        params={"pageNum": page, "pageSize": page_size, "type": 4},
                        timeout=8)
        d = r.json()
        rows_raw = d.get("rows") or []
        rows = []
        for item in rows_raw:
            rows.append({
                "subjectId":   item.get("subjectId"),
                "name":        item.get("subjectName") or "",
                "description": item.get("description") or "",
                "createTime":  item.get("createTime") or "",
                "rankDate":    item.get("rankDate") or "",
                "pctChg":      item.get("pctChg"),
            })
        return {"total": d.get("total", 0), "rows": rows}
    except Exception:
        return {"total": 0, "rows": []}


def fetch_subject_child_tree(session, subject_id):
    """返回题材子树（含 stocks），可用于备用 stockId 来源"""
    try:
        r = session.get(f"{BASE}/subject/child-tree/{subject_id}", timeout=8)
        return r.json().get("data") or []
    except Exception:
        return []


def fetch_subject_child_stock_tree(session, subject_id):
    """返回题材关联股票树（扁平化股票列表，含 stockId/stockName/pctChg）"""
    try:
        r = session.get(f"{BASE}/subject/child-stock-tree/{subject_id}", timeout=8)
        return r.json().get("data") or []
    except Exception:
        return []


def fetch_subject_lead(session, subject_id, date_str):
    """
    返回题材领涨/领跌股列表，结构：
    [{"stockId":"600519","stockName":"贵州茅台","pctChg":3.21,"isLead":true}, ...]
    """
    try:
        r = session.get(f"{BASE}/subject/lead/{subject_id}",
                        params={"date": date_str, "subjectId": subject_id}, timeout=6)
        return r.json().get("data") or []
    except Exception:
        return []


def fetch_index_history(session, code="000001.SH", days=365):
    """
    返回指数历史收盘价列表，用于迷你走势图
    结构: [{"trade_date":"20260225","close":3320.5, ...}, ...]
    """
    try:
        r = session.get(f"{BASE}/realtime/index/history/{code}/{days}",
                        params={"platform": "app"}, timeout=8)
        return r.json().get("data") or []
    except Exception:
        return []


def fetch_stock_subject_tree(session, stock_id):
    """
    返回个股关联题材树，结构：
    [{"subjectId":...,"name":...,"pctChg":...,"children":[...]}, ...]
    """
    try:
        r = session.get(f"{BASE}/stock/subject-tree/{stock_id}", timeout=8)
        return r.json().get("data") or []
    except Exception:
        return []


def fetch_subject_history_cycle(session, cycle=1):
    """
    返回历史热门题材按日期分组列表
    cycle: 1=近期
    返回结构: [{"date":"2026-02-25", "rows":[{subjectId,name,pctChg,limitUpTimes,...}, ...]}, ...]
    按日期降序排列
    """
    try:
        r = session.get(f"{BASE}/subject/history-cycle/{cycle}", timeout=8)
        import re as _re
        raw = r.json()
        # 返回结构: {"code":200,"msg":"操作成功","data":{"2026-02-25":[...],...}}
        date_dict = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(date_dict, dict):
            return []
        _date_pat = _re.compile(r'^\d{4}-\d{2}-\d{2}')
        result = []
        for date_str in sorted(date_dict.keys(), reverse=True):
            if not _date_pat.match(str(date_str)):
                continue
            rows = date_dict[date_str]
            if not isinstance(rows, list):
                continue
            result.append({"date": date_str[:10], "rows": rows})
        return result
    except Exception:
        return []


def fetch_realtime_rank(session, start=0, end=49, sort=1):
    """
    返回股票实时排名列表
    sort: 1=涨幅 2=跌幅 3=成交额
    结构: [{"stockId","stockName","pctChg","price","amount"}, ...]
    """
    try:
        r = session.get(f"{BASE}/stock/realtime-rank",
                        params={"start": start, "end": end, "sort": sort}, timeout=8)
        return r.json().get("data") or []
    except Exception:
        return []
