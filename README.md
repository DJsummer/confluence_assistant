# Confluence AI 助手

基于 Confluence 知识库的企业内部智能问答系统，支持 RAG 检索和 LangChain Agent 两种模式，集成 Jira 和 Pronto 查询能力。

## 架构概览

```
Confluence API
     │
     ▼
ETL 数据同步（Celery，每 6h 增量）
     │
     ▼
文档切分（chunker）
     │
     ▼
Embedding（bge-m3，本地推理）
     │
     ▼
Milvus 向量数据库 + PostgreSQL 元数据
     │
     ▼
┌────────────────┬──────────────────────────┐
│   RAG 模式      │       Agent 模式          │
│  向量检索 +     │  LangChain Tool Calling   │
│  CrossEncoder  │  自动调用：                │
│  rerank        │  - search_confluence      │
│                │  - get_jira_issue         │
│                │  - get_pronto_pr          │
└────────────────┴──────────────────────────┘
     │
     ▼
LLM（阿里百炼 / Ollama，SSE 流式输出）
     │
     ▼
FastAPI + Chat UI（Nokia 蓝白风格）
http://localhost:8000
```

## 目录结构

```
rag/
├── .env.example              # 配置模板
├── requirements.txt
├── config/
│   └── settings.py           # 统一配置（pydantic-settings）
├── services/
│   ├── confluence_loader.py  # Confluence 递归抓取（分页/并发/增量）
│   ├── chunker.py            # 文档切分（token 控制 + overlap + 表格感知）
│   ├── embedding_service.py  # 本地 bge-m3 推理 + Redis 缓存
│   ├── retriever.py          # 向量检索 + CrossEncoder rerank + 查询缓存
│   ├── llm_service.py        # OpenAI-compatible API + 多轮对话 + 流式输出
│   ├── agent_service.py      # LangChain Tool Calling Agent
│   ├── jira_service.py       # Jira DC REST API（Bearer Token 认证）
│   └── pronto_service.py     # Pronto PR 链接与标题获取
├── db/
│   ├── vector_store.py       # Milvus HNSW 索引，权限 filter
│   └── metadata_db.py        # PostgreSQL 元数据 + 增量同步时间戳
├── workers/
│   └── sync_worker.py        # Celery 任务，每 6h 自动增量同步
├── api/
│   ├── main.py               # FastAPI 入口 + 静态文件服务
│   ├── routes/
│   │   ├── chat.py           # POST /chat/  POST /chat/stream
│   │   ├── agent.py          # POST /agent/ POST /agent/stream
│   │   └── sync.py           # POST /sync/  GET  /sync/{task_id}
│   └── static/
│       └── index.html        # Chat UI（Nokia 蓝白风格，无需 Node.js）
└── docker/
    ├── Dockerfile
    └── docker-compose.yml    # 全栈一键启动（含源码热更新挂载）
```

## 快速开始

### 1. 配置环境

```bash
cd rag
cp .env.example .env
```

编辑 `.env`，填写必填项：

```env
# Confluence
CONFLUENCE_USER=your_username
CONFLUENCE_TOKEN=your_token
ROOT_TITLE=YOUR_PAGE_TITLE
SPACE_KEY=YOUR_SPACE_KEY

# Jira（可选，用于 Agent 模式查询 issue）
JIRA_BASE=https://jiradc.your-company.com
JIRA_USER=your_username
JIRA_TOKEN=your_personal_access_token   # Jira DC PAT

# Pronto（可选，用于 Agent 模式查询 PR）
PRONTO_BASE=https://pronto.your-company.com
PRONTO_USER=your_username
PRONTO_TOKEN=your_token

# LLM（二选一）
# 方式一：阿里百炼
LLM_MODEL=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VLLM_API_KEY=sk-your_key

# 方式二：本地 Ollama
LLM_MODEL=qwen2.5:7b
LLM_BASE_URL=http://host.docker.internal:11434/v1
VLLM_API_KEY=ollama
```

### 2. 启动服务

```bash
cd docker
docker compose up -d
```

### 3. 触发数据同步（首次）

```bash
curl -X POST http://localhost:8000/sync/ \
  -H "Content-Type: application/json" \
  -d '{"full_sync": true}'
```

### 4. 打开 Chat UI

浏览器访问：**http://localhost:8000**

---

## 两种问答模式

### RAG 模式（默认）
直接向 Confluence 向量库检索，返回相关文档片段后由 LLM 生成回答。适合知识查询类问题。

### Agent 模式
LangChain Tool Calling Agent，LLM 自主决定调用哪些工具：

| 工具 | 触发场景 | 示例 |
|---|---|---|
| `search_confluence` | 技术问题、流程说明 | "XXX 的部署流程是什么？" |
| `get_jira_issue` | 包含 Jira key | "FPB-1495109 是什么需求？" |
| `get_pronto_pr` | 包含 PR ID | "PR700839 是什么问题？" |

---

## API 说明

### 流式问答（推荐）

```
POST /chat/stream      # RAG 流式（SSE）
POST /agent/stream     # Agent 流式（SSE，含工具调用事件）
```

SSE 事件格式（Agent）：
```
data: {"type": "step",   "tool": "get_jira", "input": "FPB-123"}
data: {"type": "result", "tool": "get_jira", "output": "..."}
data: {"type": "answer", "text": "最终回答..."}
data: {"type": "done"}
```

### 同步接口

```
POST /sync/          # 触发同步，返回 task_id
GET  /sync/{task_id} # 查询任务状态
```

---

## 生产特性

| 特性 | 实现方式 |
|---|---|
| 流式输出 | SSE，首 token ~2s |
| 增量同步 | 对比 `updated_at`，未变更页面跳过 |
| 并发抓取 | `ThreadPoolExecutor`，8 线程并行 |
| 表格支持 | HTML `<table>` 转 Markdown 行列结构 |
| Embedding 缓存 | Redis 7 天缓存，避免重复推理 |
| 查询缓存 | Redis 5 分钟，热门问题极速响应 |
| 多轮对话 | 保留最近 6 轮历史 |
| 离线推理 | `HF_HUB_OFFLINE=1`，模型缓存到 Docker volume |
| 热更新 | 源码挂载到容器，保存即生效（无需重建镜像）|
| 403 降级 | Jira 无权限时返回链接，Agent 自动 fallback 搜索 |

## 依赖服务

| 服务 | 用途 | 端口 |
|---|---|---|
| Milvus | 向量数据库 | 19530 |
| PostgreSQL | 元数据存储 | 5432 |
| Redis | Embedding/查询缓存 + Celery broker | 6379 |

## 模型说明

| 模型 | 用途 | 大小 |
|---|---|---|
| `BAAI/bge-m3` | 中英文 Embedding（dim=1024）| ~570MB |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker | ~90MB |
| `qwen-plus`（推荐）| 问答生成，阿里百炼 API | 按 token 计费 |
| `qwen2.5:7b` | 本地 Ollama 推理 | ~4.7GB |

