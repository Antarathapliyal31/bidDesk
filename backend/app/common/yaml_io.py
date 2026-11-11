"""Strict YAML loader shared across the semantic bundle and gap-rules loaders.

Catches every failure mode worth distinguishing (missing file, bad UTF-8,
unreadable file, YAML syntax error, duplicate mapping keys, empty file,
non-mapping top level) and raises `YamlLoadError` tagged with the file
name. Callers wrap the raised error in their own exception type so the
"SemanticLayerError" / "GapRulesError" contracts stay stable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class YamlLoadError(RuntimeError):
    """Raised when a YAML file can't be loaded cleanly."""


class _DuplicateKey(Exception):
    def __init__(self, key: Any, line: int) -> None:
        super().__init__(f"duplicate key '{key}' at line {line}")
        self.key = key
        self.line = line


class StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys.

    PyYAML's default silently keeps the last of duplicate keys, which hides
    typos like declaring `r_value` twice under `fields:`. Overriding
    `construct_mapping` makes that a hard error.

    Caveat: YAML merge-key overrides (`<<: *anchor` followed by a field
    that shadows one from the anchor) would also be flagged as duplicate
    keys by this loader. The bundle doesn't use anchors or merge keys
    today, so this is fine; a future caller that needs merge-key semantics
    would have to loosen this check.
    """

    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None, None, f"expected a mapping node, got {node.id}", node.start_mark
            )
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise _DuplicateKey(key, key_node.start_mark.line + 1)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def read_yaml(path: Path, *, missing_label: str = "file") -> dict[str, Any]:
    """Read and parse a YAML file with strict semantics.

    Raises YamlLoadError with the file name on any failure. `missing_label`
    lets callers customise the "missing <label>:" phrasing (e.g.
    "bundle file" for the semantic layer).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise YamlLoadError(f"missing {missing_label}: {path.name}") from exc
    except UnicodeDecodeError as exc:
        raise YamlLoadError(f"{path.name}: not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise YamlLoadError(f"{path.name}: could not read file: {exc}") from exc
    try:
        data = yaml.load(text, Loader=StrictSafeLoader)
    except _DuplicateKey as exc:
        raise YamlLoadError(f"{path.name}: duplicate key '{exc.key}' at line {exc.line}") from exc
    except yaml.YAMLError as exc:
        raise YamlLoadError(f"{path.name}: YAML parse error: {exc}") from exc
    if data is None:
        raise YamlLoadError(f"{path.name}: file is empty")
    if not isinstance(data, dict):
        raise YamlLoadError(f"{path.name}: top level must be a mapping")
    return data
