"""
夸克服务器 - 增量更新引擎
功能：对比久赢恒丰题材列表，检测新增/修改题材，从夸克服务器补充股票数据
用法：
  python -m engine.kuake.incremental              # 增量更新
  python -m engine.kuake.incremental --force      # 强制全量重新拉取
  python -m engine.kuake.incremental --id 9063417 9014636  # 只更新指定题材
"""
import requests
import json
import csv
import time
import random
import binascii
import argparse
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
AES_KEY        = "4ZFUgq/mkqveDgNNZ9JZ/A==".encode("utf-8")
AES_IV         = "8ebc27e624514c0e".encode("utf-8")

TXCFGL_TOKEN = _load_txcfgl_token()
TXCFGL_HEADERS = {
    "user-agent": "Dart/3.4 (dart:io)",
    "appplatformbrand": "",
    "appversion": "10407",
    "appplatform": "ANDROID",
    "accept-encoding": "gzip",
    "host": "app.txcfgl.com",
    "authorization": TXCFGL_TOKEN,
}

ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR    = os.path.join(ROOT, "data")
LOG_DIR     = os.path.join(ROOT, "logs")
OUTPUT_JSON = os.path.join(DATA_DIR, "subject_stock_full.json")
OUTPUT_CSV  = os.path.join(DATA_DIR, "subject_stock_full.csv")

# /ticaitupu 需要 Bearer token（通过 /login/v2 获取），服务端自然响应约 2s/次

# ===== 请求统计 =====
_stats = {
    "start_time": None,
    "request_count": 0,
    "success_count": 0,
    "empty_count": 0,
    "fail_count": 0,
    "total_stocks": 0,
    "intervals": [],
    "last_request_time": None,
}


def _record_request(success=True, stock_count=0):
    now = time.time()
    _stats["request_count"] += 1
    if success and stock_count > 0:
        _stats["success_count"] += 1
        _stats["total_stocks"] += stock_count
    elif success:
        _stats["empty_count"] += 1
    else:
        _stats["fail_count"] += 1
    if _stats["last_request_time"]:
        _stats["intervals"].append(round(now - _stats["last_request_time"], 2))
    _stats["last_request_time"] = now



def _save_stats_log(mode="incremental"):
    if _stats["request_count"] == 0:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    elapsed = time.time() - _stats["start_time"] if _stats["start_time"] else 0
    intervals = _stats["intervals"]
    avg_interval = sum(intervals) / len(intervals) if intervals else 0
    min_interval = min(intervals) if intervals else 0
    max_interval = max(intervals) if intervals else 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"kuake_{mode}_{ts}.log")
    lines = [
        f"===== 夸克{mode}抓取统计 =====",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"总耗时: {elapsed:.1f}s ({elapsed/60:.1f}min)",
        f"请求总数: {_stats['request_count']}",
        f"  成功(有数据): {_stats['success_count']}",
        f"  成功(无数据): {_stats['empty_count']}",
        f"  失败: {_stats['fail_count']}",
        f"获取股票总数: {_stats['total_stocks']}",
        f"请求间隔: avg={avg_interval:.2f}s  min={min_interval:.2f}s  max={max_interval:.2f}s",
        f"等效QPS: {_stats['request_count']/elapsed:.2f}" if elapsed > 0 else "等效QPS: N/A",
        f"",
        f"===== 间隔明细 =====",
        *[f"  #{i+1}: {v}s" for i, v in enumerate(intervals)],
    ]
    report = "\n".join(lines)

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n{report}")
    print(f"\n[日志] -> {log_path}")


# ===== Sessions =====
kuake_session  = requests.Session()
kuake_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
})

txcfgl_session = requests.Session()
txcfgl_session.headers.update(TXCFGL_HEADERS)


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


