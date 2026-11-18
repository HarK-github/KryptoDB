import warnings
warnings.warn(
    "kryptodb.integrity.partition_crypto moved to kryptodb.legacy.crypto.partition_crypto",
    DeprecationWarning,
    stacklevel=2,
)
from kryptodb.legacy.crypto.partition_crypto import *  # noqa: F401, F403
