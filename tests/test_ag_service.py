"""
测试：engine/ag_service.py
覆盖：行情工具函数、缓存线程安全、数据结构校验
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

from engine import ag_service


class TestSinaCode(unittest.TestCase):
    """_sina_code: A股代码转新浪格式"""

    def test_sh_prefix(self):
        self.assertEqual(ag_service._sina_code("600036"), "sh600036")
        self.assertEqual(ag_service._sina_code("688021"), "sh688021")

    def test_sz_prefix(self):
        self.assertEqual(ag_service._sina_code("000001"), "sz000001")
        self.assertEqual(ag_service._sina_code("300750"), "sz300750")
        self.assertEqual(ag_service._sina_code("002594"), "sz002594")

    def test_strip_whitespace(self):
        self.assertEqual(ag_service._sina_code(" 600036 "), "sh600036")


class TestFetchQuotes(unittest.TestCase):
    """fetch_quotes: 解析新浪行情响应"""

    _MOCK_RESPONSE = (
        'var hq_str_sh600036="招商银行,38.50,38.10,38.80,39.10,38.00,'
        '38.80,38.82,12345678,987654321.00,100,38.79,200,38.78,300,38.77,'
        '400,38.76,500,38.75,100,38.81,200,38.82,300,38.83,400,38.84,500,'
        '38.85,2026-02-26,15:00:00,00,";'
    )

    def test_parse_pct_change(self):
        mock_resp = MagicMock()
        mock_resp.text = self._MOCK_RESPONSE
        mock_resp.encoding = "gbk"
        with patch("requests.get", return_value=mock_resp):
            result = ag_service.fetch_quotes(["600036"])
        self.assertIn("600036", result)
        pct, price = result["600036"]
        # (38.80 - 38.10) / 38.10 * 100 ≈ +1.837
        self.assertAlmostEqual(pct, (38.80 - 38.10) / 38.10 * 100, places=2)
        self.assertAlmostEqual(price, 38.80, places=2)

    def test_empty_ids_returns_empty(self):
        result = ag_service.fetch_quotes([])
        self.assertEqual(result, {})

    def test_network_error_returns_empty(self):
        with patch("requests.get", side_effect=Exception("timeout")):
            result = ag_service.fetch_quotes(["600036"])
        self.assertEqual(result, {})

    def test_malformed_response_skipped(self):
        mock_resp = MagicMock()
        mock_resp.text = 'var hq_str_sh000001="bad_data";'
        mock_resp.encoding = "gbk"
        with patch("requests.get", return_value=mock_resp):
            result = ag_service.fetch_quotes(["000001"])
        self.assertEqual(result, {})


class TestCacheThreadSafety(unittest.TestCase):
    """缓存读写线程安全：并发读取不崩溃，写入后读取一致"""

    def test_concurrent_reads(self):
        errors = []
        def _read():
            try:
                for _ in range(100):
                    ag_service.get_quote_cache()
                    ag_service.get_history_cache()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_read) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [], f"Thread errors: {errors}")

    def test_get_quote_cache_returns_copy(self):
        """修改返回值不影响内部缓存"""
        with ag_service._quote_lock:
            ag_service._quote_cache["test"] = (1.0, 10.0)
        cache = ag_service.get_quote_cache()
        cache["test"] = (99.0, 99.0)  # 修改副本
        with ag_service._quote_lock:
            self.assertEqual(ag_service._quote_cache["test"], (1.0, 10.0))
        # 清理
        with ag_service._quote_lock:
            ag_service._quote_cache.pop("test", None)

    def test_get_history_cache_returns_copy(self):
        with ag_service._history_lock:
            ag_service._history_cache["2026-01-01"] = {"600036": 1.5}
        cache = ag_service.get_history_cache()
        cache["2026-01-01"]["600036"] = 99.0
        with ag_service._history_lock:
            self.assertEqual(ag_service._history_cache["2026-01-01"]["600036"], 1.5)
        with ag_service._history_lock:
            ag_service._history_cache.pop("2026-01-01", None)


class TestGraphAShareIds(unittest.TestCase):
    """_graph_a_share_ids: 从图谱文件收集A股ID"""

    _MOCK_GRAPH = {
        "nodes": [
            {"type": "stock", "stockId": "600036"},
            {"type": "stock", "stockId": "000001"},
            {"type": "stock", "stockId": "600036"},   # 重复，应去重
            {"type": "l2", "id": "l2::AI::数据中心", "curatedStocks": [
                {"market": "A", "code": "300750"},
                {"market": "HK", "code": "00700"},    # 非A股，应忽略
            ]},
            {"type": "l1", "id": "l1::AI"},           # 非stock节点，应忽略
        ],
        "edges": []
    }

    def test_collects_stock_nodes(self):
        with patch("builtins.open", unittest.mock.mock_open(
                read_data=json.dumps(self._MOCK_GRAPH))):
            ids = ag_service._graph_a_share_ids()
        self.assertIn("600036", ids)
        self.assertIn("000001", ids)

    def test_deduplication(self):
        with patch("builtins.open", unittest.mock.mock_open(
                read_data=json.dumps(self._MOCK_GRAPH))):
            ids = ag_service._graph_a_share_ids()
        self.assertEqual(ids.count("600036"), 1)

    def test_curated_a_shares_included(self):
        with patch("builtins.open", unittest.mock.mock_open(
                read_data=json.dumps(self._MOCK_GRAPH))):
            ids = ag_service._graph_a_share_ids()
        self.assertIn("300750", ids)

    def test_hk_stocks_excluded(self):
        with patch("builtins.open", unittest.mock.mock_open(
                read_data=json.dumps(self._MOCK_GRAPH))):
            ids = ag_service._graph_a_share_ids()
        self.assertNotIn("00700", ids)

    def test_file_not_found_returns_empty(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            ids = ag_service._graph_a_share_ids()
        self.assertEqual(ids, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
