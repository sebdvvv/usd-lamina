"""USD write / export helpers used by usd-lamina."""

from __future__ import annotations

import os
import re
import shutil
from typing import Sequence

from pxr import Sdf, Usd, UsdGeom

try:
    from pxr import UsdUtils  # type: ignore
except ImportError:  # pragma: no cover
    UsdUtils = None  # type: ignore

from .layers import (
    export_layer,
    layer_has_content,
    relativize_external_refs,
    resolve_layer_asset,
)
from .paths import (
    asset_path_without_args,
    clean_path,
    path_relative_to_dir,
)

_SAFE_FOLDER = re.compile(r"[^\w.\-]+", re.UNICODE)


def _usd_rel_asset(abs_path: str, out_dir: str) -> str:
    """Relative asset path for USDA ``@…@`` links; prefer ``./`` when local."""
    rel = path_relative_to_dir(abs_path, out_dir)
    if not rel or os.path.isabs(rel) or ":" in rel[:3]:
        return rel
    if not rel.startswith("."):
        rel = "./" + rel
    return rel


def flatten_stage(stage: Usd.Stage) -> Sdf.Layer:
    """Bake the fully composed stage into one layer (no sublayers / refs)."""
    return stage.Flatten()


def build_referenced_export_layer(
    root_layer: Sdf.Layer,
    edits_layer: Sdf.Layer | None,
    out_path: str,
) -> Sdf.Layer:
    """Compose-root + session edits as one layer that still points at source files."""
    if root_layer is None:
        raise ValueError("No compose root layer")
    if UsdUtils is None:
        raise RuntimeError("pxr.UsdUtils is required for referenced export")

    out_path = clean_path(out_path)
    out_dir = os.path.dirname(os.path.abspath(out_path)) or os.getcwd()

    file_subs: list[str] = []
    anon_subs: list[Sdf.Layer] = []
    for sub in list(root_layer.subLayerPaths or []):
        layer, fs = resolve_layer_asset(root_layer, sub)
        if layer is not None and layer.anonymous:
            anon_subs.append(layer)
            continue
        if fs:
            file_subs.append(path_relative_to_dir(os.path.abspath(fs), out_dir))
        else:
            raw = asset_path_without_args(sub)
            file_subs.append(raw.replace("\\", "/") if raw else sub)

    root_copy = Sdf.Layer.CreateAnonymous("export_root_copy.usda")
    root_copy.TransferContent(root_layer)
    root_copy.subLayerPaths = []
    try:
        data = dict(root_copy.customLayerData or {})
        data.pop("lamina_anon_overs", None)
        data.pop("layerview_anon_overs", None)
        root_copy.customLayerData = data
    except Exception:  # noqa: BLE001
        pass

    out = Sdf.Layer.CreateAnonymous("export_referenced.usda")
    stacked: list[Sdf.Layer] = []
    if layer_has_content(edits_layer):
        stacked.append(edits_layer)  # type: ignore[arg-type]
    stacked.extend(anon_subs)

    if stacked:
        out.TransferContent(stacked[0])
        for weak in stacked[1:]:
            UsdUtils.StitchLayers(out, weak)
        UsdUtils.StitchLayers(out, root_copy)
    else:
        out.TransferContent(root_copy)

    out.subLayerPaths = file_subs
    relativize_external_refs(out, out_dir)
    return out


def build_payload_export_layer(
    layer_paths: Sequence[str],
    edits_layer: Sdf.Layer | None,
    out_path: str,
) -> Sdf.Layer:
    """Export as BASE sublayer + stronger files as payloads on the default prim."""
    filled: list[str] = []
    for p in layer_paths:
        cp = clean_path(p)
        if cp:
            filled.append(cp)
    if not filled:
        raise ValueError("No layers to export as payloads")

    out_path = clean_path(out_path)
    out_dir = os.path.dirname(os.path.abspath(out_path)) or os.getcwd()

    rel_filled: list[str] = []
    for p in filled:
        if os.path.isfile(p):
            rel_filled.append(path_relative_to_dir(os.path.abspath(p), out_dir))
        else:
            rel_filled.append(p.replace("\\", "/"))

    base = rel_filled[-1]
    stronger = rel_filled[:-1]

    root = Sdf.Layer.CreateAnonymous("export_payload.usda")
    stage = Usd.Stage.Open(root)
    if stage is None:
        raise RuntimeError("Failed to open payload export stage")

    root.subLayerPaths = [base]
    if stronger:
        if not stage.GetDefaultPrim():
            world = stage.DefinePrim("/World", "Xform")
            stage.SetDefaultPrim(world)
        target = stage.GetDefaultPrim()
        for asset in stronger:
            target.GetPayloads().AddPayload(asset)

    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)

    if layer_has_content(edits_layer):
        if UsdUtils is None:
            raise RuntimeError("pxr.UsdUtils is required to include edit layer")
        out = Sdf.Layer.CreateAnonymous("export_payload_with_edits.usda")
        out.TransferContent(edits_layer)
        UsdUtils.StitchLayers(out, root)
        relativize_external_refs(out, out_dir)
        return out

    relativize_external_refs(root, out_dir)
    return root


