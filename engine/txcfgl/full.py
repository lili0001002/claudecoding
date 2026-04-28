"""
久赢恒丰 - 全量查询引擎
功能：拉取 top-history 全部题材（type=2新题材/type=3驱动事件），
      用本地缓存补全被屏蔽的 stockId，保存完整结果到 data/subject_stocks_result.json
用法：python -m engine.txcfgl.full
"""
import requests
import json
import re
import os
from datetime import date


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
CACHE_FILE  = os.path.join(DATA_DIR, "subject_stock_full.json")
OUTPUT_FILE = os.path.join(DATA_DIR, "subject_stocks_result.json")

session = requests.Session()
session.headers.update(HEADERS)

# ===== 本地缓存索引 =====
_selected_id_index = {}   # selectedId -> {stockId, stockName, group}
_subject_id_index  = {}   # subjectId  -> {subjectName, stocks:[...]}


def load_cache():
    """加载本地题材-股票缓存，建立双索引"""
    global _selected_id_index, _subject_id_index
    if not os.path.exists(CACHE_FILE):
        print(f"[缓存] 文件不存在: {CACHE_FILE}，请先运行 engine.kuake.full")
        return
    with open(CACHE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for item in data:
        sid = item.get("subjectId")
        _subject_id_index[sid] = item
        for s in (item.get("stocks") or []):
            sel_id = s.get("selectedId")
            if sel_id:
                _selected_id_index[sel_id] = {
                    "stockId":   s.get("stockId", ""),
                    "stockName": s.get("stockName", ""),
                    "group":     s.get("group", ""),
                }
    print(f"[缓存] {len(_subject_id_index)} 个题材, {len(_selected_id_index)} 个 selectedId 索引")


# ===== 久赢恒丰 API =====

def fetch_top_history(page_num=1, page_size=50, type_=2):
    """拉取 top-history，page_size=50 尽量多拉"""
    r = session.get(
        "https://app.txcfgl.com/api/app/subject/top-history",
        params={"pageNum": page_num, "pageSize": page_size, "keyword": "", "type": type_},
    )
    return r.json()


def fetch_subject_mapping(subject_id, query_date, sort=2):
    r = session.get(
        f"https://app.txcfgl.com/api/app/subject/mapping/{subject_id}",
        params={"date": query_date, "sort": sort},
    )
    return r.json()


def parse_subject_name(description):
    m = re.search(r'【[^：:]+[：:]\s*(.+?)】', description or "")
    return m.group(1).strip() if m else "未知"


def extract_stocks(node, group_path="", result=None):
    """递归提取 mapping stocks，用本地缓存补全被屏蔽的 stockId"""
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
    return stocks


# ===== 主流程 =====

def run(page_size=50):
    load_cache()
    today      = date.today().strftime("%Y-%m-%d")
    all_results = []

    for type_val in [2, 3]:
        page_num = 1
        while True:
            raw  = fetch_top_history(page_num=page_num, page_size=page_size, type_=type_val)
            rows = raw.get("rows") or []
            if not rows:
                break

            for item in rows:
                subject_id   = item["subjectId"]
                rank_date    = (item.get("rankDate") or today)[:10]
                subject_name = parse_subject_name(item.get("description", ""))

                mapping = fetch_subject_mapping(subject_id, query_date=rank_date)
                stocks  = extract_stocks_from_mapping(mapping)

                from_cache = sum(1 for s in stocks if s["source"] == "cache")
                from_api   = sum(1 for s in stocks if s["source"] == "api")
                no_code    = sum(1 for s in stocks if not s["stockId"])

                all_results.append({
                    "type":        type_val,
                    "subjectId":   subject_id,
                    "subjectName": subject_name,
                    "rankDate":    rank_date,
                    "pctChg":      item.get("pctChg"),
                    "stocks":      stocks,
                })

                print(f"[type={type_val} p{page_num}] {subject_name}  {len(stocks)}只"
                      f"  [API:{from_api} 缓存:{from_cache} 无代码:{no_code}]")

            # 判断是否还有下一页
            total = raw.get("total") or 0
            if page_num * page_size >= total:
                break
            page_num += 1

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    total_stocks = sum(len(r["stocks"]) for r in all_results)
    print(f"\n[完成] {len(all_results)} 条记录, 共 {total_stocks} 只股票")
    print(f"  -> {OUTPUT_FILE}")


if __name__ == "__main__":
    run()
