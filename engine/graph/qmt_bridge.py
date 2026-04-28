"""
QMT 行情桥接服务（本机 Windows 运行）
1. 确保 QMT 客户端已启动（调用 qmt_launcher）
2. 订阅 AI图谱所有股票的实时行情
3. 每5秒将行情批量 POST 到服务器 /api/ai-quotes-push

用法:
    python -m engine.graph.qmt_bridge
    python -m engine.graph.qmt_bridge --server http://10.0.0.2:8888
"""
import json
import os
import sys
import time
import logging
import threading
import argparse
import requests

log = logging.getLogger(__name__)

# 默认服务器地址（Ubuntu 局域网）
DEFAULT_SERVER = "http://10.0.0.2:8888"

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
AI_GRAPH_FILE = os.path.join(ROOT, "data", "ai_graph.json")

# QMT xtquant 路径注入
QMT_BIN = r"D:\国金证券QMT交易端\bin.x64"
_xtquant_path = os.path.join(os.path.dirname(QMT_BIN), "userdata_mini", "bin.x64")
for _p in [QMT_BIN, _xtquant_path]:
    if _p not in sys.path and os.path.exists(_p):
        sys.path.insert(0, _p)


def _load_stock_ids() -> list[str]:
    """从 ai_graph.json 提取所有 stockId"""
    try:
        with open(AI_GRAPH_FILE, encoding="utf-8") as f:
            g = json.load(f)
        ids = []
        for n in g.get("nodes", []):
            if n.get("type") == "stock":
                sid = str(n.get("stockId", "") or "")
                if sid and sid != "111":
                    ids.append(sid)
        # 去重
        return list(dict.fromkeys(ids))
    except Exception as e:
        log.warning("[Bridge] 读取 ai_graph.json 失败: %s", e)
        return []


def _xt_code(stock_id: str) -> str:
    """将 6位代码转为 xtdata 格式 (600xxx.SH / 000xxx.SZ)"""
    s = stock_id.strip()
    if s.startswith("6") or s.startswith("9"):
        return s + ".SH"
    elif s.startswith("43") or s.startswith("83") or s.startswith("87"):
        return s + ".BJ"
    else:
        return s + ".SZ"


class QmtBridge:
    def __init__(self, server_url: str, push_interval: int = 5):
        self.server_url  = server_url.rstrip("/")
        self.push_interval = push_interval
        self._quotes: dict = {}   # xtcode -> {pctChg, price, time}
        self._lock = threading.Lock()
        self._stock_ids: list[str] = []
        self._xt_codes:  list[str] = []
        self._running = False

    def start(self):
        from engine.graph.qmt_launcher import ensure_qmt_running
        if not ensure_qmt_running():
            log.error("[Bridge] QMT 启动失败，退出")
            return

        self._stock_ids = _load_stock_ids()
        if not self._stock_ids:
            log.warning("[Bridge] 没有找到股票ID，请先运行 build_graph.py")
            return
        self._xt_codes = [_xt_code(sid) for sid in self._stock_ids]
        log.info("[Bridge] 订阅 %d 只股票", len(self._xt_codes))

        self._running = True
        self._subscribe()
        self._push_loop()

    def _subscribe(self):
        """订阅实时行情快照"""
        try:
            from xtquant import xtdata
            xtdata.connect()

            def on_data(data):
                with self._lock:
                    for xt_code, v in data.items():
                        if not isinstance(v, dict):
                            continue
                        pre   = v.get("lastClose") or v.get("preClose") or 0
                        price = v.get("lastPrice") or v.get("close") or 0
                        pct   = round((price - pre) / pre * 100, 2) if pre else 0
                        # 还原为 6位代码
                        sid = xt_code.split(".")[0]
                        self._quotes[sid] = {
                            "pctChg": pct,
                            "price":  round(price, 2),
                            "time":   str(v.get("time", "")),
                        }

            # 批量订阅（xtdata 建议分批）
            batch = 200
            for i in range(0, len(self._xt_codes), batch):
                xtdata.subscribe_quote(
                    self._xt_codes[i:i+batch],
                    period="tick",
                    count=-1,
                    callback=on_data,
                )
            log.info("[Bridge] 订阅完成")
        except Exception as e:
            log.error("[Bridge] 订阅失败: %s", e)

    def _push_loop(self):
        """每 push_interval 秒推送一次到服务器"""
        log.info("[Bridge] 推送循环启动，间隔 %ds → %s", self.push_interval, self.server_url)
        while self._running:
            time.sleep(self.push_interval)
            with self._lock:
                payload = dict(self._quotes)
            if not payload:
                continue
            try:
                r = requests.post(
                    f"{self.server_url}/api/ai-quotes-push",
                    json=payload,
                    timeout=5,
                )
                log.debug("[Bridge] 推送 %d 条，状态 %s", len(payload), r.status_code)
            except Exception as e:
                log.warning("[Bridge] 推送失败: %s", e)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="QMT 行情桥接服务")
    parser.add_argument("--server",   default=DEFAULT_SERVER, help="服务器地址")
    parser.add_argument("--interval", type=int, default=5,   help="推送间隔(秒)")
    args = parser.parse_args()

    bridge = QmtBridge(server_url=args.server, push_interval=args.interval)
    try:
        bridge.start()
    except KeyboardInterrupt:
        log.info("[Bridge] 已停止")


if __name__ == "__main__":
    main()
