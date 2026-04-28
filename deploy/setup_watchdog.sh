#!/bin/bash
# 一键部署：softdog 内核看门狗（死机自动硬重启）
# 用法：bash setup_watchdog.sh

set -e

echo "=== 1. 加载 softdog 内核模块 ==="
sudo modprobe softdog
echo "softdog" | sudo tee /etc/modules-load.d/softdog.conf

echo "=== 2. 安装 watchdog 守护进程 ==="
sudo apt-get install -y watchdog

echo "=== 3. 配置 watchdog ==="
sudo tee /etc/watchdog.conf > /dev/null << 'EOF'
watchdog-device = /dev/watchdog
watchdog-timeout = 60
interval = 10
max-load-1 = 24
min-memory = 1
EOF

sudo systemctl enable watchdog
sudo systemctl restart watchdog
echo "watchdog 状态: $(systemctl is-active watchdog)"

echo ""
echo "=== 完成 ==="
echo "softdog 看门狗已启动：系统死机60秒后自动硬重启"
