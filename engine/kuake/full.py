"""
夸克服务器 - 全量抓取引擎
功能：从久赢恒丰获取题材列表，通过夸克 /ticaitupu 接口拉取股票数据
用法：python -m engine.kuake.full
注意：需先登录获取 Bearer token，服务端响应约 2s/次，630 个题材约 21 分钟
"""
import requests
import json
import csv
import time
import random
import binascii
import os
from datetime import datetime
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


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
KUAKE_BASE     = "http://111.170.164.89:600"
KUAKE_PHONE = os.environ.get("KUAKE_PHONE", "")
KUAKE_PASSWORD = os.environ.get("KUAKE_PASSWORD", "")
TXCFGL_TOKEN = _load_txcfgl_token()
AES_KEY     = "4ZFUgq/mkqveDgNNZ9JZ/A==".encode("utf-8")
AES_IV      = "8ebc27e624514c0e".encode("utf-8")

ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR    = os.path.join(ROOT, "data")
LOG_DIR     = os.path.join(ROOT, "logs")
OUTPUT_JSON = os.path.join(DATA_DIR, "subject_stock_full.json")
OUTPUT_CSV  = os.path.join(DATA_DIR, "subject_stock_full.csv")

# ===== 请求统计 =====
_stats = {
    "start_time":       None,
    "request_count":    0,
    "success_count":    0,
    "empty_count":      0,
    "fail_count":       0,
    "total_stocks":     0,
    "intervals":        [],
    "last_req_time":    None,
}


def _record(success=True, stock_count=0):
    now = time.time()
    _stats["request_count"] += 1
    if success and stock_count > 0:
        _stats["success_count"] += 1
        _stats["total_stocks"] += stock_count
    elif success:
        _stats["empty_count"] += 1
    else:
        _stats["fail_count"] += 1
    if _stats["last_req_time"]:
        _stats["intervals"].append(round(now - _stats["last_req_time"], 2))
    _stats["last_req_time"] = now


def _save_log():
    if not _stats["request_count"]:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    elapsed   = time.time() - _stats["start_time"]
    intervals = _stats["intervals"]
    avg_i = sum(intervals) / len(intervals) if intervals else 0
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    lines = [
        "===== 夸克全量抓取统计 =====",
        f"时间:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"总耗时:     {elapsed:.1f}s ({elapsed/60:.1f}min)",
        f"请求总数:   {_stats['request_count']}",
        f"  有数据:   {_stats['success_count']}",
        f"  无数据:   {_stats['empty_count']}",
        f"  失败:     {_stats['fail_count']}",
        f"股票总数:   {_stats['total_stocks']}",
        f"平均响应:   {avg_i:.2f}s",
        f"等效QPS:    {_stats['request_count']/elapsed:.2f}" if elapsed else "等效QPS: N/A",
    ]
    report = "\n".join(lines)
    log_path = os.path.join(LOG_DIR, f"kuake_full_{ts}.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(report + "\n\n间隔明细:\n" +
                "\n".join(f"  #{i+1}: {v}s" for i, v in enumerate(intervals)))
    print(f"\n{report}")
    print(f"[日志] -> {log_path}")


# ===== HTTP Sessions =====
kuake_session = requests.Session()
kuake_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
})

txcfgl_session = requests.Session()
txcfgl_session.headers.update({
    "user-agent":    "Dart/3.4 (dart:io)",
    "appversion":    "10407",
    "appplatform":   "ANDROID",
    "accept-encoding": "gzip",
    "host":          "app.txcfgl.com",
    "authorization": TXCFGL_TOKEN,
})


# ===== 工具函数 =====

def kuake_login():
    """调用 /login/v2 获取 Bearer token 并设置到 session"""
    try:
        r = kuake_session.post(
            f"{KUAKE_BASE}/login/v2",
            json={"phone": KUAKE_PHONE, "password": KUAKE_PASSWORD, "cid": ""},
            timeout=8,
        )
        data = r.json()
        if not data.get("stat"):
            print(f"[夸克] 登录失败: {data}")
            return False
        token = data["data"]["token"]
        kuake_session.headers.update({"Authorization": f"Bearer {token}"})
        member = data["data"].get("member_type", "")
        expire = data["data"].get("member_expire_time", "")
        print(f"[夸克] 登录成功  会员={member}  到期={expire}")
        return True
    except Exception as e:
        print(f"[夸克] 登录异常: {e}")
        return False


def decrypt(hex_str):
    ct      = binascii.unhexlify(hex_str.strip())
    cipher  = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    raw     = unpad(cipher.decrypt(ct), AES.block_size).decode("utf-8", errors="replace")
    cleaned = "".join(c for c in raw if c >= " " or c in "\n\r\t")
    return json.loads(cleaned)


