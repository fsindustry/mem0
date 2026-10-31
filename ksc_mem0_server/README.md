# ksc_mem0_server

全 PostgreSQL、按 product_id 分库的多租户记忆服务,基于 mem0 官方 SDK(不改动 `mem0/` 核心)。

## 架构

- **单实例多 product 路由**:一套服务,请求带 `X-Product-Id`,按 product 路由到独立 PG 库。
- **全 PostgreSQL**:向量(pgvector)+ 关系存储统一用 PostgreSQL,无 MySQL/Milvus。
- **按 product_id 分库**:每个 product 一个独立 PG 库 `mem0_{pid_normalized}`(pid 中 `-`→`_`),非分表。
- **保留 mem0 最新底座**:事实记忆用 mem0 `Memory`(仅替换历史存储为 PG);画像/摘要/异步等子系统在服务层外挂(P1+ 迁移中)。

## 快速启动

```bash
# 1. 起 PostgreSQL(pgvector)
cd ksc_mem0_server
docker-compose up -d postgres

# 2. 本地运行(需先装 mem0:hatch shell dev_py_3_11,或 pip install -e ./mem0)
cp .env.example .env  # 编辑:POSTGRES_HOST=localhost, POSTGRES_PORT=5433, OPENAI_API_KEY=...
python -m ksc_mem0_server

# 或完整 docker-compose
OPENAI_API_KEY=sk-... docker-compose up
```

## API(摘)

请求需带 `X-Product-Id`(必填)与 `X-API-Key`(`AUTH_DISABLED=true` 时免)。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/products` | 登记 product(建库) |
| GET  | `/v1/products` | 列已登记 product |
| POST | `/memories` | 添加记忆(事实) |
| POST | `/search` | 搜索记忆 |
| GET  | `/memories` | 列记忆(带 user_id/agent_id/run_id) |
| GET  | `/memories/{id}` | 取单条 |
| PUT  | `/memories/{id}` | 更新 |
| DELETE | `/memories/{id}` | 删除 |
| DELETE | `/memories` | 按 scope 删除全部 |
| GET  | `/memories/{id}/history` | 变更历史 |
| GET  | `/health` `/ready` | 健康检查 |

## 测试

```bash
docker-compose up -d postgres
cd ksc_mem0_server && pytest
```

## 路线图

- **P0**(当前):骨架 + 应用库 + per-product 建库 + PG history + 事实记忆 + 轻量鉴权。
- P1:策略三类型 + 画像记忆 + 异步 consumer。
- P2:摘要记忆 + 失败补偿 scanner。
- P3:digests 话题场景(membox/memscene)。
- P4:shared_space 共享记忆。
- P5:测试补全 + 文档 + OpenAPI。
