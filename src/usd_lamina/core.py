"""usd-lamina — core (session + USD helpers, no Qt).

UI: :mod:`usd_lamina.ui`
Launch: ``python -m usd_lamina``
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from pxr import Sdf, Usd

try:
    from pxr import UsdShade
except ImportError:  # pragma: no cover
    UsdShade = None  # type: ignore

from usd_lamina.usdlib.attr import (
    _shade_connect_label,
    attr_time_samples,
    attr_value_for_dialog,
    format_attr_display,
    format_attr_value,
    get_attr_value,
    parse_attr_input,
)
from usd_lamina.usdlib.layers import (
    _as_plain,
    _layer_metadata,
    _opinion_from_path,
    _version_from_path,
    open_layer,
)
from usd_lamina.usdlib.load import _load_type_label, build_composed_stage
from usd_lamina.usdlib.paths import clean_path
from usd_lamina.usdlib.prims import (
    _iter_all_prim_children,
    _prim_metadata,
    prim_query_time,
    prim_visibility_label,
)
from usd_lamina.usdlib.write import (
    write_flattened,
    write_layered,
    write_payload,
    write_referenced,
)

# Local aliases / re-exports for ui.py
_clean_path = clean_path
_open_layer = open_layer


def attr_connection_paths(attr: Usd.Attribute) -> list[str]:
    """Authored connection target paths on a USD attribute (may be empty)."""
    if not attr:
        return []
    try:
        if attr.HasAuthoredConnections():
            return [str(p) for p in attr.GetConnections()]
    except Exception:  # noqa: BLE001
        pass
    if UsdShade is not None:
        try:
            if UsdShade.ConnectableAPI.HasConnectedSource(attr):
                src = UsdShade.ConnectableAPI.GetConnectedSource(attr)
                if src:
                    return [_shade_connect_label(src)]
        except Exception:  # noqa: BLE001
            pass
    return []


def find_prims_bound_to_material(
    stage: Usd.Stage, material_path: str
) -> list[Usd.Prim]:
    """Return prims whose resolved material binding is ``material_path``."""
    if not stage or not material_path or UsdShade is None:
        return []
    target = Sdf.Path(material_path)
    out: list[Usd.Prim] = []
    try:
        prims = stage.Traverse(Usd.PrimAllPrimsPredicate)
    except Exception:  # noqa: BLE001
        prims = stage.Traverse()
    for prim in prims:
        if not prim or not prim.IsValid() or not prim.IsActive():
            continue
        try:
            api = UsdShade.MaterialBindingAPI(prim)
            bound_mat, _rel = api.ComputeBoundMaterial()
        except Exception:  # noqa: BLE001
            continue
        if not bound_mat:
            continue
        try:
            if bound_mat.GetPath() == target:
                out.append(prim)
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda p: str(p.GetPath()))
    return out

def _file_format(path):
    if not path or path in (".", "/"):
        return "-"
    clean = path.split(":", 1)[0] if "://" not in path else path
    ext = os.path.splitext(clean)[1].lstrip(".").lower()
    return ext or "-"

def _basename(path):
    if not path:
        return "(empty)"
    return os.path.basename(path.rstrip("/\\")) or path

def _meta_tooltip(meta, file_path=""):
    lines = []
    if file_path:
        lines.append(file_path)
    for label, key in (
        ("Version", "version"),
        ("Opinion", "opinion"),
        ("Department", "department"),
        ("Tags", "tags"),
        ("Creator", "creator"),
        ("Doc", "doc"),
        ("Comment", "comment"),
    ):
        value = meta.get(key) or ""
        if value:
            lines.append("%s: %s" % (label, value))
    raw = meta.get("raw") or {}
    if isinstance(raw, dict) and raw and "assetInfo" not in raw:
        # Layer customLayerData dump (short)
        dump = _as_plain(raw)
        if dump and dump not in "\n".join(lines):
            lines.append("customLayerData: %s" % dump[:300])
    return "\n".join(lines)

def _resolve_layer_path(anchor, relative):
    try:
        return anchor.ComputeAbsolutePath(relative)
    except Exception:
        return relative

def _file_exists(path):
    if not path or path.startswith("anon:"):
        return True
    check = path.split(":", 1)[0] if "SDF_FORMAT_ARGS" in path else path
    return os.path.exists(check)

NUM_LAYERS = 6

MAX_LOAD_FILES = 5

def slot_strength_label(index: int, num: int = NUM_LAYERS) -> str:
    """Paper stack: BASE Layer (bottom) → Layer 2… → TOP Layer (strongest)."""
    if index >= num - 1:
        return "BASE Layer"
    if index == 0:
        return "TOP Layer"
    # From base upward: Layer 2, Layer 3, … (index 1 = Layer num-1)
    return f"Layer {num - index}"

COMPOSE_MODES = (
    ("sublayer", "Sublayer (over)"),
    ("reference", "Reference"),
    ("payload", "Payload"),
)

def prim_type_label(prim: Usd.Prim) -> str:
    t = prim.GetTypeName()
    return t if t else "Prim"

_PINNED_ATTR_NAMES = (
    "path",
    "type",
    "loaded",
    "active",
    "visibility",
    "instanceable",
    "specifier",
)

def _collect_shader_network(
    prim: Usd.Prim, *, depth: int = 0, max_depth: int = 5, _seen: set[str] | None = None
) -> list[dict[str, Any]]:
    """Expand a Material/Shader prim into connected shade-network child rows."""
    if UsdShade is None or depth > max_depth or not prim or not prim.IsValid():
        return []
    seen = _seen if _seen is not None else set()
    path = str(prim.GetPath())
    if path in seen:
        return []
    seen.add(path)

    nodes: list[dict[str, Any]] = []
    mat = UsdShade.Material(prim)
    if mat:
        outputs: list[tuple[str, Any]] = []
        for out_label, getter_name in (
            ("outputs:surface", "GetSurfaceOutput"),
            ("outputs:displacement", "GetDisplacementOutput"),
            ("outputs:volume", "GetVolumeOutput"),
        ):
            getter = getattr(mat, getter_name, None)
            if callable(getter):
                try:
                    outputs.append((out_label, getter()))
                except Exception:  # noqa: BLE001
                    pass
        for out_label, output in outputs:
            if not output:
                continue
            try:
                connected = output.GetConnectedSource()
            except Exception:  # noqa: BLE001
                connected = None
            if not connected:
                attr = output.GetAttr() if hasattr(output, "GetAttr") else None
                if attr and attr.HasAuthoredValueOpinion():
                    nodes.append(
                        {
                            "kind": "connect",
                            "name": out_label,
                            "type": "output",
                            "value": format_attr_value(attr),
                            "owner": path,
                            "has_conn": False,
                            "children": [],
                        }
                    )
                continue
            src_api, base_name, source_type = connected
            src_prim = src_api.GetPrim() if hasattr(src_api, "GetPrim") else None
            child_rows = (
                _collect_shader_network(
                    src_prim, depth=depth + 1, max_depth=max_depth, _seen=seen
                )
                if src_prim
                else []
            )
            nodes.append(
                {
                    "kind": "connect",
                    "name": out_label,
                    "type": f"{source_type}:{base_name}",
                    "value": _shade_connect_label(connected),
                    "goto": str(src_prim.GetPath()) if src_prim else "",
                    "owner": path,
                    "has_conn": True,
                    "children": child_rows,
                }
            )

    shader = UsdShade.Shader(prim)
    if shader:
        node_id = shader.GetIdAttr().Get() if shader.GetIdAttr() else None
        if node_id:
            nodes.append(
                {
                    "kind": "connect",
                    "name": "info:id",
                    "type": "token",
                    "value": str(node_id),
                    "owner": path,
                    "has_conn": False,
                    "children": [],
                }
            )
        for inp in shader.GetInputs():
            try:
                connected = inp.GetConnectedSource()
            except Exception:  # noqa: BLE001
                connected = None
            if connected:
                src_api, base_name, source_type = connected
                src_prim = src_api.GetPrim() if hasattr(src_api, "GetPrim") else None
                child_rows = (
                    _collect_shader_network(
                        src_prim, depth=depth + 1, max_depth=max_depth, _seen=seen
                    )
                    if src_prim
                    else []
                )
                nodes.append(
                    {
                        "kind": "connect",
                        "name": f"inputs:{inp.GetBaseName()}",
                        "type": f"{source_type}:{base_name}",
                        "value": _shade_connect_label(connected),
                        "goto": str(src_prim.GetPath()) if src_prim else "",
                        "owner": path,
                        "has_conn": True,
                        "children": child_rows,
                    }
                )
            else:
                attr = inp.GetAttr()
                if attr and (attr.HasAuthoredValueOpinion() or attr.HasValue()):
                    nodes.append(
                        {
                            "kind": "connect",
                            "name": f"inputs:{inp.GetBaseName()}",
                            "type": str(attr.GetTypeName()),
                            "value": format_attr_value(attr),
                            "owner": path,
                            "has_conn": False,
                            "children": [],
                        }
                    )
    return nodes

def collect_material_binding_nodes(prim: Usd.Prim) -> list[dict[str, Any]]:
    """Material binding rows (expandable) for the attribute tree."""
    if UsdShade is None:
        return []

    nodes: list[dict[str, Any]] = []
    api = UsdShade.MaterialBindingAPI(prim)

    # Resolved binding (direct or collection / inherited)
    try:
        bound_mat, bound_rel = api.ComputeBoundMaterial()
    except Exception:  # noqa: BLE001
        bound_mat, bound_rel = None, None

    if bound_mat and bound_mat.GetPrim().IsValid():
        mat_prim = bound_mat.GetPrim()
        mat_path = str(mat_prim.GetPath())
        rel_name = bound_rel.GetName() if bound_rel else "material:binding"
        strength = ""
        try:
            if bound_rel:
                strength = str(
                    UsdShade.MaterialBindingAPI.GetMaterialBindingStrength(bound_rel)
                )
        except Exception:  # noqa: BLE001
            strength = ""
        children: list[dict[str, Any]] = [
            {
                "kind": "connect",
                "name": "bound material",
                "type": mat_prim.GetTypeName() or "Material",
                "value": mat_path,
                "goto": mat_path,
                "children": [],
            },
        ]
        if bound_rel:
            children.append(
                {
                    "kind": "connect",
                    "name": "binding rel",
                    "type": "relationship",
                    "value": rel_name,
                    "children": [],
                }
            )
        if strength:
            children.append(
                {
                    "kind": "connect",
                    "name": "strength",
                    "type": "",
                    "value": strength,
                    "children": [],
                }
            )
        # Expand shade network under the bound material
        net = _collect_shader_network(mat_prim)
        if net:
            children.append(
                {
                    "kind": "connect",
                    "name": "shader network",
                    "type": "Material",
                    "value": mat_path,
                    "goto": mat_path,
                    "children": net,
                }
            )
        nodes.append(
            {
                "kind": "bind",
                "name": "material:binding (resolved)",
                "type": "relationship",
                "value": mat_path,
                "goto": mat_path,
                "children": children,
            }
        )
    else:
        nodes.append(
            {
                "kind": "bind",
                "name": "material:binding (resolved)",
                "type": "relationship",
                "value": "<unbound>",
                "children": [],
            }
        )

    # Authored direct binding targets (even if compute failed / unbound)
    try:
        direct_rel = api.GetDirectBindingRel()
        if direct_rel and direct_rel.HasAuthoredTargets():
            targets = [str(t) for t in direct_rel.GetTargets()]
            children = [
                {
                    "kind": "connect",
                    "name": f"target[{i}]",
                    "type": "path",
                    "value": t,
                    "goto": t if t.startswith("/") else "",
                    "children": [],
                }
                for i, t in enumerate(targets)
            ]
            nodes.append(
                {
                    "kind": "bind",
                    "name": "material:binding (direct)",
                    "type": "relationship",
                    "value": ", ".join(targets) if targets else "<empty>",
                    "goto": targets[0] if targets else "",
                    "children": children,
                }
            )
    except Exception:  # noqa: BLE001
        pass

    # Collection bindings
    try:
        for coll_binding in api.GetCollectionBindings():
            coll = coll_binding.GetCollection()
            mat = coll_binding.GetMaterial()
            coll_name = coll.GetName() if coll else "?"
            mat_path = str(mat.GetPath()) if mat and mat.GetPrim() else "<none>"
            mat_prim = mat.GetPrim() if mat else None
            children = [
                {
                    "kind": "connect",
                    "name": "collection",
                    "type": "",
                    "value": coll_name,
                    "children": [],
                },
                {
                    "kind": "connect",
                    "name": "material",
                    "type": "Material",
                    "value": mat_path,
                    "goto": mat_path if mat_path.startswith("/") else "",
                    "children": _collect_shader_network(mat_prim) if mat_prim else [],
                },
            ]
            nodes.append(
                {
                    "kind": "bind",
                    "name": f"material:binding:{coll_name}",
                    "type": "collection",
                    "value": mat_path,
                    "goto": mat_path if mat_path.startswith("/") else "",
                    "children": children,
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return nodes

def collect_prim_rows(
    prim: Usd.Prim,
    time: Usd.TimeCode | None = None,
) -> list[dict[str, Any]]:
    """Return attribute-tree nodes: kind/name/type/value[/children/goto]."""
    pinned = {
        "path": str(prim.GetPath()),
        "type": prim_type_label(prim),
        "loaded": str(prim.IsLoaded()),
        "active": str(prim.IsActive()),
        "visibility": prim_visibility_label(prim),
        "instanceable": str(prim.IsInstanceable()),
        "specifier": str(prim.GetSpecifier()),
    }
    rows: list[dict[str, Any]] = [
        {"kind": "meta", "name": name, "type": "", "value": pinned[name], "children": []}
        for name in _PINNED_ATTR_NAMES
    ]
    if prim.HasAuthoredDocumentation():
        rows.append(
            {
                "kind": "meta",
                "name": "documentation",
                "type": "",
                "value": prim.GetDocumentation() or "",
                "children": [],
            }
        )

    # Material bindings (expandable) — after pinned meta
    rows.extend(collect_material_binding_nodes(prim))

    pinned_names = set(_PINNED_ATTR_NAMES)
    skip_rels = {
        "material:binding",
        "material:binding:collection",
    }
    owner = str(prim.GetPath())
    for attr in prim.GetAttributes():
        name = attr.GetName()
        if name in pinned_names:
            continue
        conns = attr_connection_paths(attr)
        rows.append(
            {
                "kind": "attr",
                "name": name,
                "type": str(attr.GetTypeName()),
                "value": format_attr_display(attr, time),
                "owner": owner,
                "has_conn": bool(conns),
                "children": [],
            }
        )
    for rel in prim.GetRelationships():
        name = rel.GetName()
        # Skip raw binding rels — shown in the bind section with expansion
        if name == "material:binding" or name.startswith("material:binding:"):
            continue
        if name in skip_rels:
            continue
        targets = rel.GetTargets()
        t_strs = [str(t) for t in targets]
        children = [
            {
                "kind": "connect",
                "name": f"target[{i}]",
                "type": "path",
                "value": t,
                "goto": t if t.startswith("/") else "",
                "children": [],
            }
            for i, t in enumerate(t_strs)
        ]
        rows.append(
            {
                "kind": "rel",
                "name": name,
                "type": "relationship",
                "value": ", ".join(t_strs) if t_strs else "<empty>",
                "goto": t_strs[0] if t_strs else "",
                "children": children,
            }
        )
    return rows

class LayerViewSession:
    """Non-UI session state for compose / edits / dirty tracking / export."""

    def __init__(self) -> None:
        self.stage: Usd.Stage | None = None
        self.root_layer: Sdf.Layer | None = None
        self.edits_layer: Sdf.Layer | None = None
        self.edit_target_layer: Sdf.Layer | None = None
        self.dirty_prims: set[str] = set()
        self.dirty_attrs: set[tuple[str, str]] = set()

    def clear(self) -> None:
        self.stage = None
        self.root_layer = None
        self.edits_layer = None
        self.edit_target_layer = None
        self.dirty_prims.clear()
        self.dirty_attrs.clear()

    def compose(
        self,
        layer_paths: Sequence[str],
        mode: str = "sublayer",
    ) -> tuple[Usd.Stage | None, str, str]:
        """Compose layers. Returns ``(stage, error, notice)``."""
        stage, root, err, notice = build_composed_stage(list(layer_paths), mode=mode)
        if err:
            self.clear()
            return None, err, notice or ""
        edit = Sdf.Layer.CreateAnonymous("edits.usda")
        stage.GetSessionLayer().subLayerPaths.insert(0, edit.identifier)
        stage.SetEditTarget(edit)
        self.stage = stage
        self.root_layer = root
        self.edits_layer = edit
        self.edit_target_layer = edit
        self.dirty_prims.clear()
        self.dirty_attrs.clear()
        return stage, "", notice or ""

    def mark_dirty(self, prim_path: str, attr_name: str | None = None) -> None:
        self.dirty_prims.add(prim_path)
        if attr_name:
            self.dirty_attrs.add((prim_path, attr_name))

    def clear_dirty(self) -> None:
        self.dirty_prims.clear()
        self.dirty_attrs.clear()

    def export_flattened(self, out_path: str) -> str:
        if not self.stage:
            raise RuntimeError("No composed stage")
        return write_flattened(self.stage, out_path)

    def export_referenced(self, out_path: str) -> str:
        if not self.root_layer:
            raise RuntimeError("No compose root layer")
        return write_referenced(
            self.root_layer,
            out_path,
            edits_layer=self.edits_layer or self.edit_target_layer,
        )

    def export_payload(self, layer_paths: Sequence[str], out_path: str) -> str:
        return write_payload(
            list(layer_paths),
            out_path,
            edits_layer=self.edits_layer or self.edit_target_layer,
        )

    def export_layered(
        self,
        layer_entries: Sequence[tuple[str, str]],
        out_path: str,
        *,
        pack_assets: bool = True,
    ) -> str:
        """Write main USDA with relative sublayers; optionally pack copies."""
        return write_layered(
            list(layer_entries),
            out_path,
            pack_assets=pack_assets,
            edits_layer=self.edits_layer or self.edit_target_layer,
        )

