# ksc_mem0_server

全 PostgreSQL、按 product_id 分库的多租户记忆服务,基于 mem0 官方 SDK(不改动 `mem0/` 核心)。

## 架构

```
请求(X-Product-Id, X-API-Key)
   │
   ▼
FastAPI app(单实例)── auth middleware ── ProductRegistry ──┐
                                                          │ get_or_create(product_id)
   ┌──────────────────────────────────────────────────────┘
   │
   ▼  ProductRuntime(每 product 一套)
   ├── pool(psycopg3,该 product 库共享)
   ├── facts: FactsMemory(mem0 Memory 子类,history→PG,向量→pgvector mem0)
   ├── strategy_store: 三类型 profile/fact/summary + global merged_prompt
   ├── profile: 画像记忆(state/events/runs + pgvector mem0_profile + 两步 LLM + 租约)
   ├── summary: 摘要记忆(session 级 K=(user,run)+ 多策略 fan-out + pgvector mem0_summary)
   ├── digests: 话题场景(membox 会话内话题 + memscene 跨会话场景 + overall 总览 + 版本链)
   ├── shared_space: 共享记忆空间(空间/成员/规则 + 增量登记 + 批次抽取 + pgvector mem0_shared)
   ├── tasks: GlobalTaskQueue 内存队列 + TaskConsumer(按 product 路由 + serialize_key FIFO)
   └── extraction: ExtractionFailureStore 失败补偿 + 全局 scanner

后台 worker(app lifespan):
   ├── FailureCompensationScanner:遍历各 product 补偿失败任务
   └── (各子系统 publisher interval 触发,需手动启用)

存储:一套 PG 实例
   ├── 应用库 mem0_app(products/api_keys/settings,SQLAlchemy)
   └── 各 product 库 mem0_{pid_normalized}(关系表 + pgvector 向量表,psycopg3 直连)
```

## 关键设计

- **按 product_id 分库**(非分表):每 product 一个独立 PG 库 `mem0_{pid_normalized}`(pid 中 `-`→`_`),首次访问按需建库建表。表名无 `_{pid}` 后缀(因已独立库)。
- **全 PostgreSQL**:关系表(普通 PG)+ 向量(pgvector),无 MySQL/Milvus。
- **不动 mem0 核心**:事实记忆用 mem0 `Memory`(仅 override `__init__/reset/close` 把 `self.db` 换成 `PostgreSQLHistoryManager`);vector_store 由 mem0 用 MemoryConfig 创建(pgvector 连该 product 库)。
- **轻量 API key 鉴权**:`X-API-Key`(应用库 `api_keys` 表 sha256 比对)+ `ADMIN_API_KEY`;`AUTH_DISABLED=true` 本地开发。
- **多 product 路由**:请求带 `X-Product-Id`,`ProductRegistry.get_or_create` 取/建对应 `ProductRuntime`,挂到 `request.state`。

## 快速启动

```bash
# 1. 起 PostgreSQL(pgvector)
cd ksc_mem0_server && docker-compose up -d postgres

# 2. 装 mem0(本仓库)与依赖
pip install -e ./mem0
pip install -r ksc_mem0_server/requirements.txt

# 3. 配置并运行
cp .env.example .env  # 编辑 POSTGRES_HOST=localhost, POSTGRES_PORT=5433, OPENAI_API_KEY=...
python -m ksc_mem0_server

# 或完整 docker-compose
OPENAI_API_KEY=sk-... docker-compose up
```

## API

请求需带 `X-Product-Id`(业务端点必填)与 `X-API-Key`(`AUTH_DISABLED=true` 时免)。

### 事实记忆(`/memories`)
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/memories` | 添加记忆(global 策略 prompt 自动注入 custom_instructions) |
| POST | `/search` | 搜索记忆 |
| GET | `/memories` | 列记忆(user_id/agent_id/run_id 过滤) |
| GET | `/memories/{id}` | 取单条 |
| PUT | `/memories/{id}` | 更新 |
| DELETE | `/memories/{id}` | 删除 |
| DELETE | `/memories` | 按 scope 删除全部 |
| GET | `/memories/{id}/history` | 变更历史 |

### 策略(`/v1/strategies`)
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/strategies/types` | 类型字典(profile/fact/summary) |
| POST | `/v1/strategies` | 创建策略 |
| GET | `/v1/strategies` | 列策略 |
| GET/PUT/DELETE | `/v1/strategies/{id}` | CRUD |
| POST | `/v1/strategies/{id}/enable` `/disable` | 启用/停用 |

### 画像记忆(`/v1/profile`)
| POST `/search` | 检索画像 | GET `` | 列画像 | POST `/trigger` | 触发画像生成 |

