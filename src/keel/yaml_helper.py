"""YAML parsing helpers with C-extension acceleration."""

from __future__ import annotations

from typing import Any

import yaml

try:
    from yaml import CSafeDumper as _SafeDumper
    from yaml import CSafeLoader as _SafeLoader
except ImportError:
    from yaml import SafeDumper as _SafeDumper
    from yaml import SafeLoader as _SafeLoader

def load(stream: str | bytes) -> Any:
    """Parse a YAML document using the C-extension if available."""
    return yaml.load(stream, Loader=_SafeLoader)  # nosec B506

def dump(data: Any, **kwargs: Any) -> str:
    """Serialize a YAML document using the C-extension if available."""
    return yaml.dump(data, Dumper=_SafeDumper, **kwargs)

YAMLError = yaml.YAMLError

