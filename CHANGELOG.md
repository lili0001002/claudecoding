# 久赢恒丰系统 变更记录

## 2026-02-25 本次会话变更汇总

---

### 一、基础设施：外网访问方案重构

**问题**：Cloudflare Quick Tunnel 服务器侧访问 CF IP 超时，导致隧道频繁断线且无法申请新域名。

**修复方案**：改用 反向 SSH 隧道（Ubuntu 服务器 → 阿里云服务器）

| 项目 | 详情 |
|---|---|
| 阿里云服务器 | 47.110.85.207，root / Nan@niushangshi888，Ubuntu Linux |
| Ubuntu→阿里云 免密SSH | `ssh-keygen` + `ssh-copy-id` 已配置 |
| 阿里云 GatewayPorts | 已设为 yes（`/etc/ssh/sshd_config`） |
| 阿里云安全组 | 已放行 TCP 8888 |
| systemd 服务 | `/etc/systemd/system/jiuying-tunnel.service` 改为反向SSH隧道，`Restart=always RestartSec=5` |
| 外网访问地址 | **http://47.110.85.207:8888/** |
| 废弃服务 | `tunnel-watchdog.service` 已禁用删除 |

**相关文件**：
- 服务器：`/etc/systemd/system/jiuying-tunnel.service`
- 本地：`C:\Users\Administrator\Desktop\久赢恒丰\deploy\jiuying-tunnel.service`（待同步）

---

### 二、钉钉推送链接更新

**文件**：`engine/notify.py`

**变更**：
- 移除 `_get_public_url()` 读取 cloudflared 日志逻辑
- 改为固定常量 `PUBLIC_URL = "http://47.110.85.207:8888"`
- 推送链接已从 `http://192.168.1.11:8888/` 改为 `http://47.110.85.207:8888/`

---

### 三、Bug 修复：股票数据为空（三层防护）

#### Bug 根本原因
App 对未购买会员的用户返回 masked 股票（`stockId="111"`, `stockName="****"`），需要从本地缓存 `subject_stock_full.json` 用 `selectedId` 反查真实股票代码。当缓存中没有对应 `selectedId` 时，会写入空值。

#### 修复层 1：去重逻辑不再丢弃无 stockId 的股票
**文件**：`engine/txcfgl/incremental.py`，`extract_stocks_from_mapping()` 函数

```python
# 修复前：stockId 为空直接丢弃
sid = s.get("stockId") or s.get("selectedId")
if not sid:
    continue

# 修复后：优先用 stockId，没有则用 sel:{selectedId} 作 key
key = stock_id if stock_id else (f"sel:{sel_id}" if sel_id else None)
if not key:
    continue
```

#### 修复层 2：更新记录时保护旧有好数据
**文件**：`engine/txcfgl/incremental.py`，`run()` 函数更新分支

新增逻辑：若本次更新的股票仍有空 `stockId`，先从旧记录里找同 `selectedId` 的有效数据补全，防止好数据被空数据覆盖。

#### 修复层 3：全量修复存量空数据
**脚本**：`C:\Users\Administrator\Desktop\fix_empty_stocks.py`（临时脚本，已执行）

执行结果：`result.json` 里 **101 只**空股票全部补全，剩余为空 0 只。

#### 缓存更新
- 重新执行 `engine.kuake.full`，缓存更新至 635 个题材，最大 `selectedId` 从 252560 → 253705

> **注意**：若未来再出现空股票，重跑 `python -m engine.kuake.full` 刷新缓存即可。

---

### 四、Web 页面性能优化（tab 切换无刷新）

**文件**：`engine/web_viewer.py`

**变更前**：每次切 tab 都向服务器发完整请求，服务器读 JSON + 查新浪行情（全量）+ 渲染 HTML，耗时 3-6 秒。

**变更后**：

| 项目 | 改动 |
|---|---|
| Tab 切换 | 三个 tab 内容一次性渲染，前端 JS 控制 `display` 显隐，切换 0ms |
| 行情数据 | 移除服务端 `fetch_quotes()` 阻塞调用，改为前端 JS `DOMContentLoaded` 后异步请求 |
| 新增接口 | `GET /api/quotes?ids=600519,000001,...` 返回 JSON 行情数据 |
| Tab 标签 | 新增条数角标显示（全部 87 / 新题材 N / 驱动事件 N） |
| `<a>` → `<span>` | Tab 元素改为 `span` + `data-tab` 属性，`history.replaceState` 保持 URL 参数 |

---

### 五、Web 页面交互优化（题材全文弹窗）

**文件**：`engine/web_viewer.py`

**问题**：题材名称超长时被 `text-overflow:ellipsis` 截断，看不到全文。

**修复**：
- `card-name` 加 `data-full="{完整题材名}"` 属性
- 点击题材名 → 弹出全文遮罩弹窗，显示完整标题 + 类型/数量/日期
- 关闭：点击 ✕、点击遮罩背景、按 `Escape`
- 新增 CSS：`.fulltext-mask`、`.fulltext-box`、`.fulltext-close`、`.fulltext-title`、`.fulltext-meta`

---

### 六、待办事项（下次会话继续）

- [ ] 整合 `engine/txcfgl/market.py` 新接口到 web_viewer
  - `fetch_index_snapshot()` → 页面顶部显示四大指数（上证/深证/创业板/深综）
  - `fetch_up_down()` → 显示涨跌统计（涨家数 / 跌家数 / 平）
  - `fetch_stock_daily()` → 个股详情页 K 线图
  - `fetch_subject_query()` → 题材详情页显示完整描述/原因
  - `fetch_market_amount()` → 显示大盘成交额
- [ ] `engine/txcfgl/full.py` 已新增全量拉取脚本，可考虑定期执行刷新历史数据

---

### 文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `engine/notify.py` | 修改 | 推送链接改为阿里云固定地址 |
| `engine/txcfgl/incremental.py` | 修改 | 三层空股票防护修复 |
| `engine/txcfgl/market.py` | 新增 | 大盘/交易日历/个股数据接口模块 |
| `engine/txcfgl/full.py` | 新增 | 全量拉取脚本 |
| `engine/web_viewer.py` | 修改 | tab无刷新切换 + 行情异步 + 题材全文弹窗 |
| 服务器 `/etc/systemd/system/jiuying-tunnel.service` | 修改 | 改为反向SSH隧道方案 |
| 服务器 `/etc/systemd/system/tunnel-watchdog.service` | 删除 | 已禁用，不再需要 |

---

### 服务器当前运行状态

```
jiuying-web.service     ✅ active  端口8888
jiuying-tunnel.service  ✅ active  反向SSH隧道→阿里云
scheduler.service       ✅ active  定时任务调度
```

### 外网访问
```
http://47.110.85.207:8888/
```
