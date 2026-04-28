"""
从久赢恒丰数据构建 AI 产业链图谱
输入: data/subject_stocks_result.json + data/subject_stock_full.json
输出: data/ai_graph.json

用法:
    python -m engine.graph.build_graph
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
RESULT_FILE = os.path.join(ROOT, "data", "subject_stocks_result.json")
FULL_FILE   = os.path.join(ROOT, "data", "subject_stock_full.json")
OUTPUT_FILE = os.path.join(ROOT, "data", "ai_graph.json")

from engine.graph.keyword_map import AI_KEYWORD_MAP, L1_COLORS, LOGIC_CHAINS, L2_STOCK_MAP, match_subject


def _subject_text(r: dict) -> str:
    """合并题材所有文本字段用于关键词匹配"""
    parts = [
        r.get("subjectName", ""),
        r.get("description", ""),
        r.get("reason", ""),
        r.get("detail", ""),
        r.get("bizKey", "") or "",
    ]
    return " ".join(str(p) for p in parts if p)


def build() -> dict:
    # 1. 加载数据
    with open(RESULT_FILE, encoding="utf-8") as f:
        results = json.load(f)
    print(f"[数据] result.json: {len(results)} 条题材")

    # 2. 按 subjectId 去重，保留最新一条（rankDate 最大）
    dedup: dict[str, dict] = {}
    for r in results:
        sid = str(r.get("subjectId", ""))
        if not sid:
            continue
        existing = dedup.get(sid)
        if not existing or (r.get("rankDate", "") > existing.get("rankDate", "")):
            dedup[sid] = r
    print(f"[数据] 去重后: {len(dedup)} 个唯一题材")

    # 3. 构建图谱结构
    # 节点类型: root / l1 / l2 / subject / stock
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_seen: set[tuple[str, str, str]] = set()

    def add_node(nid, **kwargs):
        if nid not in nodes:
            nodes[nid] = {"id": nid, **kwargs}
        return nid

    def add_edge(src, tgt, etype="contains"):
        key = (src, tgt, etype)
        if key in edge_seen:
            return
        edge_seen.add(key)
        edges.append({"source": src, "target": tgt, "type": etype})

    # 根节点
    add_node("root", type="root", name="AI全景图谱", color="#1a1a2e", level=0)

    # L1/L2 骨架
    for l1, l2_dict in AI_KEYWORD_MAP.items():
        l1_id = f"l1::{l1}"
        add_node(l1_id, type="l1", name=l1, color=L1_COLORS.get(l1, "#666"),
                 level=1, subjectCount=0)
        add_edge("root", l1_id)
        for l2 in l2_dict:
            l2_id = f"l2::{l1}::{l2}"
            add_node(l2_id, type="l2", name=l2, color=L1_COLORS.get(l1, "#666"),
                     level=2, parent_l1=l1, subjectCount=0)
            add_edge(l1_id, l2_id)

    # 4. 将题材归入赛道
    unmatched = []
    matched_count = 0

    for sid, r in dedup.items():
        text   = _subject_text(r)
        matches = match_subject(text)
        if not matches:
            unmatched.append(r.get("subjectName", sid))
            continue

        # 主分类（score最高）
        l1_main, l2_main, score = matches[0]
        matched_count += 1

        pct    = r.get("pctChg")
        stocks = r.get("stocks") or []

        subj_node_id = f"subj::{sid}"
        add_node(subj_node_id,
                 type="subject",
                 name=r.get("subjectName", ""),
                 subjectId=int(sid) if sid.isdigit() else sid,
                 pctChg=pct,
                 rankDate=(r.get("rankDate", "") or "")[:10],
                 createTime=r.get("createTime", "") or "",
                 stockCount=len(stocks),
                 level=3,
                 l1=l1_main,
                 l2=l2_main,
                 allMatches=[(m[0], m[1]) for m in matches[:3]],
                 color=L1_COLORS.get(l1_main, "#999"),
                 hot=0)

        l2_id = f"l2::{l1_main}::{l2_main}"
        add_edge(l2_id, subj_node_id)

        # 更新 L1/L2 题材计数
        nodes[l2_id]["subjectCount"] = nodes[l2_id].get("subjectCount", 0) + 1
        l1_id = f"l1::{l1_main}"
        nodes[l1_id]["subjectCount"] = nodes[l1_id].get("subjectCount", 0) + 1

        # 5. 个股节点（只挂当前题材，不重复建立）
        for s in stocks[:20]:  # 每题材最多20只
            stock_id = str(s.get("stockId", "") or "")
            if not stock_id or stock_id == "111":
                continue
            sname = s.get("stockName", "") or ""
            if sname in ("****", ""):
                continue
            stock_node_id = f"stock::{stock_id}"
            add_node(stock_node_id,
                     type="stock",
                     name=sname,
                     stockId=stock_id,
                     pctChg=s.get("pctChg"),
                     level=4,
                     color="#bdbdbd")
            add_edge(subj_node_id, stock_node_id, etype="includes")

    # 6. 逻辑链路（L2 -> L2），用于前端固定展示传导关系
    logic_chains = []
    for c in LOGIC_CHAINS:
        steps = []
        for l1, l2 in (c.get("steps") or []):
            l2_id = f"l2::{l1}::{l2}"
            if l2_id in nodes:
                steps.append({"l1": l1, "l2": l2, "id": l2_id})
        if len(steps) >= 2:
            logic_chains.append({"name": c.get("name", "逻辑链"), "steps": steps})
            for i in range(len(steps) - 1):
                add_edge(steps[i]["id"], steps[i + 1]["id"], etype="logic_chain")

    # 6b. 将 L2_STOCK_MAP 精选股票挂载到对应 L2 节点，并写入 curatedStocks 字段
    curated_count = 0
    for (l1, l2), stocks in L2_STOCK_MAP.items():
        l2_id = f"l2::{l1}::{l2}"
        if l2_id not in nodes:
            continue
        listed = [s for s in stocks if s.get("market") != "未上市"]
        nodes[l2_id]["curatedStocks"] = listed
        for s in listed:
            cs_id = f"cs::{s['code']}::{s['market']}"
            add_node(cs_id,
                     type="curated_stock",
                     name=s["name"],
                     code=s["code"],
                     market=s["market"],
                     note=s.get("note", ""),
                     level=4,
                     color="#90caf9")
            add_edge(l2_id, cs_id, etype="curated")
            curated_count += 1

    print(f"[匹配] 命中: {matched_count}  未匹配: {len(unmatched)}")
    if unmatched:
        print(f"[未匹配示例] {unmatched[:10]}")
    print(f"[精选股票] L2赛道={len(L2_STOCK_MAP)}  精选股票节点={curated_count}")

    # 7. 输出统计
    type_counts = {}
    for n in nodes.values():
        t = n.get("type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
    print(f"[节点统计] {type_counts}")
    print(f"[边数量] {len(edges)}")

    graph = {
        "meta": {
            "generated": __import__("datetime").datetime.now().isoformat(),
            "totalSubjects": matched_count,
            "totalStocks": type_counts.get("stock", 0),
            "logicChains": logic_chains,
        },
        "nodes": list(nodes.values()),
        "edges": edges,
    }
    return graph


def main():
    graph = build()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    print(f"[输出] {OUTPUT_FILE}  ({os.path.getsize(OUTPUT_FILE)//1024} KB)")


if __name__ == "__main__":
    main()
