from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Confluence ───────────────────────────────────────────
    confluence_base: str = "https://confluence.ext.net.nokia.com"
    confluence_user: str = ""
    confluence_token: str = ""

    # ── Embedding（本地 bge-m3，sentence-transformers 推理）──
    embedding_model: str = "BAAI/bge-m3"   # HuggingFace model id 或本地路径
    embedding_device: str = "cuda"          # "cuda" | "cpu"
    embedding_batch_size: int = 32
    vector_dim: int = 1024                  # bge-m3 dense dim

    # ── LLM（vLLM OpenAI-compatible API）────────────────────
    llm_model: str = "Qwen/Qwen2.5-7B-Instruct"
    llm_base_url: str = "http://vllm:8000/v1"
    vllm_api_key: str = "token-local"       # vLLM 要求非空，任意字符串即可

    # ── Milvus ───────────────────────────────────────────────
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "confluence_docs"

    # ── PostgreSQL ───────────────────────────────────────────
    postgres_dsn: str = "postgresql://rag:rag@localhost:5432/ragdb"

    # ── Redis ────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── 切分 ─────────────────────────────────────────────────
    chunk_size: int = 512
    chunk_overlap: int = 64

    # ── Confluence 抓取目标 ──────────────────────────────────
    root_title: str = ""
    space_key: str = ""

    # ── Jira ─────────────────────────────────────────────
    jira_base: str = "https://jiradc.ext.net.nokia.com"
    jira_user: str = ""
    jira_token: str = ""

    # ── Pronto ─────────────────────────────────────────────
    pronto_base: str = "https://pronto.ext.net.nokia.com"

    # ── 模型缓存目录（HuggingFace 下载 / 本地已有模型时指定绝对路径）──
    hf_cache_dir: str = "/models"           # docker volume 挂载路径，与 HF_HOME 一致
    hf_hub_offline: bool = True             # 模型已缓存后禁止网络请求

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
