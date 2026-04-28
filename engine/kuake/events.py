"""
夸克"题材挖掘"（新事件）抓取
端点: GET /ticaiwajue?userid=3064&app_version=1.0.0&app_version_code=1
返回: rows[] 每条含 id/subjectId/subjectName/description/type/createTime/pctChg/heat
  type=2 -> 新题材
  type=3 -> 驱动事件

策略：
  - 单次请求拿最新 20 条
  - 与 data/kuake_events.json 已有数据按 id 去重合并
  - 拟人化：登录后 5-15s 间隔再请求；调度层每小时跑一次
"""
import os
import sys
import json
import time
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from engine.kuake.incremental import kuake_login, kuake_session, KUAKE_BASE, decrypt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data")
EVENTS_FILE = os.path.join(DATA_DIR, "kuake_events.json")

USER_ID = 3064  # 用户实际请求中携带的 userid


def _atomic_save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_existing():
    if not os.path.exists(EVENTS_FILE):
        return []
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception:
        return []


def humanize_sleep(lo=5, hi=15):
    t = random.uniform(lo, hi)
    print(f"[新事件] 拟人化 sleep {t:.1f}s", flush=True)
    time.sleep(t)


def fetch_events():
    r = kuake_session.get(
        f"{KUAKE_BASE}/ticaiwajue",
        params={"userid": USER_ID, "app_version": "1.0.0", "app_version_code": "1"},
        timeout=15,
    )
    txt = r.text.strip()
    d = None
    try:
        d = r.json()
    except Exception:
        try:
            d = decrypt(txt)
        except Exception:
            print(f"[新事件] 解析失败 status={r.status_code} body={txt[:300]!r}", flush=True)
            return None
    if not isinstance(d, dict) or d.get("code") != 200:
        print(f"[新事件] 响应异常: {str(d)[:200]}", flush=True)
        return None
    return d.get("rows") or []


def run():
    if not kuake_login():
        print("[新事件] 登录失败，跳过本次", flush=True)
        return
    humanize_sleep(5, 15)
    rows = fetch_events()
    if rows is None:
        return

    existing = _load_existing()
    seen = {it.get("id") for it in existing if it.get("id") is not None}
    new_items = []
    for it in rows:
        rid = it.get("id")
        if rid is None or rid in seen:
            continue
        seen.add(rid)
        new_items.append(it)

    if new_items:
        merged = existing + new_items
        merged.sort(key=lambda x: x.get("id", 0), reverse=True)
        _atomic_save(EVENTS_FILE, merged)
        print(
            f"[新事件] fetch={len(rows)} new={len(new_items)} total={len(merged)}  "
            f"newest_id={merged[0].get('id')} oldest_id={merged[-1].get('id')}",
            flush=True,
        )
        # 打印新增条目摘要便于日志巡检
        for it in new_items:
            t = it.get("type")
            label = "新题材" if t == 2 else "驱动事件" if t == 3 else f"type={t}"
            desc = (it.get("description") or "").replace("\n", " ")[:80]
            print(
                f"  + id={it.get('id')} {label}  [{it.get('subjectName')}] {desc}",
                flush=True,
            )
        # 顺带补抓新事件中尚未在 kuake 缓存里的题材股票（拟人化、限量）
        _backfill_event_subject_stocks(new_items, max_subjects=5)
    else:
        print(
            f"[新事件] fetch={len(rows)} new=0 total={len(existing)}（无新增）",
            flush=True,
        )


def _backfill_event_subject_stocks(new_items, max_subjects=5):
    """对刚抓到的事件中那些在 subject_stock_full.json 还没建索引的 subjectId，
    拟人化调 /ticaitupu 补一次。单次跑限 max_subjects，剩下的下个 hour 再补。
    避免影响 scheduler 120s 超时。"""
    try:
        from engine.kuake.incremental import (
            fetch_kuake_stocks, extract_stocks, OUTPUT_JSON,
        )
    except Exception as e:
        print(f"[新事件补缓存] import 失败: {e}", flush=True)
        return
    try:
        with open(OUTPUT_JSON, encoding="utf-8") as f:
            cache = json.load(f) or []
    except Exception:
        cache = []
    cache_sids = {it.get("subjectId") for it in cache}

    todo = []
    seen = set()
    for it in new_items:
        sid = it.get("subjectId")
        if not sid or sid in cache_sids or sid in seen:
            continue
        seen.add(sid)
        todo.append((sid, it.get("subjectName") or ""))

    todo = todo[:max_subjects]
    if not todo:
        print("[新事件补缓存] 无需补抓", flush=True)
        return

    print(f"[新事件补缓存] 待补 {len(todo)} 个 subjectId（拟人化间隔）", flush=True)
    added = 0
    for sid, name in todo:
        humanize_sleep(5, 12)
        try:
            tree = fetch_kuake_stocks(sid)
            stocks = extract_stocks(tree, parent_name=name)
            if not stocks:
                print(f"  - sid={sid} {name} 空响应，跳过", flush=True)
                continue
            cache.append({
                "subjectId": sid,
                "subjectName": name,
                "stocks": stocks,
            })
            added += 1
            print(f"  + sid={sid} {name}  +{len(stocks)} 只", flush=True)
        except Exception as e:
            print(f"  ! sid={sid} {name}  ERR {type(e).__name__}: {e}", flush=True)

    if added:
        tmp = OUTPUT_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, OUTPUT_JSON)
        print(f"[新事件补缓存] 写回 cache，新增 {added} 个题材", flush=True)


if __name__ == "__main__":
    run()