def write_flattened(stage: Usd.Stage, out_path: str) -> str:
    """Bake ``stage`` to a single layer file. Returns the written path."""
    flat = flatten_stage(stage)
    export_layer(flat, out_path)
    return clean_path(out_path)


def write_referenced(
    root_layer: Sdf.Layer,
    out_path: str,
    edits_layer: Sdf.Layer | None = None,
) -> str:
    """Write compose root (+ optional edits) keeping links to source files."""
    layer = build_referenced_export_layer(root_layer, edits_layer, out_path)
    export_layer(layer, out_path)
    return clean_path(out_path)


def write_payload(
    layer_paths: Sequence[str],
    out_path: str,
    edits_layer: Sdf.Layer | None = None,
) -> str:
    """Write BASE as sublayer + stronger paths as payloads. Returns path."""
    layer = build_payload_export_layer(layer_paths, edits_layer, out_path)
    export_layer(layer, out_path)
    return clean_path(out_path)


def sanitize_layer_folder_name(name: str) -> str:
    """Safe single path segment for a packed layer folder."""
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    name = _SAFE_FOLDER.sub("_", name).strip("._")
    return name or "layer"


def write_layered(
    layer_entries: Sequence[tuple[str, str]],
    out_path: str,
    *,
    pack_assets: bool = True,
    edits_layer: Sdf.Layer | None = None,
) -> str:
    """Write a main USDA whose sublayers are the UI layer stack (strongest first)."""
    out_path = clean_path(out_path)
    out_dir = os.path.dirname(os.path.abspath(out_path)) or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)

    filled: list[tuple[str, str]] = []
    used_names: set[str] = set()
    for raw_name, raw_path in layer_entries:
        src = clean_path(raw_path)
        if not src or not os.path.isfile(src):
            continue
        base = sanitize_layer_folder_name(
            raw_name or os.path.splitext(os.path.basename(src))[0]
        )
        name = base
        i = 1
        while name.lower() in used_names:
            name = f"{base}_{i}"
            i += 1
        used_names.add(name.lower())
        filled.append((name, os.path.abspath(src)))

    if not filled and not layer_has_content(edits_layer):
        raise ValueError("No layers to export")

    rel_subs: list[str] = []

    if layer_has_content(edits_layer):
        if pack_assets:
            edits_folder = sanitize_layer_folder_name("_edits")
            edits_dir = os.path.join(out_dir, edits_folder)
            os.makedirs(edits_dir, exist_ok=True)
            edits_file = os.path.join(edits_dir, "edits.usda")
            export_layer(edits_layer, edits_file)  # type: ignore[arg-type]
            rel_subs.append(_usd_rel_asset(edits_file, out_dir))

    for folder_name, src in filled:
        if pack_assets:
            layer_dir = os.path.join(out_dir, folder_name)
            os.makedirs(layer_dir, exist_ok=True)
            dest_name = os.path.basename(src)
            dest_file = os.path.join(layer_dir, dest_name)
            if os.path.normcase(os.path.abspath(src)) != os.path.normcase(
                os.path.abspath(dest_file)
            ):
                shutil.copy2(src, dest_file)
            rel_subs.append(_usd_rel_asset(dest_file, out_dir))
        else:
            rel_subs.append(_usd_rel_asset(src, out_dir))

    main = Sdf.Layer.CreateAnonymous("export_layered.usda")
    if layer_has_content(edits_layer) and not pack_assets:
        main.TransferContent(edits_layer)  # type: ignore[arg-type]
    main.subLayerPaths = rel_subs
    relativize_external_refs(main, out_dir)
    export_layer(main, out_path)
    return clean_path(out_path)
