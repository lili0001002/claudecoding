"""
systemd OnFailure 告警脚本
用法：python -m engine.alert_service <service_name>
当 jiuying-web / jiuying-tunnel 等服务崩溃时由 systemd 调用
"""
import sys
import subprocess
import os

# 把项目根目录加入 path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from engine.notify import send_alert

if __name__ == "__main__":
    service = sys.argv[1] if len(sys.argv) > 1 else "未知服务"
    # 取最近 20 行 journal 日志
    try:
        log = subprocess.check_output(
            ["journalctl", "-u", service, "-n", "20", "--no-pager", "--output=short"],
            stderr=subprocess.STDOUT, text=True
        )
    except Exception as e:
        log = f"(获取日志失败: {e})"
    send_alert(f"服务崩溃: {service}", log)
