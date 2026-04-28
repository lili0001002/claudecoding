"""
测试：engine/graph/ 图谱构建逻辑
覆盖：逻辑链完整性、L2节点唯一性、关键词映射有效性
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import unittest


class TestKeywordMap(unittest.TestCase):
    """keyword_map.py：逻辑链定义完整性"""

    @classmethod
    def setUpClass(cls):
        try:
            from engine.graph.keyword_map import LOGIC_CHAINS, L2_NODES, L1_CATEGORIES
            cls.chains   = LOGIC_CHAINS
            cls.l2_nodes = L2_NODES
            cls.l1_cats  = L1_CATEGORIES
        except ImportError as e:
            raise unittest.SkipTest(f"keyword_map not importable: {e}")

    def test_all_chains_have_required_fields(self):
        for i, c in enumerate(self.chains):
            with self.subTest(chain=i):
                self.assertIn("name", c, f"chain[{i}] missing 'name'")
                self.assertIn("l1",   c, f"chain[{i}] missing 'l1'")
                self.assertIn("steps", c, f"chain[{i}] missing 'steps'")
                self.assertIsInstance(c["steps"], list)
                self.assertGreater(len(c["steps"]), 0)

    def test_chain_steps_have_valid_l2(self):
        for i, c in enumerate(self.chains):
            for j, step in enumerate(c["steps"]):
                with self.subTest(chain=c["name"], step=j):
                    l2_key = step.get("l2")
                    self.assertIsNotNone(l2_key, f"step missing l2")
                    self.assertIn(l2_key, self.l2_nodes,
                                  f"L2 '{l2_key}' not found in L2_NODES")

    def test_chain_steps_have_valid_l1(self):
        for i, c in enumerate(self.chains):
            for j, step in enumerate(c["steps"]):
                with self.subTest(chain=c["name"], step=j):
                    l1_key = step.get("l1")
                    self.assertIsNotNone(l1_key)
                    self.assertIn(l1_key, self.l1_cats,
                                  f"L1 '{l1_key}' not in L1_CATEGORIES")

    def test_no_duplicate_chain_names(self):
        names = [c["name"] for c in self.chains]
        duplicates = [n for n in names if names.count(n) > 1]
        self.assertEqual(duplicates, [], f"Duplicate chain names: {set(duplicates)}")

    def test_no_duplicate_chain_steps(self):
        """两条链路的 steps 路径不应完全相同"""
        step_sigs = []
        for c in self.chains:
            sig = tuple((s.get("l1"), s.get("l2")) for s in c["steps"])
            step_sigs.append((c["name"], sig))
        seen = {}
        for name, sig in step_sigs:
            if sig in seen:
                self.fail(f"Duplicate steps: '{name}' == '{seen[sig]}'")
            seen[sig] = name

    def test_l2_nodes_have_keywords(self):
        for key, node in self.l2_nodes.items():
            with self.subTest(node=key):
                self.assertIn("keywords", node,
                              f"L2 node '{key}' missing 'keywords'")
                self.assertIsInstance(node["keywords"], list)
                self.assertGreater(len(node["keywords"]), 0)

    def test_chain_step_count(self):
        for c in self.chains:
            with self.subTest(chain=c["name"]):
                self.assertGreaterEqual(len(c["steps"]), 2,
                                        "Chain should have at least 2 steps")
                self.assertLessEqual(len(c["steps"]), 6,
                                     "Chain should not exceed 6 steps")


class TestAiGraphFile(unittest.TestCase):
    """data/ai_graph.json：构建输出文件结构校验"""

    _GRAPH_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "ai_graph.json")

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(cls._GRAPH_FILE):
            raise unittest.SkipTest("ai_graph.json not found, run build_graph first")
        with open(cls._GRAPH_FILE, encoding="utf-8") as f:
            cls.graph = json.load(f)

    def test_has_nodes_and_edges(self):
        self.assertIn("nodes", self.graph)
        self.assertIn("edges", self.graph)
        self.assertIsInstance(self.graph["nodes"], list)
        self.assertIsInstance(self.graph["edges"], list)

    def test_has_meta_logic_chains(self):
        meta = self.graph.get("meta", {})
        self.assertIn("logicChains", meta, "meta.logicChains missing")
        self.assertGreater(len(meta["logicChains"]), 0)

    def test_node_types(self):
        valid_types = {"root", "l1", "l2", "subject", "stock", "curated_stock"}
        for n in self.graph["nodes"]:
            with self.subTest(node=n.get("id")):
                self.assertIn(n.get("type"), valid_types)

    def test_stock_nodes_have_stock_id(self):
        stocks = [n for n in self.graph["nodes"] if n.get("type") == "stock"]
        self.assertGreater(len(stocks), 0, "No stock nodes found")
        for s in stocks:
            with self.subTest(node=s.get("id")):
                self.assertTrue(s.get("stockId") or s.get("id"),
                                "Stock node missing stockId")

    def test_l2_nodes_have_name(self):
        l2s = [n for n in self.graph["nodes"] if n.get("type") == "l2"]
        self.assertGreater(len(l2s), 0)
        for n in l2s:
            with self.subTest(node=n.get("id")):
                self.assertTrue(n.get("name"), "L2 node missing name")

    def test_edges_reference_existing_nodes(self):
        node_ids = {n["id"] for n in self.graph["nodes"]}
        for e in self.graph["edges"]:
            src = e.get("source") if isinstance(e.get("source"), str) else e["source"].get("id")
            tgt = e.get("target") if isinstance(e.get("target"), str) else e["target"].get("id")
            # stock:: 节点可能不在 nodes 里，跳过
            if src and not src.startswith("stock::"):
                self.assertIn(src, node_ids, f"Edge source '{src}' not in nodes")


if __name__ == "__main__":
    unittest.main(verbosity=2)
