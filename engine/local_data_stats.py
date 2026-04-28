"""
量化数据源扫描模块
业务归属：久赢恒丰（量化数据管理）

管理层通过 /api/mgr/local-stats HTTP 接口调用此模块，获取本机数据盘统计。
独立可导入，无外部依赖，仅使用标准库 os / datetime。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List

# ── 数据盘源定义（D: 优先，F: 兼容）──────────────────────────────────
DATA_SOURCES: List[Dict[str, Any]] = [
    {
        "key":       "a_stock_l2",
        "label":     "沪深京A股  Level2 逐笔",
        "icon":      "📈",
        "path":      r"D:\2026\2026 Level2 Data",
        "mode":      "date_tree",   # {YYYYMM}/{YYYY-MM-DD}/{code}.csv
        "desc":      "TranID/Time/Price/Volume/Type…11列",
        "alt_paths": [r"F:\2026\2026 Level2 Data"],
    },
    {
        "key":       "bj_l2",
        "label":     "北交所  Level2 Tick",
        "icon":      "📊",
        "path":      r"D:\bj_level2_data",
        "mode":      "month_flat",  # {code}_BJ/{code}_BJ_{YYYYMM}.csv
        "desc":      "tick快照 33列",
        "alt_paths": [r"F:\bj_level2_data"],
    },
    {
        "key":       "futures",
        "label":     "期货  Tick / 日线",
        "icon":      "🔮",
        "path":      r"D:\futures_data",
        "mode":      "auto",
        "desc":      "期货合约 tick & 日线",
        "alt_paths": [r"D:\期货交易数据", r"D:\期货", r"D:\futures", r"F:\期货", r"F:\futures"],
    },
    {
        "key":       "daily",
        "label":     "日线数据",
        "icon":      "📅",
        "path":      r"D:\daily_data",
        "mode":      "auto",
        "desc":      "A股 / ETF 日线 OHLCV",
        "alt_paths": [r"D:\daily", r"D:\日线", r"F:\daily_data", r"F:\daily", r"F:\日线"],
    },
]


def fmt_size(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1024 ** 2:
        return f"{n_bytes / 1024:.1f} KB"
    if n_bytes < 1024 ** 3:
        return f"{n_bytes / 1024 ** 2:.1f} MB"
    return f"{n_bytes / 1024 ** 3:.2f} GB"


def _scan_date_tree(root: str) -> dict:
    """扫描 {YYYYMM}/{YYYY-MM-DD}/{code}.csv 层级结构"""
    total_files = total_size = 0
    dates: set = set()
    try:
        for month in os.listdir(root):
            mp = os.path.join(root, month)
            if not os.path.isdir(mp) or not month.isdigit():
                continue
            for day in os.listdir(mp):
                dp = os.path.join(mp, day)
                if not os.path.isdir(dp):
                    continue
                try:
                    datetime.strptime(day, "%Y-%m-%d")
                    dates.add(day)
                except ValueError:
                    continue
                for f in os.listdir(dp):
                    if f.endswith(".csv"):
                        total_files += 1
                        try:
                            total_size += os.path.getsize(os.path.join(dp, f))
                        except OSError:
                            pass
    except Exception:
        pass
    return {
        "files": total_files,
        "size":  total_size,
        "size_fmt": fmt_size(total_size),
        "latest": max(dates) if dates else "—",
        "days":  len(dates),
    }


def _scan_month_flat(root: str) -> dict:
    """扫描 {code}_BJ/{code}_BJ_{YYYYMM}.csv 层级结构"""
    total_files = total_size = 0
    months: set = set()
    try:
        for code_dir in os.listdir(root):
            dp = os.path.join(root, code_dir)
            if not os.path.isdir(dp):
                continue
            for f in os.listdir(dp):
                if not f.endswith(".csv"):
                    continue
                total_files += 1
                try:
                    total_size += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
                parts = f.replace(".csv", "").split("_")
                if parts:
                    m = parts[-1]
                    if m.isdigit() and len(m) == 6:
                        months.add(m)
    except Exception:
        pass
    if months:
        mx = max(months)
        latest = f"{mx[:4]}-{mx[4:]}"
    else:
        latest = "—"
    return {
        "files": total_files,
        "size":  total_size,
        "size_fmt": fmt_size(total_size),
        "latest": latest,
        "days":  len(months),
    }


def _scan_auto(root: str) -> dict:
    """通用递归扫描（适用于期货/日线等平铺结构）"""
    total_files = total_size = 0
    try:
        for dirpath, _, filenames in os.walk(root):
            for f in filenames:
                if f.endswith((".csv", ".pkl", ".parquet", ".h5", ".feather")):
                    total_files += 1
                    try:
                        total_size += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
    except Exception:
        pass
    return {
        "files": total_files,
        "size":  total_size,
        "size_fmt": fmt_size(total_size),
        "latest": "—",
        "days":  0,
    }


def scan_source(src: Dict[str, Any]) -> Dict[str, Any]:
    """扫描单个数据源，自动尝试备用路径"""
    root = src["path"]
    if not os.path.isdir(root):
        for alt in src.get("alt_paths", []):
            if os.path.isdir(alt):
                root = alt
                break
    if not os.path.isdir(root):
        return {
            "exists": False, "files": 0, "size": 0, "size_fmt": "0 B",
            "latest": "—", "days": 0,
            "key": src["key"], "label": src["label"], "icon": src.get("icon", ""),
            "path": root,
        }
    mode = src.get("mode", "auto")
    if mode == "date_tree":
        result = _scan_date_tree(root)
    elif mode == "month_flat":
        result = _scan_month_flat(root)
    else:
        result = _scan_auto(root)
    result.update({
        "exists": True, "path": root,
        "key":   src["key"],
        "label": src["label"],
        "icon":  src.get("icon", ""),
        "desc":  src.get("desc", ""),
    })
    return result


def scan_all() -> Dict[str, Any]:
    """扫描所有数据源，返回汇总结果（供 HTTP 接口序列化为 JSON）"""
    sources = []
    total_files = total_size = 0
    for src in DATA_SOURCES:
        r = scan_source(src)
        sources.append(r)
        total_files += r["files"]
        total_size  += r["size"]
    return {
        "sources":         sources,
        "total_files":     total_files,
        "total_size":      total_size,
        "total_size_fmt":  fmt_size(total_size),
        "source_count":    len(sources),
    }
