import warnings
warnings.warn(
    "kryptodb.storage.neo4j_client moved to kryptodb.infrastructure.neo4j.client",
    DeprecationWarning,
    stacklevel=2,
)
from kryptodb.infrastructure.neo4j.client import *  # noqa: F401, F403
