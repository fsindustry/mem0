"""ksc_mem0_server — 全 PostgreSQL 多租户记忆服务。

基于 mem0 官方 SDK(不改动 mem0/ 核心),在服务层外挂实现:
  - 按 product_id 分库(每 product 一个独立 PostgreSQL 数据库)
  - 向量(pgvector)与关系存储统一用 PostgreSQL
  - 画像/摘要/话题场景/共享空间/异步并行提取+失败补偿/策略三类型

P0 阶段:骨架 + 应用库 + per-product 建库 + PG history + 事实记忆 + 轻量鉴权。
"""

__version__ = "0.1.0"
