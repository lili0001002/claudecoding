# 生成规范（Generation Spec）

> 灵感来源：OpenSpec / AI-Native 开发范式
> 核心思想：任何 AI 对本项目的变更，必须在此规范约束下进行——
> 让 AI 生成的代码可预测、可审计、可回滚。

---

## 一、模块职责边界（Module Contract）

每个文件只做一件事。违反此约定的变更需在 PR 说明中显式声明。

| 文件 | 唯一职责 | 禁止包含 |
|------|---------|---------|
| `engine/web_viewer.py` | HTTP 路由 + HTML 渲染 | 任何业务数据计算 |
| `engine/ag_service.py` | AI图谱行情/历史K线缓存 | HTTP 逻辑、HTML 字符串 |
| `engine/static/aigraph.js` | 图谱前端渲染与交互 | 后端 API 调用以外的副作用 |
| `engine/static/aigraph.css` | 图谱样式 | JS 逻辑 |
| `engine/graph/keyword_map.py` | L1/L2/逻辑链关键词定义 | 运行时逻辑 |
| `engine/txcfgl/market.py` | QMT/久赢恒丰 API 封装 | 业务逻辑 |
| `engine/notify.py` | 钉钉推送 | 业务逻辑 |

---

## 二、逻辑链生成规范（Logic Chain Generation）

### 2.1 逻辑链数据结构

```python
# engine/graph/keyword_map.py 中每条 LOGIC_CHAINS 条目格式
{
    "name": "传导链路名称",          # 简洁描述方向，如"数据中心→服务器→PCB"
    "l1": "一级产业分类",            # 必须是 L1_CATEGORIES 中已有的值
    "steps": [                       # 传导路径，2-5个步骤
        {
            "l1": "产业分类",
            "l2": "二级子模块名称",  # 必须与 L2_NODES 中的 key 匹配
        }
    ]
}
```

### 2.2 AI 生成逻辑链的 Prompt 模板

当需要 AI 补充新逻辑链时，使用以下 prompt：

```
你是一位A股产业链研究员。请根据以下约束生成新的产业传导逻辑链：

【约束】
1. 每条链路必须有明确的产业传导逻辑（上游→下游，或政策→受益方）
2. 步骤数量：2-5步
3. 每步的 l2 字段必须是下列已有节点之一：
   {已有的 L2_NODES 列表}
4. l1 必须是下列分类之一：
   {已有的 L1_CATEGORIES 列表}
5. 不得与以下现有链路重复（steps 相同则视为重复）：
   {现有 LOGIC_CHAINS 的 steps 摘要}

【输出格式】
严格输出 JSON 数组，不得包含注释或额外文字：
[
  {"name": "...", "l1": "...", "steps": [{"l1": "...", "l2": "..."}, ...]},
  ...
]
```

### 2.3 题材-L2 映射生成规范

```
当 build_graph.py 无法匹配某题材到 L2 节点时，AI 辅助映射规则：

1. 关键词提取：从题材名称提取核心词（≤3个）
2. 语义匹配：与 L2_NODES 的 keywords 列表做语义相似度判断
3. 置信度阈值：≥0.7 才写入映射，否则标记为 unmatched
4. 禁止臆造：不得创建不在 L2_NODES 中的新节点
```

---

## 三、前端变更规范（Frontend Change Rules）

### 3.1 涨幅显示一致性

所有涨幅展示必须遵守统一约定：

```javascript
// ✅ 正确：始终生成占位 span，用 display:none 隐藏而非条件不生成
html += '<span data-xxx="id" style="'+(val?'':'display:none')+'">'+val+'</span>';

// ❌ 错误：用 if(val) 控制 DOM 生成，导致行情刷新后找不到节点
if(val) html += '<span data-xxx="id">'+val+'</span>';
```

### 3.2 实时更新绑定规范

每个需要实时更新的 DOM 元素，必须满足：

