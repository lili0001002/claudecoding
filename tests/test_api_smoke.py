"""
冒烟测试：对运行中的本地服务发 HTTP 请求，验证所有 API 端点正常响应
运行前提：服务已启动（python -m engine.web_viewer）
运行方式：python -m pytest tests/test_api_smoke.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import unittest
import urllib.request
import urllib.error

BASE = os.environ.get("TEST_BASE_URL", "http://10.0.0.2:8888")
TIMEOUT = 10


def _get(path):
    url = BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": "smoke-test/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, r.read()


def _service_available():
    try:
        _get("/")
        return True
    except Exception:
        return False


@unittest.skipUnless(_service_available(), f"Service not available at {BASE}")
class TestApiEndpoints(unittest.TestCase):

    def test_index_returns_html(self):
        status, body = _get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!DOCTYPE html>", body)

    def test_ai_graph_structure(self):
        status, body = _get("/api/ai-graph")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertIn("nodes", d)
        self.assertIn("edges", d)
        self.assertIn("meta", d)
        self.assertIn("logicChains", d["meta"])
        chains = d["meta"]["logicChains"]
        self.assertGreater(len(chains), 0, "No logic chains returned")
        print(f"\n  nodes={len(d['nodes'])}, chains={len(chains)}")

    def test_ai_quotes_returns_dict(self):
        status, body = _get("/api/ai-quotes")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertIsInstance(d, dict)
        print(f"\n  quotes={len(d)}")

    def test_ag_history_structure(self):
        status, body = _get("/api/ag-history")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertIsInstance(d, dict)
        # 历史数据：key 是日期字符串，value 是 {stockId: pct}
        for date_key, stocks in list(d.items())[:3]:
            with self.subTest(date=date_key):
                self.assertRegex(date_key, r"^\d{4}-\d{2}-\d{2}$",
                                 "Date key format should be YYYY-MM-DD")
                self.assertIsInstance(stocks, dict)
        print(f"\n  history_dates={len(d)}")

    def test_subject_tree_returns_list(self):
        status, body = _get("/api/subject-tree")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertIsInstance(d, list)
        print(f"\n  subjects={len(d)}")

    def test_404_for_unknown_path(self):
        try:
            _get("/api/nonexistent-endpoint-xyz")
            self.fail("Expected 404 but got success")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_aigraph_tab_renders(self):
        status, body = _get("/?tab=aigraph")
        self.assertEqual(status, 200)
        self.assertIn(b"aigraph", body.lower())


@unittest.skipUnless(_service_available(), f"Service not available at {BASE}")
class TestStaticAssets(unittest.TestCase):
    """静态资源完整性：CSS/JS 是否被正确注入到 HTML"""

    def test_html_contains_ag_css_classes(self):
        _, body = _get("/?tab=aigraph")
        # aigraph.css 中定义的核心类
        for cls in [b"ag-nav", b"ag-step", b"ag-chain", b"ag-streak-badge"]:
            with self.subTest(cls=cls):
                self.assertIn(cls, body, f"CSS class '{cls}' not found in HTML")

    def test_html_contains_ag_js_functions(self):
        _, body = _get("/?tab=aigraph")
        # aigraph.js 中定义的核心函数
        for fn in [b"_agStreakInfo", b"_agApplyHotness", b"_agL2StockIds",
                   b"_renderAigraphPC", b"_agStreakBadge"]:
            with self.subTest(fn=fn):
                self.assertIn(fn, body, f"JS function '{fn}' not found in HTML")


if __name__ == "__main__":
    unittest.main(verbosity=2)
