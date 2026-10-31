"""USD load / compose helpers."""

from __future__ import annotations

import os
from typing import Any, Sequence

from pxr import Sdf, Usd, UsdGeom

from .layers import (
    extract_sublayer_stack_paths,
    open_layer,
    sublayer_paths_allowing_duplicates,
)
from .paths import clean_path, path_key

COMPOSE_MODES = (
    "sublayer",
    "reference",
    "payload",
)

_LOAD_TYPE_LABELS = {
    "stage": "stage",
    "root": "rootLayer",
    "sublayer": "sublayer",
    "reference": "reference",
    "payload": "payload",
    "inherit": "inherit",
    "specialize": "specialize",
    "variant": "variant",
}


def _load_type_label(
    arc_kind: str,
    load_type: str | None = None,
    arc: Any = None,
) -> str:
    """Explicit USD load mechanism (payload / reference / sublayer / ...)."""
    kind = load_type or arc_kind
    label = _LOAD_TYPE_LABELS.get(kind)
    if not label:
        return "-"
    if kind == "payload" and arc is not None:
        try:
            if arc.GetTargetNode() is not None and arc.GetTargetNode().IsCulled():
                return "payload (unloaded)"
        except Exception:  # noqa: BLE001
            pass
    return label


def open_stage(path: str) -> Usd.Stage | None:
    """Open a USD stage from a filesystem path."""
    path = clean_path(path)
    if not path or not os.path.isfile(path):
        return None
    try:
        return Usd.Stage.Open(path)
    except Exception:  # noqa: BLE001
        return None


def build_composed_stage(
    layer_paths: Sequence[str],
    mode: str = "sublayer",
) -> tuple[Usd.Stage | None, Sdf.Layer | None, str, str]:
    """Build a stage from layered file paths.

    ``layer_paths[0]`` = strongest … ``layer_paths[-1]`` = base (weakest).
    Empty slots are skipped. Returns ``(stage, root_layer, error, notice)``.
    """
    filled: list[str] = []
    for p in layer_paths:
        cp = clean_path(p)
        if cp:
            if not os.path.isfile(cp):
                return None, None, f"Missing file:\n{cp}", ""
            filled.append(cp)

    if not filled:
        return None, None, "No layers set.", ""

    mode = (mode or "sublayer").strip().lower()
    if mode not in COMPOSE_MODES:
        return None, None, f"Unknown compose mode: {mode}", ""

    root = Sdf.Layer.CreateAnonymous("compose_root.usda")
    stage = Usd.Stage.Open(root)
    if stage is None:
        return None, None, "Failed to open anonymous compose stage.", ""

    notice = ""
    _anon_keep: list[Sdf.Layer] = []

    if mode == "sublayer":
        sub_paths, _anon_keep, notice = sublayer_paths_allowing_duplicates(filled)
        if not sub_paths and notice:
            return None, None, notice, ""
        try:
            root.subLayerPaths = sub_paths
        except Exception as exc:  # noqa: BLE001
            return None, None, f"Failed to set sublayers:\n{exc}", ""
    else:
        base = filled[-1]
        stronger = filled[:-1]
        base_key = path_key(base)
        stronger_unique: list[str] = []
        seen_strong: set[str] = {base_key}
        for path in stronger:
            key = path_key(path)
            if key in seen_strong:
                notice = (
                    "Skipped duplicate path(s) in reference/payload mode "
                    "(same file as a weaker slot)."
                )
                continue
            seen_strong.add(key)
            stronger_unique.append(path)
        root.subLayerPaths = [base]
        if stronger_unique:
            if not stage.GetDefaultPrim():
                world = stage.DefinePrim("/World", "Xform")
                stage.SetDefaultPrim(world)
            target = stage.GetDefaultPrim()
            for path in stronger_unique:
                if mode == "reference":
                    target.GetReferences().AddReference(path)
                else:
                    target.GetPayloads().AddPayload(path)

    if _anon_keep:
        root.customLayerData = dict(root.customLayerData or {})
        root.customLayerData["lamina_anon_overs"] = [
            layer.identifier for layer in _anon_keep
        ]

    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    return stage, root, "", notice


def load_composed_stage(
    layer_paths: Sequence[str],
    mode: str = "sublayer",
) -> tuple[Usd.Stage | None, Sdf.Layer | None, str, str]:
    """Alias for :func:`build_composed_stage`."""
    return build_composed_stage(layer_paths, mode=mode)


# Re-export for callers that imported these from load historically.
__all__ = [
    "COMPOSE_MODES",
    "build_composed_stage",
    "extract_sublayer_stack_paths",
    "load_composed_stage",
    "open_layer",
    "open_stage",
    "_load_type_label",
]
