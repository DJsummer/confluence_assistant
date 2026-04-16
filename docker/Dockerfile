FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（lxml / psycopg2 编译需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制整个 rag 包（import 根路径为 /app）
COPY . .

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