def _looks_like_ad_response(nodes, requested_sid):
    """夸克账号能力不足时会塞「加 QQ 群」式占位响应：stockName='QQ群'，
    或返回别的 subjectId 的股票。识别这种降级响应避免污染缓存。"""
    samples = []
    def _walk(ns):
        for n in ns or []:
            for s in (n.get("stocks") or []):
                samples.append(s)
            _walk(n.get("children") or [])
    _walk(nodes)
    if not samples:
        return False
    bad_names = {"QQ群", "加群", "联系客服", "客服", "vip"}
    for s in samples:
        nm = (s.get("name") or s.get("stockName") or "").strip()
        if nm in bad_names or "QQ" in nm:
            return True
    mismatched = sum(
        1 for s in samples
        if s.get("subjectId") and s.get("subjectId") != requested_sid
    )
    if mismatched and mismatched / len(samples) >= 0.5:
        return True
    return False


def fetch_kuake_stocks(subject_id):
    r   = kuake_session.get(f"{KUAKE_BASE}/ticaitupu", params={"id": subject_id}, timeout=15)
    txt = r.text.strip()
    if not txt:
        _record_request(success=True, stock_count=0)
        return []
    data = None
    try:
        data = r.json().get("data") or []
    except Exception:
        try:
            data = decrypt(txt).get("data") or []
        except Exception:
            _record_request(success=False)
            return []
    if _looks_like_ad_response(data, subject_id):
        print(f"[夸克] subject={subject_id}: 疑似降级/广告响应，丢弃（会员到期/权限不足？）")
        _record_request(success=True, stock_count=0)
        return []
    _record_request(success=True, stock_count=len(data))
    return data


_BAD_STOCK_NAMES = {"QQ群", "邮箱", "加群", "联系客服", "客服", "vip", "VIP"}


def _is_polluted_stock(name):
    if not name:
        return False
    nm = name.strip()
    if nm in _BAD_STOCK_NAMES:
        return True
    if "QQ" in nm or "qq群" in nm.lower():
        return True
    return False