| 要求 | 说明 |
|------|------|
| `data-xxx` 属性 | 存储绑定的业务 ID（如 `data-navpct="3"`） |
| `_agApplyHotness` 中有对应更新逻辑 | 行情刷新时自动更新 |
| 初始值在渲染时计算 | 历史数据加载后显示，行情加载后更新 |

### 3.3 连续天数徽章规范

```javascript
// streak badge 必须用容器 span 包裹，以便动态替换
html += '<span data-navstreak="'+ci+'">' + _agStreakBadge(si) + '</span>';
// 不得直接 innerHTML _agStreakBadge(si)，否则无法在历史数据到来后更新
```

---

## 四、后端变更规范（Backend Change Rules）

### 4.1 新增 API 端点规范

```python
# web_viewer.py Handler.do_GET 中新增端点模板
elif parsed.path == "/api/new-endpoint":
    # 1. 从服务层获取数据（不在 Handler 中计算）
    data = some_service.get_data()
    # 2. 序列化
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    # 3. 标准响应头
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)
    return
```

### 4.2 后台线程规范

```python
# ag_service.py 中新增后台任务模板
def _new_task_loop():
    while True:
        try:
            # 业务逻辑
            pass
        except Exception:
            pass  # 静默降级，不崩溃主线程
        time.sleep(INTERVAL)

# 在 start_background_tasks() 中注册
threading.Thread(target=_new_task_loop, daemon=True, name="task-name").start()
```

### 4.3 缓存读写规范

```python
# 全局缓存变量命名：_xxx_cache
# 锁命名：_xxx_lock
# 读取：始终通过 get_xxx_cache() 函数，返回副本（dict()）
# 写入：只在后台线程中，持有锁时写入

_xxx_cache: dict = {}
_xxx_lock = threading.Lock()

def get_xxx_cache() -> dict:
    with _xxx_lock:
        return dict(_xxx_cache)
```

---

## 五、部署变更规范（Deploy Change Rules）

### 5.1 静态文件更新（无需重启）

```powershell
# 只修改前端样式或逻辑
scp engine/static/aigraph.css libowei@10.0.0.2:/home/libowei/server/jiuying/engine/static/
scp engine/static/aigraph.js  libowei@10.0.0.2:/home/libowei/server/jiuying/engine/static/
# 浏览器强刷（Ctrl+Shift+R）即可生效，无需重启服务
```

### 5.2 Python 文件更新（需重启）

```powershell
scp engine/web_viewer.py libowei@10.0.0.2:/home/libowei/server/jiuying/engine/
scp engine/ag_service.py libowei@10.0.0.2:/home/libowei/server/jiuying/engine/
ssh libowei@10.0.0.2 "sudo systemctl restart jiuying-web"
# 验证：
ssh libowei@10.0.0.2 "systemctl is-active jiuying-web"
```

### 5.3 图谱数据更新（本地）

```bash
# 在 Ubuntu 服务器上执行（需要访问内网 QMT）
cd ~/server/jiuying
~/server/venv/bin/python -m engine.graph.build_graph
# ai_graph.json 自动更新，/api/ai-graph 立即生效（每次请求读文件）
```

---

## 六、AI 变更审计检查表（AI Change Checklist）

每次 AI 生成代码后，执行以下检查：

```bash
# 1. 语法检查
python -m py_compile engine/web_viewer.py engine/ag_service.py

# 2. 模块导入检查
python -c "from engine import ag_service, web_viewer"

# 3. 测试集回归
python -m pytest tests/ -q

# 4. 服务冒烟测试（部署后）
curl -s http://10.0.0.2:8888/api/ai-graph | python3 -c "import sys,json; d=json.load(sys.stdin); print('nodes:', len(d['nodes']), 'chains:', len(d['meta']['logicChains']))"
curl -s http://10.0.0.2:8888/api/ai-quotes | python3 -c "import sys,json; d=json.load(sys.stdin); print('quotes:', len(d))"
curl -s http://10.0.0.2:8888/api/ag-history | python3 -c "import sys,json; d=json.load(sys.stdin); print('history_dates:', len(d))"
```
