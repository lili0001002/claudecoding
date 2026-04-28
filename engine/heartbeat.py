"""
心跳脚本 - 每小时由 cron 调用，推送服务器存活状态到钉钉
若服务器死机，钉钉将停止收到心跳，即可感知异常
"""
import subprocess
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.notify import send_dingtalk


def get_uptime():
    try:
        out = subprocess.check_output(["uptime", "-p"], text=True).strip()
        return out.replace("up ", "")
    except Exception:
        return "未知"


def get_load():
    try:
        out = subprocess.check_output(["uptime"], text=True)
        # load average: 0.10, 0.08, 0.05
        part = out.split("load average:")[-1].strip()
        return part.split(",")[0].strip()
    except Exception:
        return "?"


def get_mem():
    try:
        out = subprocess.check_output(["free", "-m"], text=True).splitlines()
        for line in out:
            if line.startswith("Mem:"):
                parts = line.split()
                total, used = int(parts[1]), int(parts[2])
                pct = used * 100 // total
                return f"{used}M/{total}M ({pct}%)"
    except Exception:
        pass
    return "?"


def get_disk():
    try:
        out = subprocess.check_output(["df", "-h", "/"], text=True).splitlines()
        parts = out[1].split()
        return f"{parts[2]}/{parts[1]} ({parts[4]})"
    except Exception:
        return "?"


def check_services():
    services = ["jiuying-web", "jiuying-tunnel"]
    results = []
    for svc in services:
        try:
            ret = subprocess.call(
                ["systemctl", "is-active", "--quiet", svc],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            results.append(f"{svc}: {'运行中' if ret == 0 else '**异常**'}")
        except Exception:
            results.append(f"{svc}: 未知")
    return results


if __name__ == "__main__":
    from datetime import datetime
    now      = datetime.now().strftime("%m-%d %H:%M")
    uptime   = get_uptime()
    load     = get_load()
    mem      = get_mem()
    disk     = get_disk()
    services = check_services()

    svc_lines = "\n".join(f"- {s}" for s in services)
    content = (
        f"## 服务器心跳 {now}\n\n"
        f"- 运行时长：{uptime}\n"
        f"- CPU负载：{load}\n"
        f"- 内存：{mem}\n"
        f"- 磁盘：{disk}\n\n"
        f"**服务状态**\n{svc_lines}"
    )
    send_dingtalk(f"心跳 {now}", content)
