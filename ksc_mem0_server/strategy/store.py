"""StrategyStore:策略三类型 + global,版本化快照 + 跨 worker 热刷新(PostgreSQL 版)。

迁移自参考库 release-v2.2 ksc-mem0/mem0_server/strategy/store.py,改造:
  - MySQL → PostgreSQL(DDL 语法、ON CONFLICT、now())
  - 按 product_id 分库:表名去 `_{pid}` 后缀(mem0_strategies),每 product 独立库
  - 版本表复用 P0 的 revisions 表(feature='strategy'),产品级单调递增

策略类型:
  - profile / fact / summary:进 strategies_by_type 桶,各子系统读取
  - global:不进桶,拼 merged_prompt 注入事实记忆 add 的 custom_instructions 插槽
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STRATEGY_TABLE = "mem0_strategies"
_REVISIONS_TABLE = "revisions"
_REVISION_FEATURE = "strategy"
_GLOBAL_TYPE = "global"

# status:1=启用,-1=软删,0=停用
STATUS_ACTIVE = 1
STATUS_DISABLED = 0
STATUS_DELETED = -1

_STRATEGIES_DDL = """
CREATE TABLE IF NOT EXISTS mem0_strategies (
    id            VARCHAR(64) PRIMARY KEY,
    product_id    VARCHAR(64) NOT NULL,
    name          VARCHAR(128) NOT NULL,
    strategy_type VARCHAR(64) NOT NULL,
    prompt        TEXT,
    status        SMALLINT NOT NULL DEFAULT 1,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
)
"""

_STRATEGIES_INDEX = "CREATE INDEX IF NOT EXISTS idx_strategies_type ON mem0_strategies (strategy_type)"


@dataclass
class StrategySnapshot:
    """策略快照(COW 原子替换,读无锁)。"""

    strategies_by_type: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    merged_prompt: str = ""
    revision: int = 0


class StrategyStore:
    """单 product 的策略存储 + 内存快照 + 跨 worker 版本轮询刷新。"""

    def __init__(self, pool, product_id: str, check_interval: float = 3.0) -> None:
        self.pool = pool
        self.product_id = product_id
        self.check_interval = check_interval
        self._lock = threading.RLock()
        self._snapshot: StrategySnapshot = StrategySnapshot()
        self._create_table()
        self.force_reload()

    # --- 建表 ---
    def _create_table(self) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_STRATEGIES_DDL)
                cur.execute(_STRATEGIES_INDEX)
            conn.commit()

    # --- 版本(revisions 表) ---
    def _bump_revision(self) -> int:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {_REVISIONS_TABLE} (feature, revision)
                    VALUES (%s, 1)
                    ON CONFLICT (feature) DO UPDATE SET revision = {_REVISIONS_TABLE}.revision + 1, updated_at = now()
                    RETURNING revision
                    """,
                    (_REVISION_FEATURE,),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else 0

    def _get_revision(self) -> int:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT revision FROM {_REVISIONS_TABLE} WHERE feature = %s",
                    (_REVISION_FEATURE,),
                )
                row = cur.fetchone()
        return row[0] if row else 0

    # --- 加载 / 刷新 ---
    def load_all(self) -> StrategySnapshot:
        """从 DB 加载启用策略,分桶 + 拼 merged_prompt,带版本。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, name, strategy_type, prompt FROM {_STRATEGY_TABLE} WHERE status = %s",
                    (STATUS_ACTIVE,),
                )
                rows = cur.fetchall()
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        global_prompts: List[str] = []
        for sid, name, stype, prompt in rows:
            item = {"strategy_id": sid, "name": name, "prompt": prompt or ""}
            if stype == _GLOBAL_TYPE:
                if prompt:
                    global_prompts.append(prompt)
            else:
                by_type.setdefault(stype, []).append(item)
        return StrategySnapshot(
            strategies_by_type=by_type,
            merged_prompt="\n\n---\n\n".join(global_prompts),
            revision=self._get_revision(),
        )

    def force_reload(self) -> None:
        with self._lock:
            self._snapshot = self.load_all()

    def check_and_reload(self) -> bool:
        """DB 版本变化则重载,返回是否变更。"""
        db_rev = self._get_revision()
        with self._lock:
            if db_rev == self._snapshot.revision:
                return False
            self._snapshot = self.load_all()
        logger.info("StrategyStore reloaded: product=%s rev=%s", self.product_id, db_rev)
        return True

    def current_revision(self) -> int:
        with self._lock:
            return self._snapshot.revision

    def database_revision(self) -> int:
        return self._get_revision()

    # --- 读 getter(无锁,读不可变 snapshot) ---
    def get_merged_prompt(self) -> str:
        with self._lock:
            return self._snapshot.merged_prompt

    def get_enabled_strategies_by_type(self, strategy_type: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._snapshot.strategies_by_type.get(strategy_type, [])]

    def list_active_prompts_by_type(self, strategy_type: str) -> List[Dict[str, Any]]:
        return self.get_enabled_strategies_by_type(strategy_type)

    def list_active_ids_by_type(self, strategy_type: str) -> List[str]:
        return [item["strategy_id"] for item in self.get_enabled_strategies_by_type(strategy_type)]

    def get_active_strategy_by_id(self, strategy_type: str, strategy_id: str) -> Optional[Dict[str, Any]]:
        for item in self.get_enabled_strategies_by_type(strategy_type):
            if item["strategy_id"] == strategy_id:
                return item
        return None

    # --- CRUD(每次写 bump_revision 触发跨 worker 重载) ---
    def create_strategy(self, name: str, strategy_type: str, prompt: str) -> Dict[str, Any]:
        sid = str(uuid.uuid4())
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {_STRATEGY_TABLE} (id, product_id, name, strategy_type, prompt, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (sid, self.product_id, name, strategy_type, prompt, STATUS_ACTIVE),
                )
            conn.commit()
        self._bump_revision()
        self.force_reload()
        return {"id": sid, "name": name, "strategy_type": strategy_type, "prompt": prompt, "status": STATUS_ACTIVE}

    def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, name, strategy_type, prompt, status FROM {_STRATEGY_TABLE} WHERE id = %s",
                    (strategy_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "strategy_type": row[2], "prompt": row[3], "status": row[4]}

    def list_strategies(
        self, strategy_type: Optional[str] = None, include_deleted: bool = False
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: list = []
        if not include_deleted:
            clauses.append("status != %s")
            params.append(STATUS_DELETED)
        if strategy_type:
            clauses.append("strategy_type = %s")
            params.append(strategy_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, name, strategy_type, prompt, status FROM {_STRATEGY_TABLE}{where} ORDER BY created_at",
                    params,
                )
                rows = cur.fetchall()
        return [
            {"id": r[0], "name": r[1], "strategy_type": r[2], "prompt": r[3], "status": r[4]} for r in rows
        ]

    def update_strategy(self, strategy_id: str, name: Optional[str] = None, prompt: Optional[str] = None) -> None:
        sets: list[str] = []
        params: list = []
        if name is not None:
            sets.append("name = %s")
            params.append(name)
        if prompt is not None:
            sets.append("prompt = %s")
            params.append(prompt)
        if not sets:
            return
        sets.append("updated_at = now()")
        params.append(strategy_id)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {_STRATEGY_TABLE} SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
            conn.commit()
        self._bump_revision()
        self.force_reload()

    def set_strategy_status(self, strategy_id: str, status: int) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {_STRATEGY_TABLE} SET status = %s, updated_at = now() WHERE id = %s",
                    (status, strategy_id),
                )
            conn.commit()
        self._bump_revision()
        self.force_reload()

    def delete_strategy(self, strategy_id: str) -> None:
        """软删(status=-1)。"""
        self.set_strategy_status(strategy_id, STATUS_DELETED)
