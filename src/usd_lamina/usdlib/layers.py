"""USD layer helpers: open / resolve / relativize / export."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any, Sequence

from pxr import Sdf

from .paths import (
    asset_path_without_args,
    clean_path,
    path_key,
    path_relative_to_dir,
    set_path_writable,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_OPINION_TOKENS = (
    ("lookdev", "look"),
    ("look", "look"),
    ("surf", "look"),
    ("setdress", "setdress"),
    ("layout", "layout"),
    ("anim", "anim"),
    ("cache", "geo"),
    ("geo", "geo"),
    ("uv", "uv"),
    ("light", "light"),
    ("fx", "fx"),
    ("cfx", "cfx"),
    ("dmp", "dmp"),
)

_DEPT_KEYS = (
    "department",
    "dept",
    "Department",
    "pipeDepartment",
    "discipline",
    "context",
    "task",
)

_TAG_KEYS = (
    "tags",
    "Tags",
    "label",
    "labels",
    "keywords",
)

_VERSION_KEYS = (
    "version",
    "Version",
    "assetVersion",
    "publishVersion",
)

_VERSION_RE = re.compile(
    r"(?:^|[_\-./\\])v(?P<num>\d{2,4})(?:[_\-./\\]|$)",
    re.IGNORECASE,
)


def _as_plain(value: Any) -> str:
    """Flatten Vt / USD values into a short display string."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_as_plain(v) for v in value]
        return ", ".join(p for p in parts if p)
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            pv = _as_plain(v)
            if pv:
                parts.append("%s=%s" % (k, pv))
        return "; ".join(parts)
    text = str(value).strip()
    if text.startswith("Vt.") or text.startswith("<"):
        try:
            return ", ".join(str(x) for x in list(value))
        except Exception:  # noqa: BLE001
            pass
    return text


def _dict_get_ci(data: Any, keys: Sequence[str]) -> str:
    if not data:
        return ""
    for key in keys:
        if key in data:
            return _as_plain(data[key])
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        hit = lowered.get(key.lower())
        if hit is not None:
            return _as_plain(hit)
    return ""


def _version_from_path(path: str) -> str:
    if not path:
        return ""
    match = _VERSION_RE.search(path.replace("\\", "/"))
    if not match:
        return ""
    return "v%s" % match.group("num")


def _opinion_from_path(path: str) -> str:
    if not path:
        return ""
    lowered = path.replace("\\", "/").lower()
    parts = re.split(r"[/_.\-]+", lowered)
    part_set = set(parts)
    for token, label in _OPINION_TOKENS:
        if token in part_set:
            return label
    for token, label in _OPINION_TOKENS:
        if token in lowered:
            return label
    return ""


def _layer_metadata(layer: Sdf.Layer | None) -> dict[str, Any]:
    """Read USD-native layer metadata + path heuristics."""
    meta: dict[str, Any] = {
        "version": "",
        "opinion": "",
        "department": "",
        "tags": "",
        "doc": "",
        "comment": "",
        "creator": "",
        "raw": {},
    }
    if layer is None:
        return meta

    path = layer.identifier or ""
    custom: dict[str, Any] = {}
    try:
        custom = dict(layer.customLayerData) if layer.customLayerData else {}
    except Exception:  # noqa: BLE001
        custom = {}
    meta["raw"] = custom

    meta["version"] = (
        _dict_get_ci(custom, _VERSION_KEYS) or _version_from_path(path)
    )
    try:
        asset_ver = layer.GetVersion() if hasattr(layer, "GetVersion") else ""
        if asset_ver and not meta["version"]:
            meta["version"] = _as_plain(asset_ver)
    except Exception:  # noqa: BLE001
        pass

    meta["department"] = _dict_get_ci(custom, _DEPT_KEYS)
    meta["tags"] = _dict_get_ci(custom, _TAG_KEYS)
    meta["creator"] = _dict_get_ci(
        custom, ("creator", "Creator", "author", "Author")
    )
    meta["opinion"] = (
        _dict_get_ci(
            custom, ("opinion", "Opinion", "layerType", "purpose", "role")
        )
        or _opinion_from_path(path)
    )

    try:
        meta["doc"] = (layer.documentation or "").strip()
    except Exception:  # noqa: BLE001
        pass
    try:
        meta["comment"] = (layer.comment or "").strip()
    except Exception:  # noqa: BLE001
        pass

    if not meta["tags"] and custom:
        extras = []
        skip = set(
            k.lower()
            for k in _VERSION_KEYS
            + _DEPT_KEYS
            + _TAG_KEYS
            + ("creator", "author", "opinion", "layertype", "purpose", "role")
        )
        for key, value in custom.items():
            if str(key).lower() in skip:
                continue
            plain = _as_plain(value)
            if plain and len(plain) < 80:
                extras.append("%s=%s" % (key, plain))
        if extras:
            meta["tags"] = "; ".join(extras[:6])

    return meta


def open_layer(path: str) -> Sdf.Layer | None:
    """Find-or-open a USD layer. Returns ``None`` if missing / invalid."""
    path = clean_path(path) if path else ""
    if not path:
        return None
    try:
        return Sdf.Layer.FindOrOpen(path)
    except Exception:  # noqa: BLE001
        return None


def resolve_layer_asset(
    anchor: Sdf.Layer, asset_path: str
) -> tuple[Sdf.Layer | None, str]:
    """Resolve ``asset_path`` relative to ``anchor`` → (layer, filesystem path)."""
    raw = asset_path_without_args(asset_path or "")
    if not raw:
        return None, ""
    layer = Sdf.Layer.Find(raw)
    if layer is None and anchor is not None:
        layer = Sdf.Layer.FindRelativeToLayer(anchor, raw)
    if layer is None:
        layer = Sdf.Layer.FindOrOpen(raw)
    fs = ""
    if layer is not None and not layer.anonymous:
        fs = layer.realPath or layer.identifier or raw
    elif os.path.isfile(raw):
        fs = os.path.abspath(raw)
    return layer, fs


