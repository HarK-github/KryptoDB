import warnings
warnings.warn(
    "kryptodb.storage.redis_client moved to kryptodb.infrastructure.redis.client",
    DeprecationWarning,
    stacklevel=2,
)
from kryptodb.infrastructure.redis.client import *  # noqa: F401, F403
