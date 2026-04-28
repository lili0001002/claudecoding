"""
维护工具：修复 result.json 里 stockId 为空但 selectedId 有值的股票
用最新 subject_stock_full.json 缓存重新补全

用法（在服务器上）：
    cd /home/libowei/server/jiuying
    python -m engine.tools.fix_empty_stocks
"""
import json
import os

ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CACHE_FILE  = os.path.join(ROOT, "data", "subject_stock_full.json")
RESULT_FILE = os.path.join(ROOT, "data", "subject_stocks_result.json")


def build_sel_index(cache_file):
    with open(cache_file, encoding="utf-8") as f:
        cache = json.load(f)
    index = {}
    for item in cache:
        for s in (item.get("stocks") or []):
            sel_id = s.get("selectedId")
            sid    = s.get("stockId", "")
            sname  = s.get("stockName", "")
            if sel_id and sid and sid != "111" and sname and sname != "****":
                index[sel_id] = {"stockId": sid, "stockName": sname}
    return index


def run():
    print(f"[缓存] 加载 {CACHE_FILE}")
    sel_index = build_sel_index(CACHE_FILE)
    print(f"[缓存] {len(sel_index)} 个有效 selectedId")

    with open(RESULT_FILE, encoding="utf-8") as f:
        results = json.load(f)

    total_fixed = 0
    total_still_empty = 0

    for r in results:
        fixed_in_record = 0
        for s in (r.get("stocks") or []):
            sid   = s.get("stockId", "")
            sname = s.get("stockName", "")
            sel   = s.get("selectedId")
            if (not sid or sid == "111" or not sname or sname == "****") and sel:
                cached = sel_index.get(sel)
                if cached and cached["stockId"]:
                    s["stockId"]   = cached["stockId"]
                    s["stockName"] = cached["stockName"]
                    s["source"]    = "cache_fixed"
                    fixed_in_record += 1
                else:
                    total_still_empty += 1
        if fixed_in_record:
            total_fixed += fixed_in_record
            print(f"  修复 {fixed_in_record:3d} 只: {r.get('subjectName','')[:40]}")

    print(f"\n共修复: {total_fixed} 只股票")
    print(f"仍然为空（缓存也无）: {total_still_empty} 只")

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[保存] {RESULT_FILE}")


if __name__ == "__main__":
    run()
