import sys
import importlib.metadata

# Metadata hooks for libraries like transformers that query importlib.metadata
_orig_version = importlib.metadata.version
def _safe_version(pkg_name):
    try:
        return _orig_version(pkg_name)
    except importlib.metadata.PackageNotFoundError:
        if pkg_name == "packaging":
            return "24.0.0"
        raise
importlib.metadata.version = _safe_version

_orig_distribution = importlib.metadata.distribution
def _safe_distribution(distribution_name):
    try:
        return _orig_distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        if distribution_name == "packaging":
            import types
            d = types.SimpleNamespace()
            d.version = "24.0.0"
            return d
        raise
importlib.metadata.distribution = _safe_distribution

try:
    import packaging
    import packaging.version
    import packaging.specifiers
    import packaging.requirements
except ImportError:
    import types
    import re

    class InvalidVersion(ValueError): pass
    class InvalidSpecifier(ValueError): pass
    class InvalidRequirement(ValueError): pass

    class Version:
        def __init__(self, v):
            self.v = str(v)
            parts = [int(p) for p in re.findall(r"\d+", self.v)] or [0]
            self.major = parts[0]
            self.minor = parts[1] if len(parts) > 1 else 0
            self.micro = parts[2] if len(parts) > 2 else 0
            self.release = tuple(parts)

        @property
        def public(self):
            return self.v

        @property
        def base_version(self):
            return f"{self.major}.{self.minor}.{self.micro}"

        def __str__(self):
            return self.v

        def __repr__(self):
            return f"<Version('{self.v}')>"

        def __lt__(self, other):
            other_parts = other.release if isinstance(other, Version) else Version(other).release
            return self.release < other_parts

        def __le__(self, other):
            other_parts = other.release if isinstance(other, Version) else Version(other).release
            return self.release <= other_parts

        def __gt__(self, other):
            other_parts = other.release if isinstance(other, Version) else Version(other).release
            return self.release > other_parts

        def __ge__(self, other):
            other_parts = other.release if isinstance(other, Version) else Version(other).release
            return self.release >= other_parts

        def __eq__(self, other):
            other_parts = other.release if isinstance(other, Version) else Version(other).release
            return self.release == other_parts

    def parse(v):
        return Version(v)

    pkg = types.ModuleType("packaging")
    pkg_ver = types.ModuleType("packaging.version")
    pkg_ver.parse = parse
    pkg_ver.Version = Version
    pkg_ver.InvalidVersion = InvalidVersion
    pkg.version = pkg_ver

    pkg_spec = types.ModuleType("packaging.specifiers")
    pkg_spec.SpecifierSet = lambda *a, **kw: True
    pkg_spec.InvalidSpecifier = InvalidSpecifier
    pkg.specifiers = pkg_spec

    pkg_req = types.ModuleType("packaging.requirements")
    pkg_req.Requirement = lambda *a, **kw: None
    pkg_req.InvalidRequirement = InvalidRequirement
    pkg.requirements = pkg_req

    sys.modules["packaging"] = pkg
    sys.modules["packaging.version"] = pkg_ver
    sys.modules["packaging.specifiers"] = pkg_spec
    sys.modules["packaging.requirements"] = pkg_req