def extract_stocks(nodes, parent_name=""):
    stocks = []
    for node in nodes:
        group = node.get("name") or parent_name
        for s in (node.get("stocks") or []):
            stock_id = s.get("stockId", "")
            stock_name = s.get("name", "")
            # 跳过夸克塞进的"广告/导流"假股（QQ群/邮箱/客服等）
            if _is_polluted_stock(stock_name):
                continue
            if not (stock_id and len(stock_id) == 6 and stock_id.isdigit()):
                continue
            stocks.append({
                "group":      group,
                "stockId":    stock_id,
                "stockName":  stock_name,
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


def fetch_txcfgl_subject_list():
    r     = txcfgl_session.get("https://app.txcfgl.com/api/app/subject/list", timeout=10)
    items = r.json().get("data") or []
    print(f"[久赢恒丰] 获取到 {len(items)} 个题材")
    return items


def load_cache():
    if not os.path.exists(OUTPUT_JSON):
        return {}
    with open(OUTPUT_JSON, encoding="utf-8") as f:
        data = json.load(f)
    return {item["subjectId"]: item for item in data}


def save_cache(cache_dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    data = sorted(cache_dict.values(), key=lambda x: x.get("subjectId", 0))

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["subjectId", "subjectName", "subjectPctChg",
                         "group", "stockId", "stockName", "stockPctChg",
                         "importance", "top", "remark", "reason"])
        for item in data:
            for s in (item.get("stocks") or []):
                writer.writerow([
                    item["subjectId"], item["subjectName"], item.get("pctChg"),
                    s["group"], s["stockId"], s["stockName"], s["pctChg"],
                    s["importance"], s["top"],
                    (s["remark"] or "")[:100], (s["reason"] or "")[:100],
                ])

    total_s = sum(len(v.get("stocks") or []) for v in cache_dict.values())
    print(f"[保存] {len(data)} 个题材, 共 {total_s} 条股票记录")
    print(f"  -> {OUTPUT_JSON}")
    print(f"  -> {OUTPUT_CSV}")


# ===== 主流程 =====

def run(force=False, only_ids=None):
    _stats["start_time"] = time.time()
    kuake_login()

    local_cache     = load_cache()
    print(f"[本地缓存] {len(local_cache)} 个题材")

    remote_subjects = fetch_txcfgl_subject_list()
    remote_index    = {s["subjectId"]: s for s in remote_subjects}

    if only_ids:
        to_update = [remote_index[i] for i in only_ids if i in remote_index]
        print(f"[指定更新] {len(to_update)} 个题材")
    elif force:
        to_update = remote_subjects
        print(f"[强制全量] {len(to_update)} 个题材")
    else:
        to_update = []
        for subj in remote_subjects:
            sid = subj["subjectId"]
            if sid not in local_cache:
                to_update.append(subj)
                print(f"  [新增] {subj.get('name')}({sid})")
            else:
                remote_ut = subj.get("updateTime") or ""
                local_ut  = local_cache[sid].get("updateTime") or ""
                if remote_ut and remote_ut > local_ut:
                    to_update.append(subj)
                    print(f"  [更新] {subj.get('name')}({sid})  {local_ut} -> {remote_ut}")
        print(f"[增量检测] 需更新 {len(to_update)} 个题材")

    if not to_update:
        print("本地数据已是最新，无需更新")
        _save_stats_log("incremental")
        return

    updated, failed = 0, []

    for i, subj in enumerate(to_update):
        sid  = subj["subjectId"]
        name = subj.get("name", "未知")

        nodes  = fetch_kuake_stocks(sid)
        stocks = extract_stocks(nodes)

        if stocks:
            local_cache[sid] = {
                "subjectId":   sid,
                "subjectName": name,
                "level":       subj.get("level"),
                "parentId":    subj.get("parentId"),
                "pctChg":      subj.get("pctChg"),
                "updateTime":  subj.get("updateTime", ""),
                "stocks":      stocks,
            }
            updated += 1
            print(f"  [{i+1}/{len(to_update)}] {name}({sid}): {len(stocks)} 只", flush=True)
        else:
            entry = local_cache.get(sid, {
                "subjectId": sid, "subjectName": name,
                "level": subj.get("level"), "parentId": subj.get("parentId"),
                "stocks": [],
            })
            entry["pctChg"]     = subj.get("pctChg")
            entry["updateTime"] = subj.get("updateTime", "")
            local_cache[sid]    = entry
            failed.append(name)
            print(f"  [{i+1}/{len(to_update)}] {name}({sid}): 夸克无数据，保留旧数据", flush=True)

        # 增量落盘：进程随时可能挂，每抓一个立即写一次磁盘，下次 incremental 模式自动跳过已抓的
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            data = sorted(local_cache.values(), key=lambda x: x.get("subjectId", 0))
            tmp = OUTPUT_JSON + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, OUTPUT_JSON)
        except Exception as e:
            print(f"    [增量落盘失败] {e}", flush=True)

        # 每次请求后随机等待 5~15 秒（服务端自然响应 ~2s，实际总间隔 7~17s）
        delay = random.uniform(5, 15)
        # 每处理 5~10 个后额外长休息，进一步降低风险
        batch_size = getattr(run, "_batch_size", random.randint(5, 10))
        run._batch_size = batch_size
        if (i + 1) % batch_size == 0 and i + 1 < len(to_update):
            long_pause = random.uniform(30, 60)
            print(f"    (批次休息 {long_pause:.0f}s，下批间隔重置)")
            time.sleep(long_pause)
            run._batch_size = random.randint(5, 10)
        else:
            print(f"    (下次间隔 {delay:.1f}s)")
            time.sleep(delay)

    save_cache(local_cache)
    print(f"\n完成: 更新 {updated} 个, 无数据 {len(failed)} 个")
    if failed:
        print(f"  无数据题材: {failed[:10]}")

    _save_stats_log("incremental")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="夸克服务器增量更新")
    parser.add_argument("--force", action="store_true", help="强制全量重新拉取")
    parser.add_argument("--id", type=int, nargs="+", dest="ids", help="只更新指定 subjectId")
    args = parser.parse_args()
    run(force=args.force, only_ids=args.ids)
