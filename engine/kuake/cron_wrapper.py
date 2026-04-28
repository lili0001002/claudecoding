#!/usr/bin/env python3
"""
夸克爬虫 Cron 包装器 - 防止过载和封号
保护机制：
1. 单实例锁（防止重复运行）
2. 冷却期检查（24小时内只运行一次）
3. 失败重试限制（连续失败3次后停止24小时）
"""
import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).parent.parent.parent
LOCK_FILE = ROOT / "logs" / "kuake_cron.lock"
STATE_FILE = ROOT / "logs" / "kuake_cron_state.json"
LOG_DIR = ROOT / "logs"

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_run": None, "fail_count": 0, "last_fail": None}

def save_state(state):
    LOG_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def check_lock():
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            os.kill(pid, 0)
            return False  # 进程存在，已在运行
        except (ProcessLookupError, ValueError):
            LOCK_FILE.unlink()  # 清理过期锁
    return True

def acquire_lock():
    LOG_DIR.mkdir(exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))

def release_lock():
    LOCK_FILE.unlink(missing_ok=True)

def main():
    state = load_state()
    now = datetime.now()

    # 检查单实例锁
    if not check_lock():
        print(f"[{now}] 夸克爬虫已在运行，跳过")
        return 0

    # 检查冷却期（24小时）
    if state["last_run"]:
        last = datetime.fromisoformat(state["last_run"])
        if now - last < timedelta(hours=24):
            print(f"[{now}] 距上次运行不足24小时，跳过（上次: {last}）")
            return 0

    # 检查失败计数（连续失败3次后停止24小时）
    if state["fail_count"] >= 3 and state["last_fail"]:
        last_fail = datetime.fromisoformat(state["last_fail"])
        if now - last_fail < timedelta(hours=24):
            print(f"[{now}] 连续失败{state['fail_count']}次，冷却中（上次失败: {last_fail}）")
            return 0
        else:
            state["fail_count"] = 0  # 冷却期过后重置

    acquire_lock()
    try:
        print(f"[{now}] 开始夸克增量爬取...")
        sys.path.insert(0, str(ROOT))
        from engine.kuake.incremental import run
        run()

        # 成功：更新状态
        state["last_run"] = now.isoformat()
        state["fail_count"] = 0
        save_state(state)
        print(f"[{now}] 夸克爬取完成")
        return 0

    except Exception as e:
        # 失败：记录失败
        state["fail_count"] += 1
        state["last_fail"] = now.isoformat()
        save_state(state)
        print(f"[{now}] 夸克爬取失败（{state['fail_count']}/3）: {e}")
        return 1

    finally:
        release_lock()

if __name__ == "__main__":
    sys.exit(main())
