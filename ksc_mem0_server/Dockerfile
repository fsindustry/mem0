FROM python:3.12-slim

WORKDIR /app

# psycopg/sqlalchemy 等大多有 wheel;保留 build-essential 以防个别包需编译
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# mem0 官方 SDK(本仓库 ./mem0,editable 安装,保留最新底座)
COPY pyproject.toml README.md LICENSE ./
COPY mem0/ ./mem0/
RUN pip install --no-cache-dir -e ./mem0

# 本服务
COPY ksc_mem0_server/ ./ksc_mem0_server/
RUN pip install --no-cache-dir -r ksc_mem0_server/requirements.txt

ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["python", "-m", "ksc_mem0_server"]
