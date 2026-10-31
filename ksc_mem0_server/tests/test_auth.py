"""鉴权测试:hash_key / _verify_admin / _verify_api_key。"""

import uuid

from ksc_mem0_server.auth import _verify_api_key, _verify_admin, hash_key
from ksc_mem0_server.config import reset_settings
from ksc_mem0_server.db import SessionLocal
from ksc_mem0_server.models import ApiKey


def test_hash_key_deterministic_and_distinct():
    assert hash_key("abc") == hash_key("abc")
    assert hash_key("abc") != hash_key("abd")
    assert hash_key("abc") != hash_key("ABC")


def test_verify_admin(pg_settings, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "secret-admin")
    reset_settings()
    assert _verify_admin("secret-admin") is True
    assert _verify_admin("wrong") is False
    assert _verify_admin(None) is False


def test_verify_api_key_lookup(pg_settings):
    key = f"live-key-{uuid.uuid4().hex}"
    with SessionLocal() as session:
        session.add(ApiKey(key_prefix=key[:8], key_hash=hash_key(key), label="t", product_scope="*"))
        session.commit()
    assert _verify_api_key(key) is not None
    assert _verify_api_key("no-such-key") is None


def test_verify_api_key_revoked_excluded(pg_settings):
    from datetime import datetime, timezone

    key = f"rev-key-{uuid.uuid4().hex}"
    with SessionLocal() as session:
        session.add(
            ApiKey(
                key_prefix=key[:8],
                key_hash=hash_key(key),
                label="t",
                product_scope="*",
                revoked_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    assert _verify_api_key(key) is None
