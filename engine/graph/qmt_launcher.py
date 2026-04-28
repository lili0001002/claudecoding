"""
QMT 客户端自动启动器
方案A：命令行参数直接登录，失败后降级 pyautogui

用法（由 qmt_bridge.py 自动调用，也可单独运行）：
    python -m engine.graph.qmt_launcher
"""
import os
import subprocess
import time
import logging

log = logging.getLogger(__name__)

QMT_EXE  = r"D:\国金证券QMT交易端\bin.x64\XtItClient.exe"
QMT_USER = "8886067188"
QMT_PWD  = "451370"


def is_running() -> bool:
    """检查 XtItClient.exe 进程是否已在运行"""
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if p.info["name"] and "XtItClient" in p.info["name"]:
                return True
    except Exception:
        pass
    return False


def _try_xtdata_connect(timeout: int = 20) -> bool:
    """尝试连接 xtdata，返回是否成功"""
    try:
        import sys
        qmt_python = os.path.join(os.path.dirname(QMT_EXE), "..", "userdata_mini", "bin.x64")
        if qmt_python not in sys.path:
            sys.path.insert(0, qmt_python)
        from xtquant import xtdata
        end = time.time() + timeout
        while time.time() < end:
            try:
                xtdata.connect()
                stocks = xtdata.get_stock_list_in_sector("沪深京A股")
                if stocks:
                    log.info("[QMT] xtdata 连接成功，股票数量: %d", len(stocks))
                    return True
            except Exception:
                pass
            time.sleep(1)
    except ImportError:
        log.warning("[QMT] xtquant 未找到，跳过 xtdata 验证")
        return True  # 无法验证，假设成功
    return False


def _launch_with_args() -> bool:
    """方案A：通过命令行参数启动 QMT"""
    try:
        cmd = [QMT_EXE, f"/user={QMT_USER}", f"/pwd={QMT_PWD}", "/silent"]
        log.info("[QMT] 尝试命令行参数启动: %s", " ".join(cmd))
        subprocess.Popen(cmd, cwd=os.path.dirname(QMT_EXE))
        time.sleep(8)
        return _try_xtdata_connect(timeout=20)
    except Exception as e:
        log.warning("[QMT] 命令行参数启动失败: %s", e)
        return False


def _launch_with_pyautogui() -> bool:
    """降级方案B：pyautogui 自动填写登录框"""
    try:
        import pyautogui
        import pygetwindow as gw
        log.info("[QMT] 降级到 pyautogui 方式登录...")
        subprocess.Popen([QMT_EXE], cwd=os.path.dirname(QMT_EXE))
        # 等待登录窗口出现
        for _ in range(30):
            time.sleep(1)
            wins = [w for w in gw.getAllWindows() if "QMT" in (w.title or "") or "国金" in (w.title or "")]
            if wins:
                break
        time.sleep(2)
        # 找账号输入框并填写（坐标依赖分辨率，此处使用图像匹配更稳定）
        # 简化：直接 Tab 键序列 + 输入
        pyautogui.hotkey("alt", "tab")
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.typewrite(QMT_USER, interval=0.05)
        pyautogui.press("tab")
        pyautogui.typewrite(QMT_PWD, interval=0.05)
        pyautogui.press("enter")
        time.sleep(5)
        return _try_xtdata_connect(timeout=25)
    except Exception as e:
        log.warning("[QMT] pyautogui 登录失败: %s", e)
        return False


def ensure_qmt_running() -> bool:
    """
    确保 QMT 客户端已启动并就绪。
    返回 True 表示就绪，False 表示失败。
    """
    if is_running():
        log.info("[QMT] 客户端已在运行，跳过启动")
        return True

    if not os.path.exists(QMT_EXE):
        log.error("[QMT] 客户端不存在: %s", QMT_EXE)
        return False

    log.info("[QMT] 客户端未运行，开始启动...")

    # 方案A：命令行参数
    if _launch_with_args():
        log.info("[QMT] 命令行方案A启动成功")
        return True

    # 降级方案B
    log.warning("[QMT] 方案A失败，尝试 pyautogui 降级...")
    if _launch_with_pyautogui():
        log.info("[QMT] pyautogui 降级成功")
        return True

    log.error("[QMT] 所有启动方案均失败")
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
    ok = ensure_qmt_running()
    print("[QMT] 启动结果:", "成功" if ok else "失败")