def relativize_external_refs(layer: Sdf.Layer, out_dir: str) -> None:
    """Rewrite ``layer`` external refs (sublayers, references, …) as relative."""
    if layer is None:
        return
    try:
        refs = list(layer.GetExternalReferences() or [])
    except Exception:  # noqa: BLE001
        refs = []
    for old in refs:
        raw = asset_path_without_args(old)
        if not raw or raw.startswith("anon:"):
            continue
        _resolved, fs = resolve_layer_asset(layer, old)
        if not fs or not os.path.exists(fs):
            if not os.path.isabs(raw):
                continue
            fs = raw
        new = path_relative_to_dir(fs, out_dir)
        if new and new != old:
            try:
                layer.UpdateExternalReference(old, new)
            except Exception:  # noqa: BLE001
                pass


def layer_has_content(layer: Sdf.Layer | None) -> bool:
    """True if ``layer`` appears to hold authored prim content."""
    if layer is None:
        return False
    try:
        if layer.empty:
            return False
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(layer.GetPrimAtPath("/")) or bool(list(layer.rootPrims))
    except Exception:  # noqa: BLE001
        return True


def export_layer(layer: Sdf.Layer, out_path: str) -> None:
    """Write ``layer`` to disk; format follows the file extension."""
    out_path = clean_path(out_path)
    ext = os.path.splitext(out_path)[1].lower()
    if ext not in (".usda", ".usdc", ".usd"):
        raise ValueError("Output must be .usda, .usdc, or .usd")
    set_path_writable(out_path)
    if not layer.Export(out_path):
        raise RuntimeError(f"Export failed: {out_path}")


def save_layer(layer: Sdf.Layer) -> bool:
    """``layer.Save()`` after making the file writable if needed."""
    path = ""
    try:
        path = (layer.realPath or "").strip()
    except Exception:  # noqa: BLE001
        path = ""
    if not path:
        try:
            ident = (layer.identifier or "").strip()
        except Exception:  # noqa: BLE001
            ident = ""
        if ident and not ident.startswith("anon:"):
            path = ident
    if path:
        set_path_writable(clean_path(path))
    return bool(layer.Save())


def sublayer_paths_allowing_duplicates(
    filled: Sequence[str],
) -> tuple[list[str], list[Sdf.Layer], str]:
    """USD forbids duplicate subLayer paths.

    Keep the weakest (BASE-most) occurrence as the real file on disk.
    Each stronger repeat becomes an empty anonymous over layer.

    ``filled`` is strongest-first. Returns
    ``(sublayer_paths_strongest_first, anon_keep_alive, notice)``.
    """
    resolved_rev: list[str] = []
    keep_alive: list[Sdf.Layer] = []
    seen: set[str] = set()
    n_anon = 0
    for path in reversed(list(filled)):
        key = path_key(path)
        if key not in seen:
            seen.add(key)
            resolved_rev.append(path)
            continue
        base_name = os.path.splitext(os.path.basename(path))[0] or "layer"
        anon = Sdf.Layer.CreateAnonymous(f"over_{base_name}_{n_anon}.usda")
        n_anon += 1
        keep_alive.append(anon)
        resolved_rev.append(anon.identifier)
    note = ""
    if n_anon:
        note = (
            f"{n_anon} duplicate path(s) -> empty over layer(s) for overrides "
            "(same file cannot appear twice; edit the over, not a 2nd copy)"
        )
    return list(reversed(resolved_rev)), keep_alive, note


def extract_sublayer_stack_paths(usd_path: str) -> list[str]:
    """Resolve direct ``subLayers`` of ``usd_path`` as absolute filesystem paths.

    Returns paths in **BASE-first** order (weakest → strongest). If the file
    has no sublayers, returns ``[usd_path]`` alone.
    """
    usd_path = os.path.abspath(clean_path(usd_path))
    if not usd_path or not os.path.isfile(usd_path):
        return []
    layer = Sdf.Layer.FindOrOpen(usd_path)
    if layer is None:
        return [usd_path]
    subs = list(layer.subLayerPaths or [])
    if not subs:
        return [usd_path]
    strong_first: list[str] = []
    seen: set[str] = set()
    for sub in subs:
        resolved, fs = resolve_layer_asset(layer, sub)
        if resolved is not None and resolved.anonymous:
            continue
        path = ""
        if fs and os.path.isfile(fs):
            path = os.path.abspath(fs)
        else:
            raw = asset_path_without_args(sub)
            cand = raw
            if not os.path.isabs(cand):
                cand = os.path.normpath(
                    os.path.join(os.path.dirname(usd_path), raw.replace("\\", "/"))
                )
            if os.path.isfile(cand):
                path = os.path.abspath(cand)
        if not path:
            continue
        key = path_key(path)
        if key in seen:
            continue
        seen.add(key)
        strong_first.append(path)
    if not strong_first:
        return [usd_path]
    return list(reversed(strong_first))


def iter_prim_specs(
    layer: Sdf.Layer, prim_path: Sdf.Path | None = None
) -> Iterator[Sdf.PrimSpec]:
    """Iterate PrimSpecs under ``prim_path`` (default: layer root)."""

    def _traverse(path: Sdf.Path) -> None:
        if path == prim_path:
            return
        if not path.IsPrimPath():
            return
        paths.append(path)

    paths: list[Sdf.Path] = []

    if prim_path is None:
        prim_path = layer.pseudoRoot.path

    layer.Traverse(prim_path, _traverse)
    return map(layer.GetPrimAtPath, paths)
