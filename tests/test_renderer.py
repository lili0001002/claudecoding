"""
测试：engine/web_viewer.py 页面渲染逻辑
覆盖：HTML 结构完整性、静态资源注入、CSS/JS 关键内容
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest


class TestStaticLoading(unittest.TestCase):
    """_load_static：从 engine/static/ 读取文件"""

    def test_css_loaded(self):
        from engine.web_viewer import CSS
        self.assertIsInstance(CSS, str)
        self.assertGreater(len(CSS), 100, "CSS should not be empty")

    def test_js_loaded(self):
        from engine.web_viewer import JS
        self.assertIsInstance(JS, str)
        self.assertGreater(len(JS), 100, "JS should not be empty")

    def test_css_contains_aigraph_classes(self):
        from engine.web_viewer import CSS
        for cls in [".ag-nav", ".ag-step", ".ag-chain", ".ag-streak-badge"]:
            with self.subTest(cls=cls):
                self.assertIn(cls, CSS, f"CSS missing class '{cls}'")

    def test_js_contains_aigraph_functions(self):
        from engine.web_viewer import JS
        for fn in ["_agStreakInfo", "_agApplyHotness", "_agL2StockIds",
                   "_renderAigraphPC", "_agStreakBadge", "_agBuildIndex"]:
            with self.subTest(fn=fn):
                self.assertIn(fn, JS, f"JS missing function '{fn}'")

    def test_load_static_missing_file_returns_empty(self):
        from engine.web_viewer import _load_static
        result = _load_static("nonexistent_file_xyz.css")
        self.assertEqual(result, "")


class TestRenderIndex(unittest.TestCase):
    """render_index：主页 HTML 生成"""

    @classmethod
    def setUpClass(cls):
        # 准备最小化的测试数据
        cls.test_data = [
            {
                "type": 2,
                "name": "测试题材",
                "pctChg": 2.5,
                "rankDate": "2026-02-26T15:00:00",
                "stocks": [{"stockId": "600036", "stockName": "招商银行",
                             "selectedId": 12345, "importance": 1}],
                "reason": "测试原因",
                "detail": "<p>测试详情</p>",
                "bizKey": "test",
                "limitUpCnt": 1,
                "market": {"upCnt": 3000, "downCnt": 1000, "amount": 9999.0,
                           "indexList": []}
            }
        ]

    def test_renders_html(self):
        from engine.web_viewer import render_index
        html = render_index(self.test_data)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<html", html)

    def test_contains_tabs(self):
        from engine.web_viewer import render_index
        html = render_index(self.test_data)
        for tab in ["新题材", "驱动事件", "题材轮动", "AI图谱"]:
            with self.subTest(tab=tab):
                self.assertIn(tab, html)

    def test_aigraph_tab_pane_present(self):
        from engine.web_viewer import render_index
        html = render_index(self.test_data, tab="aigraph")
        self.assertIn("aigraph-wrap", html)
        self.assertIn('data-tab="aigraph"', html)

    def test_css_injected(self):
        from engine.web_viewer import render_index, CSS
        html = render_index(self.test_data)
        # CSS 内容应被注入
        sample = CSS[:50].strip() if CSS else ""
        if sample:
            self.assertIn(sample, html)

    def test_js_injected(self):
        from engine.web_viewer import render_index, JS
        html = render_index(self.test_data)
        sample = JS[:50].strip() if JS else ""
        if sample:
            self.assertIn(sample, html)

    def test_active_tab_honored(self):
        from engine.web_viewer import render_index
        for tab in ["new", "event", "cycle", "aigraph"]:
            with self.subTest(tab=tab):
                html = render_index(self.test_data, tab=tab)
                # 活跃 tab 应有 active class
                self.assertIn(f'data-tab="{tab}"', html)


class TestRenderDetail(unittest.TestCase):
    """render_detail：详情页 HTML 生成"""

    @classmethod
    def setUpClass(cls):
        cls.test_data = [
            {
                "type": 2,
                "name": "AI服务器",
                "pctChg": 3.2,
                "rankDate": "2026-02-26T15:00:00",
                "stocks": [{"stockId": "600036", "stockName": "招商银行",
                             "selectedId": 12345, "importance": 1}],
                "reason": "AI算力爆发",
                "detail": "<p>AI服务器需求激增</p>",
                "bizKey": "ai_server",
                "limitUpCnt": 2,
                "market": {"upCnt": 3000, "downCnt": 1000, "amount": 9999.0,
                           "indexList": []}
            }
        ]

    def test_renders_html(self):
        from engine.web_viewer import render_detail
        html = render_detail(self.test_data, 0)
        self.assertIsInstance(html, str)
        self.assertIn("AI服务器", html)

    def test_out_of_range_returns_error(self):
        from engine.web_viewer import render_detail
        html = render_detail(self.test_data, 99)
        self.assertIn("未找到", html)

    def test_negative_index_returns_error(self):
        from engine.web_viewer import render_detail
        html = render_detail(self.test_data, -1)
        self.assertIn("未找到", html)

    def test_contains_stock_name(self):
        from engine.web_viewer import render_detail
        html = render_detail(self.test_data, 0)
        self.assertIn("招商银行", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
