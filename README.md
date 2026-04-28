# 久赢恒丰 题材股票数据系统

移动端 Web 看板 + 自动增量抓取 + 钉钉推送

外网访问：`http://47.110.85.207:8888/`

---

## 目录结构

```
久赢恒丰/
├── data/
│   ├── subject_stock_full.json       # 全量题材-股票映射缓存（635题材 / 28868条 selectedId）
│   ├── subject_stock_full.csv        # 同上，CSV格式
│   └── subject_stocks_result.json    # 增量结果（题材详情 + 股票列表 + 大盘快照）
│
├── engine/
│   ├── kuake/                        # 第一层：stockId 补全（夸克服务器）
│   │   ├── full.py                   # 全量：初始化本地缓存（首次/重置用）
│   │   └── incremental.py            # 增量：检测新题材，补全 selectedId→stockId
│   ├── txcfgl/                       # 第二层：业务数据抓取（久赢恒丰）
│   │   ├── full.py                   # 全量：拉取 top-history 所有页
│   │   ├── incremental.py            # 增量：拉取最新页，含大盘快照+自动夸克补全
│   │   └── market.py                 # 市场数据模块（大盘/日K/主营/轮动/子树等）
│   ├── graph/                        # AI图谱构建
│   │   ├── keyword_map.py            # L1/L2/逻辑链关键词映射（31条传导链）
│   │   ├── build_graph.py            # 从题材数据构建 ai_graph.json
│   │   ├── qmt_bridge.py             # QMT 本地行情桥接
│   │   └── qmt_launcher.py           # QMT 启动器
│   ├── static/                       # 前端静态资源（AI-Native：独立可维护）
│   │   ├── main.css                  # 通用样式（topbar/tabs/cards/股票网格等）
│   │   ├── aigraph.css               # AI图谱专属样式（导航栏/链路卡片/步骤模块）
│   │   ├── main.js                   # 通用JS（Tab切换/行情/K线/题材树）
│   │   └── aigraph.js                # AI图谱JS（图谱渲染/涨幅计算/连续天数/热度）
│   ├── tools/                        # 维护工具
│   │   └── fix_empty_stocks.py       # 修复 result.json 里 stockId 为空的记录
│   ├── ag_service.py                 # AI图谱后端服务（行情缓存/历史K线/后台线程）
│   ├── alert_service.py              # Token 失效告警服务（独立进程）
│   ├── heartbeat.py                  # 服务器心跳推送（cron 调用）
│   ├── notify.py                     # 钉钉推送模块
│   └── web_viewer.py                 # Web 看板服务（HTTP路由+页面渲染，端口 8888）
│
├── deploy/                           # 服务器部署配置
│   ├── jiuying-web.service           # systemd：Web 看板服务
│   ├── jiuying-tunnel.service        # systemd：反向 SSH 隧道（Ubuntu→阿里云）
│   └── setup_watchdog.sh             # 隧道看门狗安装脚本
│
├── _archive/                         # 归档
│   ├── debug/                        # 历史调试脚本（check_*.py 等）
│   └── *.py                          # 早期探索脚本
│
├── CHANGELOG.md
└── README.md
```

---

## 快速使用

### 日常增量（唯一需要手动跑的命令）

```bash
python -m engine.txcfgl.incremental
```

自动完成：

1. 拉取大盘四指数快照 + 涨跌统计
2. 抓取 `top-history`（type=2 新题材 + type=3 驱动事件）
3. 新题材有无代码股票 → **自动调夸克补全 stockId** → 重新抓取
4. 每条记录附加 `createTime / detail / reason / bizKey / market` 字段
5. 保存到 `data/subject_stocks_result.json` + 钉钉推送

```bash
python -m engine.txcfgl.incremental --pages 3        # 拉取前3页
python -m engine.txcfgl.incremental --type 2         # 只拉新题材
```

### Web 看板

```bash
python -m engine.web_viewer --token <Bearer Token>
# 本地访问 http://localhost:8888
# 外网访问 http://47.110.85.207:8888
```

**三个 Tab：**

| Tab | 内容 |
|-----|------|
| **新题材** | 左列：题材列表（涨幅 + 日期时间）；右侧：分组股票子树（涨跌幅/股票名/代码/驱动原因）；长按题材显示事件详情 |
| **驱动事件** | 近期驱动事件卡片列表（含相关股票） |
| **题材轮动** | 多日期横向对比表格（最近10个交易日 × Top25题材，含涨停数） |

**股票详情（点击股票名）：**
- 60日日K走势图（Canvas 绘制）
- 主营业务构成条形图
- 关联题材列表

### 初始化（首次部署或缓存损坏时）

```bash
# 1. 从夸克服务器全量建立 selectedId→stockId 缓存
python -m engine.kuake.full

# 2. 全量拉取久赢恒丰历史 top-history
python -m engine.txcfgl.full
```

### 维护工具

```bash
# 修复 result.json 里 stockId 为空但 selectedId 有值的历史记录
python -m engine.tools.fix_empty_stocks

# 只更新指定 subjectId 的夸克缓存
python -m engine.kuake.incremental --id 9063417 9064170

# 夸克账号续费后强制全量重建缓存
python -m engine.kuake.incremental --force
```

---

## 数据流

