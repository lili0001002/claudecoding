"""
部署后自查脚本 (deploy_check.py)
每次变更部署后运行，验证所有关键端点和功能是否正常。

用法:
  远程执行: ssh libowei@10.0.0.2 "cd /home/libowei/server/jiuying && /home/libowei/server/venv/bin/python deploy_check.py"
  或本地:   python deploy_check.py --host http://47.110.85.207:8888
"""
import json
import re
import sys
import urllib.request
import urllib.error

HOST = "http://127.0.0.1:8888"
if "--host" in sys.argv:
    HOST = sys.argv[sys.argv.index("--host") + 1]

CHECKS = []
_pass = 0
_fail = 0


def check(name, func):
    global _pass, _fail
    try:
        ok, detail = func()
        status = "✓" if ok else "✗"
        if ok:
            _pass += 1
        else:
            _fail += 1
        print(f"  {status} {name}: {detail}")
    except Exception as e:
        _fail += 1
        print(f"  ✗ {name}: EXCEPTION {e}")


def http_get(path, timeout=10):
    url = HOST + path
    req = urllib.request.Request(url, headers={"User-Agent": "deploy-check/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def check_home():
    status, body = http_get("/")
    html = body.decode("utf-8", errors="replace")
    has_tabs = "tab-pane" in html
    has_script = "<script>" in html
    # JS 括号平衡检查
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    braces_ok = True
    if scripts:
        code = scripts[0]
        balance = code.count("{") - code.count("}")
        braces_ok = balance == 0
    ok = status == 200 and has_tabs and has_script and braces_ok
    detail = f"HTTP {status}, tabs={has_tabs}, script={has_script}, braces={'OK' if braces_ok else 'MISMATCH:'+str(balance)}"
    return ok, detail


def check_events():
    status, body = http_get("/api/events")
    text = body.decode("utf-8", errors="replace")
    is_html = "<div" in text or "<details" in text
    has_cards = "card" in text
    not_empty = len(text) > 100
    ok = status == 200 and is_html and has_cards and not_empty
    detail = f"HTTP {status}, html={is_html}, cards={has_cards}, len={len(text)}"
    return ok, detail


def check_quotes():
    status, body = http_get("/api/quotes?ids=600519")
    d = json.loads(body)
    ok = status == 200 and isinstance(d, dict)
    detail = f"HTTP {status}, keys={len(d)}"
    return ok, detail


def check_ai_graph():
    status, body = http_get("/api/ai-graph")
    d = json.loads(body)
    nodes = len(d.get("nodes", []))
    chains = len((d.get("meta") or {}).get("logicChains") or [])
    ok = status == 200 and nodes > 0 and chains > 0
    detail = f"HTTP {status}, nodes={nodes}, chains={chains}"
    return ok, detail


def check_subject_tree():
    status, body = http_get("/api/subject-tree")
    d = json.loads(body)
    ok = status == 200 and isinstance(d, list) and len(d) > 0
    detail = f"HTTP {status}, days={len(d) if isinstance(d, list) else 'invalid'}"
    return ok, detail


def check_ai_quotes():
    status, body = http_get("/api/ai-quotes")
    d = json.loads(body)
    ok = status == 200 and isinstance(d, dict)
    detail = f"HTTP {status}, stocks={len(d)}"
    return ok, detail


def check_masked_stocks():
    try:
        import os
        result_file = os.path.join(os.path.dirname(__file__), "data", "subject_stocks_result.json")
        with open(result_file, encoding="utf-8") as f:
            data = json.load(f)
        masked = 0
        for r in data:
            for s in r.get("stocks", []):
                sid = s.get("stockId", "")
                sname = s.get("stockName", "")
                if sid in ("111", "") or sname in ("****", ""):
                    masked += 1
        ok = masked == 0
        detail = f"masked={masked}" if not ok else "clean"
        return ok, detail
    except FileNotFoundError:
        return True, "skip(file not found locally)"


def check_ime_reports():
    status, body = http_get("/api/ime-reports?page=1")
    d = json.loads(body)
    total = d.get("total", -1)
    items = d.get("items", None)
    ok = status == 200 and total >= 0 and isinstance(items, list)
    detail = f"HTTP {status}, total={total}, items={len(items) if items is not None else 'none'}"
    if d.get("error"):
        detail += f", error={d['error']}"
        ok = False
    return ok, detail


def check_ime_posts():
    status, body = http_get("/api/ime-posts?limit=5")
    d = json.loads(body)
    total = d.get("total", -1)
    items = d.get("items", None)
    ok = status == 200 and total >= 0 and isinstance(items, list)
    detail = f"HTTP {status}, total={total}, items={len(items) if items is not None else 'none'}"
    if d.get("error"):
        detail += f", error={d['error']}"
        ok = False
    return ok, detail


def check_gzip():
    url = HOST + "/"
    req = urllib.request.Request(url, headers={
        "User-Agent": "deploy-check/1.0",
        "Accept-Encoding": "gzip"
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        encoding = r.headers.get("Content-Encoding", "")
        ok = "gzip" in encoding.lower()
        size = len(r.read())
        detail = f"encoding={encoding or 'none'}, size={size}"
        return ok, detail


print(f"\n{'='*50}")
print(f"  部署自查 - {HOST}")
print(f"{'='*50}\n")

print("[页面与端点]")
check("首页加载", check_home)
check("驱动事件 /api/events", check_events)
check("行情接口 /api/quotes", check_quotes)
check("AI图谱 /api/ai-graph", check_ai_graph)
check("题材树 /api/subject-tree", check_subject_tree)
check("图谱行情 /api/ai-quotes", check_ai_quotes)
check("研报分析 /api/ime-reports", check_ime_reports)
check("星球帖子 /api/ime-posts", check_ime_posts)

print("\n[数据质量]")
check("屏蔽股票检查", check_masked_stocks)

print("\n[性能]")
check("Gzip压缩", check_gzip)

print(f"\n{'='*50}")
print(f"  结果: {_pass} 通过, {_fail} 失败")
print(f"{'='*50}\n")

sys.exit(0 if _fail == 0 else 1)
