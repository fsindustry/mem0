"""shared_space 共享记忆空间子系统。"""

from .handler import SharedSpaceExtractHandler, register_shared_space_handler
from .member_catalog import MemberCatalogStore
from .prompts import build_shared_extract_messages, parse_extract_actions
from .registration import SharedSpaceRegistrar, compute_source_version
from .schema import ensure_shared_space_schema
from .scheduler import (
    SHARED_SPACE_CATEGORY,
    SHARED_SPACE_TASK_TYPE,
    InFlightGate,
    SharedSpacePublisher,
    invoke_trigger,
    make_trigger,
)
from .store import (
    PENDING_STATUS_DONE,
    PENDING_STATUS_PENDING,
    PENDING_STATUS_PROCESSING,
    DuplicateSpaceNameError,
    SharedSpaceStore,
    normalize_member_type,
)
from .tokens import estimate_tokens
from .vector_store import SharedVectorStore, shared_collection_name

__all__ = [
    "ensure_shared_space_schema",
    "SharedSpaceStore",
    "SharedVectorStore",
    "MemberCatalogStore",
    "SharedSpaceRegistrar",
    "SharedSpaceExtractHandler",
    "register_shared_space_handler",
    "compute_source_version",
    "build_shared_extract_messages",
    "parse_extract_actions",
    "DuplicateSpaceNameError",
    "InFlightGate",
    "SharedSpacePublisher",
    "SHARED_SPACE_CATEGORY",
    "SHARED_SPACE_TASK_TYPE",
    "make_trigger",
    "invoke_trigger",
    "PENDING_STATUS_PENDING",
    "PENDING_STATUS_PROCESSING",
    "PENDING_STATUS_DONE",
    "normalize_member_type",
    "estimate_tokens",
    "shared_collection_name",
]
