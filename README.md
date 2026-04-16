# Confluence AI 助手

基于 Confluence 知识库的企业内部 RAG（检索增强生成）问答系统。

## 架构概览

```
Confluence API
     │
     ▼
ETL 数据同步（Celery）
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
混合检索（向量 + CrossEncoder rerank）
     │
     ▼
LLM（Ollama / vLLM，本地部署）
     │
     ▼
FastAPI + Chat UI（http://localhost:8000）
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
│   └── llm_service.py        # Ollama/vLLM OpenAI-compatible API + 多轮对话
├── db/
│   ├── vector_store.py       # Milvus HNSW 索引，权限 filter
│   └── metadata_db.py        # PostgreSQL 元数据 + 增量同步时间戳
├── workers/
│   └── sync_worker.py        # Celery 任务，每 6h 自动增量同步
├── api/
│   ├── main.py               # FastAPI 入口 + 静态文件服务
│   ├── routes/
│   │   ├── chat.py           # POST /chat/
│   │   └── sync.py           # POST /sync/
│   └── static/
│       └── index.html        # Chat UI（纯 HTML，无需 Node.js）
└── docker/
    ├── Dockerfile
    └── docker-compose.yml    # 全栈一键启动
```

## 快速开始

### 1. 配置环境

```bash
cd rag
cp .env.example .env
```

编辑 `.env`，填写以下必填项：

```env
CONFLUENCE_USER=your_username
CONFLUENCE_TOKEN=your_personal_access_token
ROOT_TITLE=YOUR_PAGE_TITLE     # Confluence 根页面标题
SPACE_KEY=YOUR_SPACE_KEY       # Space Key
```

### 2. 准备本地 LLM（使用 Ollama）

```bash
# 安装 Ollama: https://ollama.com
ollama pull qwen2.5:7b
```

### 3. 启动服务

**无 GPU（CPU 模式）：**
```bash
cd docker
docker compose up -d
```

**有 NVIDIA GPU：**
```bash
cd docker
docker compose --profile gpu up -d
```

### 4. 触发数据同步

```bash
# 全量同步（首次）
curl -X POST http://localhost:8000/sync/ \
  -H "Content-Type: application/json" \
  -d '{"full_sync": true}'
```

### 5. 打开 Chat UI

浏览器访问：**http://localhost:8000**

---

## API 说明

### 问答接口

```
POST /chat/
```

```json
{
  "question": "XXX 的部署流程是什么？",
  "top_k": 5,
  "user_groups": [],
  "history": []
}
```

响应：
```json
{
  "answer": "...",
  "sources": [
    {"title": "...", "path": "Root > A > B", "url": "https://...", "score": 0.85}
  ]
}
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
| 增量同步 | 对比 `updated_at`，未变更页面跳过 |
| 并发抓取 | `ThreadPoolExecutor`，8 线程并行拉页面 |
| 表格支持 | HTML `<table>` 转 Markdown 行列结构 |
| Embedding 缓存 | Redis 7 天缓存，避免重复推理 |
| 查询缓存 | Redis 5 分钟，热门问题极速响应 |
| 权限过滤 | Milvus `array_contains` metadata filter |
| 多轮对话 | 保留最近 6 轮历史 |
| 离线推理 | `HF_HUB_OFFLINE=1`，模型缓存到 Docker volume |

## 依赖服务

| 服务 | 用途 | 端口 |
|---|---|---|
| Milvus | 向量数据库 | 19530 |
| PostgreSQL | 元数据存储 | 5432 |
| Redis | Embedding/查询缓存 + Celery broker | 6379 |
| Ollama | 本地 LLM 推理 | 11434 |

## 模型说明

| 模型 | 用途 | 大小 |
|---|---|---|
| `BAAI/bge-m3` | 中英文 Embedding | ~570MB |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker | ~90MB |
| `qwen2.5:7b`（推荐）| 问答生成 | ~4.7GB |