def fetch_subject_list():
    """从久赢恒丰获取全部题材列表"""
    r = txcfgl_session.get("https://app.txcfgl.com/api/app/subject/list", timeout=10)
    items = r.json().get("data") or []
    print(f"[题材列表] 久赢恒丰返回 {len(items)} 个题材")
    return items


def fetch_stocks(subject_id):
    """GET /ticaitupu?id={subject_id}（需 Bearer token，服务端自然响应 ~2s）"""
    r   = kuake_session.get(f"{KUAKE_BASE}/ticaitupu", params={"id": subject_id}, timeout=15)
    txt = r.text.strip()
    if not txt:
        _record(success=True, stock_count=0)
        return []
    try:
        data = r.json().get("data") or []
        _record(success=True, stock_count=len(data))
        return data
    except Exception:
        pass
    try:
        data = decrypt(txt).get("data") or []
        _record(success=True, stock_count=len(data))
        return data
    except Exception:
        _record(success=False)
        return []


def extract_stocks(nodes, parent_name=""):
    stocks = []
    for node in nodes:
        group = node.get("name") or parent_name
        for s in (node.get("stocks") or []):
            stock_id = s.get("stockId", "")
            if not (stock_id and len(stock_id) == 6 and stock_id.isdigit()):
                continue
            stocks.append({
                "group":      group,
                "stockId":    stock_id,
                "stockName":  s.get("name", ""),
                "pctChg":     s.get("pctChg"),
                "importance": s.get("importance"),
                "top":        s.get("top"),
                "remark":     s.get("remark", ""),
                "reason":     s.get("reason", ""),
                "selectedId": s.get("selectedId"),
                "subjectId":  s.get("subjectId"),
            })
        for child in (node.get("children") or []):
            stocks.extend(extract_stocks([child], group))
    return stocks


def save(all_results):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["subjectId", "subjectName", "subjectPctChg",
                         "group", "stockId", "stockName", "stockPctChg",
                         "importance", "top", "remark", "reason"])
        for res in all_results:
            for s in (res.get("stocks") or []):
                writer.writerow([
                    res["subjectId"], res["subjectName"], res.get("pctChg"),
                    s["group"], s["stockId"], s["stockName"], s["pctChg"],
                    s["importance"], s["top"],
                    (s["remark"] or "")[:100], (s["reason"] or "")[:100],
                ])

    total_s = sum(len(r.get("stocks") or []) for r in all_results)
    total_w = sum(1 for r in all_results if r.get("stocks"))
    print(f"[保存] {len(all_results)} 个题材, {total_w} 个有股票, 共 {total_s} 条")
    print(f"  -> {OUTPUT_JSON}")
    print(f"  -> {OUTPUT_CSV}")


# ===== 主流程 =====

def load_cache():
    if not os.path.exists(OUTPUT_JSON):
        return {}
    with open(OUTPUT_JSON, encoding="utf-8") as f:
        data = json.load(f)
    return {item["subjectId"]: item for item in data}


def run():
    _stats["start_time"] = time.time()
    kuake_login()

    # 加载本地缓存，只补缺
    cache = load_cache()
    print(f"[缓存] 已有 {len(cache)} 个题材")

    subjects   = fetch_subject_list()
    to_fetch   = [s for s in subjects if s.get("subjectId") not in cache]
    skip_count = len(subjects) - len(to_fetch)
    print(f"[跳过] {skip_count} 个（已缓存）  [待抓] {len(to_fetch)} 个")

    if not to_fetch:
        print("所有题材已在缓存中，无需抓取。如需强制刷新请传 force=True")
        _save_log()
        return

    for i, subj in enumerate(to_fetch):
        sid  = subj.get("subjectId")
        name = subj.get("name", "未知")

        nodes  = fetch_stocks(sid)
        stocks = extract_stocks(nodes)

        cache[sid] = {
            "subjectId":   sid,
            "subjectName": name,
            "level":       subj.get("level"),
            "parentId":    subj.get("parentId"),
            "pctChg":      subj.get("pctChg"),
            "updateTime":  subj.get("updateTime", ""),
            "stocks":      stocks,
        }

        tag = f"{len(stocks)} 只" if stocks else "无数据"
        delay = random.uniform(5, 15)
        batch_size = getattr(run, "_batch_size", random.randint(5, 10))
        run._batch_size = batch_size
        if (i + 1) % batch_size == 0 and i + 1 < len(to_fetch):
            long_pause = random.uniform(30, 60)
            print(f"[{i+1:3d}/{len(to_fetch)}] {name}({sid}): {tag}  (批次休息 {long_pause:.0f}s)")
            time.sleep(long_pause)
            run._batch_size = random.randint(5, 10)
        else:
            print(f"[{i+1:3d}/{len(to_fetch)}] {name}({sid}): {tag}  (下次间隔 {delay:.1f}s)")
            time.sleep(delay)

    save(list(cache.values()))
    _save_log()


if __name__ == "__main__":
    run()
