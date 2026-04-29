"""
久赢恒丰 - 增量查询引擎
功能：拉取最新 top-history（type=2新题材/type=3驱动事件），
      用本地缓存补全被屏蔽的 stockId，追加/更新到 data/subject_stocks_result.json
用法：python -m engine.txcfgl.incremental
"""
import requests
import json
import re
import os
import argparse
from datetime import date
import traceback
from engine.notify import send_dingtalk, format_record_message, format_summary_message, send_alert
from engine.txcfgl.market import (
    fetch_index_snapshot, fetch_market_amount, fetch_up_down,
    fetch_latest_trade_date, fetch_subject_query,
)


def _load_txcfgl_token():
    token = os.environ.get("TXCFGL_TOKEN", "")
    if not token:
        try:
            token_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                      "data", "tokens.json")
            with open(token_path, encoding="utf-8") as f:
                token = (json.load(f).get("txcfgl") or {}).get("token") or ""
        except Exception:
            token = ""
    if token and not token.lower().startswith("bearer "):
        token = "Bearer " + token
    return token

# ===== 配置 =====
TOKEN = _load_txcfgl_token()
HEADERS = {
    "user-agent": "Dart/3.4 (dart:io)",
    "appplatformbrand": "",
    "appversion": "10407",
    "appplatform": "ANDROID",
    "accept-encoding": "gzip",
    "host": "app.txcfgl.com",
    "authorization": TOKEN,
}

ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR    = os.path.join(ROOT, "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "subject_stocks_result.json")

session = requests.Session()
session.headers.update(HEADERS)

# ===== 本地 selectedId 缓存索引 =====
# 由夸克 subject_stock_full.json 构建，selectedId -> {stockId, stockName}
_selected_id_index: dict = {}
_subject_stocks_index: dict = {}  # subjectId → [{stockId, stockName, importance, group}]

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                          "data", "subject_stock_full.json")


