"""shared_space 共享记忆空间子系统。"""

from .member_catalog import MemberCatalogStore
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
    "compute_source_version",
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
