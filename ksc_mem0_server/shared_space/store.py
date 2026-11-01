"""SharedSpaceStore:5 张关系表 CRUD + 租约(PostgreSQL 版)。

迁移自参考库 shared_space/store.py(MySQL→PG,去 _{pid} 后缀)。
  - spaces:空间主表(含抽取配置、pending_tokens、租约)
  - rules:用户自定义规则
  - members:成员(user/agent)
  - pending:源 fact 消费登记(pending/processing/done/obsolete 四态)
  - batches:抽取批次(状态/租约/输入输出快照/重试)
表名固定(去后缀,每 product 独立库)。
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PENDING_STATUS_PENDING = "pending"
PENDING_STATUS_PROCESSING = "processing"
PENDING_STATUS_DONE = "done"
PENDING_STATUS_OBSOLETE = "obsolete"


class DuplicateSpaceNameError(Exception):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_space(row: Sequence) -> Dict[str, Any]:
    return {
        "space_id": row[0],
        "name": row[1],
        "instance_id": row[2],
        "instance_name": row[3],
        "extract_enabled": bool(row[4]),
        "extract_prompt": row[5],
        "last_enabled_time": row[6],
        "last_disabled_time": row[7],
        "last_update_time": row[8],
        "cursor_time": row[9],
        "last_extract_time": row[10],
        "create_time": row[11],
        "pending_tokens": int(row[12] or 0),
        "token_threshold": row[13],
        "next_run_at": row[14],
        "run_lease_until": row[15],
        "run_lease_token": row[16],
    }


_SPACE_COLS = (
    "space_id, name, instance_id, instance_name, extract_enabled, extract_prompt, "
    "last_enabled_time, last_disabled_time, last_update_time, cursor_time, last_extract_time, "
    "create_time, pending_tokens, token_threshold, next_run_at, run_lease_until, run_lease_token"
)


class SharedSpaceStore:
    def __init__(self, pool, product_id: str) -> None:
        self.pool = pool
        self.product_id = product_id

    # --- 空间 ---
    def create_space(
        self,
        name: str,
        instance_id: str = "",
        instance_name: str = "",
        extract_enabled: bool = False,
        extract_prompt: Optional[str] = None,
        token_threshold: Optional[int] = None,
    ) -> Dict[str, Any]:
        space_id = str(uuid.uuid4())
        now = _utc_now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO shared_spaces
                        (space_id, name, instance_id, instance_name, extract_enabled, extract_prompt,
                         last_enabled_time, last_update_time, create_time, token_threshold)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (space_id, name, instance_id, instance_name, int(extract_enabled), extract_prompt,
                         now if extract_enabled else None, now, now, token_threshold),
                    )
                except Exception as e:
                    if "uk_name" in str(e) or "unique" in str(e).lower():
                        raise DuplicateSpaceNameError(name) from e
                    raise
            conn.commit()
        return self.get_space(space_id)

    def get_space(self, space_id: str) -> Optional[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_SPACE_COLS} FROM shared_spaces WHERE space_id = %s", (space_id,))
                row = cur.fetchone()
        return _row_to_space(row) if row else None

    def list_spaces(self, text: str = "", offset: int = 0, limit: int = 100) -> Tuple[int, List[Dict[str, Any]]]:
        like = f"%{text}%" if text else "%"
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM shared_spaces WHERE name LIKE %s", (like,))
                total = cur.fetchone()[0]
                cur.execute(
                    f"SELECT {_SPACE_COLS} FROM shared_spaces WHERE name LIKE %s ORDER BY create_time DESC LIMIT %s OFFSET %s",
                    (like, limit, offset),
                )
                rows = cur.fetchall()
        return total, [_row_to_space(r) for r in rows]

    def count_spaces(self) -> int:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM shared_spaces")
                return cur.fetchone()[0]

    def list_enabled_spaces(self) -> List[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_SPACE_COLS} FROM shared_spaces WHERE extract_enabled = 1 ORDER BY next_run_at NULLS FIRST")
                rows = cur.fetchall()
        return [_row_to_space(r) for r in rows]

    def update_name(self, space_id: str, name: str) -> bool:
        return self._update_space(space_id, {"name": name})

    def update_prompt(self, space_id: str, prompt: str) -> bool:
        return self._update_space(space_id, {"extract_prompt": prompt})

    def set_enabled(self, space_id: str, enabled: bool) -> bool:
        now = _utc_now()
        fields: Dict[str, Any] = {"extract_enabled": int(enabled), "last_update_time": now}
        if enabled:
            fields["last_enabled_time"] = now
        else:
            fields["last_disabled_time"] = now
        return self._update_space(space_id, fields)

    def _update_space(self, space_id: str, fields: Dict[str, Any]) -> bool:
        if not fields:
            return False
        sets = ", ".join(f"{k} = %s" for k in fields)
        params = list(fields.values()) + [space_id]
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE shared_spaces SET {sets} WHERE space_id = %s", params)
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def delete_space(self, space_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM shared_space_rules WHERE space_id = %s", (space_id,))
                cur.execute("DELETE FROM shared_space_members WHERE space_id = %s", (space_id,))
                cur.execute("DELETE FROM shared_space_fact_pending WHERE space_id = %s", (space_id,))
                cur.execute("DELETE FROM shared_space_batches WHERE space_id = %s", (space_id,))
                cur.execute("DELETE FROM shared_spaces WHERE space_id = %s", (space_id,))
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def adjust_pending_tokens(self, space_id: str, delta: int) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE shared_spaces SET pending_tokens = GREATEST(0, pending_tokens + %s) WHERE space_id = %s",
                    (delta, space_id),
                )
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def set_next_run_at(self, space_id: str, next_run_at: Optional[datetime]) -> bool:
        return self._update_space(space_id, {"next_run_at": next_run_at})

    def update_extract_progress(self, space_id: str, cursor_time: datetime) -> bool:
        return self._update_space(space_id, {"cursor_time": cursor_time, "last_extract_time": _utc_now(), "last_update_time": _utc_now()})

    # --- 运行租约(防多节点并发抽取同一空间)---
    def try_acquire_run_lease(self, space_id: str, token: str, ttl_seconds: int) -> bool:
        now = _utc_now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE shared_spaces SET run_lease_token = %s, run_lease_until = %s
                    WHERE space_id = %s AND (run_lease_until IS NULL OR run_lease_until <= %s)
                    """,
                    (token, now + timedelta(seconds=ttl_seconds), space_id, now),
                )
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def release_run_lease(self, space_id: str, token: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE shared_spaces SET run_lease_token = NULL, run_lease_until = NULL WHERE space_id = %s AND run_lease_token = %s",
                    (space_id, token),
                )
                n = cur.rowcount
            conn.commit()
        return bool(n)

    # --- 规则 ---
    def add_rule(self, space_id: str, content: str) -> Optional[Dict[str, Any]]:
        rule_id = str(uuid.uuid4())
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO shared_space_rules (rule_id, space_id, content) VALUES (%s, %s, %s)",
                    (rule_id, space_id, content),
                )
            conn.commit()
        return self.get_rule(space_id, rule_id)

    def get_rule(self, space_id: str, rule_id: str) -> Optional[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT rule_id, space_id, content, create_time, last_update_time FROM shared_space_rules WHERE space_id = %s AND rule_id = %s",
                    (space_id, rule_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {"rule_id": row[0], "space_id": row[1], "content": row[2], "create_time": row[3], "last_update_time": row[4]}

    def update_rule(self, space_id: str, rule_id: str, content: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE shared_space_rules SET content = %s, last_update_time = %s WHERE space_id = %s AND rule_id = %s",
                    (content, _utc_now(), space_id, rule_id),
                )
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def delete_rule(self, space_id: str, rule_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM shared_space_rules WHERE space_id = %s AND rule_id = %s",
                    (space_id, rule_id),
                )
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def list_rules(self, space_id: str, offset: int = 0, limit: int = 100) -> Tuple[int, List[Dict[str, Any]]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM shared_space_rules WHERE space_id = %s", (space_id,))
                total = cur.fetchone()[0]
                cur.execute(
                    "SELECT rule_id, space_id, content, create_time, last_update_time FROM shared_space_rules WHERE space_id = %s ORDER BY create_time LIMIT %s OFFSET %s",
                    (space_id, limit, offset),
                )
                rows = cur.fetchall()
        return total, [
            {"rule_id": r[0], "space_id": r[1], "content": r[2], "create_time": r[3], "last_update_time": r[4]}
            for r in rows
        ]

    def list_all_rules(self, space_id: str) -> List[Dict[str, Any]]:
        _, rules = self.list_rules(space_id, limit=10000)
        return rules

    # --- 成员 ---
    def add_members(self, space_id: str, members: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """添加成员 [(member_id, member_type)],返回新增的(去重已存在的)。"""
        added: List[Tuple[str, str]] = []
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                for member_id, member_type in members:
                    try:
                        cur.execute(
                            "INSERT INTO shared_space_members (space_id, member_id, member_type) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            (space_id, member_id, member_type),
                        )
                        if cur.rowcount:
                            added.append((member_id, member_type))
                    except Exception:
                        logger.exception("add_members failed member=%s/%s", member_id, member_type)
            conn.commit()
        return added

    def remove_members(self, space_id: str, members: List[Tuple[str, str]]) -> int:
        n = 0
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                for member_id, member_type in members:
                    cur.execute(
                        "DELETE FROM shared_space_members WHERE space_id = %s AND member_id = %s AND member_type = %s",
                        (space_id, member_id, member_type),
                    )
                    n += cur.rowcount
            conn.commit()
        return n

    def list_members(self, space_id: str, offset: int = 0, limit: int = 100) -> Tuple[int, List[Dict[str, Any]]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM shared_space_members WHERE space_id = %s", (space_id,))
                total = cur.fetchone()[0]
                cur.execute(
                    "SELECT space_id, member_id, member_type, joined_time FROM shared_space_members WHERE space_id = %s ORDER BY joined_time LIMIT %s OFFSET %s",
                    (space_id, limit, offset),
                )
                rows = cur.fetchall()
        return total, [
            {"space_id": r[0], "member_id": r[1], "member_type": r[2], "joined_time": r[3]} for r in rows
        ]

    def list_all_members(self, space_id: str) -> List[Tuple[str, str]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT member_id, member_type FROM shared_space_members WHERE space_id = %s",
                    (space_id,),
                )
                return [(r[0], r[1]) for r in cur.fetchall()]

    def list_member_space_ids(self, member_id: str, member_type: str) -> List[str]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT space_id FROM shared_space_members WHERE member_id = %s AND member_type = %s",
                    (member_id, member_type),
                )
                return [r[0] for r in cur.fetchall()]

    # --- pending(源 fact 消费登记)---
    def register_pending(
        self, space_id: str, fact_id: str, source_version: str, tokens: int,
        source_created_at: Optional[datetime], user_id: str = "", agent_id: str = "",
    ) -> bool:
        """幂等登记 pending;已登记(同 space+fact+version)返回 False。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO shared_space_fact_pending
                    (space_id, fact_id, source_version, status, tokens, source_created_at, user_id, agent_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (space_id, fact_id, source_version) DO NOTHING
                    """,
                    (space_id, fact_id, source_version, PENDING_STATUS_PENDING, tokens, source_created_at, user_id, agent_id),
                )
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def list_pending_candidates(self, space_id: str, limit: int) -> List[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT fact_id, source_version, tokens, user_id, agent_id, source_created_at
                    FROM shared_space_fact_pending
                    WHERE space_id = %s AND status = %s
                    ORDER BY source_created_at NULLS LAST LIMIT %s
                    """,
                    (space_id, PENDING_STATUS_PENDING, limit),
                )
                rows = cur.fetchall()
        return [
            {"fact_id": r[0], "source_version": r[1], "tokens": r[2], "user_id": r[3], "agent_id": r[4], "source_created_at": r[5]}
            for r in rows
        ]

    def claim_pending(self, space_id: str, fact_ids: List[str], batch_id: str) -> List[str]:
        """把 pending 转 processing(绑定 batch_id),返回成功 claim 的 fact_id。"""
        claimed: List[str] = []
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                for fid in fact_ids:
                    cur.execute(
                        """
                        UPDATE shared_space_fact_pending SET status = %s, batch_id = %s, update_time = %s
                        WHERE space_id = %s AND fact_id = %s AND status = %s
                        """,
                        (PENDING_STATUS_PROCESSING, batch_id, _utc_now(), space_id, fid, PENDING_STATUS_PENDING),
                    )
                    if cur.rowcount:
                        claimed.append(fid)
            conn.commit()
        return claimed

    def finish_pending(self, space_id: str, fact_ids: List[str], source_versions: List[str]) -> int:
        n = 0
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                for fid, sv in zip(fact_ids, source_versions):
                    cur.execute(
                        "UPDATE shared_space_fact_pending SET status = %s, update_time = %s WHERE space_id = %s AND fact_id = %s AND source_version = %s",
                        (PENDING_STATUS_DONE, _utc_now(), space_id, fid, sv),
                    )
                    n += cur.rowcount
            conn.commit()
        return n

    def obsolete_pending(self, fact_id: str, statuses: Optional[List[str]] = None) -> List[tuple]:
        """作废未完成登记(非 done),返回 [(space_id, tokens)] 供回退 pending_tokens。"""
        statuses = statuses or [PENDING_STATUS_PENDING, PENDING_STATUS_PROCESSING]
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT space_id, tokens FROM shared_space_fact_pending WHERE fact_id = %s AND status = ANY(%s)",
                    (fact_id, list(statuses)),
                )
                rows = cur.fetchall()
                cur.execute(
                    "UPDATE shared_space_fact_pending SET status = %s, update_time = %s WHERE fact_id = %s AND status = ANY(%s)",
                    (PENDING_STATUS_OBSOLETE, _utc_now(), fact_id, list(statuses)),
                )
            conn.commit()
        return [(r[0], r[1]) for r in rows]

    def release_pending(self, space_id: str, fact_ids: List[str]) -> int:
        """把 processing 回退为 pending(批次失败时)。"""
        n = 0
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                for fid in fact_ids:
                    cur.execute(
                        "UPDATE shared_space_fact_pending SET status = %s, batch_id = NULL, update_time = %s WHERE space_id = %s AND fact_id = %s AND status = %s",
                        (PENDING_STATUS_PENDING, _utc_now(), space_id, fid, PENDING_STATUS_PROCESSING),
                    )
                    n += cur.rowcount
            conn.commit()
        return n

    def get_source_fingerprint(self, space_id: str, fact_id: str) -> Optional[str]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT source_version FROM shared_space_fact_pending WHERE space_id = %s AND fact_id = %s AND status = %s LIMIT 1",
                    (space_id, fact_id, PENDING_STATUS_PROCESSING),
                )
                row = cur.fetchone()
        return row[0] if row else None

    # --- 批次 ---
    def create_batch(self, space_id: str, batch_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO shared_space_batches (batch_id, space_id, status) VALUES (%s, %s, %s)",
                    (batch_id, space_id, "running"),
                )
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def update_batch(self, batch_id: str, fields: Dict[str, Any]) -> bool:
        if not fields:
            return False
        sets = ", ".join(f"{k} = %s" for k in fields)
        params = list(fields.values()) + [batch_id]
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE shared_space_batches SET {sets} WHERE batch_id = %s", params)
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def list_batches_by_status(self, space_id: str, status: str) -> List[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT batch_id, space_id, status, rounds, retries, input_snapshot, output_snapshot, claimed_at, lease_until, create_time, update_time FROM shared_space_batches WHERE space_id = %s AND status = %s",
                    (space_id, status),
                )
                rows = cur.fetchall()
        return [
            {"batch_id": r[0], "space_id": r[1], "status": r[2], "rounds": r[3], "retries": r[4],
             "input_snapshot": r[5], "output_snapshot": r[6], "claimed_at": r[7], "lease_until": r[8],
             "create_time": r[9], "update_time": r[10]}
            for r in rows
        ]

    def recover_stale_batches(self, space_id: str, stale_seconds: int) -> int:
        """超 lease 的 running 批次回退为 pending(fact 释放)。"""
        cutoff = _utc_now() - timedelta(seconds=stale_seconds)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT batch_id FROM shared_space_batches WHERE space_id = %s AND status = %s AND lease_until IS NOT NULL AND lease_until <= %s",
                    (space_id, "running", cutoff),
                )
                batch_ids = [r[0] for r in cur.fetchall()]
                for bid in batch_ids:
                    cur.execute(
                        "UPDATE shared_space_fact_pending SET status = %s, batch_id = NULL WHERE space_id = %s AND batch_id = %s",
                        (PENDING_STATUS_PENDING, space_id, bid),
                    )
                    cur.execute(
                        "UPDATE shared_space_batches SET status = %s, update_time = %s WHERE batch_id = %s",
                        ("failed", _utc_now(), bid),
                    )
            conn.commit()
        return len(batch_ids)


def normalize_member_type(member_type: str) -> Optional[str]:
    """规范化成员类型:user/agent(小写),非法返回 None。"""
    if not member_type:
        return None
    t = member_type.strip().lower()
    return t if t in ("user", "agent") else None