```
┌─────────────────────────────────────────────────────────────┐
│  夸克服务器 (111.170.164.89:600)                             │
│  engine/kuake/{full,incremental}.py                          │
│       → data/subject_stock_full.json                         │
│         (635题材 / 28868条 selectedId → stockId 映射)        │
└────────────────────────┬────────────────────────────────────┘
                         │ 自动触发（新题材无代码时）
┌────────────────────────▼────────────────────────────────────┐
│  久赢恒丰 (app.txcfgl.com)                                   │
│  engine/txcfgl/incremental.py                                │
│       ├── subject/top-history      → 题材列表                │
│       ├── subject/mapping/{id}     → 股票（stockId全屏蔽）   │
│       ├── subject/query/{id}       → 题材详情                │
│       ├── realtime/index           → 大盘四指数              │
│       └── 缓存 selectedId 精确反查真实 stockId               │
│       → data/subject_stocks_result.json                      │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│  engine/web_viewer.py  (HTTP :8888)                          │
│                                                              │
│  本地 API                        来源                        │
│  /api/subject-tree           result.json（离线）             │
│  /api/subject-child-tree     txcfgl child-stock-tree +       │
│                              subject_stock_full 精确补全      │
│  /api/subject-cycle          txcfgl history-cycle/1（实时）  │
│  /api/subject-query          txcfgl subject/query（实时）    │
│  /api/quotes                 新浪行情（实时）                 │
│  /api/stock-detail           txcfgl 日K + 主营（实时）       │
└─────────────────────────────────────────────────────────────┘
```

---

## stockId 补全机制

久赢恒丰**所有接口**的 `stockId` 均被服务端屏蔽为 `"111"`，`stockName` 屏蔽为 `"****"`。

补全流程（`selectedId` 是唯一匹配键）：

1. **增量抓取时**：`incremental.py` 先查本地 `_selected_id_index`，未命中则调夸克补全
2. **Web 子树展示时**：`web_viewer.py` 启动时从 `subject_stock_full.json` 建立全局 `_sel_id_index`，请求时按 `selectedId` 精确匹配，三级兜底：
   - ① result.json 本题材缓存
   - ② subject_stock_full.json 全量索引
   - ③ 保留原始（显示不完整）

---

## 久赢恒丰接口清单

| 接口 | 用途 |
|------|------|
| `subject/top-history` | 题材列表（新题材 type=2 / 驱动事件 type=3） |
| `subject/mapping/{id}` | 题材股票列表（stockId 全屏蔽） |
| `subject/child-stock-tree/{id}` | 题材股票分组子树（stockId 全屏蔽） |
| `subject/query/{id}` | 题材详情文本（detail / reason / bizKey） |
| `subject/history-cycle/1` | 题材轮动（近60日按日期分组 Top30） |
| `realtime/index` | 大盘四指数实时行情 |
| `realtime/index-amount` | 大盘成交额 |
| `stock/up-down-daily` | 涨跌家数统计 |
| `trade-date/recent/{n}` | 最近 n 个交易日 |
| `data/one-stock-daily` | 个股日K（60日） |
| `data/one-stock-main-business/v2` | 个股主营业务（层级1/2/3） |
| `stock/subject-tree/{id}` | 个股关联题材树 |
| `stock/realtime-rank` | 实时涨幅排行 |

---

## 部署（Ubuntu 服务器）

### 服务器信息

| | |
|--|--|
| 本地 IP | `10.0.0.2` |
| 外网 IP | `47.110.85.207`（阿里云反向隧道） |
| 用户 | `libowei` |
| Python | `~/server/venv` |
| 项目路径 | `~/server/jiuying/` |

### systemd 服务

```bash
sudo systemctl start jiuying-web       # Web 看板（:8888）
sudo systemctl start jiuying-tunnel    # 反向 SSH 隧道到阿里云
sudo journalctl -u jiuying-web -n 20 --no-pager
```

### cron 定时任务（周一至周五 9:00–17:00 每整点）

```
0 9-17 * * 1-5 cd ~/server/jiuying && venv/bin/python -m engine.txcfgl.incremental
```

### 同步本地代码到服务器

```powershell
scp engine\web_viewer.py libowei@10.0.0.2:/home/libowei/server/jiuying/engine/web_viewer.py
scp engine\txcfgl\incremental.py libowei@10.0.0.2:/home/libowei/server/jiuying/engine/txcfgl/incremental.py
ssh libowei@10.0.0.2 "sudo systemctl restart jiuying-web"
```

---

## 认证信息

| 服务 | 说明 |
|------|------|
| 久久恒丰 Bearer Token | 通过环境变量 TXCFGL_TOKEN 或本地忽略文件 data/tokens.json 注入，不写入源码 |
| 夸克服务器 | 通过环境变量 `KUAKE_PHONE` / `KUAKE_PASSWORD` 注入，不写入源码 |
| 钉钉 Webhook | 通过环境变量 `DINGTALK_WEBHOOK` / `DINGTALK_SECRET` 或本地忽略文件注入 |

## 注意事项

- 夸克账号到期后存量 28868 条 `selectedId` 仍有效，**只有新出现题材**的 stockId 无法补全
- `incremental.py` 已内置 Token 失效检测，失效时自动发钉钉告警并终止
- 少数题材（如人形机器人 9014636）在夸克服务器无数据，引擎已自动跳过
- 夸克解密：AES-CBC + PKCS7，Key=`4ZFUgq/mkqveDgNNZ9JZ/A==`（utf8，24B），IV=`8ebc27e624514c0e`（utf8，16B）