### 摘要记忆(`/v1/summary`)
| POST `/search` | 检索摘要 | GET `` | 列摘要 | POST `/trigger` | 触发摘要生成 |

### 话题场景(`/v1/digests`)
| POST `/seal` | 密封消息为 membox | POST `/aggregate` | 触发 memscene 聚合 |
| POST `/scenes/search` | 检索场景 | GET `/scenes` | 列场景 | GET `/memboxes` | 列 membox |
| GET/POST/DELETE `/feedback` | 用户反馈 | GET/PUT `/settings` | 配置 | GET `/versions` | 版本链 |

### 共享记忆空间(`/v1/shared_spaces`)
| POST `` | 创建空间 | GET `` | 列空间 | GET/DELETE `/{id}` | 空间详情/删除 |
| PUT `/{id}/name` `/prompt` `/enabled` | 空间更新 | POST `/{id}/trigger` | 触发抽取 |
| POST/DELETE/GET `/{id}/members` | 成员管理 | GET `/members/spaces` | 成员反查空间 |
| POST/GET/PUT/DELETE `/{id}/rules` | 规则 CRUD |
| POST `/{id}/memories/search` | 检索共享记忆 | GET `/{id}/memories` | 列共享记忆 |

### 失败补偿(`/v1/extraction`)
| GET `/failures` | 列失败记录 | POST `/failures/{id}/retry` | 重置 DEAD → PENDING |
| DELETE `/failures/{id}` `/` | 删除记录/批量 | POST `/compensate` | 手动触发扫描 |

### 产品登记(`/v1/products`)
| POST `` | 登记 product(建库) | GET `` | 列 product | GET `/{id}` | 状态 |

### 健康
| GET `/health` `/ready` | 健康检查 |

## 测试

```bash
docker-compose up -d postgres
python3 -m pytest ksc_mem0_server/tests -q   # 135 测试
ruff check ksc_mem0_server/                  # lint
```

测试覆盖:PG history、schema 自愈、registry 建库/缓存/并发、FactsMemory、鉴权、各子系统 store/闭环/API、失败补偿 scanner、e2e 多 product 分库隔离。

## 子系统与参考库对照

| 子系统 | 参考库(release-v2.2) | 改造 |
|--------|----------------------|------|
| 事实记忆 | mem0 原生 + mem0_ext/memory.py | MySQL→PG history,Milvus→pgvector |
| 策略 | strategy/ | MySQL→PG |
| 画像 | profile/ | MySQL→PG + Milvus→pgvector + 多 product 路由 |
| 摘要 | summary/ | 同上,session 级 |
| 话题场景 | digests/ | MySQL→PG + Milvus→pgvector,简化(见下) |
| 共享空间 | shared_space/ | MySQL→PG + Milvus→pgvector,简化(见下) |
| 异步任务 | tasks/ | TaskItem 加 product_id,多 product 路由 |
| 失败补偿 | extraction/ | MySQL→PG + 全局 scanner 遍历各 product |

## 已知简化项(留后续迭代)

- **digests**:跳过 sweeper 切段(主记忆 episode 结构)、day/range 中间场景、attach 回写 membox scene_ids、croniter 调度(用 interval)。
- **shared_space**:跳过 history/memory 上下文收集、representative_vectors、多轮续抽、UPDATE/DELETE 动作、fact content 从主记忆 fetch、croniter 调度。

## 应用库迁移(Alembic)

应用库 `mem0_app`(products/api_keys/settings)用 Alembic 管理 schema,配置在 `ksc_mem0_server/alembic.ini`(`%(here)s` 定位,任意 cwd 可运行):

```bash
# 升级到最新版本
APP_DB_NAME=mem0_app POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
  python3 -m alembic -c ksc_mem0_server/alembic.ini upgrade head

# 降级到 base
python3 -m alembic -c ksc_mem0_server/alembic.ini downgrade base

# 查看当前版本
python3 -m alembic -c ksc_mem0_server/alembic.ini current
```

URL 由 `env.py` 从环境变量构建(与 `db.py` 一致,覆盖 `alembic.ini` 静态值);per-product 库由 `runtime/schema.py` 运行时按需建库建表,不进 Alembic。开发期亦可用 `db.py` 的 `create_all` 一键建表,生产建议走 Alembic。

## 配置

环境变量见 `.env.example`。核心:`POSTGRES_*`、`APP_DB_NAME`、`PRODUCT_DB_PREFIX`、`OPENAI_API_KEY`、`MEM0_LLM_*`、`MEM0_EMBEDDER_*`、`EMBEDDING_DIMS`、`AUTH_DISABLED`、`ADMIN_API_KEY`。