def load_kuake_cache():
    """加载夸克缓存，构建 selectedId 索引 + subjectId 级别股票索引"""
    global _selected_id_index, _subject_stocks_index
    _selected_id_index   = {}
    _subject_stocks_index = {}
    if not os.path.exists(CACHE_FILE):
        print("[缓存] 夸克缓存不存在，跳过索引构建")
        return
    with open(CACHE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for item in data:
        sub_id = item.get("subjectId")
        stocks_list = []
        for s in (item.get("stocks") or []):
            sel = s.get("selectedId")
            sid = s.get("stockId", "")
            sname = s.get("stockName", "")
            valid = sid and len(str(sid)) == 6 and str(sid).isdigit()
            if valid:
                # selectedId 索引（用于精确匹配屏蔽股票）
                if sel:
                    _selected_id_index[sel] = {
                        "stockId":   sid,
                        "stockName": sname,
                    }
                # subjectId 级别索引（用于整题材回退）
                stocks_list.append({
                    "stockId":   sid,
                    "stockName": sname,
                    "selectedId": sel,
                    "importance": s.get("importance"),
                    "group":     s.get("group", ""),
                    "source":    "cache",
                })
        if sub_id and stocks_list:
            _subject_stocks_index[sub_id] = stocks_list
    print(f"[缓存] 夸克 selectedId 索引: {len(_selected_id_index)} 条  "
          f"subjectId 索引: {len(_subject_stocks_index)} 个题材")


def load_existing_results():
    """加载已有结果，以 (subjectId, rankDate) 为 key 去重"""
    if not os.path.exists(OUTPUT_FILE):
        return [], {}
    with open(OUTPUT_FILE, encoding="utf-8") as f:
        data = json.load(f)
    index = {(r["subjectId"], r["rankDate"]): i for i, r in enumerate(data)}
    return data, index


# ===== 久赢恒丰 API =====

def fetch_top_history(page_num=1, page_size=10, type_=2):
    r = session.get(
        "https://app.txcfgl.com/api/app/subject/top-history",
        params={"pageNum": page_num, "pageSize": page_size, "keyword": "", "type": type_},
    )
    data = r.json()
    # Token 失效检测：HTTP 401 或业务码 401/未授权
    if r.status_code in (401, 403):
        send_alert("Token已失效，请更新",
                   f"HTTP {r.status_code}\n请更新 engine/txcfgl/incremental.py 顶部 TOKEN")
        raise RuntimeError(f"Token失效 HTTP {r.status_code}")
    code = data.get("code") or data.get("status")
    msg  = data.get("msg") or data.get("message") or ""
    if code in (401, 403) or "token" in msg.lower() or "未授权" in msg or "登录" in msg:
        send_alert("Token已失效，请更新",
                   f"接口返回: code={code} msg={msg}\n请更新 engine/txcfgl/incremental.py 顶部 TOKEN")
        raise RuntimeError(f"Token失效: {msg}")
    return data


def fetch_subject_mapping(subject_id, query_date, sort=2):
    r = session.get(
        f"https://app.txcfgl.com/api/app/subject/mapping/{subject_id}",
        params={"date": query_date, "sort": sort},
    )
    return r.json()


def fetch_hot_subjects(page_size=30):
    """拉取热门题材列表（挖掘榜），返回含真实 subjectId 的题材列表"""
    r = session.get(
        "https://app.txcfgl.com/api/app/subject/hot",
        params={"pageNum": 1, "pageSize": page_size},
    )
    data = r.json()
    if r.status_code in (401, 403):
        send_alert("Token已失效，请更新",
                   f"HTTP {r.status_code}\n请更新 engine/txcfgl/incremental.py 顶部 TOKEN")
        raise RuntimeError(f"Token失效 HTTP {r.status_code}")
    return data.get("rows") or data.get("data") or []


def fetch_child_stock_tree(subject_id):
    """拉取题材的股票树结构（含分组层级），替代旧 mapping 接口"""
    r = session.get(
        f"https://app.txcfgl.com/api/app/subject/child-stock-tree/{subject_id}",
    )
    return r.json()


def parse_subject_name(description):
    m = re.search(r'【[^：:]+[：:]\s*(.+?)】', description or "")
    return m.group(1).strip() if m else "未知"


def extract_stocks(node, group_path="", result=None):
    if result is None:
        result = []
    name         = node.get("name", "")
    current_path = f"{group_path}/{name}".strip("/") if name else group_path

    for s in (node.get("stocks") or []):
        selected_id  = s.get("selectedId")
        raw_stock_id = s.get("stockId", "")
        raw_name     = s.get("name", "")

        masked = (raw_stock_id in ("111", "") or raw_name in ("****", ""))
        if masked and selected_id:
            cached     = _selected_id_index.get(selected_id)
            stock_id   = cached["stockId"]   if cached else ""
            stock_name = cached["stockName"] if cached else ""
            source     = "cache" if cached else "masked"
        elif masked:
            stock_id   = ""
            stock_name = ""
            source     = "masked"
        else:
            stock_id   = raw_stock_id
            stock_name = raw_name
            source     = "api"

        result.append({
            "group":      current_path,
            "selectedId": selected_id,
            "stockId":    stock_id,
            "stockName":  stock_name,
            "pctChg":     s.get("pctChg"),
            "importance": s.get("importance"),
            "reason":     s.get("reason", ""),
            "source":     source,
        })

    for child in (node.get("children") or []):
        extract_stocks(child, current_path, result)
    return result


def extract_stocks_from_mapping(data):
    stocks = []
    for item in (data.get("data") or []):
        extract_stocks(item, "", stocks)
    # 按 stockId 去重，保留 importance 最高的那条
    # key 优先用 stockId，其次用 selectedId，两者都没有才丢弃
    seen = {}
    for s in stocks:
        stock_id  = s.get("stockId")
        sel_id    = s.get("selectedId")
        key = stock_id if stock_id else (f"sel:{sel_id}" if sel_id else None)
        if not key:
            continue
        if key not in seen or (s.get("importance") or 0) > (seen[key].get("importance") or 0):
            seen[key] = s
    return list(seen.values())


def extract_stocks_from_tree(data_list):
    """从 child-stock-tree 数据提取股票，使用 selectedId 缓存反解屏蔽股票"""
    stocks = []

    def walk(node, group_path=""):
        name = node.get("name", "")
        current = f"{group_path}/{name}".strip("/") if name else group_path
        for s in (node.get("stocks") or []):
            selected_id  = s.get("selectedId")
            raw_stock_id = s.get("stockId", "")
            raw_name     = s.get("name", "")
            masked = (raw_stock_id in ("111", "") or raw_name in ("****", ""))
            if masked and selected_id:
                cached     = _selected_id_index.get(selected_id)
                stock_id   = cached["stockId"]   if cached else ""
                stock_name = cached["stockName"] if cached else ""
                source     = "cache" if cached else "masked"
            elif masked:
                stock_id   = ""
                stock_name = ""
                source     = "masked"
            else:
                stock_id   = raw_stock_id
                stock_name = raw_name
                source     = "api"
            stocks.append({
                "group":      current,
                "selectedId": selected_id,
                "stockId":    stock_id,
                "stockName":  stock_name,
                "pctChg":     s.get("pctChg"),
                "importance": s.get("importance"),
                "reason":     s.get("reason", ""),
                "createTime": s.get("createTime", ""),
                "source":     source,
            })
        for child in (node.get("children") or []):
            walk(child, current)

    for node in (data_list or []):
        walk(node)

    seen = {}
    for s in stocks:
        stock_id = s.get("stockId")
        sel_id   = s.get("selectedId")
        key = stock_id if stock_id else (f"sel:{sel_id}" if sel_id else None)
        if not key:
            continue
        if key not in seen or (s.get("importance") or 0) > (seen[key].get("importance") or 0):
            seen[key] = s
    return list(seen.values())


def get_fallback_stocks_from_kuake(subject_id):
    """从夸克 subjectId 索引直接获取股票（用于 selectedId 无法匹配的情况）"""
    return list(_subject_stocks_index.get(subject_id) or [])


def load_kuake_new_subject_fallback(today: str) -> list:
    """txcfgl hot 为空时，从 kuake_events.json 兜底当日 type=2 新题材。"""
    events_file = os.path.join(DATA_DIR, "kuake_events.json")
    try:
        with open(events_file, encoding="utf-8") as f:
            rows = json.load(f) or []
    except Exception:
        return []

    out = []
    seen = set()
    for ev in rows:
        if ev.get("type") != 2:
            continue
        create_time = ev.get("createTime") or ""
        if create_time[:10] != today:
            continue
        subject_id = ev.get("subjectId")
        if not subject_id or subject_id in seen:
            continue
        seen.add(subject_id)

        desc = ev.get("description") or ""
        head = desc.lstrip()
        end = head.find("】")
        subject_name = head[:end + 1] if head.startswith("【") and end > 0 else (ev.get("subjectName") or "")
        stocks = get_fallback_stocks_from_kuake(subject_id)
        out.append({
            "subjectId": subject_id,
            "subjectName": subject_name or ev.get("subjectName") or f"topic_{subject_id}",
            "rankDate": create_time[:10] or today,
            "createTime": create_time,
            "pctChg": ev.get("pctChg"),
            "detail": desc,
            "reason": ev.get("subjectName") or "",
            "limitUpCount": None,
            "stockCount": len(stocks),
            "stocks": stocks,
        })
    return out




# ===== 夸克补全（仅对新题材、有屏蔽股票时触发，使用拟人化间隔）=====

def _kuake_patch(subject_ids):
    """对指定新题材调用夸克增量接口补全 selectedId 缓存，失败时告警"""
    try:
        from engine.kuake.incremental import run as kuake_run
        print(f"[夸克] 补全 {len(subject_ids)} 个新题材: {subject_ids}")
        kuake_run(only_ids=subject_ids)   # 内部已有 5~15s 随机延迟
        # 重建 selectedId 索引
        global _selected_id_index
        _selected_id_index = {}
        load_kuake_cache()
    except Exception as e:
        print(f"[夸克] 补全失败: {e}")
        send_alert("夸克补全失败",
                   f"新题材 {subject_ids} 补全异常\n"
                   f"错误: {type(e).__name__}: {e}\n"
                   f"请检查夸克账号是否有效")


# ===== 主流程 =====

def run(pages=1, types=None, page_size=10, use_hot=True):
    """增量更新。
    use_hot=True（默认）: 使用 /subject/hot + child-stock-tree（账号无需高级订阅）
    use_hot=False: 回退到旧 top-history type=2/3 路径（需高级订阅）
    """
    load_kuake_cache()   # 启动时加载夸克 selectedId 缓存
    if types is None:
        types = [2, 3]

    today = date.today().strftime("%Y-%m-%d")

    # 拉取大盘快照（一次，供本次所有 record 共享）
    market_snapshot = {}
    try:
        idx_data   = fetch_index_snapshot(session)
        amount_data = fetch_market_amount(session)
        up_down    = fetch_up_down(session, today)
        ud = up_down[0] if up_down else {}
        market_snapshot = {
            "index":     idx_data,
            "amount":    amount_data.get("amount"),
            "addAmount": amount_data.get("addAmount"),
            "upCount":   ud.get("upCount"),
            "downCount": ud.get("downCount"),
            "stayCount": ud.get("stayCount"),
        }
        total = (ud.get("upCount") or 0) + (ud.get("downCount") or 0) + (ud.get("stayCount") or 0)
        sh = idx_data.get("000001.SH", {})
        print(f"[大盘] 上证{sh.get('close','?')} ({'+' if (sh.get('pctChg') or 0)>=0 else ''}{sh.get('pctChg','?')}%)  "
              f"涨:{ud.get('upCount','?')} 跌:{ud.get('downCount','?')} 平:{ud.get('stayCount','?')}  "
              f"成交额:{round((amount_data.get('amount') or 0)/1e8, 0):.0f}亿")
    except Exception as e:
        print(f"[大盘] 获取失败: {e}")

    # 加载已有结果（用于去重/覆盖更新）
    existing, existing_index = load_existing_results()
    new_count = 0
    update_count = 0
    this_run_records = []   # 本次新增/更新的记录，后处理只扫这部分

    if use_hot:
        # ── 新路径：/subject/hot + child-stock-tree ─────────────────
        print("[模式] hot+tree（无需高级订阅）")
        hot_rows = fetch_hot_subjects(page_size=30)
        print(f"[热门题材] 获取 {len(hot_rows)} 条")
        page_items = []
        ids_need_patch = []
        fallback_rows = []
        if not hot_rows:
            fallback_rows = load_kuake_new_subject_fallback(today)
            print(f"[热门题材] txcfgl 为空，kuake 新题材兜底 {len(fallback_rows)} 条")

        for item in hot_rows:
            subject_id   = item.get("subjectId") or item.get("id")
            subject_name = item.get("name") or item.get("subjectName", "")
            rank_date    = today
            tree_data    = fetch_child_stock_tree(subject_id).get("data") or []
            stocks       = extract_stocks_from_tree(tree_data)
            no_code      = sum(1 for s in stocks if not s["stockId"])
            key          = (subject_id, rank_date)

            # 夸克补全触发条件（有屏蔽股票时）：
            # 1. 当天首次出现的 hot 题材 → 每天刷新一次夸克数据
            # 2. kuake 缓存完全没有此题材 → 全新题材
            if no_code > 0 and (key not in existing_index
                                or not _subject_stocks_index.get(subject_id)):
                ids_need_patch.append(subject_id)

            page_items.append((item, subject_id, rank_date, subject_name, stocks, key))

        for rec in fallback_rows:
            subject_id   = rec.get("subjectId")
            subject_name = rec.get("subjectName", "")
            rank_date    = rec.get("rankDate") or today
            stocks       = rec.get("stocks") or []
            key          = (subject_id, rank_date)
            page_items.append((rec, subject_id, rank_date, subject_name, stocks, key))

        if ids_need_patch:
            _kuake_patch(ids_need_patch)
            # _kuake_patch 内部已 load_kuake_cache()，此处再调一次确保生效
            load_kuake_cache()
            patched = set(ids_need_patch)
            page_items_new = []
            for (item, subject_id, rank_date, subject_name, stocks, key) in page_items:
                if subject_id in patched:
                    tree_data = fetch_child_stock_tree(subject_id).get("data") or []
                    stocks    = extract_stocks_from_tree(tree_data)
                # 统一应用 subjectId 级别回退（使用最新缓存）
                if not any(s["stockId"] for s in stocks):
                    fallback = get_fallback_stocks_from_kuake(subject_id)
                    if fallback:
                        stocks = fallback
                page_items_new.append((item, subject_id, rank_date, subject_name, stocks, key))
            page_items = page_items_new
        else:
            # 无需补全时也统一应用 subjectId 回退
            page_items_new = []
            for (item, subject_id, rank_date, subject_name, stocks, key) in page_items:
                if not any(s["stockId"] for s in stocks):
                    fallback = get_fallback_stocks_from_kuake(subject_id)
                    if fallback:
                        stocks = fallback
                page_items_new.append((item, subject_id, rank_date, subject_name, stocks, key))
            page_items = page_items_new

        for item, subject_id, rank_date, subject_name, stocks, key in page_items:
            from_cache = sum(1 for s in stocks if s["source"] == "cache")
            from_api   = sum(1 for s in stocks if s["source"] == "api")
            no_code    = sum(1 for s in stocks if not s["stockId"])
            type_val   = 2  # hot 模式统一用 type=2（显示在"新题材" tab）

            # 拉取题材详情元信息（detail/reason/bizKey 等）
            try:
                meta = fetch_subject_query(session, subject_id)
            except Exception:
                meta = {}

            record = {
                "type":        type_val,
                "subjectId":   subject_id,
                "subjectName": subject_name,
                "rankDate":    rank_date,
                "createTime":  item.get("createTime", ""),
                "pctChg":      item.get("pctChg"),
                "detail":      meta.get("detail") or item.get("detail", ""),
                "reason":      meta.get("reason") or item.get("reason", ""),
                "bizKey":      meta.get("bizKey", ""),
                "limitUpCount":item.get("limitUpCount") or meta.get("limitUpCount"),
                "stockCount":  item.get("stockCount")  or meta.get("stockCount"),
                "market":      market_snapshot,
                "stocks":      stocks,
            }

            key = (subject_id, rank_date)
            if key in existing_index:
                existing[existing_index[key]] = record
                update_count += 1
                tag = "更新"
            else:
                existing_index[key] = len(existing)
                existing.append(record)
                this_run_records.append(record)
                new_count += 1
                tag = "新增"

            mode_label = "hot" if use_hot else f"type={type_val} p{page_num if not use_hot else 1}"
            print(f"[{tag} {mode_label}] {subject_name}  "
                  f"{len(stocks)}只  [API:{from_api} 缓存:{from_cache} 无代码:{no_code}]")

        # 驱动事件改由 engine/kuake/events.py 写入 data/kuake_events.json，
        # web_viewer 直接读取该文件。这里只清理残留的旧 _from_hot_stocks 记录。
        before = len(existing)
        existing[:] = [
            r for r in existing
            if not (r.get("type") == 3 and (
                r.get("_from_hot_stocks") or
                (r.get("subjectName") or "").startswith("【驱动事件")
            ))
        ]
        removed = before - len(existing)
        if removed:
            print(f"[驱动事件] 清理旧 hot-stocks 兜底记录 {removed} 条")

    else:
        # ── 旧路径：top-history type=2/3（需高级订阅，当前账号已失效）─
        print("[模式] top-history（旧路径，需高级订阅）")
        for type_val in types:
            for page_num in range(1, pages + 1):
                raw  = fetch_top_history(page_num=page_num, page_size=page_size, type_=type_val)
                rows = raw.get("rows") or []
                if not rows:
                    print(f"[type={type_val} p{page_num}] 无数据，停止")
                    break

                page_items = []
                ids_need_patch = []
                for item in rows:
                    subject_id   = item["subjectId"]
                    rank_date    = (item.get("rankDate") or today)[:10]
                    subject_name = parse_subject_name(item.get("description", ""))
                    mapping      = fetch_subject_mapping(subject_id, query_date=rank_date)
                    stocks       = extract_stocks_from_mapping(mapping)
                    no_code      = sum(1 for s in stocks if not s["stockId"])
                    key          = (subject_id, rank_date)
                    if no_code > 0:
                        if key not in existing_index:
                            ids_need_patch.append(subject_id)
                        else:
                            old_rec = existing[existing_index[key]]
                            old_masked = sum(1 for s in old_rec.get("stocks", [])
                                             if s.get("source") == "masked")
                            if old_masked > 0:
                                ids_need_patch.append(subject_id)
                    page_items.append((item, subject_id, rank_date, subject_name, stocks, key))

                if ids_need_patch:
                    _kuake_patch(ids_need_patch)
                    patched = set(ids_need_patch)
                    page_items_new = []
                    for (item, subject_id, rank_date, subject_name, stocks, key) in page_items:
                        if subject_id in patched:
                            mapping = fetch_subject_mapping(subject_id, query_date=rank_date)
                            stocks  = extract_stocks_from_mapping(mapping)
                        page_items_new.append((item, subject_id, rank_date, subject_name, stocks, key))
                    page_items = page_items_new

                for item, subject_id, rank_date, subject_name, stocks, key in page_items:
                    from_cache = sum(1 for s in stocks if s["source"] == "cache")
                    from_api   = sum(1 for s in stocks if s["source"] == "api")
                    no_code    = sum(1 for s in stocks if not s["stockId"])

                    try:
                        meta = fetch_subject_query(session, subject_id)
                    except Exception:
                        meta = {}

                    record = {
                        "type":        type_val,
                        "subjectId":   subject_id,
                        "subjectName": subject_name,
                        "rankDate":    rank_date,
                        "createTime":  item.get("createTime", ""),
                        "pctChg":      item.get("pctChg"),
                        "detail":      meta.get("detail", ""),
                        "reason":      meta.get("reason", ""),
                        "bizKey":      meta.get("bizKey", ""),
                        "limitUpCount":item.get("limitUpCount") or meta.get("limitUpCount"),
                        "stockCount":  item.get("stockCount")  or meta.get("stockCount"),
                        "market":      market_snapshot,
                        "stocks":      stocks,
                    }

                    key = (subject_id, rank_date)
                    if key in existing_index:
                        existing[existing_index[key]] = record
                        update_count += 1
                        tag = "更新"
                    else:
                        existing_index[key] = len(existing)
                        existing.append(record)
                        this_run_records.append(record)
                        new_count += 1
                        tag = "新增"

                    print(f"[{tag} type={type_val} p{page_num}] {subject_name}  "
                          f"{len(stocks)}只  [API:{from_api} 缓存:{from_cache} 无代码:{no_code}]")

    # 后处理：统计本次新增记录中仍有屏蔽的股票，告警
    if this_run_records:
        still_masked = []
        cache_fixed  = 0
        for r in this_run_records:
            m = [s for s in r.get("stocks", []) if s.get("source") == "masked"]
            c = sum(1 for s in r.get("stocks", []) if s.get("source") == "cache")
            cache_fixed += c
            if m:
                still_masked.append(f"{r['subjectName']}({r['subjectId']}): {len(m)}只仍屏蔽")

        if cache_fixed:
            print(f"[补全] 夸克缓存修复 {cache_fixed} 只屏蔽股票")
        if still_masked:
            print(f"[屏蔽] {len(still_masked)} 个新题材仍有屏蔽股票（夸克缓存未覆盖）:")
            for line in still_masked:
                print(f"  - {line}")
            send_alert("新题材仍有屏蔽股票",
                       f"夸克补全后仍有 {len(still_masked)} 个题材未解析\n" +
                       "\n".join(still_masked[:10]) +
                       "\n可在网页端点击「补全屏蔽」按钮手动触发")

    # 保存
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    total_stocks = sum(len(r["stocks"]) for r in existing)
    print(f"\n[完成] 新增:{new_count} 更新:{update_count}  "
          f"共 {len(existing)} 条记录, {total_stocks} 只股票")
    print(f"  -> {OUTPUT_FILE}")

    # 钉钉推送（仅有新增时才推送，避免无意义打扰）
    if new_count > 0:
        orig_len = len(existing) - new_count
        new_records = [r for i, r in enumerate(existing) if i >= orig_len]
        title, content = format_summary_message(new_count, update_count,
                                                len(existing), total_stocks, new_records)
        ok = send_dingtalk(title, content)
        print(f"[钉钉] 汇总推送{'成功' if ok else '失败'}")
    else:
        print(f"[钉钉] 无新增，跳过推送")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="久赢恒丰增量查询")
    parser.add_argument("--pages", type=int, default=1, help="拉取页数（默认1页）")
    parser.add_argument("--type",  type=int, nargs="+", dest="types",
                        choices=[2, 3], help="type=2新题材 type=3驱动事件（默认两者都拉）")
    parser.add_argument("--size",  type=int, default=10, help="每页条数（默认10）")
    args = parser.parse_args()
    try:
        run(pages=args.pages, types=args.types, page_size=args.size)
    except RuntimeError:
        # Token失效已在内部告警，直接退出
        raise
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[异常] {e}\n{tb}")
        send_alert("久赢恒丰执行异常", f"{type(e).__name__}: {e}\n\n{tb}")
        raise
