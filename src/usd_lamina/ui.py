"""usd-lamina — Qt UI (StageExplorer and panels).

Core: :mod:`usd_lamina.core`
Launch: ``python -m usd_lamina``
"""

from __future__ import annotations

import math
import os
import sys
import traceback
from typing import Any

# Import pxr BEFORE Qt — on Windows, loading Qt first often breaks OpenUSD DLLs.
from pxr import Gf, Sdf, Usd, UsdGeom

try:
    from pxr import UsdShade
except ImportError:
    UsdShade = None

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
    try:
        from shiboken6 import isValid as _qt_is_valid  # type: ignore
    except ImportError:  # pragma: no cover
        _qt_is_valid = None  # type: ignore
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
        try:
            from shiboken2 import isValid as _qt_is_valid  # type: ignore
        except ImportError:
            _qt_is_valid = None
    except ImportError as _qt_exc:
        raise ImportError(
            "PySide6 is required (Python 3.11+ / Qt 6).\n"
            "  python -m pip install PySide6\n"
            "PySide2 is still accepted on Python 3.10."
        ) from _qt_exc

def _alias_qt_enums() -> None:
    """Expose Qt5-style ``Qt.UserRole`` names on strict Qt6 bindings."""
    mapping = {
        "UserRole": "ItemDataRole",
        "DisplayRole": "ItemDataRole",
        "ForegroundRole": "ItemDataRole",
        "LeftButton": "MouseButton",
        "RightButton": "MouseButton",
        "MiddleButton": "MouseButton",
        "ControlModifier": "KeyboardModifier",
        "NoBrush": "BrushStyle",
        "NoPen": "PenStyle",
        "KeepAspectRatio": "AspectRatioMode",
        "SmoothTransformation": "TransformationMode",
        "AlignLeft": "AlignmentFlag",
        "AlignCenter": "AlignmentFlag",
        "AlignVCenter": "AlignmentFlag",
        "AlignHCenter": "AlignmentFlag",
        "AlignTop": "AlignmentFlag",
        "CustomContextMenu": "ContextMenuPolicy",
        "TextSelectableByMouse": "TextInteractionFlag",
        "ClosedHandCursor": "CursorShape",
        "WaitCursor": "CursorShape",
        "Horizontal": "Orientation",
        "Vertical": "Orientation",
        "ToolButtonTextOnly": "ToolButtonStyle",
        "ToolButtonTextBesideIcon": "ToolButtonStyle",
        "RightArrow": "ArrowType",
        "DownArrow": "ArrowType",
        "RichText": "TextFormat",
        "MatchExactly": "MatchFlag",
        "MatchRecursive": "MatchFlag",
        "ItemIsSelectable": "ItemFlag",
        "ItemIsEnabled": "ItemFlag",
        "ItemIsEditable": "ItemFlag",
        "WA_TransparentForMouseEvents": "WidgetAttribute",
        "LeftDockWidgetArea": "DockWidgetArea",
        "RightDockWidgetArea": "DockWidgetArea",
        "AscendingOrder": "SortOrder",
    }
    for name, group in mapping.items():
        if getattr(QtCore.Qt, name, None) is not None:
            continue
        nested = getattr(QtCore.Qt, group, None)
        if nested is None:
            continue
        val = getattr(nested, name, None)
        if val is None:
            continue
        try:
            setattr(QtCore.Qt, name, val)
        except (TypeError, AttributeError):
            pass


_alias_qt_enums()

from usd_lamina import core as _core
from usd_lamina import theme as themes
from usd_lamina.usdlib.layers import export_layer, extract_sublayer_stack_paths
from usd_lamina.usdlib.prims import _classify_map_prim
from usd_lamina.usdlib.view import (
    _MAP_VIEWS,
    _nice_scale_step,
    _project_world_to_map,
    _stage_up_axis,
)

# Core helpers / constants (Qt-free)
NUM_LAYERS = _core.NUM_LAYERS
MAX_LOAD_FILES = _core.MAX_LOAD_FILES
COMPOSE_MODES = _core.COMPOSE_MODES
_clean_path = _core._clean_path
_open_layer = _core._open_layer
_file_format = _core._file_format
_basename = _core._basename
_as_plain = _core._as_plain
_version_from_path = _core._version_from_path
_opinion_from_path = _core._opinion_from_path
_layer_metadata = _core._layer_metadata
_prim_metadata = _core._prim_metadata
_meta_tooltip = _core._meta_tooltip
_resolve_layer_path = _core._resolve_layer_path
_file_exists = _core._file_exists
_load_type_label = _core._load_type_label
slot_strength_label = _core.slot_strength_label
_iter_all_prim_children = _core._iter_all_prim_children
prim_type_label = _core.prim_type_label
prim_visibility_label = _core.prim_visibility_label
prim_query_time = _core.prim_query_time
collect_prim_rows = _core.collect_prim_rows
attr_value_for_dialog = _core.attr_value_for_dialog
parse_attr_input = _core.parse_attr_input
attr_connection_paths = _core.attr_connection_paths
find_prims_bound_to_material = _core.find_prims_bound_to_material
LayerViewSession = _core.LayerViewSession


def _qt_signal(*args):
    return QtCore.Signal(*args)


def _qt_menu_exec(menu: QtWidgets.QMenu, global_pos: QtCore.QPoint):
    """Prefer Qt6 ``exec``; fall back to deprecated ``exec_``."""
    fn = getattr(menu, "exec", None) or getattr(menu, "exec_", None)
    return fn(global_pos)


def _qt_item_alive(item: QtWidgets.QTreeWidgetItem | None) -> bool:
    if item is None:
        return False
    if _qt_is_valid is None:
        return True
    try:
        return bool(_qt_is_valid(item))
    except Exception:  # noqa: BLE001
        return False


# =============================================================================
# USD Composition browser
# =============================================================================

_COLUMNS = (
    "Name",
    "Arc",
    "LoadType",
    "Prim / Target",
    "Format",
    "Version",
    "Opinion",
    "Department",
    "Tags",
    "File",
)

# Column indices
_COL_NAME = 0
_COL_LOAD = 2
_COL_PRIM = 3
_COL_FILE = 9

# Per-item payload for copy / tooling (stored on column 0)
_ROLE_NAME = QtCore.Qt.UserRole + 1
_ROLE_PRIM = QtCore.Qt.UserRole + 2
_ROLE_FILE = QtCore.Qt.UserRole + 3
_ROLE_LAYER = QtCore.Qt.UserRole + 4

_ZOOM_MIN = 7.0
_ZOOM_MAX = 28.0
_ZOOM_STEP = 1.0

_ARC_LABELS = {
    "root": "Root Layer",
    "sublayer": "Sublayer",
    "reference": "Reference",
    "payload": "Payload",
    "inherit": "Inherit",
    "specialize": "Specialize",
    "variant": "Variant",
    "prim": "Prim",
    "layerStack": "Layer Stack",
    "composition": "Composition",
    "stage": "Stage",
}

# Filename / path tokens that usually mean an opinion layer.




# _v019, _v19, /v019/, .v019. — common publish naming



























def _add_row(
    parent,
    name,
    arc,
    file_path="",
    prim_target="",
    tooltip="",
    version="",
    opinion="",
    department="",
    tags="",
    layer=None,
    prim=None,
    load_type=None,
    usd_arc=None,
):
    # Enrich from USD-native metadata when a layer/prim is available.
    meta = {"version": "", "opinion": "", "department": "", "tags": ""}
    if layer is not None:
        meta = _layer_metadata(layer)
    elif file_path:
        # Path-only heuristics when layer could not be opened
        meta = {
            "version": _version_from_path(file_path),
            "opinion": _opinion_from_path(file_path),
            "department": "",
            "tags": "",
            "creator": "",
            "doc": "",
            "comment": "",
            "raw": {},
        }
    if prim is not None:
        pmeta = _prim_metadata(prim)
        meta["version"] = meta["version"] or pmeta["version"]
        meta["opinion"] = meta["opinion"] or pmeta["opinion"]
        meta["department"] = meta["department"] or pmeta["department"]
        meta["tags"] = meta["tags"] or pmeta["tags"]
        if pmeta.get("raw"):
            meta["raw"] = pmeta["raw"]

    version = version or meta.get("version") or "-"
    opinion = opinion or meta.get("opinion") or "-"
    department = department or meta.get("department") or "-"
    tags = tags or meta.get("tags") or "-"

    fmt = _file_format(file_path) if file_path else "-"
    display_file = file_path or "-"
    display_prim = prim_target or "-"
    display_load = _load_type_label(arc, load_type=load_type, arc=usd_arc)
    item = QtWidgets.QTreeWidgetItem(
        [
            name,
            _ARC_LABELS.get(arc, arc),
            display_load,
            display_prim,
            fmt,
            version,
            opinion,
            department,
            tags,
            display_file,
        ]
    )
    tip = tooltip or _meta_tooltip(meta, file_path) or name
    if display_load != "-":
        tip = (tip + "\nLoadType: " + display_load) if tip else ("LoadType: " + display_load)
    for col in range(len(_COLUMNS)):
        item.setToolTip(col, tip)
    item.setData(0, _ROLE_NAME, name or "")
    item.setData(0, _ROLE_PRIM, prim_target if prim_target and prim_target != "-" else "")
    item.setData(0, _ROLE_FILE, file_path or "")
    layer_id = ""
    if layer is not None:
        try:
            layer_id = layer.identifier or ""
        except Exception:
            layer_id = ""
    if not layer_id and file_path:
        layer_id = file_path
    item.setData(0, _ROLE_LAYER, layer_id)
    if file_path and not _file_exists(file_path):
        item.setForeground(_COL_FILE, QtGui.QColor("#e74c3c"))
    if isinstance(parent, QtWidgets.QTreeWidget):
        parent.addTopLevelItem(item)
    else:
        parent.addChild(item)
    return item


def _walk_sublayers(parent_item, layer, visited):
    identifier = layer.identifier
    if identifier in visited:
        _add_row(
            parent_item,
            _basename(identifier),
            "sublayer",
            identifier,
            tooltip="(cycle) " + identifier,
            layer=layer,
            load_type="sublayer",
        )
        return
    visited = set(visited)
    visited.add(identifier)

    for i, rel in enumerate(layer.subLayerPaths):
        abs_path = _resolve_layer_path(layer, rel)
        nested = _open_layer(abs_path)
        child = _add_row(
            parent_item,
            _basename(abs_path) or ("sublayer[%d]" % i),
            "sublayer",
            abs_path,
            prim_target="subLayer[%d]" % i,
            tooltip="%s\n-> %s" % (rel, abs_path),
            layer=nested,
            load_type="sublayer",
        )
        if nested is not None:
            _walk_sublayers(child, nested, visited)
        else:
            _add_row(
                child,
                "(unreadable)",
                "sublayer",
                abs_path,
                version=_version_from_path(abs_path),
                opinion=_opinion_from_path(abs_path),
                load_type="sublayer",
            )


def _arc_type_name(arc):
    try:
        t = arc.GetArcType()
        name = str(t).split(".")[-1].lower()
        mapping = {
            "reference": "reference",
            "payload": "payload",
            "inherit": "inherit",
            "specialize": "specialize",
            "variant": "variant",
            "root": "root",
        }
        return mapping.get(name, name)
    except Exception:
        return "arc"


def _arc_target_layer_path(arc):
    try:
        node = arc.GetTargetNode()
        if node and node.layerStack and node.layerStack.identifier:
            root = node.layerStack.identifier.rootLayer
            if root is not None:
                return root.identifier
    except Exception:
        pass
    try:
        intro = arc.GetIntroducingLayer()
        if intro is not None:
            return intro.identifier
    except Exception:
        pass
    return ""


def _arc_prim_path(arc):
    parts = []
    try:
        target = arc.GetTargetNode()
        if target is not None:
            parts.append(str(target.path))
    except Exception:
        pass
    try:
        intro_path = arc.GetIntroducingPrimPath()
        if intro_path:
            parts.append("via " + str(intro_path))
    except Exception:
        pass
    return " | ".join(parts) if parts else ""


def _variant_label(arc):
    try:
        node = arc.GetTargetNode()
        sel = getattr(node, "GetVariantSelection", None)
        if callable(sel):
            pairs = sel()
            if pairs:
                return " ".join("%s=%s" % (k, v) for k, v in dict(pairs).items())
    except Exception:
        pass
    try:
        intro = arc.GetIntroducingLayer()
        path = arc.GetIntroducingPrimPath()
        if intro is not None and path:
            prim_spec = intro.GetPrimAtPath(path)
            if prim_spec is not None and prim_spec.variantSelections:
                return " ".join(
                    "%s=%s" % (k, v)
                    for k, v in prim_spec.variantSelections.items()
                )
    except Exception:
        pass
    return ""


def _walk_prim_arcs(parent_item, prim, layer_visited, prim_visited):
    path_str = str(prim.GetPath())
    if path_str in prim_visited:
        return
    prim_visited.add(path_str)

    try:
        query = Usd.PrimCompositionQuery(prim)
        arcs = list(query.GetCompositionArcs())
    except Exception:
        arcs = []

    meaningful = []
    for arc in arcs:
        kind = _arc_type_name(arc)
        if kind == "root":
            continue
        meaningful.append((kind, arc))

    if not meaningful and not prim.GetChildren():
        return

    prim_item = _add_row(
        parent_item,
        prim.GetName() or path_str,
        "prim",
        prim_target=path_str,
        tooltip=path_str,
        prim=prim,
        load_type="",
    )

    for kind, arc in meaningful:
        file_path = _arc_target_layer_path(arc)
        target = _arc_prim_path(arc)
        if kind == "variant":
            vlabel = _variant_label(arc)
            if vlabel:
                target = (vlabel + " | " + target).strip(" |")

        layer = _open_layer(file_path) if file_path else None
        arc_item = _add_row(
            prim_item,
            _basename(file_path) if file_path else kind,
            kind,
            file_path,
            prim_target=target or path_str,
            tooltip=file_path or path_str,
            layer=layer,
            load_type=kind,
            usd_arc=arc,
        )
        if layer is not None and file_path not in layer_visited:
            _walk_sublayers(arc_item, layer, layer_visited)

    for child in prim.GetChildren():
        _walk_prim_arcs(prim_item, child, layer_visited, prim_visited)


def _populate_tree(tree, stage, stage_path, edit_target_id=""):
    tree.clear()
    root_layer = stage.GetRootLayer()
    session_layer = stage.GetSessionLayer()

    if stage.HasDefaultPrim():
        default_prim = str(stage.GetDefaultPrim().GetPath())
    else:
        default_prim = "/"

    stage_item = _add_row(
        tree,
        _basename(stage_path),
        "stage",
        stage_path,
        prim_target=default_prim,
        layer=root_layer,
        load_type="stage",
    )

    # Session / edit stack (anonymous edits layer lives here in Stage Explorer)
    session_item = _add_row(
        stage_item, "Session / Edit Stack", "layerStack", load_type=""
    )
    sess_root = _add_row(
        session_item,
        _basename(session_layer.identifier),
        "root",
        session_layer.identifier,
        prim_target="/",
        layer=session_layer,
        load_type="root",
    )
    _walk_sublayers(sess_root, session_layer, set())

    stack_item = _add_row(
        stage_item, "Layer Stack", "layerStack", load_type=""
    )
    root_item = _add_row(
        stack_item,
        _basename(root_layer.identifier),
        "root",
        root_layer.identifier,
        prim_target="/",
        layer=root_layer,
        load_type="root",
    )
    _walk_sublayers(root_item, root_layer, set())

    # Resolved strength order (includes session + root sublayers)
    resolved = _add_row(
        stage_item, "Resolved Layer Stack", "layerStack", load_type=""
    )
    for i, layer in enumerate(stage.GetLayerStack()):
        _add_row(
            resolved,
            "[%d] %s" % (i, _basename(layer.identifier)),
            "sublayer" if i else "root",
            layer.identifier,
            prim_target="stack[%d]" % i,
            layer=layer,
            load_type="sublayer" if i else "root",
        )

    comp_item = _add_row(
        stage_item, "Prim Composition", "composition", load_type=""
    )
    pseudo = stage.GetPseudoRoot()
    layer_visited = set([root_layer.identifier, session_layer.identifier])
    prim_visited = set()
    for child in pseudo.GetChildren():
        _walk_prim_arcs(comp_item, child, layer_visited, prim_visited)

    if edit_target_id:
        _highlight_edit_target(tree, edit_target_id)

    tree.expandToDepth(2)


def _highlight_edit_target(tree, edit_target_id):
    """Bold + tint rows matching the current edit-target layer identifier."""
    brush = QtGui.QBrush(QtGui.QColor("#2d6a4f"))
    font_bold = QtGui.QFont()
    font_bold.setBold(True)

    def walk(item):
        lid = item.data(0, _ROLE_LAYER) or ""
        if lid and lid == edit_target_id:
            for col in range(tree.columnCount()):
                item.setForeground(col, brush)
                item.setFont(col, font_bold)
            tip = item.toolTip(0) or ""
            if "EDIT TARGET" not in tip:
                item.setToolTip(0, (tip + "\n[EDIT TARGET]").strip())
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))


def _clipboard_set(text):
    text = text or ""
    clipboard = QtWidgets.QApplication.clipboard()
    clipboard.setText(text)
    print("Copied to clipboard: %s" % text)


def _item_copy_values(item):
    """Return (name, prim_path, file_path) for a tree item."""
    if item is None:
        return "", "", ""
    name = item.data(0, _ROLE_NAME) or item.text(_COL_NAME) or ""
    prim = item.data(0, _ROLE_PRIM) or ""
    if not prim:
        cell = item.text(_COL_PRIM) or ""
        if cell and cell != "-":
            prim = cell
    file_path = item.data(0, _ROLE_FILE) or ""
    if not file_path:
        cell = item.text(_COL_FILE) or ""
        if cell and cell != "-":
            file_path = cell
    return name, prim, file_path


class _CompositionTree(QtWidgets.QTreeWidget):
    """Tree with Ctrl+wheel zoom; context menu owned by CompositionView."""

    def __init__(self, parent=None):
        QtWidgets.QTreeWidget.__init__(self, parent)
        self.setIndentation(max(1, self.indentation() // 2))
        base = self.font().pointSizeF()
        self._font_pt = base if base > 0 else 10.0

    def wheelEvent(self, event):
        if event.modifiers() & QtCore.Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta == 0:
                delta = event.pixelDelta().y()
            if delta != 0:
                step = _ZOOM_STEP if delta > 0 else -_ZOOM_STEP
                self._set_zoom(self._font_pt + step)
            event.accept()
            return
        QtWidgets.QTreeWidget.wheelEvent(self, event)

    def _set_zoom(self, point_size):
        self._font_pt = max(_ZOOM_MIN, min(_ZOOM_MAX, float(point_size)))
        font = QtGui.QFont(self.font())
        font.setPointSizeF(self._font_pt)
        self.setFont(font)
        header = self.header()
        hfont = QtGui.QFont(header.font())
        hfont.setPointSizeF(self._font_pt)
        header.setFont(hfont)


class CompositionView(QtWidgets.QWidget):
    """Full composition browser (layer stack, arcs, load types).

    Signals
    -------
    gotoPrimPath(str)
        Prim path to select in the scene hierarchy.
    setEditTarget(str)
        Layer identifier to use as stage edit target.
    assignToSlot(int, str)
        Put a file path into compose Layer N.
    useEditsLayer()
        Switch edit target back to the anonymous edits layer.
    """

    gotoPrimPath = _qt_signal(str)
    setEditTarget = _qt_signal(str)
    assignToSlot = _qt_signal(int, str)
    useEditsLayer = _qt_signal()

    def __init__(self, num_slots: int = 6, parent=None):
        super().__init__(parent)
        self._num_slots = num_slots
        self._stage = None
        self._stage_path = ""
        self._edit_target_id = ""

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QtWidgets.QHBoxLayout()
        self._title = QtWidgets.QLabel("<b>USD Composition</b>")
        self._title.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        header.addWidget(self._title, stretch=1)
        expand_btn = QtWidgets.QPushButton("Expand All")
        collapse_btn = QtWidgets.QPushButton("Collapse All")
        refresh_btn = QtWidgets.QPushButton("Refresh")
        header.addWidget(expand_btn)
        header.addWidget(collapse_btn)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        self.tree = _CompositionTree()
        self.tree.setColumnCount(len(_COLUMNS))
        self.tree.setHeaderLabels(list(_COLUMNS))
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setSortingEnabled(False)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionsMovable(True)
        for i in range(len(_COLUMNS)):
            hdr.setSectionResizeMode(i, QtWidgets.QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 110)
        self.tree.setColumnWidth(3, 200)
        self.tree.setColumnWidth(4, 55)
        self.tree.setColumnWidth(5, 60)
        self.tree.setColumnWidth(6, 80)
        self.tree.setColumnWidth(7, 90)
        self.tree.setColumnWidth(8, 120)
        self.tree.setColumnWidth(9, 360)
        layout.addWidget(self.tree, stretch=1)

        tip = QtWidgets.QLabel(
            "LoadType = sublayer / reference / payload / …  "
            "Green bold = current edit target.  "
            "Double-click prim → Scene tab.  "
            "Right-click: edit target, assign to Layer #, copy.  "
            "Ctrl+Wheel: zoom."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#aaa;")
        layout.addWidget(tip)

        expand_btn.clicked.connect(self.tree.expandAll)
        collapse_btn.clicked.connect(self.tree.collapseAll)
        refresh_btn.clicked.connect(self.refresh)

    def set_stage(self, stage, stage_path="", edit_target_id=""):
        self._stage = stage
        self._stage_path = stage_path or (
            stage.GetRootLayer().identifier if stage else ""
        )
        self._edit_target_id = edit_target_id or ""
        self.refresh()

    def set_edit_target_id(self, layer_id: str):
        self._edit_target_id = layer_id or ""
        self.refresh()

    def refresh(self):
        if self._stage is None:
            self.tree.clear()
            self._title.setText("<b>USD Composition</b>  —  no stage")
            return
        label = self._stage_path or "(composed stage)"
        self._title.setText("<b>USD Composition</b>  —  %s" % label)
        _populate_tree(
            self.tree,
            self._stage,
            self._stage_path or label,
            edit_target_id=self._edit_target_id,
        )

    def _on_double_click(self, item, column):
        _ = column
        _, prim, _ = _item_copy_values(item)
        if prim and prim.startswith("/"):
            # Strip "via …" / variant noise — take first path-like token
            token = prim.split("|")[0].strip().split()[0]
            if token.startswith("/"):
                self.gotoPrimPath.emit(token)

    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            item = self.tree.currentItem()
        name, prim, file_path = _item_copy_values(item)
        layer_id = (item.data(0, _ROLE_LAYER) if item else "") or ""

        menu = QtWidgets.QMenu(self)

        act_edit = menu.addAction("Set as Edit Target")
        act_edit.setEnabled(bool(layer_id))
        act_edit.triggered.connect(
            lambda _c=False, t=layer_id: self.setEditTarget.emit(t)
        )

        act_edits = menu.addAction("Use Anonymous Edits Layer")
        act_edits.triggered.connect(lambda _c=False: self.useEditsLayer.emit())

        if file_path and not str(file_path).startswith("anon:"):
            slot_menu = menu.addMenu("Assign file to slot…")
            for i in range(self._num_slots):
                act = slot_menu.addAction(slot_strength_label(i, self._num_slots))
                act.triggered.connect(
                    lambda _c=False, idx=i, p=file_path: self.assignToSlot.emit(idx, p)
                )

        menu.addSeparator()

        if prim and "/" in prim:
            token = prim.split("|")[0].strip().split()[0]
            act_goto = menu.addAction("Go to Prim in Scene")
            act_goto.setEnabled(token.startswith("/"))
            act_goto.triggered.connect(
                lambda _c=False, t=token: self.gotoPrimPath.emit(t)
            )
            menu.addSeparator()

        act_name = menu.addAction("Copy Primitive Name")
        act_name.setEnabled(bool(name))
        act_name.triggered.connect(lambda _c=False, t=name: _clipboard_set(t))

        act_prim = menu.addAction("Copy Primitive Path")
        act_prim.setEnabled(bool(prim))
        act_prim.triggered.connect(lambda _c=False, t=prim: _clipboard_set(t))

        act_file = menu.addAction("Copy Drive Path")
        act_file.setEnabled(bool(file_path))
        act_file.triggered.connect(lambda _c=False, t=file_path: _clipboard_set(t))

        menu.addSeparator()
        act_all = menu.addAction("Copy All (name | prim | file)")
        bundle = " | ".join(p for p in (name, prim, file_path) if p)
        act_all.setEnabled(bool(bundle))
        act_all.triggered.connect(lambda _c=False, t=bundle: _clipboard_set(t))

        _qt_menu_exec(menu, self.tree.viewport().mapToGlobal(pos))


# =============================================================================
# 2D top-view scene map (wireframe, no GL)
# =============================================================================

_MAP_COLOR_SET = QtGui.QColor("#4a9eff")  # blue
_MAP_COLOR_CHAR = QtGui.QColor("#fa8072")  # salmon
_MAP_COLOR_CAM = QtGui.QColor("#c084fc")  # purple
_MAP_COLOR_DEFAULT = QtGui.QColor("#8a8a8a")
_MAP_COLOR_GRID = QtGui.QColor("#3a3a3a")
_MAP_COLOR_SELECT = QtGui.QColor("#ffe566")
_MAP_COLOR_AXIS_X = QtGui.QColor("#e74c3c")  # red   → +X
_MAP_COLOR_AXIS_Y = QtGui.QColor("#2ecc71")  # green → +Y
_MAP_COLOR_AXIS_Z = QtGui.QColor("#3498db")  # blue  → +Z
_MAP_ROLE_PATH = QtCore.Qt.UserRole + 40


def _map_pen(color: QtGui.QColor, width: float = 1.2, selected: bool = False) -> QtGui.QPen:
    c = _MAP_COLOR_SELECT if selected else color
    pen = QtGui.QPen(c)
    pen.setWidthF(2.4 if selected else width)
    pen.setCosmetic(True)
    return pen


def _qt_event_pos(event) -> QtCore.QPoint:
    """Mouse position — Qt6 deprecates ``QMouseEvent.pos()``."""
    pos_fn = getattr(event, "position", None)
    if callable(pos_fn):
        try:
            return pos_fn().toPoint()
        except Exception:  # noqa: BLE001
            pass
    return event.pos()




class _MapGraphicsView(QtWidgets.QGraphicsView):
    """Top-view map: wheel zoom, middle-drag pan, click select."""

    primClicked = _qt_signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(
            QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing
        )
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor("#1e1e1e")))
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self._panning = False
        self._pan_last = QtCore.QPoint()

    def wheelEvent(self, event):  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        self.scale(factor, factor)
        parent = self.parent()
        if parent is not None and hasattr(parent, "_update_scale_bar"):
            parent._update_scale_bar()

    def mousePressEvent(self, event):  # noqa: N802
        pos = _qt_event_pos(event)
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = True
            self._pan_last = pos
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            item = self.itemAt(pos)
            path = ""
            while item is not None:
                path = item.data(_MAP_ROLE_PATH) or ""
                if path:
                    break
                item = item.parentItem()
            if path:
                self.primClicked.emit(str(path))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        pos = _qt_event_pos(event)
        if self._panning:
            delta = pos - self._pan_last
            self._pan_last = pos
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SceneMap2DView(QtWidgets.QWidget):
    """Wireframe orthographic map of the composed stage (bounds + camera frustums)."""

    gotoPrimPath = _qt_signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stage: Usd.Stage | None = None
        self._selected_path: str = ""
        self._items_by_path: dict[str, list] = {}
        self._map_view: str = "Top"
        self._display_mesh_edges: bool = True
        self._query_time: Usd.TimeCode = Usd.TimeCode.Default()

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(6)
        self.view_combo = QtWidgets.QComboBox()
        self.view_combo.addItems(list(_MAP_VIEWS))
        self.view_combo.setCurrentText("Top")
        self.view_combo.setToolTip("Orthographic view plane")
        self.view_combo.setMinimumWidth(90)
        self.view_combo.currentTextChanged.connect(self._on_map_view_changed)
        toolbar.addWidget(self.view_combo, 0, QtCore.Qt.AlignLeft)

        self._title = QtWidgets.QLabel("<b>2D Scene View</b> — wireframe")
        toolbar.addWidget(self._title, 1)

        opt_btn = QtWidgets.QToolButton()
        opt_btn.setText("Options")
        opt_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        opt_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        opt_menu = QtWidgets.QMenu(opt_btn)
        self.act_mesh_edges = opt_menu.addAction("Display Mesh Edges (instead of Bbox)")
        self.act_mesh_edges.setCheckable(True)
        self.act_mesh_edges.setChecked(True)
        self.act_mesh_edges.toggled.connect(self._on_mesh_edges_toggled)
        opt_btn.setMenu(opt_menu)
        toolbar.addWidget(opt_btn)

        self.btn_focus = QtWidgets.QPushButton("Focus")
        self.btn_focus.setToolTip(
            "Focus view (F) — selected prim if any, otherwise everything"
        )
        self.btn_focus.clicked.connect(self.focus_view)
        toolbar.addWidget(self.btn_focus)
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.rebuild)
        toolbar.addWidget(self.btn_refresh)
        root.addLayout(toolbar)

        legend = QtWidgets.QLabel(
            '<span style="color:#e74c3c"><b>X</b></span> '
            '<span style="color:#2ecc71"><b>Y</b></span> '
            '<span style="color:#3498db"><b>Z</b></span> '
            '<span style="color:#888">@ origin</span> &nbsp; '
            '<span style="color:#4a9eff">■ Set</span> &nbsp; '
            '<span style="color:#fa8072">■ Char</span> &nbsp; '
            '<span style="color:#c084fc">■ Camera + frustum</span> &nbsp; '
            '<span style="color:#8a8a8a">■ Other</span> &nbsp; '
            "<i>Wheel zoom · middle-drag pan · click select · F focus</i>"
        )
        legend.setTextFormat(QtCore.Qt.RichText)
        root.addWidget(legend)

        self._scene = QtWidgets.QGraphicsScene(self)
        self._view = _MapGraphicsView(self)
        self._view.setScene(self._scene)
        self._view.primClicked.connect(self._on_prim_clicked)
        root.addWidget(self._view, 1)

        self._scale_label = QtWidgets.QLabel("Scale: —")
        self._scale_label.setStyleSheet("color:#aaa; padding: 2px 4px;")
        root.addWidget(self._scale_label)

        self._hint = QtWidgets.QLabel("Compose a stage to build the map.")
        self._hint.setStyleSheet("color:#888;")
        root.addWidget(self._hint)

    def _on_map_view_changed(self, text: str) -> None:
        self._map_view = text or "Top"
        self.rebuild()

    def _on_mesh_edges_toggled(self, checked: bool) -> None:
        self._display_mesh_edges = bool(checked)
        self.rebuild()

    def set_stage(self, stage: Usd.Stage | None) -> None:
        self._stage = stage
        self.rebuild(fit=True)

    def set_query_time(self, time: Usd.TimeCode, *, fit: bool = False) -> None:
        """Evaluate bounds / cameras at ``time`` (timeline scrub / play)."""
        self._query_time = time if time is not None else Usd.TimeCode.Default()
        if self._stage is not None:
            self.rebuild(fit=fit)

    def set_selected_path(self, path: str) -> None:
        self._selected_path = path or ""
        self._restyle_selection()

    def _on_prim_clicked(self, path: str) -> None:
        self.set_selected_path(path)
        self.gotoPrimPath.emit(path)

    def focus_view(self, paths: list[str] | None = None) -> None:
        """Frame prims in ``paths``, or current selection, or the whole map.

        ``paths is None`` → use ``_selected_path`` when set, else everything.
        ``paths == []`` → always frame everything (ignore selection).
        """
        # fitInView on a 0×0 viewport corrupts the transform → blank/black view.
        vp = self._view.viewport()
        if vp is None or vp.width() < 16 or vp.height() < 16:
            QtCore.QTimer.singleShot(
                50, lambda p=paths: self.focus_view(p)
            )
            return

        if paths is None:
            focus_paths = [self._selected_path] if self._selected_path else []
        else:
            focus_paths = list(paths)

        rect = QtCore.QRectF()
        found = False
        if focus_paths:
            roots = list(focus_paths)
            for path, items in self._items_by_path.items():
                if not any(
                    path == root or path.startswith(root + "/") for root in roots
                ):
                    continue
                for it in items:
                    try:
                        br = it.sceneBoundingRect()
                    except Exception:  # noqa: BLE001
                        continue
                    if br.isNull() or not br.isValid():
                        continue
                    if not found:
                        rect = QtCore.QRectF(br)
                        found = True
                    else:
                        rect = rect.united(br)

        if not found:
            rect = self._scene.itemsBoundingRect()
            found = (
                not rect.isNull()
                and rect.isValid()
                and rect.width() > 0
                and rect.height() > 0
            )

        if not found:
            return
        self._view.resetTransform()
        pad = max(rect.width(), rect.height()) * 0.08 + 1.0
        self._view.fitInView(
            rect.adjusted(-pad, -pad, pad, pad), QtCore.Qt.KeepAspectRatio
        )
        self._update_scale_bar()

    # Back-compat alias used by compose / tab-show timers
    def fit_view(self) -> None:
        self.focus_view(paths=[])  # empty list → force whole scene (ignore selection)

    def _update_scale_bar(self) -> None:
        # World units per screen pixel (approximate from view transform).
        try:
            p0 = self._view.mapToScene(0, 0)
            p1 = self._view.mapToScene(100, 0)
            world_per_100px = abs(p1.x() - p0.x())
        except Exception:  # noqa: BLE001
            self._scale_label.setText("Scale: —")
            return
        if world_per_100px <= 1e-12:
            self._scale_label.setText("Scale: —")
            return
        step = _nice_scale_step(world_per_100px * 0.5)
        px = 100.0 * (step / world_per_100px)
        if step >= 1000:
            label = f"{step / 1000:g} km"
        elif step >= 1:
            label = f"{step:g} m"
        else:
            label = f"{step * 100:g} cm"
        self._scale_label.setText(
            f"Scale: ▬ {label}  (~{px:.0f}px)   ·   grid step auto"
        )

    def _restyle_selection(self) -> None:
        for path, items in self._items_by_path.items():
            selected = path == self._selected_path
            for it in items:
                color = it.data(QtCore.Qt.UserRole + 41)
                if color is None:
                    continue
                if isinstance(it, QtWidgets.QGraphicsPathItem):
                    it.setPen(_map_pen(color, 1.0 if not selected else 2.2, selected))
                elif isinstance(it, QtWidgets.QGraphicsRectItem):
                    it.setPen(_map_pen(color, 1.2, selected))
                elif isinstance(it, QtWidgets.QGraphicsPolygonItem):
                    it.setPen(_map_pen(color, 1.4, selected))
                elif isinstance(it, QtWidgets.QGraphicsEllipseItem):
                    it.setPen(_map_pen(color, 1.4, selected))

    def rebuild(self, fit: bool = True) -> None:
        self._scene.clear()
        self._items_by_path.clear()
        if not self._stage:
            self._hint.setText("Compose a stage to build the map.")
            self._title.setText("<b>2D Scene View</b> — no stage")
            self._update_scale_bar()
            return

        up = _stage_up_axis(self._stage)
        view = self._map_view or "Top"
        mode = "mesh edges" if self._display_mesh_edges else "bbox"
        time = self._query_time or Usd.TimeCode.Default()
        t_label = "default" if time.IsDefault() else f"t={time.GetValue():g}"
        self._title.setText(
            f"<b>2D Scene View</b> — {view} · {mode} · {t_label} · upAxis={up}"
        )
        try:
            bbox_cache = UsdGeom.BBoxCache(
                time, ["default", "render", "proxy"], useExtentsHint=True
            )
        except TypeError:
            bbox_cache = UsdGeom.BBoxCache(time, ["default", "render", "proxy"])

        # Scene bounds for grid
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        drawn = 0
        edge_count = 0

        def _expand(mx: float, my: float) -> None:
            nonlocal min_x, min_y, max_x, max_y
            min_x = min(min_x, mx)
            max_x = max(max_x, mx)
            min_y = min(min_y, my)
            max_y = max(max_y, my)

        def _add_item(path: str, item, color: QtGui.QColor) -> None:
            item.setData(_MAP_ROLE_PATH, path)
            item.setData(QtCore.Qt.UserRole + 41, color)
            item.setZValue(10)
            self._scene.addItem(item)
            self._items_by_path.setdefault(path, []).append(item)

        def _proj(wx: float, wy: float, wz: float) -> tuple[float, float]:
            return _project_world_to_map(wx, wy, wz, up, view)

        try:
            prims = list(self._stage.Traverse(Usd.PrimAllPrimsPredicate))
        except Exception:  # noqa: BLE001
            prims = list(self._stage.Traverse())

        # --- Cameras (always) ---
        for prim in prims:
            if not prim or not prim.IsValid() or not prim.IsActive():
                continue
            if prim.IsA(UsdGeom.Camera):
                self._draw_camera(
                    prim, time, up, view, _MAP_COLOR_CAM, _add_item, _expand
                )
                drawn += 1

        if self._display_mesh_edges:
            drawn, edge_count = self._draw_mesh_edges_pass(
                prims, time, up, view, _proj, _add_item, _expand, drawn
            )
        else:
            drawn = self._draw_bbox_pass(
                prims, bbox_cache, up, view, _proj, _add_item, _expand, drawn
            )

        extent = 1.0
        if min_x < max_x and min_y < max_y:
            extent = max(max_x - min_x, max_y - min_y, 1.0)
            # Include world origin in the grid so axes stay readable in context.
            self._draw_grid(
                min(min_x, 0.0),
                min(min_y, 0.0),
                max(max_x, 0.0),
                max(max_y, 0.0),
            )
        self._draw_world_axes(up, view, extent)

        if drawn:
            if self._display_mesh_edges:
                self._hint.setText(
                    f"Mesh edges: {drawn} prim(s), ~{edge_count} edges  ·  "
                    f"{view}  ·  click to select"
                )
            else:
                self._hint.setText(
                    f"Wireframe bounds: {drawn}  ·  {view}  ·  click a box to select"
                )
        else:
            self._hint.setText(
                "No drawable bounds / meshes / cameras found on this stage."
            )
        self._restyle_selection()
        if fit:
            self.fit_view()
        else:
            self._update_scale_bar()

    def _draw_bbox_pass(
        self,
        prims,
        bbox_cache,
        up: str,
        view: str,
        proj,
        add_item,
        expand,
        drawn: int,
    ) -> int:
        """Coarse AABB rectangles; skip descendants once a parent is drawn."""
        _ = up
        drawn_roots: list[str] = []
        max_boxes = 800

        def _under_drawn_root(path: str) -> bool:
            for root in drawn_roots:
                if path == root or path.startswith(root + "/"):
                    return True
            return False

        def _world_aabb(prim: Usd.Prim):
            try:
                world = bbox_cache.ComputeWorldBound(prim)
                box = world.ComputeAlignedBox()
                if box.IsEmpty():
                    return None
                mn = box.GetMin()
                mx = box.GetMax()
                for v in (mn[0], mn[1], mn[2], mx[0], mx[1], mx[2]):
                    if v != v or abs(v) > 1e12:
                        return None
                return mn, mx
            except Exception:  # noqa: BLE001
                return None

        def _should_draw_boundable(prim: Usd.Prim, category: str) -> bool:
            try:
                model = Usd.ModelAPI(prim)
                if model.IsModel() or model.IsGroup():
                    return True
            except Exception:  # noqa: BLE001
                pass
            if category in ("char", "set"):
                return True
            depth = str(prim.GetPath()).count("/")
            if depth <= 4:
                return True
            tname = str(prim.GetTypeName() or "")
            if tname in (
                "Mesh",
                "Cube",
                "Sphere",
                "Cylinder",
                "Cone",
                "Capsule",
                "Points",
                "BasisCurves",
                "NurbsCurves",
            ):
                return True
            return False

        for prim in prims:
            if not prim or not prim.IsValid() or not prim.IsActive():
                continue
            if prim.IsA(UsdGeom.Camera):
                continue
            path = str(prim.GetPath())
            category = _classify_map_prim(prim)
            if drawn >= max_boxes:
                break
            if not prim.IsA(UsdGeom.Boundable):
                continue
            if _under_drawn_root(path):
                continue
            if not _should_draw_boundable(prim, category):
                continue

            aabb = _world_aabb(prim)
            if aabb is None:
                continue
            mn, mx = aabb
            corners3 = [
                (mn[0], mn[1], mn[2]),
                (mx[0], mn[1], mn[2]),
                (mx[0], mx[1], mx[2]),
                (mn[0], mx[1], mx[2]),
                (mn[0], mn[1], mx[2]),
                (mx[0], mn[1], mx[2]),
                (mx[0], mx[1], mn[2]),
                (mn[0], mx[1], mn[2]),
            ]
            pts2 = [proj(c[0], c[1], c[2]) for c in corners3]
            xs = [p[0] for p in pts2]
            ys = [p[1] for p in pts2]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            if abs(x1 - x0) < 1e-9 and abs(y1 - y0) < 1e-9:
                continue
            if abs(x1 - x0) < 1e-4:
                x0 -= 0.05
                x1 += 0.05
            if abs(y1 - y0) < 1e-4:
                y0 -= 0.05
                y1 += 0.05

            color = {
                "set": _MAP_COLOR_SET,
                "char": _MAP_COLOR_CHAR,
                "camera": _MAP_COLOR_CAM,
            }.get(category, _MAP_COLOR_DEFAULT)

            rect = QtWidgets.QGraphicsRectItem(x0, y0, x1 - x0, y1 - y0)
            rect.setBrush(QtCore.Qt.NoBrush)
            rect.setPen(_map_pen(color, 1.2))
            rect.setToolTip(f"{path}\n[{category}] · {view}")
            add_item(path, rect, color)
            label = QtWidgets.QGraphicsSimpleTextItem(prim.GetName() or path)
            label.setBrush(QtGui.QBrush(color))
            label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            label.setPos(x0, y0)
            label.setData(_MAP_ROLE_PATH, path)
            label.setData(QtCore.Qt.UserRole + 41, color)
            self._scene.addItem(label)
            self._items_by_path.setdefault(path, []).append(label)
            expand(x0, y0)
            expand(x1, y1)
            drawn_roots.append(path)
            drawn += 1
        return drawn

    def _draw_mesh_edges_pass(
        self,
        prims,
        time,
        up: str,
        view: str,
        proj,
        add_item,
        expand,
        drawn: int,
    ) -> tuple[int, int]:
        """Project mesh wire edges instead of AABB boxes."""
        _ = up
        max_meshes = 250
        max_edges_total = 60000
        max_edges_per_mesh = 8000
        meshes_drawn = 0
        edges_total = 0

        for prim in prims:
            if meshes_drawn >= max_meshes or edges_total >= max_edges_total:
                break
            if not prim or not prim.IsValid() or not prim.IsActive():
                continue
            if prim.IsA(UsdGeom.Camera):
                continue
            if not prim.IsA(UsdGeom.Mesh):
                continue

            path = str(prim.GetPath())
            category = _classify_map_prim(prim)
            color = {
                "set": _MAP_COLOR_SET,
                "char": _MAP_COLOR_CHAR,
            }.get(category, _MAP_COLOR_DEFAULT)

            n_edges = self._draw_mesh_edges(
                prim,
                time,
                path,
                color,
                view,
                proj,
                add_item,
                expand,
                max_edges=min(max_edges_per_mesh, max_edges_total - edges_total),
            )
            if n_edges <= 0:
                continue
            edges_total += n_edges
            meshes_drawn += 1
            drawn += 1

        return drawn, edges_total

    def _draw_mesh_edges(
        self,
        prim: Usd.Prim,
        time,
        path: str,
        color: QtGui.QColor,
        view: str,
        proj,
        add_item,
        expand,
        *,
        max_edges: int,
    ) -> int:
        """Build unique face edges, project to map, draw as one path item."""
        try:
            mesh = UsdGeom.Mesh(prim)
            points = mesh.GetPointsAttr().Get(time)
            counts = mesh.GetFaceVertexCountsAttr().Get(time)
            indices = mesh.GetFaceVertexIndicesAttr().Get(time)
            if not points or not counts or not indices:
                return 0
            xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time)
        except Exception:  # noqa: BLE001
            return 0

        # Unique undirected edges from face loops
        edge_set: set[tuple[int, int]] = set()
        cursor = 0
        try:
            for count in counts:
                n = int(count)
                if n < 2:
                    cursor += max(n, 0)
                    continue
                face = [int(indices[cursor + k]) for k in range(n)]
                cursor += n
                for i in range(n):
                    a, b = face[i], face[(i + 1) % n]
                    if a == b:
                        continue
                    edge_set.add((a, b) if a < b else (b, a))
                    if len(edge_set) >= max_edges:
                        break
                if len(edge_set) >= max_edges:
                    break
        except Exception:  # noqa: BLE001
            return 0

        if not edge_set:
            return 0

        # World-project points on demand
        pts2: dict[int, tuple[float, float]] = {}
        fpath = QtGui.QPainterPath()
        first_pt = None
        n_drawn = 0
        for a, b in edge_set:
            for idx in (a, b):
                if idx in pts2:
                    continue
                try:
                    p = points[idx]
                    wp = xf.Transform(Gf.Vec3d(float(p[0]), float(p[1]), float(p[2])))
                    pts2[idx] = proj(float(wp[0]), float(wp[1]), float(wp[2]))
                except Exception:  # noqa: BLE001
                    pts2[idx] = (0.0, 0.0)
            pa, pb = pts2[a], pts2[b]
            fpath.moveTo(pa[0], pa[1])
            fpath.lineTo(pb[0], pb[1])
            expand(pa[0], pa[1])
            expand(pb[0], pb[1])
            if first_pt is None:
                first_pt = pa
            n_drawn += 1

        if n_drawn == 0:
            return 0

        wire = QtWidgets.QGraphicsPathItem(fpath)
        wire.setPen(_map_pen(color, 0.9))
        wire.setBrush(QtCore.Qt.NoBrush)
        wire.setToolTip(f"{path}\n[mesh edges] · {view}")
        add_item(path, wire, color)

        if first_pt is not None:
            label = QtWidgets.QGraphicsSimpleTextItem(prim.GetName() or path)
            label.setBrush(QtGui.QBrush(color))
            label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            label.setPos(first_pt[0], first_pt[1])
            label.setData(_MAP_ROLE_PATH, path)
            label.setData(QtCore.Qt.UserRole + 41, color)
            self._scene.addItem(label)
            self._items_by_path.setdefault(path, []).append(label)
        return n_drawn

    def _draw_world_axes(self, up: str, view: str, extent: float) -> None:
        """RGB XYZ gizmo at world origin (0,0,0) for the current ortho plane.

        X=red, Y=green, Z=blue. Axes pointing into/out of the view (near-zero
        projected length) are skipped so only the two in-plane axes show.
        """
        arm = max(float(extent) * 0.12, 1.0)
        ox, oy = _project_world_to_map(0.0, 0.0, 0.0, up, view)
        axes = (
            ("X", (arm, 0.0, 0.0), _MAP_COLOR_AXIS_X),
            ("Y", (0.0, arm, 0.0), _MAP_COLOR_AXIS_Y),
            ("Z", (0.0, 0.0, arm), _MAP_COLOR_AXIS_Z),
        )
        for label, (wx, wy, wz), color in axes:
            sx, sy = _project_world_to_map(wx, wy, wz, up, view)
            dx, dy = sx - ox, sy - oy
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                continue  # axis ≈ view direction — not visible in this plane
            line = QtWidgets.QGraphicsLineItem(ox, oy, sx, sy)
            line.setPen(_map_pen(color, 2.2))
            line.setZValue(40)
            self._scene.addItem(line)

            # Arrow head (screen-space sized via ItemIgnoresTransformations)
            length = (dx * dx + dy * dy) ** 0.5
            ux, uy = dx / length, dy / length
            # Place a small filled triangle tip in scene units
            tip = 0.18 * arm
            px, py = -uy, ux  # perpendicular
            poly = QtGui.QPolygonF(
                [
                    QtCore.QPointF(sx, sy),
                    QtCore.QPointF(sx - ux * tip + px * tip * 0.35, sy - uy * tip + py * tip * 0.35),
                    QtCore.QPointF(sx - ux * tip - px * tip * 0.35, sy - uy * tip - py * tip * 0.35),
                ]
            )
            arrow = QtWidgets.QGraphicsPolygonItem(poly)
            arrow.setBrush(QtGui.QBrush(color))
            arrow.setPen(QtGui.QPen(color))
            arrow.setZValue(41)
            self._scene.addItem(arrow)

            text = QtWidgets.QGraphicsSimpleTextItem(label)
            font = text.font()
            font.setBold(True)
            font.setPointSize(max(font.pointSize(), 10))
            text.setFont(font)
            text.setBrush(QtGui.QBrush(color))
            text.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            text.setPos(sx, sy)
            text.setZValue(42)
            self._scene.addItem(text)

    def _draw_grid(
        self, min_x: float, min_y: float, max_x: float, max_y: float
    ) -> None:
        span = max(max_x - min_x, max_y - min_y, 1.0)
        step = _nice_scale_step(span / 8.0)
        pad = step * 2
        x0 = (min_x - pad) // step * step
        y0 = (min_y - pad) // step * step
        x1 = max_x + pad
        y1 = max_y + pad
        path = QtGui.QPainterPath()
        x = x0
        while x <= x1:
            path.moveTo(x, y0)
            path.lineTo(x, y1)
            x += step
        y = y0
        while y <= y1:
            path.moveTo(x0, y)
            path.lineTo(x1, y)
            y += step
        grid = QtWidgets.QGraphicsPathItem(path)
        grid.setPen(_map_pen(_MAP_COLOR_GRID, 0.8))
        grid.setZValue(0)
        self._scene.addItem(grid)
        tick = QtWidgets.QGraphicsSimpleTextItem(f"grid {step:g}")
        tick.setBrush(QtGui.QBrush(_MAP_COLOR_GRID.lighter(140)))
        tick.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        tick.setPos(x0, y0)
        tick.setZValue(0)
        self._scene.addItem(tick)

    def _draw_camera(self, prim, time, up, view, color, add_item, expand) -> None:
        path = str(prim.GetPath())
        # Gaffer often authors xformOp:transform as time samples only.
        time = prim_query_time(prim, time)
        cam = UsdGeom.Camera(prim)
        try:
            gf_cam = cam.GetCamera(time)
            frustum = gf_cam.frustum
            corners = list(frustum.ComputeCorners())
            eye = frustum.GetPosition()
        except Exception:  # noqa: BLE001
            try:
                xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time)
                eye = xf.ExtractTranslation()
                corners = []
            except Exception:  # noqa: BLE001
                return

        eye2 = _project_world_to_map(eye[0], eye[1], eye[2], up, view)
        expand(eye2[0], eye2[1])

        s = 0.35
        diamond = QtGui.QPolygonF(
            [
                QtCore.QPointF(eye2[0], eye2[1] - s),
                QtCore.QPointF(eye2[0] + s, eye2[1]),
                QtCore.QPointF(eye2[0], eye2[1] + s),
                QtCore.QPointF(eye2[0] - s, eye2[1]),
            ]
        )
        mark = QtWidgets.QGraphicsPolygonItem(diamond)
        mark.setBrush(QtCore.Qt.NoBrush)
        mark.setPen(_map_pen(color, 1.6))
        mark.setToolTip(f"{path}\n[camera] · {view}")
        add_item(path, mark, color)

        label = QtWidgets.QGraphicsSimpleTextItem(prim.GetName() or "Camera")
        label.setBrush(QtGui.QBrush(color))
        label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        label.setPos(eye2[0] + 0.4, eye2[1] + 0.4)
        label.setData(_MAP_ROLE_PATH, path)
        label.setData(QtCore.Qt.UserRole + 41, color)
        self._scene.addItem(label)
        self._items_by_path.setdefault(path, []).append(label)

        if len(corners) < 8:
            return

        pts = [
            _project_world_to_map(c[0], c[1], c[2], up, view) for c in corners
        ]
        for p in pts:
            expand(p[0], p[1])

        fpath = QtGui.QPainterPath()
        for i, j in ((0, 1), (1, 2), (2, 3), (3, 0)):
            fpath.moveTo(pts[i][0], pts[i][1])
            fpath.lineTo(pts[j][0], pts[j][1])
        for i, j in ((4, 5), (5, 6), (6, 7), (7, 4)):
            fpath.moveTo(pts[i][0], pts[i][1])
            fpath.lineTo(pts[j][0], pts[j][1])
        for i in range(4):
            fpath.moveTo(pts[i][0], pts[i][1])
            fpath.lineTo(pts[i + 4][0], pts[i + 4][1])
        for i in range(4):
            fpath.moveTo(eye2[0], eye2[1])
            fpath.lineTo(pts[i][0], pts[i][1])

        wire = QtWidgets.QGraphicsPathItem(fpath)
        wire.setPen(_map_pen(color, 1.0))
        wire.setBrush(QtCore.Qt.NoBrush)
        wire.setToolTip(f"{path}\nfrustum · {view}")
        add_item(path, wire, color)


# =============================================================================
# Stage explorer / layering UI
# =============================================================================

# File > Load USD File… may pick this many files in one dialog.


def _app_logo_pixmap(size: int = 128) -> QtGui.QPixmap:
    """Paint a small app mark."""
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pix)
    aa = getattr(QtGui.QPainter, "Antialiasing", None)
    if aa is None:
        aa = QtGui.QPainter.RenderHint.Antialiasing
    painter.setRenderHint(aa, True)
    painter.setBrush(QtGui.QColor("#3a6ea5"))
    painter.setPen(QtGui.QPen(QtGui.QColor("#3a6ea5"), 0))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, size * 0.18, size * 0.18)
    painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
    font = QtGui.QFont()
    font.setBold(True)
    font.setPixelSize(max(10, int(size * 0.34)))
    painter.setFont(font)
    align = getattr(QtCore.Qt, "AlignCenter", QtCore.Qt.AlignmentFlag.AlignCenter)
    painter.drawText(pix.rect(), align, "L")
    painter.end()
    return pix


def _usd_logo_icon() -> QtGui.QIcon:
    return QtGui.QIcon(_app_logo_pixmap(128))


def _usd_logo_pixmap(size: int = 128) -> QtGui.QPixmap:
    return _app_logo_pixmap(size)




# How each non-base file is wired into the compose root.

# Qt roles
ROLE_PRIM_PATH = QtCore.Qt.UserRole + 1

# Scene hierarchy columns
_COL_PRIM = 0
_COL_TYPE = 1
_COL_ACTIVE = 2
_COL_VIS = 3
_COL_PATH = 4

# Hierarchy greying: invisible = slight; inactive (cascades) = stronger
_TREE_COLOR_INVISIBLE = QtGui.QColor("#8a8a8a")
_TREE_COLOR_INACTIVE = QtGui.QColor("#555555")




def _apply_hierarchy_item_style(
    item: QtWidgets.QTreeWidgetItem,
    *,
    ancestor_inactive: bool = False,
    ancestor_invisible: bool = False,
) -> None:
    """Grey invisible slightly; inactive more, including all descendants."""
    inactive = ancestor_inactive or (item.text(_COL_ACTIVE) != "true")
    self_invisible = item.text(_COL_VIS) == "invisible"
    invisible = ancestor_invisible or self_invisible
    if inactive:
        brush: QtGui.QBrush | None = QtGui.QBrush(_TREE_COLOR_INACTIVE)
    elif invisible:
        brush = QtGui.QBrush(_TREE_COLOR_INVISIBLE)
    else:
        brush = None
    for col in range(5):
        if brush is None:
            item.setData(col, QtCore.Qt.ForegroundRole, None)
        else:
            item.setForeground(col, brush)
    for i in range(item.childCount()):
        _apply_hierarchy_item_style(
            item.child(i),
            ancestor_inactive=inactive,
            ancestor_invisible=invisible,
        )























# Always shown first in the attribute viewer (meta rows), in this order.

# Attr-tree roles
ROLE_ATTR_KIND = QtCore.Qt.UserRole + 10
ROLE_ATTR_NAME = QtCore.Qt.UserRole + 11
ROLE_ATTR_GOTO = QtCore.Qt.UserRole + 12
ROLE_ATTR_OWNER = QtCore.Qt.UserRole + 13  # prim path that owns the attribute
ROLE_ATTR_HAS_CONN = QtCore.Qt.UserRole + 14










# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

_USD_SUFFIXES = (".usda", ".usdc", ".usd")


def _usd_paths_from_mime(mime) -> list[str]:
    if not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        path = url.toLocalFile()
        if path and path.lower().endswith(_USD_SUFFIXES):
            out.append(path)
    return out


def _style_compact_header(header: QtWidgets.QHeaderView, *, height: int = 20) -> None:
    """Smaller / shorter column-name row for trees and tables."""
    if header is None:
        return
    font = header.font()
    font.setPointSize(max(8, font.pointSize() - 1))
    header.setFont(font)
    header.setMinimumHeight(height)
    header.setFixedHeight(height)
    header.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    header.setVisible(True)
    header.setHighlightSections(False)
    header.setStyleSheet(
        "QHeaderView::section {"
        f"  height: {height}px;"
        "  padding: 1px 4px;"
        "  font-size: 10px;"
        "}"
    )


class UsdDropOverlay(QtWidgets.QWidget):
    """Full-window dual drop zones shown while dragging USD files."""

    dropLayered = _qt_signal(object)  # list[str]
    dropAsLayer = _qt_signal(object)  # list[str]
    cancelled = _qt_signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        self.hide()
        self._hover: str | None = None  # "layered" | "layer" | None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        self.zone_as_layer = QtWidgets.QFrame()
        self.zone_as_layer.setObjectName("dropZoneAsLayer")
        self.zone_layered = QtWidgets.QFrame()
        self.zone_layered.setObjectName("dropZoneLayered")
        # Top = single layer; bottom = multi-layer (subLayers stack)
        for zone, title in (
            (self.zone_as_layer, "Drop as a USD Layer"),
            (self.zone_layered, "Drop as a Multi-Layer USD"),
        ):
            zone.setFrameShape(QtWidgets.QFrame.NoFrame)
            zl = QtWidgets.QVBoxLayout(zone)
            zl.setContentsMargins(24, 24, 24, 24)
            lab = QtWidgets.QLabel(title)
            lab.setAlignment(QtCore.Qt.AlignCenter)
            lab.setWordWrap(True)
            font = lab.font()
            font.setPointSize(max(16, font.pointSize() + 6))
            font.setBold(True)
            lab.setFont(font)
            lab.setStyleSheet("color: #e8e8e8; background: transparent; border: none;")
            zl.addStretch(1)
            zl.addWidget(lab)
            zl.addStretch(1)
            root.addWidget(zone, 1)

        self._apply_zone_styles(None)

    def _apply_zone_styles(self, hover: str | None) -> None:
        self._hover = hover
        # Soft fill only — highlight uses FileManagerDB purple (#AF8BC4).
        base = (
            "QFrame { border: none; border-radius: 10px; background: %s; }"
        )
        idle = "rgba(30,30,30,210)"
        purple = "rgba(175,139,196,120)"  # #AF8BC4
        self.zone_as_layer.setStyleSheet(
            base % (purple if hover == "layer" else idle)
        )
        self.zone_layered.setStyleSheet(
            base % (purple if hover == "layered" else idle)
        )

    def _zone_at(self, pos: QtCore.QPoint) -> str | None:
        if self.zone_layered.geometry().contains(pos):
            return "layered"
        if self.zone_as_layer.geometry().contains(pos):
            return "layer"
        return None

    def dragEnterEvent(self, event):  # noqa: N802
        if _usd_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            self._apply_zone_styles(self._zone_at(_qt_event_pos(event)))
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        if _usd_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            self._apply_zone_styles(self._zone_at(_qt_event_pos(event)))
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802
        # Do not hide here — moving between the two zones can fire leave.
        # The main window hides the overlay when the cursor exits the window.
        self._apply_zone_styles(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        paths = _usd_paths_from_mime(event.mimeData())
        zone = self._zone_at(_qt_event_pos(event))
        self._apply_zone_styles(None)
        if not paths or not zone:
            event.ignore()
            self.cancelled.emit()
            return
        event.acceptProposedAction()
        if zone == "layered":
            self.dropLayered.emit(paths)
        else:
            self.dropAsLayer.emit(paths)


class LayerSlot(QtWidgets.QWidget):
    """Compact file row; drag notifies the main window to show drop zones."""

    changed = _qt_signal()

    def __init__(self, index: int, strength_label: str, parent=None):
        super().__init__(parent)
        self.index = index
        self.setAcceptDrops(True)
        self.setMaximumHeight(26)
        self.setToolTip("Drop a .usda / .usdc / .usd file on the window")

        self.enabled = QtWidgets.QCheckBox()
        self.enabled.setChecked(True)
        self.enabled.setToolTip("Include this slot when composing")

        self.badge = QtWidgets.QLabel(strength_label)
        self.badge.setFixedWidth(78)
        self.badge.setAlignment(QtCore.Qt.AlignCenter)
        if strength_label == "BASE Layer":
            self.badge.setStyleSheet(
                "QLabel { background:#5a4030; color:#ffcc88; padding:1px 4px; "
                "border-radius:2px; font-size:10px; font-weight:bold; }"
            )
        elif strength_label == "TOP Layer":
            self.badge.setStyleSheet(
                "QLabel { background:#304a5a; color:#a8d4ff; padding:1px 4px; "
                "border-radius:2px; font-size:10px; font-weight:bold; }"
            )
        else:
            self.badge.setStyleSheet(
                "QLabel { background:#3a3a3a; color:#ddd; padding:1px 4px; "
                "border-radius:2px; font-size:10px; }"
            )

        self.export_name = QtWidgets.QLineEdit()
        self.export_name.setPlaceholderText("pack name")
        self.export_name.setToolTip(
            "Optional pack folder / layer name for Export Layered USDA.\n"
            "Empty = source file stem."
        )
        self.export_name.setMaximumWidth(110)
        self.export_name.setMaximumHeight(22)
        self.export_name.setAcceptDrops(False)

        self.path = QtWidgets.QLineEdit()
        self.path.setPlaceholderText("drop USD or browse…")
        self.path.setMinimumWidth(120)
        self.path.setMaximumHeight(22)
        self.path.setAcceptDrops(False)  # window overlay handles file drops

        browse = QtWidgets.QPushButton("…")
        browse.setFixedSize(22, 22)
        browse.clicked.connect(self._browse)

        clear = QtWidgets.QPushButton("×")
        clear.setFixedSize(22, 22)
        clear.clicked.connect(self._clear)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        row.addWidget(self.enabled)
        row.addWidget(self.badge)
        row.addWidget(self.export_name)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        row.addWidget(clear)

        # Ignore payload from stateChanged(int) / textChanged(str) — changed is argless.
        self.enabled.stateChanged.connect(lambda *_: self.changed.emit())
        self.path.textChanged.connect(self._on_path_changed)
        self.export_name.textChanged.connect(lambda *_: self.changed.emit())

    def dragEnterEvent(self, event):  # noqa: N802
        # Forward to main window dual-zone overlay (do not fill this slot alone).
        win = self.window()
        if _usd_paths_from_mime(event.mimeData()) and hasattr(win, "begin_usd_file_drag"):
            win.begin_usd_file_drag(event)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        if _usd_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):  # noqa: N802
        # Overlay should have taken the drop; ignore direct slot drops.
        event.ignore()

    def _browse(self):
        start = self.path.text().strip() or os.getcwd()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open USD layer",
            start,
            "USD (*.usda *.usdc *.usd);;All (*.*)",
        )
        if path:
            self.path.setText(path)

    def _clear(self) -> None:
        self.path.setText("")
        self.export_name.setText("")

    def _on_path_changed(self, *_args) -> None:
        # Prefill pack name from basename when empty (user can rename).
        if not self.export_name.text().strip():
            p = self.path.text().strip()
            stem = (
                os.path.splitext(os.path.basename(p))[0] if p else ""
            )
            self.export_name.blockSignals(True)
            if stem:
                self.export_name.setText(stem)
            self.export_name.setPlaceholderText(stem or "pack name")
            self.export_name.blockSignals(False)
        self.changed.emit()

    def value(self) -> str:
        if not self.enabled.isChecked():
            return ""
        return self.path.text().strip()

    def pack_name(self) -> str:
        """Folder / layer label for layered pack export."""
        explicit = self.export_name.text().strip()
        if explicit:
            return explicit
        p = self.path.text().strip()
        if p:
            return os.path.splitext(os.path.basename(p))[0]
        return ""


_ROLE_SHADE_KIND = QtCore.Qt.UserRole + 60  # "material" | "shader" | "group"
_ROLE_SHADE_PATH = QtCore.Qt.UserRole + 61
_ROLE_SHADE_MATERIAL = QtCore.Qt.UserRole + 62  # owning material path




class ShadersMaterialsView(QtWidgets.QWidget):
    """Tree of Materials / Shaders on the composed stage."""

    selectPrimPath = _qt_signal(str)
    selectBoundObjects = _qt_signal(str)  # material path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stage: Usd.Stage | None = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        filt_row = QtWidgets.QHBoxLayout()
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Search materials / shaders…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        filt_row.addWidget(self.filter_edit, 1)
        btn_refresh = QtWidgets.QToolButton()
        btn_refresh.setText("Refresh")
        btn_refresh.clicked.connect(self.rebuild)
        filt_row.addWidget(btn_refresh)
        root.addLayout(filt_row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type", "Path"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        _style_compact_header(self.tree.header())
        self.tree.header().setStretchLastSection(True)
        root.addWidget(self.tree, 1)

        self._hint = QtWidgets.QLabel("Compose a stage to list materials.")
        self._hint.setStyleSheet("color:#888;")
        root.addWidget(self._hint)

    def set_stage(self, stage: Usd.Stage | None) -> None:
        self._stage = stage
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        if not self._stage or UsdShade is None:
            self._hint.setText(
                "No stage."
                if not self._stage
                else "UsdShade not available in this USD build."
            )
            return

        mats_root = QtWidgets.QTreeWidgetItem(["Materials", "", ""])
        mats_root.setData(0, _ROLE_SHADE_KIND, "group")
        shaders_root = QtWidgets.QTreeWidgetItem(["Shaders", "", ""])
        shaders_root.setData(0, _ROLE_SHADE_KIND, "group")
        self.tree.addTopLevelItem(mats_root)
        self.tree.addTopLevelItem(shaders_root)

        materials: list[Usd.Prim] = []
        shaders: list[Usd.Prim] = []
        try:
            prims = self._stage.Traverse(Usd.PrimAllPrimsPredicate)
        except Exception:  # noqa: BLE001
            prims = self._stage.Traverse()
        for prim in prims:
            if not prim or not prim.IsValid():
                continue
            try:
                if prim.IsA(UsdShade.Material):
                    materials.append(prim)
                elif prim.IsA(UsdShade.Shader):
                    shaders.append(prim)
            except Exception:  # noqa: BLE001
                continue

        materials.sort(key=lambda p: str(p.GetPath()))
        shaders.sort(key=lambda p: str(p.GetPath()))

        # Map shader path → owning material path (nearest Material ancestor, else "")
        shader_owner: dict[str, str] = {}
        for sh in shaders:
            owner = ""
            parent = sh.GetParent()
            while parent and parent.IsValid():
                try:
                    if parent.IsA(UsdShade.Material):
                        owner = str(parent.GetPath())
                        break
                except Exception:  # noqa: BLE001
                    pass
                parent = parent.GetParent()
            shader_owner[str(sh.GetPath())] = owner

        for mat in materials:
            path = str(mat.GetPath())
            item = QtWidgets.QTreeWidgetItem(
                [mat.GetName() or path, "Material", path]
            )
            item.setData(0, _ROLE_SHADE_KIND, "material")
            item.setData(0, _ROLE_SHADE_PATH, path)
            item.setData(0, _ROLE_SHADE_MATERIAL, path)
            item.setToolTip(2, path)
            mats_root.addChild(item)
            # Nest shaders that live under this material
            for sh in shaders:
                sp = str(sh.GetPath())
                if shader_owner.get(sp) != path:
                    continue
                child = QtWidgets.QTreeWidgetItem(
                    [sh.GetName() or sp, "Shader", sp]
                )
                child.setData(0, _ROLE_SHADE_KIND, "shader")
                child.setData(0, _ROLE_SHADE_PATH, sp)
                child.setData(0, _ROLE_SHADE_MATERIAL, path)
                child.setToolTip(2, sp)
                item.addChild(child)

        for sh in shaders:
            sp = str(sh.GetPath())
            owner = shader_owner.get(sp, "")
            # Listed under Materials when nested; also list orphans + all under Shaders
            if owner:
                continue
            item = QtWidgets.QTreeWidgetItem(
                [sh.GetName() or sp, "Shader", sp]
            )
            item.setData(0, _ROLE_SHADE_KIND, "shader")
            item.setData(0, _ROLE_SHADE_PATH, sp)
            item.setData(0, _ROLE_SHADE_MATERIAL, "")
            item.setToolTip(2, sp)
            shaders_root.addChild(item)

        # Also list nested shaders under Shaders group for a flat browse
        for sh in shaders:
            sp = str(sh.GetPath())
            if not shader_owner.get(sp):
                continue
            item = QtWidgets.QTreeWidgetItem(
                [sh.GetName() or sp, "Shader", sp]
            )
            item.setData(0, _ROLE_SHADE_KIND, "shader")
            item.setData(0, _ROLE_SHADE_PATH, sp)
            item.setData(0, _ROLE_SHADE_MATERIAL, shader_owner.get(sp, ""))
            item.setToolTip(2, sp)
            shaders_root.addChild(item)

        mats_root.setExpanded(True)
        shaders_root.setExpanded(True)
        for col in (0, 1):
            self.tree.resizeColumnToContents(col)
        self._hint.setText(
            f"{len(materials)} material(s), {len(shaders)} shader(s)"
        )
        self._apply_filter(self.filter_edit.text())

    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip().lower()

        def walk(item: QtWidgets.QTreeWidgetItem) -> bool:
            kind = item.data(0, _ROLE_SHADE_KIND) or ""
            child_vis = False
            for i in range(item.childCount()):
                if walk(item.child(i)):
                    child_vis = True
            if kind == "group":
                item.setHidden(False)
                return True
            hay = " ".join(
                [
                    item.text(0).lower(),
                    item.text(1).lower(),
                    item.text(2).lower(),
                ]
            )
            match = (not q) or (q in hay)
            visible = match or child_vis
            item.setHidden(not visible)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _selected_material_path(self) -> str:
        items = self.tree.selectedItems()
        for item in items:
            kind = item.data(0, _ROLE_SHADE_KIND) or ""
            if kind == "material":
                return str(item.data(0, _ROLE_SHADE_PATH) or "")
            mat = item.data(0, _ROLE_SHADE_MATERIAL) or ""
            if mat:
                return str(mat)
        return ""

    def _on_selection_changed(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        path = items[0].data(0, _ROLE_SHADE_PATH) or ""
        if path:
            self.selectPrimPath.emit(str(path))

    def _on_double_clicked(self, item: QtWidgets.QTreeWidgetItem, _col: int) -> None:
        path = item.data(0, _ROLE_SHADE_PATH) or ""
        if path:
            self.selectPrimPath.emit(str(path))

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        kind = item.data(0, _ROLE_SHADE_KIND) or ""
        if kind == "group":
            return
        # Ensure the clicked row is part of the selection
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        mat_path = self._selected_material_path()
        menu = QtWidgets.QMenu(self)
        act_sel = menu.addAction("Select Objects Bound to Material")
        act_sel.setEnabled(bool(mat_path))
        if mat_path:
            act_sel.setToolTip(f"Select prims bound to {mat_path}")
        chosen = _qt_menu_exec(menu, self.tree.viewport().mapToGlobal(pos))
        if chosen == act_sel and mat_path:
            self.selectBoundObjects.emit(mat_path)


class TimelineBar(QtWidgets.QWidget):
    """Optional playback bar: range, scrub, play/stop, step, fps."""

    timeChanged = _qt_signal(float)  # current frame / time code

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self._playing = False
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._on_play_tick)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        self.btn_prev = QtWidgets.QToolButton()
        self.btn_prev.setText("◀")
        self.btn_prev.setToolTip("Previous frame")
        self.btn_prev.clicked.connect(self.step_prev)
        row.addWidget(self.btn_prev)

        self.btn_play = QtWidgets.QToolButton()
        self.btn_play.setText("Play")
        self.btn_play.setToolTip("Play / Stop")
        self.btn_play.setCheckable(True)
        self.btn_play.clicked.connect(self._on_play_clicked)
        row.addWidget(self.btn_play)

        self.btn_next = QtWidgets.QToolButton()
        self.btn_next.setText("▶")
        self.btn_next.setToolTip("Next frame")
        self.btn_next.clicked.connect(self.step_next)
        row.addWidget(self.btn_next)

        row.addWidget(self._sep())

        row.addWidget(QtWidgets.QLabel("Start"))
        self.start_spin = QtWidgets.QDoubleSpinBox()
        self.start_spin.setDecimals(3)
        self.start_spin.setRange(-1e6, 1e6)
        self.start_spin.setValue(1.0)
        self.start_spin.setMaximumWidth(72)
        self.start_spin.valueChanged.connect(self._on_range_edited)
        row.addWidget(self.start_spin)

        row.addWidget(QtWidgets.QLabel("End"))
        self.end_spin = QtWidgets.QDoubleSpinBox()
        self.end_spin.setDecimals(3)
        self.end_spin.setRange(-1e6, 1e6)
        self.end_spin.setValue(100.0)
        self.end_spin.setMaximumWidth(72)
        self.end_spin.valueChanged.connect(self._on_range_edited)
        row.addWidget(self.end_spin)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(99)
        self.slider.setValue(0)
        self.slider.setToolTip("Scrub timeline")
        self.slider.valueChanged.connect(self._on_slider_changed)
        row.addWidget(self.slider, 1)

        row.addWidget(QtWidgets.QLabel("Frame"))
        self.frame_spin = QtWidgets.QDoubleSpinBox()
        self.frame_spin.setDecimals(3)
        self.frame_spin.setRange(-1e6, 1e6)
        self.frame_spin.setValue(1.0)
        self.frame_spin.setMaximumWidth(80)
        self.frame_spin.valueChanged.connect(self._on_frame_spin_changed)
        row.addWidget(self.frame_spin)

        row.addWidget(self._sep())

        row.addWidget(QtWidgets.QLabel("FPS"))
        self.fps_spin = QtWidgets.QDoubleSpinBox()
        self.fps_spin.setDecimals(2)
        self.fps_spin.setRange(1.0, 240.0)
        self.fps_spin.setValue(24.0)
        self.fps_spin.setMaximumWidth(64)
        self.fps_spin.setToolTip("Playback framerate")
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        row.addWidget(self.fps_spin)

        self.setMaximumHeight(36)
        self._sync_slider_range()

    @staticmethod
    def _sep() -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.VLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        return line

    def current_frame(self) -> float:
        return float(self.frame_spin.value())

    def current_time_code(self) -> Usd.TimeCode:
        return Usd.TimeCode(self.current_frame())

    def fps(self) -> float:
        return max(1.0, float(self.fps_spin.value()))

    def stop(self) -> None:
        if self._playing:
            self._playing = False
            self._timer.stop()
            self.btn_play.blockSignals(True)
            self.btn_play.setChecked(False)
            self.btn_play.setText("Play")
            self.btn_play.blockSignals(False)

    def sync_from_stage(
        self, stage: Usd.Stage | None, *, emit: bool = True
    ) -> None:
        """Pull start / end / fps from the composed stage metadata."""
        self.stop()
        if stage is None:
            return
        try:
            start = float(stage.GetStartTimeCode())
        except Exception:  # noqa: BLE001
            start = 1.0
        try:
            end = float(stage.GetEndTimeCode())
        except Exception:  # noqa: BLE001
            end = start
        if end < start:
            end = start
        fps = 24.0
        try:
            fps = float(stage.GetFramesPerSecond() or 0) or fps
        except Exception:  # noqa: BLE001
            pass
        if fps <= 0:
            try:
                fps = float(stage.GetTimeCodesPerSecond() or 0) or 24.0
            except Exception:  # noqa: BLE001
                fps = 24.0
        self._updating = True
        try:
            self.start_spin.setValue(start)
            self.end_spin.setValue(end)
            self.fps_spin.setValue(fps)
            self.frame_spin.setValue(start)
            self._sync_slider_range()
            self._frame_to_slider(start)
        finally:
            self._updating = False
        if emit:
            self.timeChanged.emit(start)

    def _sync_slider_range(self) -> None:
        start = float(self.start_spin.value())
        end = float(self.end_spin.value())
        if end < start:
            end = start
            self.end_spin.blockSignals(True)
            self.end_spin.setValue(end)
            self.end_spin.blockSignals(False)
        steps = max(1, int(round(end - start)))
        self.slider.blockSignals(True)
        self.slider.setMinimum(0)
        self.slider.setMaximum(steps)
        self.slider.blockSignals(False)

    def _frame_to_slider(self, frame: float) -> None:
        start = float(self.start_spin.value())
        end = float(self.end_spin.value())
        span = max(end - start, 1e-9)
        t = (frame - start) / span
        idx = int(round(t * self.slider.maximum()))
        idx = max(0, min(self.slider.maximum(), idx))
        self.slider.blockSignals(True)
        self.slider.setValue(idx)
        self.slider.blockSignals(False)

    def _slider_to_frame(self, idx: int) -> float:
        start = float(self.start_spin.value())
        end = float(self.end_spin.value())
        vmax = max(1, self.slider.maximum())
        return start + (end - start) * (float(idx) / float(vmax))

    def _emit_frame(self, frame: float) -> None:
        start = float(self.start_spin.value())
        end = float(self.end_spin.value())
        frame = max(start, min(end, float(frame)))
        self._updating = True
        try:
            self.frame_spin.setValue(frame)
            self._frame_to_slider(frame)
        finally:
            self._updating = False
        self.timeChanged.emit(frame)

    def _on_range_edited(self, *_args) -> None:
        if self._updating:
            return
        self._sync_slider_range()
        self._emit_frame(self.current_frame())

    def _on_slider_changed(self, value: int) -> None:
        if self._updating:
            return
        self._emit_frame(self._slider_to_frame(value))

    def _on_frame_spin_changed(self, value: float) -> None:
        if self._updating:
            return
        self._emit_frame(value)

    def _on_fps_changed(self, *_args) -> None:
        if self._playing:
            self._timer.setInterval(max(1, int(round(1000.0 / self.fps()))))

    def _on_play_clicked(self, checked: bool) -> None:
        if checked:
            self._playing = True
            self.btn_play.setText("Stop")
            self._timer.start(max(1, int(round(1000.0 / self.fps()))))
        else:
            self.stop()

    def _on_play_tick(self) -> None:
        start = float(self.start_spin.value())
        end = float(self.end_spin.value())
        nxt = self.current_frame() + 1.0
        if nxt > end + 1e-9:
            nxt = start
        self._emit_frame(nxt)

    def step_prev(self) -> None:
        self.stop()
        self._emit_frame(self.current_frame() - 1.0)

    def step_next(self) -> None:
        self.stop()
        self._emit_frame(self.current_frame() + 1.0)


class StageExplorer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("usd-lamina")
        self.resize(1180, 780)
        app_icon = _usd_logo_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        # Non-UI session (compose / dirty / export); mirrored on self for widgets.
        self.session = LayerViewSession()
        self._stage: Usd.Stage | None = None
        self._root_layer: Sdf.Layer | None = None
        self._edit_target_layer: Sdf.Layer | None = None  # anonymous delta for edits
        self._closing = False
        self._busy_cursor = False
        self._composing = False
        self._block_attr_edit = False
        self._selected_path: str = ""
        self._edits_layer: Sdf.Layer | None = None
        self._status_kind = "normal"
        self._dirty_prims: set[str] = set()
        self._dirty_attrs: set[tuple[str, str]] = set()
        self._show_attr_kind = False
        self._show_timeline = False

        self._build_menu_bar()
        self.setAcceptDrops(True)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # --- layer stack: TOP (strongest) → … → BASE (bottom) ---
        stack_box = QtWidgets.QGroupBox("Layer stack  (TOP ↑    BASE ↓)")
        stack_box.setToolTip(
            "Like paper: BASE Layer is the bottom sheet; layers stack upward "
            "to TOP Layer (strongest opinion)."
        )
        stack_l = QtWidgets.QVBoxLayout(stack_box)
        stack_l.setContentsMargins(6, 6, 6, 6)
        stack_l.setSpacing(1)

        self.slots: list[LayerSlot] = []
        for i in range(NUM_LAYERS):
            label = slot_strength_label(i)
            slot = LayerSlot(i, label)
            if i == NUM_LAYERS - 1:
                tip = "BASE Layer — bottom sheet (weakest)"
            elif i == 0:
                tip = "TOP Layer — strongest opinion"
            else:
                tip = f"{label} — stronger than BASE"
            slot.setToolTip(tip)
            slot.changed.connect(self._on_layers_changed)
            self.slots.append(slot)
            stack_l.addWidget(slot)
        stack_box.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum
        )

        # compose controls
        ctrl = QtWidgets.QHBoxLayout()
        ctrl.addWidget(QtWidgets.QLabel("Compose mode:"))
        self.mode_combo = QtWidgets.QComboBox()
        for key, label in COMPOSE_MODES:
            self.mode_combo.addItem(label, key)
        self.mode_combo.setToolTip(
            "Sublayer: classic opinion stack (over).\n"
            "Reference / Payload: base as sublayer, stronger files as arcs on default prim."
        )
        ctrl.addWidget(self.mode_combo)

        self.btn_compose = QtWidgets.QPushButton("Compose / Reload")
        self.btn_compose.clicked.connect(self.compose)
        ctrl.addWidget(self.btn_compose)

        self.btn_save_session = QtWidgets.QPushButton("Save Edit Layer…")
        self.btn_save_session.setToolTip(
            "Export the anonymous edit / delta layer (overrides authored in this tool)."
        )
        self.btn_save_session.clicked.connect(self.save_edit_layer)
        ctrl.addWidget(self.btn_save_session)

        ctrl.addStretch(1)
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_clear.setToolTip(
            "Clear all layer paths and reset the session (start from scratch)."
        )
        self.btn_clear.clicked.connect(self.clear_session)
        ctrl.addWidget(self.btn_clear)
        stack_l.addLayout(ctrl)
        root_layout.addWidget(stack_box)

        # --- status bar (message + loading progress + Export USD) ---
        self.status = QtWidgets.QLabel("No stage.")
        self._progress = QtWidgets.QProgressBar()
        self._progress.setMaximumWidth(240)
        self._progress.setMaximumHeight(14)
        self._progress.setTextVisible(True)
        self._progress.hide()
        self.btn_export = QtWidgets.QToolButton()
        self.btn_export.setText("Export USD")
        self.btn_export.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.btn_export.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.btn_export.setToolTip(
            "Export Flattened USD — bake composed result (no file links).\n"
            "Export Referenced USD — keep sublayers / references to source files.\n"
            "Export Payload USD — BASE as sublayer, stronger files as payloads.\n"
            "Export Layered USDA — main USDA linking all slots (optional pack folders)."
        )
        export_menu = QtWidgets.QMenu(self.btn_export)
        export_menu.addAction("Export Flattened USD", self.save_flattened)
        export_menu.addAction("Export Referenced USD", self.save_referenced)
        export_menu.addAction("Export Payload USD", self.save_payload)
        export_menu.addAction("Export Layered USDA…", self.save_layered)
        self.btn_export.setMenu(export_menu)
        sb = self.statusBar()
        sb.addWidget(self.status, 1)
        sb.addPermanentWidget(self._progress)
        sb.addPermanentWidget(self.btn_export)
        self._set_status("No stage.", "normal")

        # --- main tabs: Scene | Composition ---
        self.main_tabs = QtWidgets.QTabWidget()

        # ===== Tab: Scene (hierarchy + attributes) =====
        scene_page = QtWidgets.QWidget()
        scene_l = QtWidgets.QVBoxLayout(scene_page)
        scene_l.setContentsMargins(0, 0, 0, 0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        left = QtWidgets.QWidget()
        left_l = QtWidgets.QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.addWidget(QtWidgets.QLabel("Scene hierarchy"))

        filter_row = QtWidgets.QHBoxLayout()
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Search prims…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit)
        left_l.addLayout(filter_row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setIndentation(max(1, self.tree.indentation() // 2))
        self.tree.setHeaderLabels(["Prim", "Type", "Active", "Visibility", "Path"])
        self.tree.setColumnWidth(_COL_PRIM, 180)
        self.tree.setColumnWidth(_COL_TYPE, 80)
        self.tree.setColumnWidth(_COL_ACTIVE, 50)
        self.tree.setColumnWidth(_COL_VIS, 70)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemSelectionChanged.connect(self._on_prim_selected)
        # Prevent right-click from collapsing an existing multi-selection.
        self.tree.viewport().installEventFilter(self)
        _style_compact_header(self.tree.header())
        left_l.addWidget(self.tree, 1)
        split.addWidget(left)

        right = QtWidgets.QWidget()
        right_l = QtWidgets.QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)

        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(QtWidgets.QLabel("primPath"))
        self.prim_path_edit = QtWidgets.QLineEdit()
        self.prim_path_edit.setPlaceholderText("/World/…")
        self.prim_path_edit.returnPressed.connect(self._goto_prim_path)
        path_row.addWidget(self.prim_path_edit, 1)
        select_btn = QtWidgets.QPushButton("Select")
        select_btn.setToolTip("Select prim by path (Enter also works)")
        select_btn.clicked.connect(self._goto_prim_path)
        path_row.addWidget(select_btn)
        right_l.addLayout(path_row)

        # Attributes (no tabs) — tree + collapsible Meta at base
        attr_page = QtWidgets.QWidget()
        attr_page_l = QtWidgets.QVBoxLayout(attr_page)
        attr_page_l.setContentsMargins(0, 0, 0, 0)
        attr_page_l.setSpacing(4)

        self.attr_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.attr_split.setChildrenCollapsible(False)

        attr_top = QtWidgets.QWidget()
        attr_top_l = QtWidgets.QVBoxLayout(attr_top)
        attr_top_l.setContentsMargins(0, 0, 0, 0)
        attr_top_l.setSpacing(4)
        self.attr_filter_edit = QtWidgets.QLineEdit()
        self.attr_filter_edit.setPlaceholderText("Search attributes…")
        self.attr_filter_edit.setClearButtonEnabled(True)
        self.attr_filter_edit.textChanged.connect(self._apply_attr_filter)
        attr_top_l.addWidget(self.attr_filter_edit)

        self.attr_tree = QtWidgets.QTreeWidget()
        self.attr_tree.setHeaderLabels(["Kind", "Name", "Type", "Value"])
        self.attr_tree.setIndentation(max(1, self.attr_tree.indentation() // 2))
        self.attr_tree.setAlternatingRowColors(True)
        self.attr_tree.setRootIsDecorated(True)
        self.attr_tree.setUniformRowHeights(True)
        self.attr_tree.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.attr_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.attr_tree.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.attr_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.attr_tree.customContextMenuRequested.connect(self._attr_menu)
        self.attr_tree.setSortingEnabled(True)
        self.attr_tree.sortByColumn(1, QtCore.Qt.AscendingOrder)  # Name
        attr_hdr = self.attr_tree.header()
        attr_hdr.setStretchLastSection(True)
        attr_hdr.setSectionsClickable(True)
        attr_hdr.setSortIndicatorShown(True)
        attr_hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        attr_hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        attr_hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        attr_hdr.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        _style_compact_header(attr_hdr)
        self.attr_tree.setColumnHidden(0, True)  # Kind off by default
        self.attr_tree.itemDoubleClicked.connect(self._on_attr_double_clicked)
        self.attr_tree.viewport().installEventFilter(self)
        attr_top_l.addWidget(self.attr_tree, 1)
        self.attr_split.addWidget(attr_top)

        # --- Meta panel (collapsible; collapsed at startup) ---
        meta_panel = QtWidgets.QWidget()
        meta_l = QtWidgets.QVBoxLayout(meta_panel)
        meta_l.setContentsMargins(0, 0, 0, 0)
        meta_l.setSpacing(2)
        meta_hdr = QtWidgets.QHBoxLayout()
        self.meta_toggle = QtWidgets.QToolButton()
        self.meta_toggle.setText("Meta")
        self.meta_toggle.setCheckable(True)
        self.meta_toggle.setChecked(False)
        self.meta_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.meta_toggle.setArrowType(QtCore.Qt.RightArrow)
        self.meta_toggle.setAutoRaise(True)
        self.meta_toggle.setToolTip("Show / hide prim meta properties")
        self.meta_toggle.toggled.connect(self._on_meta_panel_toggled)
        meta_hdr.addWidget(self.meta_toggle)
        meta_hdr.addStretch(1)
        meta_l.addLayout(meta_hdr)

        self.meta_table = QtWidgets.QTableWidget(0, 3)
        self.meta_table.setHorizontalHeaderLabels(["Name", "Type", "Value"])
        self.meta_table.verticalHeader().setVisible(False)
        self.meta_table.horizontalHeader().setVisible(True)
        self.meta_table.setAlternatingRowColors(True)
        self.meta_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.meta_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.meta_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.meta_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.meta_table.customContextMenuRequested.connect(self._meta_menu)
        self.meta_table.setSortingEnabled(True)
        meta_th = self.meta_table.horizontalHeader()
        meta_th.setStretchLastSection(True)
        meta_th.setSectionsClickable(True)
        meta_th.setSortIndicatorShown(True)
        meta_th.setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
        meta_th.setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        meta_th.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        meta_th.resizeSection(0, 110)
        meta_th.resizeSection(1, 70)
        _style_compact_header(meta_th)
        self.meta_table.setVisible(False)
        self.meta_table.viewport().installEventFilter(self)
        meta_l.addWidget(self.meta_table, 1)
        self.attr_split.addWidget(meta_panel)
        self.attr_split.setStretchFactor(0, 3)
        self.attr_split.setStretchFactor(1, 1)
        self.attr_split.setSizes([600, 28])
        self._meta_panel = meta_panel
        self._meta_split_sizes = [420, 160]

        attr_page_l.addWidget(self.attr_split, 1)
        right_l.addWidget(attr_page, 1)

        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        scene_l.addWidget(split, 1)
        self.main_tabs.addTab(scene_page, "Scene")

        # ===== Tab: Composition =====
        self.composition = CompositionView(num_slots=NUM_LAYERS)
        self.composition.gotoPrimPath.connect(self._goto_prim_from_composition)
        self.composition.setEditTarget.connect(self._set_edit_target_from_id)
        self.composition.useEditsLayer.connect(self._use_edits_layer)
        self.composition.assignToSlot.connect(self._assign_file_to_slot)
        self.main_tabs.addTab(self.composition, "Composition")

        # ===== Tab: 2D Scene View (wireframe top map) =====
        self.scene_map = SceneMap2DView()
        self.scene_map.gotoPrimPath.connect(self._goto_prim_from_map)
        self._map_tab_index = self.main_tabs.addTab(self.scene_map, "2D Scene View")
        self.main_tabs.currentChanged.connect(self._on_main_tab_changed)

        root_layout.addWidget(self.main_tabs, 1)

        # --- optional timeline (bottom) ---
        self.timeline = TimelineBar()
        self.timeline.timeChanged.connect(self._on_timeline_time_changed)
        self.timeline.setVisible(False)
        root_layout.addWidget(self.timeline)

        # --- Shaders & Materials dock (Window menu) ---
        self.shaders_panel = ShadersMaterialsView()
        self.shaders_panel.selectPrimPath.connect(self._goto_prim_from_shaders)
        self.shaders_panel.selectBoundObjects.connect(
            self._select_objects_bound_to_material
        )
        self.shaders_dock = QtWidgets.QDockWidget("Shaders & Materials", self)
        self.shaders_dock.setObjectName("shadersMaterialsDock")
        self.shaders_dock.setWidget(self.shaders_panel)
        self.shaders_dock.setAllowedAreas(
            QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea
        )
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.shaders_dock)
        self.shaders_dock.hide()
        self.shaders_dock.visibilityChanged.connect(self._on_shaders_dock_visibility)

        # Full-window USD drop overlay (two zones) — raised above content when dragging.
        self._drop_overlay = UsdDropOverlay(central)
        self._drop_overlay.dropLayered.connect(self._on_drop_layered_usd)
        self._drop_overlay.dropAsLayer.connect(self._on_drop_usd_as_layer)
        self._drop_overlay.cancelled.connect(self._hide_drop_overlay)
        self._drop_overlay.hide()
        self._drop_overlay_armed = False

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._layout_drop_overlay()

    def _layout_drop_overlay(self) -> None:
        central = self.centralWidget()
        if central is None or not hasattr(self, "_drop_overlay"):
            return
        self._drop_overlay.setGeometry(central.rect())
        self._drop_overlay.raise_()

    def begin_usd_file_drag(self, event=None) -> None:
        """Show the dual drop-zone overlay (called from child dragEnter too)."""
        if self._closing:
            return
        self._layout_drop_overlay()
        self._drop_overlay.show()
        self._drop_overlay.raise_()
        self._drop_overlay_armed = True

    def _hide_drop_overlay(self) -> None:
        if hasattr(self, "_drop_overlay"):
            self._drop_overlay.hide()
        self._drop_overlay_armed = False

    def dragEnterEvent(self, event):  # noqa: N802
        if _usd_paths_from_mime(event.mimeData()):
            self.begin_usd_file_drag(event)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        if _usd_paths_from_mime(event.mimeData()):
            if not self._drop_overlay.isVisible():
                self.begin_usd_file_drag(event)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802
        # Only hide when the cursor left the window (not when entering overlay).
        pos = self.mapFromGlobal(QtGui.QCursor.pos())
        if not self.rect().contains(pos):
            self._hide_drop_overlay()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        # Drops should land on the overlay; if not, ignore.
        event.ignore()
        self._hide_drop_overlay()

    def _on_drop_usd_as_layer(self, paths) -> None:
        """Zone: pile each file as BASE / next stronger empty slot."""
        self._hide_drop_overlay()
        paths = list(paths or [])
        if not paths:
            return
        added = 0
        for p in paths:
            if self._pile_usd_path(p):
                added += 1
            else:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Drop as Layer",
                    "Layer stack is full — clear a slot first.\n"
                    f"Could not add:\n{p}",
                )
                break
        if added:
            self._set_status(f"Dropped {added} file(s) as layer(s)", "normal")
            self.compose()

    def _on_drop_layered_usd(self, paths) -> None:
        """Zone: read subLayers from the USD and fill the layer stack slots."""
        self._hide_drop_overlay()
        paths = list(paths or [])
        if not paths:
            return
        # Use the first dropped file as the stack definition.
        src = paths[0]
        try:
            stack = extract_sublayer_stack_paths(src)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self, "Drop Layered USD", f"Failed to read layers:\n{exc}"
            )
            return
        if not stack:
            QtWidgets.QMessageBox.warning(
                self, "Drop Layered USD", f"No layers found in:\n{src}"
            )
            return
        if len(stack) > NUM_LAYERS:
            QtWidgets.QMessageBox.warning(
                self,
                "Drop Layered USD",
                f"File has {len(stack)} sublayers; using the weakest "
                f"{NUM_LAYERS} (BASE side).",
            )
            stack = stack[:NUM_LAYERS]
        self._stack_usd_paths(stack)
        names = ", ".join(os.path.basename(p) for p in stack)
        self._set_status(
            f"Layer stack from {os.path.basename(src)}: {names}", "normal"
        )
        self.compose()

    def _build_menu_bar(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        act_load = file_menu.addAction("Load USD File…")
        act_load.setShortcut(QtGui.QKeySequence.Open)
        act_load.setToolTip(
            f"Open up to {MAX_LOAD_FILES} USD files.\n"
            "Multi-select stacks them as layers (first = BASE).\n"
            "Single file piles onto BASE upward as you load more."
        )
        act_load.triggered.connect(self.load_usd_files)
        file_menu.addSeparator()
        act_quit = file_menu.addAction("E&xit")
        quit_seq = getattr(QtGui.QKeySequence, "Quit", None) or QtGui.QKeySequence(
            "Ctrl+Q"
        )
        act_quit.setShortcut(quit_seq)
        act_quit.triggered.connect(self.close)

        view_menu = menubar.addMenu("&View")
        act_map = view_menu.addAction("2D Scene View")
        act_map.setShortcut(QtGui.QKeySequence("Ctrl+2"))
        act_map.setToolTip(
            "Wireframe top map — sets (blue), chars (salmon), cameras (purple)."
        )
        act_map.triggered.connect(self.show_2d_scene_view)
        act_focus = view_menu.addAction("Focus")
        act_focus.setShortcut(QtGui.QKeySequence("F"))
        act_focus.setToolTip(
            "Focus 2D view on selected prim(s), or the whole scene if nothing selected."
        )
        act_focus.triggered.connect(self.focus_2d_view)

        window_menu = menubar.addMenu("&Window")
        self.act_shaders_panel = window_menu.addAction("Shaders & Materials")
        self.act_shaders_panel.setCheckable(True)
        self.act_shaders_panel.setChecked(False)
        self.act_shaders_panel.setToolTip(
            "Show the Materials / Shaders browser panel."
        )
        self.act_shaders_panel.toggled.connect(self._set_shaders_panel_visible)

        options_menu = menubar.addMenu("&Options")
        self.act_show_attr_kind = options_menu.addAction("Show Prim attributes kind")
        self.act_show_attr_kind.setCheckable(True)
        self.act_show_attr_kind.setChecked(False)
        self.act_show_attr_kind.setToolTip(
            "Show or hide the Kind column in the Attributes viewer."
        )
        self.act_show_attr_kind.toggled.connect(self._set_show_attr_kind)
        self.act_show_timeline = options_menu.addAction("Display Timeline")
        self.act_show_timeline.setCheckable(True)
        self.act_show_timeline.setChecked(False)
        self.act_show_timeline.setToolTip(
            "Show the frame timeline (scrub, play, range, fps) at the bottom."
        )
        self.act_show_timeline.toggled.connect(self._set_show_timeline)

        help_menu = menubar.addMenu("&Help")
        act_about = help_menu.addAction("&About")
        act_about.triggered.connect(self.show_about)

    def _set_show_attr_kind(self, checked: bool) -> None:
        self._show_attr_kind = bool(checked)
        self.attr_tree.setColumnHidden(0, not self._show_attr_kind)

    def _set_show_timeline(self, checked: bool) -> None:
        self._show_timeline = bool(checked)
        self.timeline.setVisible(self._show_timeline)
        if not self._show_timeline:
            self.timeline.stop()

    def current_time_code(self) -> Usd.TimeCode:
        """Active evaluation time from the timeline (even when the bar is hidden)."""
        if hasattr(self, "timeline") and self.timeline is not None:
            return self.timeline.current_time_code()
        return Usd.TimeCode.Default()

    def _on_timeline_time_changed(self, frame: float) -> None:
        if self._closing or not self._stage:
            return
        tc = Usd.TimeCode(float(frame))
        prim = self._selected_prim()
        if prim:
            self._fill_attr_table(prim)
        # Refresh 2D map at this time without resetting pan/zoom.
        self.scene_map.set_query_time(tc, fit=False)

    def _on_meta_panel_toggled(self, expanded: bool) -> None:
        self.meta_toggle.setArrowType(
            QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow
        )
        self.meta_table.setVisible(expanded)
        if expanded:
            # Ensure Name / Type / Value headers stay visible after collapse cycles
            self.meta_table.setColumnCount(3)
            self.meta_table.setHorizontalHeaderLabels(["Name", "Type", "Value"])
            self.meta_table.horizontalHeader().setVisible(True)
            _style_compact_header(self.meta_table.horizontalHeader())
            sizes = self._meta_split_sizes or [420, 160]
            if sizes[1] < 80:
                sizes = [sizes[0], 160]
            self.attr_split.setSizes(sizes)
        else:
            cur = self.attr_split.sizes()
            if len(cur) >= 2 and cur[1] > 40:
                self._meta_split_sizes = list(cur)
            total = sum(self.attr_split.sizes()) or 600
            self.attr_split.setSizes([max(total - 28, 1), 28])

    def show_2d_scene_view(self) -> None:
        """View > 2D Scene View — switch to the wireframe top map tab."""
        self.main_tabs.setCurrentWidget(self.scene_map)

    def focus_2d_view(self) -> None:
        """Focus 2D map: selected prim(s) if any, otherwise everything. (F)"""
        w = QtWidgets.QApplication.focusWidget()
        if isinstance(
            w,
            (
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QSpinBox,
                QtWidgets.QDoubleSpinBox,
                QtWidgets.QComboBox,
            ),
        ):
            return
        paths = [str(p.GetPath()) for p in self._selected_prims()]
        self.main_tabs.setCurrentWidget(self.scene_map)
        # None → selection-aware; explicit list when multi-select
        self.scene_map.focus_view(paths if paths else None)

    def _set_shaders_panel_visible(self, visible: bool) -> None:
        self.shaders_dock.setVisible(bool(visible))
        if visible and self._stage:
            self.shaders_panel.set_stage(self._stage)

    def _on_shaders_dock_visibility(self, visible: bool) -> None:
        if hasattr(self, "act_shaders_panel"):
            self.act_shaders_panel.blockSignals(True)
            self.act_shaders_panel.setChecked(bool(visible))
            self.act_shaders_panel.blockSignals(False)

    def _goto_prim_from_shaders(self, path: str) -> None:
        if not path:
            return
        self.prim_path_edit.setText(path)
        self._goto_prim_path()

    def _select_objects_bound_to_material(self, material_path: str) -> None:
        if not self._stage or not material_path:
            return
        prims = find_prims_bound_to_material(self._stage, material_path)
        self.tree.clearSelection()
        selected = 0
        first_item = None
        for prim in prims:
            path = str(prim.GetPath())
            found = self.tree.findItems(
                path, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, _COL_PATH
            )
            for item in found:
                item.setSelected(True)
                if first_item is None:
                    first_item = item
                selected += 1
        if first_item is not None:
            self.tree.setCurrentItem(first_item, 0, QtCore.QItemSelectionModel.NoUpdate)
            self.tree.scrollToItem(first_item)
            self.main_tabs.setCurrentIndex(0)
        mat_name = material_path.rsplit("/", 1)[-1] or material_path
        if selected:
            self._set_status(
                f"Selected {selected} object(s) bound to {mat_name}", "normal"
            )
        else:
            self._set_status(
                f"No objects bound to {mat_name}", "normal"
            )

    def _on_main_tab_changed(self, index: int) -> None:
        if index != getattr(self, "_map_tab_index", -1):
            return
        if self._stage:
            # Rebuild if empty (compose while tab was hidden), then fit.
            if not self.scene_map._items_by_path:
                self.scene_map.set_stage(self._stage)
            if self._selected_path:
                self.scene_map.set_selected_path(self._selected_path)
        QtCore.QTimer.singleShot(0, self.scene_map.fit_view)
        QtCore.QTimer.singleShot(100, self.scene_map.fit_view)

    def _set_slot_path(self, index: int, path: str, *, block: bool = True) -> None:
        slot = self.slots[index]
        if block:
            slot.blockSignals(True)
            slot.path.blockSignals(True)
            slot.enabled.blockSignals(True)
        try:
            slot.path.setText(path)
            slot.enabled.setChecked(bool(path))
        finally:
            if block:
                slot.enabled.blockSignals(False)
                slot.path.blockSignals(False)
                slot.blockSignals(False)

    def _clear_all_slots(self) -> None:
        for i in range(len(self.slots)):
            self._set_slot_path(i, "")

    def _mirror_session(self) -> None:
        """Copy LayerViewSession fields onto the window for existing UI code."""
        self._stage = self.session.stage
        self._root_layer = self.session.root_layer
        self._edits_layer = self.session.edits_layer
        self._edit_target_layer = self.session.edit_target_layer
        self._dirty_prims = self.session.dirty_prims
        self._dirty_attrs = self.session.dirty_attrs

    def clear_session(self) -> None:
        """Clear layer paths and wipe composed stage / panels — start from scratch."""
        if self._composing:
            return
        self.timeline.stop()
        self._clear_all_slots()
        self.session.clear()
        self._mirror_session()
        self._selected_path = ""
        self.tree.clear()
        self.attr_tree.clear()
        self.meta_table.setRowCount(0)
        self.prim_path_edit.clear()
        self.filter_edit.clear()
        if hasattr(self, "attr_filter_edit"):
            self.attr_filter_edit.clear()
        self.composition.set_stage(None)
        self.scene_map.set_stage(None)
        self.shaders_panel.set_stage(None)
        self._set_status("Cleared — load or drop USD files to start.", "normal")

    def _pile_usd_path(self, path: str) -> bool:
        """Place one file: empty BASE first, then next stronger empty slot."""
        path = os.path.abspath(_clean_path(path))
        if not path or not os.path.isfile(path):
            return False
        # BASE (weakest) → TOP (strongest)
        for i in range(NUM_LAYERS - 1, -1, -1):
            if not self.slots[i].path.text().strip():
                self._set_slot_path(i, path)
                return True
        return False

    def _stack_usd_paths(self, paths: list[str]) -> None:
        """Replace stack: paths[0] = BASE, later entries pile stronger toward TOP."""
        self._clear_all_slots()
        for i, path in enumerate(paths[:NUM_LAYERS]):
            slot_i = NUM_LAYERS - 1 - i
            if slot_i < 0:
                break
            self._set_slot_path(slot_i, os.path.abspath(_clean_path(path)))

    def load_usd_files(self) -> None:
        """File > Load USD File… — multi-select up to 5, or pile singles from BASE."""
        start = os.getcwd()
        for slot in reversed(self.slots):
            cur = slot.path.text().strip()
            if cur:
                start = os.path.dirname(cur) or start
                break
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            f"Load USD File… (up to {MAX_LOAD_FILES})",
            start,
            "USD (*.usda *.usdc *.usd);;All (*.*)",
        )
        if not paths:
            return
        if len(paths) > MAX_LOAD_FILES:
            QtWidgets.QMessageBox.warning(
                self,
                "Load USD",
                f"Select at most {MAX_LOAD_FILES} files at once.\n"
                f"Using the first {MAX_LOAD_FILES}.",
            )
            paths = paths[:MAX_LOAD_FILES]

        if len(paths) == 1:
            if not self._pile_usd_path(paths[0]):
                QtWidgets.QMessageBox.warning(
                    self,
                    "Load USD",
                    "Layer stack is full — clear a slot or multi-select to replace.",
                )
                return
            self._set_status(f"Piled onto stack: {paths[0]}", "normal")
        else:
            self._stack_usd_paths(paths)
            self._set_status(
                f"Stacked {len(paths)} layers (BASE ← first file)", "normal"
            )
        self.compose()

    def show_about(self) -> None:
        """Help > About — logo + USD scene features of this tool."""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("About — usd-lamina")
        dlg.setWindowIcon(self.windowIcon())
        dlg.setMinimumWidth(520)
        root = QtWidgets.QVBoxLayout(dlg)
        root.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        logo = QtWidgets.QLabel()
        pix = _usd_logo_pixmap(96)
        if not pix.isNull():
            logo.setPixmap(pix)
        logo.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        header.addWidget(logo)
        title_col = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("<h2>usd-lamina</h2>")
        acronym = QtWidgets.QLabel(
            "<b>USD L.A.M.I.N.A.</b> — "
            "Layer Assembly · Map · Inspection · Navigation · Authoring"
        )
        acronym.setWordWrap(True)
        subtitle = QtWidgets.QLabel(
            "Read, stack, diagnose, preview prims in 2D, and save OpenUSD layers."
        )
        subtitle.setWordWrap(True)
        title_col.addWidget(title)
        title_col.addWidget(acronym)
        title_col.addWidget(subtitle)
        title_col.addStretch(1)
        header.addLayout(title_col, 1)
        root.addLayout(header)

        features = QtWidgets.QTextBrowser()
        features.setOpenExternalLinks(False)
        features.setHtml(
            """
<h3>USD scene features</h3>
<ul>
  <li><b>Layer stack</b> — up to 6 slots (TOP → BASE); drag-drop USD:
      top zone piles as a layer; bottom fills from a multi-layer file's
      subLayers.</li>
  <li><b>Compose modes</b> — Sublayer (over), Reference, or Payload wiring of
      stronger files onto the base.</li>
  <li><b>Scene hierarchy</b> — prim tree with type, active, visibility; search;
      inactive / invisible greying (cascades to descendants).</li>
  <li><b>Attributes</b> — material bindings, shade networks, attrs &amp;
      relationships; sortable Name/Type/Value; Meta panel at the bottom
      (collapsible); optional Kind column (Options menu).</li>
  <li><b>Context-menu editing</b> — Set Active / Inactive; Set Visible /
      Invisible / Reset Visibility; edit attribute values (multi-prim);
      disconnect connections. Multi-select prims; right-click only.</li>
  <li><b>Composition browser</b> — layer stack, arcs, load types; set edit target;
      jump to prims; assign files back to slots.</li>
  <li><b>Anonymous edit layer</b> — session overrides; save delta layer separately.</li>
  <li><b>Export USD</b> — Flattened (baked); Referenced (sublayers / refs);
      Payload (BASE sublayer + stronger files as payloads); Layered USDA
      (main file + relative <code>@</code> links; optional pack folders per
      renamable layer).</li>
  <li><b>2D Scene View</b> — wireframe map (View menu); Top / Front / Side;
      Focus (F); Options → Display Mesh Edges; optional Timeline.</li>
  <li><b>Shaders &amp; Materials</b> — Window menu panel; select bound objects.</li>
  <li><b>Diagnostics</b> — composition strength order, duplicate-path overs,
      status / dirty tracking for unsaved edits.</li>
</ul>
<p><i>No OpenGL / Hydra viewport — inspect and author USD structure only.</i></p>
"""
        )
        features.setMinimumHeight(320)
        root.addWidget(features, 1)

        ok = getattr(QtWidgets.QDialogButtonBox, "Ok", None) or getattr(
            QtWidgets.QDialogButtonBox.StandardButton, "Ok"
        )
        buttons = QtWidgets.QDialogButtonBox(ok)
        buttons.accepted.connect(dlg.accept)
        root.addWidget(buttons)
        _dlg_exec = getattr(dlg, "exec", None) or dlg.exec_
        _dlg_exec()

    def eventFilter(self, watched, event):  # noqa: N802
        """Keep multi-selection when right-clicking an already-selected row."""
        if event.type() == QtCore.QEvent.MouseButtonPress:
            btn = event.button()
            if btn == QtCore.Qt.RightButton:
                tree = None
                if watched is self.tree.viewport():
                    tree = self.tree
                elif watched is self.attr_tree.viewport():
                    tree = self.attr_tree
                if tree is not None:
                    pos = _qt_event_pos(event)
                    item = tree.itemAt(pos)
                    if item is not None and item.isSelected():
                        # Eat the press so Qt does not ClearAndSelect.
                        tree.setCurrentItem(
                            item, 0, QtCore.QItemSelectionModel.NoUpdate
                        )
                        return True
                if watched is self.meta_table.viewport():
                    pos = _qt_event_pos(event)
                    index = self.meta_table.indexAt(pos)
                    sm = self.meta_table.selectionModel()
                    if (
                        index.isValid()
                        and sm is not None
                        and sm.isRowSelected(index.row(), self.meta_table.rootIndex())
                    ):
                        return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):  # noqa: N802
        """Abort in-flight compose first — destroying widgets mid-load crashes Qt."""
        self._closing = True
        if hasattr(self, "timeline"):
            self.timeline.stop()
        for w in (
            self.filter_edit,
            self.attr_tree,
            self.meta_table,
            self.tree,
            self.prim_path_edit,
            self.mode_combo,
        ):
            w.blockSignals(True)
        for slot in self.slots:
            slot.blockSignals(True)
            slot.path.blockSignals(True)
            slot.enabled.blockSignals(True)
        self._restore_busy_cursor()
        if self._composing:
            # Let compose() unwind safely, then close for real.
            event.ignore()
            self.hide()
            return
        self._stage = None
        super().closeEvent(event)

    def _pump_ui(self) -> bool:
        """Process UI events while loading. Returns False if the window is closing."""
        if self._closing:
            return False
        flags = getattr(QtCore.QEventLoop, "ExcludeUserInputEvents", 0)
        QtWidgets.QApplication.processEvents(flags)
        return not self._closing

    def _restore_busy_cursor(self) -> None:
        if self._busy_cursor:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._busy_cursor = False
        # Drain any leftover overrides from interrupted loads
        while QtWidgets.QApplication.overrideCursor() is not None:
            QtWidgets.QApplication.restoreOverrideCursor()

    # -- status / dirty edits ----------------------------------------------
    def _set_status(self, text: str, kind: str | None = None) -> None:
        """Update status label. kind: normal | dirty | error | busy."""
        if self._closing:
            return
        if kind is None:
            kind = "dirty" if self._dirty_prims else "normal"
        self._status_kind = kind
        self.status.setText(text)
        styles = {
            "normal": ("color:#aaa; background: transparent; padding: 1px 4px;", ""),
            "busy": ("color:#ccc; background: transparent; padding: 1px 4px;", ""),
            "dirty": (
                "color:#ffb040; background: #4a3010; padding: 2px 6px; font-weight: bold;",
                "QStatusBar { background-color: #3a2410; }",
            ),
            "error": (
                "color:#ff8888; background: #4a1515; padding: 2px 6px; font-weight: bold;",
                "QStatusBar { background-color: #3a1212; }",
            ),
        }
        label_ss, bar_ss = styles.get(kind, styles["normal"])
        self.status.setStyleSheet(label_ss)
        self.statusBar().setStyleSheet(bar_ss)

    def _clear_dirty(self) -> None:
        self.session.clear_dirty()
        self._mirror_session()

    def _mark_dirty(self, prim_path: str, attr_name: str | None = None) -> None:
        self.session.mark_dirty(prim_path, attr_name)
        self._mirror_session()
        self._refresh_dirty_fonts(prim_path, attr_name)

    def _refresh_dirty_fonts(
        self, prim_path: str | None = None, attr_name: str | None = None
    ) -> None:
        """Bold hierarchy prims / attr rows that have unsaved edits."""
        if self._closing:
            return
        bold = QtGui.QFont(self.tree.font())
        bold.setBold(True)
        normal = QtGui.QFont(self.tree.font())
        normal.setBold(False)

        def _child_snapshot(item: QtWidgets.QTreeWidgetItem) -> list:
            if not _qt_item_alive(item):
                return []
            try:
                n = item.childCount()
            except RuntimeError:
                return []
            out = []
            for i in range(n):
                if not _qt_item_alive(item):
                    break
                try:
                    child = item.child(i)
                except RuntimeError:
                    break
                if _qt_item_alive(child):
                    out.append(child)
            return out

        def walk(item: QtWidgets.QTreeWidgetItem | None) -> None:
            if not _qt_item_alive(item):
                return
            try:
                path = item.data(0, ROLE_PRIM_PATH) or ""
                item.setFont(_COL_PRIM, bold if path in self._dirty_prims else normal)
            except RuntimeError:
                return
            for child in _child_snapshot(item):
                walk(child)

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

        if self._selected_path:

            def walk_attr(item: QtWidgets.QTreeWidgetItem | None) -> None:
                if not _qt_item_alive(item):
                    return
                try:
                    name = item.data(0, ROLE_ATTR_NAME) or item.text(1)
                    dirty = (self._selected_path, name) in self._dirty_attrs
                    font = bold if dirty else normal
                    for c in range(4):
                        if not _qt_item_alive(item):
                            return
                        item.setFont(c, font)
                except RuntimeError:
                    return
                for child in _child_snapshot(item):
                    walk_attr(child)

            for i in range(self.attr_tree.topLevelItemCount()):
                walk_attr(self.attr_tree.topLevelItem(i))

            # Meta table (bottom panel)
            for r in range(self.meta_table.rowCount()):
                name_item = self.meta_table.item(r, 0)
                if name_item is None:
                    continue
                name = name_item.data(ROLE_ATTR_NAME) or name_item.text()
                dirty = (self._selected_path, name) in self._dirty_attrs
                font = bold if dirty else normal
                for c in range(3):
                    cell = self.meta_table.item(r, c)
                    if cell is not None:
                        cell.setFont(font)

    # -- busy / progress ---------------------------------------------------
    def _busy_start(self, text: str, maximum: int = 0) -> None:
        """Show status-bar progress. ``maximum=0`` → indeterminate busy bar."""
        if self._closing:
            return
        self._set_status(text, "busy")
        self._progress.setRange(0, max(0, int(maximum)))
        if maximum > 0:
            self._progress.setValue(0)
            self._progress.setFormat("%v / %m")
        else:
            self._progress.setFormat("")
        self._progress.show()
        if not self._busy_cursor:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            self._busy_cursor = True
        self._pump_ui()

    def _busy_update(self, value: int, text: str | None = None) -> None:
        if self._closing:
            return
        if text is not None:
            self._set_status(text, "busy")
        if self._progress.maximum() > 0:
            self._progress.setValue(min(value, self._progress.maximum()))
        self._pump_ui()

    def _busy_end(self) -> None:
        if not self._closing:
            self._progress.hide()
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            self._progress.setFormat("%p%")
        self._restore_busy_cursor()

    # -- layer / compose ---------------------------------------------------
    def _on_layers_changed(self):
        if self._closing:
            return
        # Auto-compose when every filled slot points at a real file (drop / browse).
        # Partial typed paths stay as a nudge to press Compose manually.
        filled = [_clean_path(s.value()) for s in self.slots]
        filled = [p for p in filled if p]
        if filled and all(os.path.isfile(p) for p in filled):
            self.compose()
        else:
            self._set_status("Layers changed — press Compose / Reload.", "normal")

    def _layer_paths(self) -> list[str]:
        return [s.value() for s in self.slots]

    def compose(self):
        if self._closing or self._composing:
            return
        mode = self.mode_combo.currentData()
        paths = self._layer_paths()
        self._composing = True
        self._busy_start("Opening USD layers…", 0)
        try:
            try:
                stage, err, notice = self.session.compose(paths, mode=mode)
            except Exception as exc:  # noqa: BLE001
                if not self._closing:
                    QtWidgets.QMessageBox.critical(
                        self, "Compose failed", f"{exc}\n\n{traceback.format_exc()}"
                    )
                return
            if self._closing:
                return
            self._mirror_session()
            if err:
                QtWidgets.QMessageBox.warning(self, "Compose", err)
                self.tree.clear()
                self.composition.set_stage(None)
                self.scene_map.set_stage(None)
                self.shaders_panel.set_stage(None)
                self._set_status(err, "error")
                return

            # Include inactive prims so the hierarchy can show / grey them.
            try:
                prim_count = sum(
                    1 for _ in stage.Traverse(Usd.PrimAllPrimsPredicate)
                )
            except Exception:  # noqa: BLE001
                prim_count = sum(1 for _ in stage.Traverse())
            if self._closing:
                return
            self._busy_start(f"Building scene tree (0/{prim_count})…", prim_count)
            self._rebuild_tree()
            if self._closing:
                return

            self._busy_start("Building composition browser…", 0)
            self._rebuild_composition()
            if self._closing:
                return

            self._busy_start("Building 2D scene map…", 0)
            self.timeline.sync_from_stage(self._stage, emit=False)
            self.scene_map.set_query_time(self.current_time_code(), fit=False)
            self.scene_map.set_stage(self._stage)
            self.shaders_panel.set_stage(self._stage)
            if self._closing:
                return

            n = sum(1 for p in paths if p)
            msg = (
                f"Composed {n} layer(s)  |  {prim_count} prims  |  mode={mode}  |  "
                f"edit target = anonymous edits layer"
            )
            if notice:
                msg = f"{msg}  |  {notice}"
            self._set_status(msg, "normal")
        finally:
            self._busy_end()
            self._composing = False
            if self._closing:
                self._stage = None
                self.close()

    def _rebuild_composition(self):
        if self._closing:
            return
        if not self._stage:
            self.composition.set_stage(None)
            self.scene_map.set_stage(None)
            return
        edit_id = (
            self._edit_target_layer.identifier if self._edit_target_layer else ""
        )
        label = "composed://" + "+".join(
            os.path.basename(p) for p in self._layer_paths() if p
        )
        self.composition.set_stage(self._stage, label, edit_target_id=edit_id)

    def _goto_prim_from_composition(self, path: str):
        self.main_tabs.setCurrentIndex(0)
        self.prim_path_edit.setText(path)
        self._goto_prim_path()

    def _goto_prim_from_map(self, path: str):
        """Select hierarchy prim from 2D map click (stay on map tab)."""
        self.prim_path_edit.setText(path)
        self._goto_prim_path()
        self.scene_map.set_selected_path(path)

    def _set_edit_target_from_id(self, layer_id: str):
        if not self._stage or not layer_id:
            return
        layer = Sdf.Layer.Find(layer_id) or Sdf.Layer.FindOrOpen(layer_id)
        if layer is None:
            QtWidgets.QMessageBox.warning(
                self, "Edit target", f"Could not open layer:\n{layer_id}"
            )
            return
        try:
            if hasattr(layer, "permissionToEdit") and not layer.permissionToEdit:
                QtWidgets.QMessageBox.warning(
                    self, "Edit target", "Layer is not editable."
                )
                return
            self._stage.SetEditTarget(Usd.EditTarget(layer))
            self._edit_target_layer = layer
            self._set_status(f"Edit target → {layer.identifier}")
            self._rebuild_composition()
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Edit target", str(exc))

    def _use_edits_layer(self):
        if not self._stage or not self._edits_layer:
            QtWidgets.QMessageBox.warning(self, "Edit", "Compose a stage first.")
            return
        self._stage.SetEditTarget(self._edits_layer)
        self._edit_target_layer = self._edits_layer
        self._set_status("Edit target → anonymous edits layer")
        self._rebuild_composition()

    def _assign_file_to_slot(self, index: int, path: str):
        if index < 0 or index >= len(self.slots):
            return
        slot = self.slots[index]
        slot.path.setText(path)
        slot.enabled.setChecked(True)
        self._set_status(
            f"Assigned to {slot_strength_label(index)}: {path}", "normal"
        )

    def _restyle_hierarchy_subtree(self, item: QtWidgets.QTreeWidgetItem) -> None:
        """Re-apply visibility/active greying for item and descendants."""
        anc_inactive = False
        anc_invisible = False
        p = item.parent()
        while p is not None:
            if p.text(_COL_ACTIVE) != "true":
                anc_inactive = True
            if p.text(_COL_VIS) == "invisible":
                anc_invisible = True
            p = p.parent()
        _apply_hierarchy_item_style(
            item,
            ancestor_inactive=anc_inactive,
            ancestor_invisible=anc_invisible,
        )

    def _rebuild_tree(self):
        if self._closing:
            return
        self.tree.clear()
        self._tree_progress_i = 0
        if not self._stage:
            return
        self.tree.setUpdatesEnabled(False)
        try:
            root_prim = self._stage.GetPseudoRoot()
            for child in _iter_all_prim_children(root_prim):
                if self._closing:
                    return
                self._add_prim_item(None, child)
            if self._closing:
                return
            self.tree.expandToDepth(1)
            self._apply_filter(self.filter_edit.text())
            for col in (_COL_TYPE, _COL_ACTIVE, _COL_VIS):
                self.tree.resizeColumnToContents(col)
            self._refresh_dirty_fonts()
        finally:
            if not self._closing:
                self.tree.setUpdatesEnabled(True)

    def _add_prim_item(
        self,
        parent: QtWidgets.QTreeWidgetItem | None,
        prim: Usd.Prim,
        *,
        ancestor_inactive: bool = False,
        ancestor_invisible: bool = False,
    ):
        if self._closing:
            return
        path = str(prim.GetPath())
        active = prim.IsActive()
        vis_label = prim_visibility_label(prim)
        inactive = ancestor_inactive or (not active)
        self_invisible = vis_label == "invisible"
        invisible = ancestor_invisible or self_invisible

        item = QtWidgets.QTreeWidgetItem(
            [
                prim.GetName() or "/",
                prim_type_label(prim),
                "true" if active else "false",
                vis_label,
                path,
            ]
        )
        item.setData(0, ROLE_PRIM_PATH, path)
        if inactive:
            brush = QtGui.QBrush(_TREE_COLOR_INACTIVE)
            for col in range(5):
                item.setForeground(col, brush)
        elif invisible:
            brush = QtGui.QBrush(_TREE_COLOR_INVISIBLE)
            for col in range(5):
                item.setForeground(col, brush)
        if path in self._dirty_prims:
            bold = QtGui.QFont(self.tree.font())
            bold.setBold(True)
            item.setFont(_COL_PRIM, bold)
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)

        self._tree_progress_i = getattr(self, "_tree_progress_i", 0) + 1
        maximum = self._progress.maximum() if self._progress.isVisible() else 0
        if maximum > 0 and (
            self._tree_progress_i % 64 == 0 or self._tree_progress_i >= maximum
        ):
            self._busy_update(
                self._tree_progress_i,
                f"Building scene tree ({self._tree_progress_i}/{maximum})…",
            )
            if self._closing:
                return

        for child in _iter_all_prim_children(prim):
            if self._closing:
                return
            self._add_prim_item(
                item,
                child,
                ancestor_inactive=inactive,
                ancestor_invisible=invisible,
            )

    def _apply_filter(self, text: str = ""):
        if self._closing:
            return
        needle = (text or "").strip().lower()

        def walk(item: QtWidgets.QTreeWidgetItem) -> bool:
            path = (item.data(0, ROLE_PRIM_PATH) or "").lower()
            name = item.text(_COL_PRIM).lower()
            typ = item.text(_COL_TYPE).lower()
            active = item.text(_COL_ACTIVE).lower()
            vis = item.text(_COL_VIS).lower()
            match = (not needle) or any(
                needle in s for s in (path, name, typ, active, vis)
            )
            child_match = False
            for i in range(item.childCount()):
                if walk(item.child(i)):
                    child_match = True
            visible = match or child_match
            item.setHidden(not visible)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _apply_attr_filter(self, text: str = ""):
        if self._closing:
            return
        needle = (text or "").strip().lower()

        def walk(item: QtWidgets.QTreeWidgetItem) -> bool:
            bits = " ".join(item.text(c).lower() for c in range(4))
            match = (not needle) or needle in bits
            child_match = False
            for i in range(item.childCount()):
                if walk(item.child(i)):
                    child_match = True
            visible = match or child_match
            item.setHidden(not visible)
            if child_match and needle:
                item.setExpanded(True)
            return visible

        for i in range(self.attr_tree.topLevelItemCount()):
            walk(self.attr_tree.topLevelItem(i))

    # -- selection / display -----------------------------------------------
    def _selected_prims(self) -> list[Usd.Prim]:
        """All valid prims from the hierarchy multi-selection (stable path order)."""
        if not self._stage:
            return []
        out: list[Usd.Prim] = []
        seen: set[str] = set()
        for item in self.tree.selectedItems():
            path = item.data(0, ROLE_PRIM_PATH)
            if not path or path in seen:
                continue
            prim = self._stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                seen.add(path)
                out.append(prim)
        out.sort(key=lambda p: str(p.GetPath()))
        return out

    def _selected_prim(self) -> Usd.Prim | None:
        """Primary selected prim (current item, else first in multi-selection)."""
        if not self._stage:
            return None
        cur = self.tree.currentItem()
        if cur is not None:
            path = cur.data(0, ROLE_PRIM_PATH)
            if path:
                prim = self._stage.GetPrimAtPath(path)
                if prim and prim.IsValid():
                    return prim
        prims = self._selected_prims()
        return prims[0] if prims else None

    def _on_prim_selected(self):
        prims = self._selected_prims()
        prim = self._selected_prim()
        if not prim:
            return
        self._selected_path = str(prim.GetPath())
        if len(prims) > 1:
            self.prim_path_edit.setText(
                f"{self._selected_path}  (+{len(prims) - 1} more)"
            )
            self._set_status(
                f"{len(prims)} prims selected — edits apply to all", "normal"
            )
        else:
            self.prim_path_edit.setText(self._selected_path)
        self._fill_attr_table(prim)
        self.scene_map.set_selected_path(self._selected_path)

    def _fill_attr_table(self, prim: Usd.Prim):
        self._block_attr_edit = True
        self.attr_tree.setSortingEnabled(False)
        self.meta_table.setSortingEnabled(False)
        self.attr_tree.clear()
        rows = collect_prim_rows(prim, self.current_time_code())
        prim_path = str(prim.GetPath())
        bold = QtGui.QFont(self.attr_tree.font())
        bold.setBold(True)

        meta_rows = [n for n in rows if n.get("kind") == "meta"]
        tree_rows = [n for n in rows if n.get("kind") != "meta"]

        # --- Meta table (Name / Type / Value — no Kind column) ---
        self.meta_table.clearContents()
        self.meta_table.setRowCount(0)
        self.meta_table.setColumnCount(3)
        self.meta_table.setHorizontalHeaderLabels(["Name", "Type", "Value"])
        self.meta_table.horizontalHeader().setVisible(True)
        _style_compact_header(self.meta_table.horizontalHeader())
        self.meta_table.setRowCount(len(meta_rows))
        for r, node in enumerate(meta_rows):
            name = node.get("name", "")
            typ = node.get("type", "") or ""
            val = node.get("value", "")
            dirty = (prim_path, name) in self._dirty_attrs
            for c, text in enumerate((name, typ, val)):
                cell = QtWidgets.QTableWidgetItem(str(text))
                cell.setFlags(
                    (cell.flags() | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
                    & ~QtCore.Qt.ItemIsEditable
                )
                # Keep sort working for numeric-looking values as text
                cell.setData(QtCore.Qt.DisplayRole, str(text))
                if c == 0:
                    cell.setData(QtCore.Qt.UserRole, name)
                    cell.setData(ROLE_ATTR_KIND, "meta")
                    cell.setData(ROLE_ATTR_NAME, name)
                    cell.setData(ROLE_ATTR_OWNER, prim_path)
                if dirty:
                    cell.setFont(bold)
                self.meta_table.setItem(r, c, cell)
            self.meta_table.item(r, 0).setToolTip("Right-click to edit")

        def add_node(
            parent: QtWidgets.QTreeWidgetItem | None, node: dict[str, Any]
        ) -> QtWidgets.QTreeWidgetItem:
            kind = node.get("kind", "")
            name = node.get("name", "")
            typ = node.get("type", "")
            val = node.get("value", "")
            item = QtWidgets.QTreeWidgetItem([kind, name, typ, val])
            item.setData(0, ROLE_ATTR_KIND, kind)
            item.setData(0, ROLE_ATTR_NAME, name)
            owner = node.get("owner") or prim_path
            item.setData(0, ROLE_ATTR_OWNER, owner)
            item.setData(0, ROLE_ATTR_HAS_CONN, bool(node.get("has_conn")))
            goto = node.get("goto") or ""
            if goto:
                item.setData(0, ROLE_ATTR_GOTO, goto)
                item.setToolTip(3, f"Double-click to select {goto}")
            elif kind in ("attr", "connect"):
                item.setToolTip(3, "Right-click to edit / disconnect")
            dirty = (owner, name) in self._dirty_attrs or (
                prim_path,
                name,
            ) in self._dirty_attrs
            for c in range(4):
                flags = item.flags()
                flags &= ~QtCore.Qt.ItemIsEditable
                item.setFlags(flags)
                if dirty:
                    item.setFont(c, bold)
            if kind == "bind":
                for c in range(4):
                    item.setForeground(c, QtGui.QBrush(QtGui.QColor("#9ecbff")))
            if parent is None:
                self.attr_tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            for child in node.get("children") or []:
                add_node(item, child)
            return item

        for node in tree_rows:
            item = add_node(None, node)
            # Auto-expand binding rows that have connections
            if node.get("kind") == "bind" and node.get("children"):
                item.setExpanded(True)
                # Also expand first-level "shader network" child
                for i in range(item.childCount()):
                    ch = item.child(i)
                    if ch.text(1) == "shader network":
                        ch.setExpanded(True)

        self._block_attr_edit = False
        self.attr_tree.setColumnHidden(0, not self._show_attr_kind)
        for col in (0, 1, 2):
            if col == 0 and not self._show_attr_kind:
                continue
            self.attr_tree.resizeColumnToContents(col)
        self.attr_tree.setSortingEnabled(True)
        self.meta_table.setSortingEnabled(True)
        self._apply_attr_filter(self.attr_filter_edit.text())

    def _on_attr_double_clicked(self, item: QtWidgets.QTreeWidgetItem, column: int):
        _ = column
        goto = item.data(0, ROLE_ATTR_GOTO) if item else ""
        if goto and str(goto).startswith("/"):
            self.prim_path_edit.setText(str(goto))
            self._goto_prim_path()

    def _goto_prim_path(self):
        if not self._stage:
            self._set_status("No stage — compose a layer first.", "error")
            return
        path = self.prim_path_edit.text().strip()
        if not path:
            return
        prim = self._stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            self._set_status(f"No prim at {path}", "error")
            return
        # select in tree
        found = self.tree.findItems(
            path, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, _COL_PATH
        )
        if found:
            self.tree.setCurrentItem(found[0])
            self.tree.scrollToItem(found[0])
            self._set_status(f"Selected {path}")
        else:
            self._set_status(f"Prim exists but is hidden by filter: {path}", "error")

    # -- edits (context menus only) ----------------------------------------
    def _ensure_edit_target(self) -> bool:
        if not self._stage or not self._edit_target_layer:
            self._set_status("Compose a stage first.", "error")
            return False
        # Keep whatever layer the user selected (Composition → Set as Edit Target)
        try:
            self._stage.SetEditTarget(Usd.EditTarget(self._edit_target_layer))
        except Exception:
            self._stage.SetEditTarget(self._edit_target_layer)
        return True

    def _prim_at(self, path: str) -> Usd.Prim | None:
        if not self._stage or not path:
            return None
        prim = self._stage.GetPrimAtPath(path)
        return prim if prim and prim.IsValid() else None

    def _resolve_attr_from_item(
        self, item: QtWidgets.QTreeWidgetItem
    ) -> tuple[Usd.Prim | None, Usd.Attribute | None, str]:
        """Resolve (owner prim, attribute, attr name) from an attr-tree row."""
        kind = item.data(0, ROLE_ATTR_KIND) or item.text(0)
        name = item.data(0, ROLE_ATTR_NAME) or item.text(1)
        owner = item.data(0, ROLE_ATTR_OWNER) or self._selected_path
        if kind == "meta" and name in ("active", "visibility", "instanceable"):
            return self._prim_at(owner), None, name
        if kind not in ("attr", "connect"):
            return self._prim_at(owner), None, name
        prim = self._prim_at(owner)
        if not prim or not name:
            return prim, None, name
        attr = prim.GetAttribute(name)
        if not attr and name.startswith("inputs:"):
            # Shade inputs sometimes resolve via ConnectableAPI only
            attr = prim.GetAttribute(name)
        return prim, attr if attr and attr.IsValid() else None, name

    def _update_prim_active_ui(self, prim: Usd.Prim, path: str) -> None:
        """Refresh hierarchy columns after an active edit (no full rebuild)."""
        active = prim.IsActive()
        found = self.tree.findItems(
            path, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, _COL_PATH
        )
        if found:
            item = found[0]
            item.setText(_COL_ACTIVE, "true" if active else "false")
            bold = QtGui.QFont(self.tree.font())
            bold.setBold(True)
            item.setFont(_COL_PRIM, bold)
            self._restyle_hierarchy_subtree(item)

    def _update_prim_visibility_ui(self, prim: Usd.Prim, path: str) -> None:
        found = self.tree.findItems(
            path, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, _COL_PATH
        )
        if found:
            item = found[0]
            item.setText(_COL_VIS, prim_visibility_label(prim))
            bold = QtGui.QFont(self.tree.font())
            bold.setBold(True)
            item.setFont(_COL_PRIM, bold)
            self._restyle_hierarchy_subtree(item)

    def _refresh_panels_after_edit(self) -> None:
        prim = self._selected_prim()
        if prim:
            self._fill_attr_table(prim)
        self._rebuild_composition()

    def _set_active_state(self, active: bool, prims: list[Usd.Prim] | None = None):
        if not self._ensure_edit_target():
            return
        targets = prims if prims is not None else self._selected_prims()
        if not targets:
            return
        for prim in targets:
            path = str(prim.GetPath())
            prim.SetActive(active)
            self._dirty_prims.add(path)
            self._dirty_attrs.add((path, "active"))
            self._update_prim_active_ui(prim, path)
        n = len(targets)
        self._set_status(
            f"Unsaved edit: active={active} on {n} prim(s)", "dirty"
        )
        self._refresh_panels_after_edit()

    def _set_visibility_state(
        self, mode: str, prims: list[Usd.Prim] | None = None
    ):
        """mode: ``visible`` | ``invisible`` | ``reset`` (clear override)."""
        if not self._ensure_edit_target():
            return
        targets = prims if prims is not None else self._selected_prims()
        if not targets:
            return
        ok = 0
        skipped = 0
        for prim in targets:
            img = UsdGeom.Imageable(prim)
            if not img:
                skipped += 1
                continue
            attr = img.GetVisibilityAttr()
            path = str(prim.GetPath())
            if mode == "reset":
                try:
                    attr.Clear()
                except Exception:  # noqa: BLE001
                    # Fallback: author inherited then clear default if possible
                    attr.Set(UsdGeom.Tokens.inherited)
                    if hasattr(attr, "ClearDefault"):
                        attr.ClearDefault()
                    else:
                        attr.Clear()
            elif mode == "invisible":
                attr.Set(UsdGeom.Tokens.invisible)
            else:
                # Set Visible — authored inherited (explicitly visible opinion)
                attr.Set(UsdGeom.Tokens.inherited)
            self._dirty_prims.add(path)
            self._dirty_attrs.add((path, "visibility"))
            self._update_prim_visibility_ui(prim, path)
            ok += 1
        label = {"visible": "inherited(visible)", "invisible": "invisible", "reset": "cleared"}[
            mode
        ]
        msg = f"Unsaved edit: visibility={label} on {ok} prim(s)"
        if skipped:
            msg += f" ({skipped} not Imageable)"
        self._set_status(msg, "dirty" if ok else "error")
        if ok:
            self._refresh_panels_after_edit()

    def _edit_attribute_value(
        self,
        prim: Usd.Prim,
        attr: Usd.Attribute,
        *,
        apply_to_selection: bool = True,
    ) -> None:
        """Dialog-edit attribute; optionally apply same value to all selected prims."""
        if not self._ensure_edit_target():
            return
        name = attr.GetName()
        type_name = str(attr.GetTypeName())
        targets = self._selected_prims() if apply_to_selection else [prim]
        if not targets:
            targets = [prim]
        # Keep only prims that have this attribute
        editable: list[tuple[Usd.Prim, Usd.Attribute]] = []
        for p in targets:
            a = p.GetAttribute(name)
            if a and a.IsValid():
                editable.append((p, a))
        if not editable:
            self._set_status(f"No selected prim has attribute {name}", "error")
            return
        initial = attr_value_for_dialog(attr, self.current_time_code())
        n = len(editable)
        prompt = (
            f"{name}  ({type_name})\n"
            f"Apply to {n} selected prim(s)"
            if n > 1
            else f"{prim.GetPath()}.{name}\nType: {type_name}"
        )
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Edit Attribute",
            prompt,
            QtWidgets.QLineEdit.Normal,
            initial,
        )
        if not ok:
            return
        try:
            new_val = parse_attr_input(attr, text)
            for p, a in editable:
                if attr_connection_paths(a):
                    self._clear_attr_connections(a)
                a.Set(new_val)
                path = str(p.GetPath())
                self._dirty_prims.add(path)
                self._dirty_attrs.add((path, name))
                if name == "visibility":
                    self._update_prim_visibility_ui(p, path)
            self._set_status(
                f"Unsaved edit: {name} = {new_val!r} on {len(editable)} prim(s)",
                "dirty",
            )
            self._refresh_panels_after_edit()
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Set attribute failed: {exc}", "error")
            QtWidgets.QMessageBox.warning(
                self, "Edit Attribute", f"Could not set value:\n{exc}"
            )

    def _clear_attr_connections(self, attr: Usd.Attribute) -> bool:
        """Remove authored connections / shade sources. Returns True if any cleared."""
        cleared = False
        if UsdShade is not None:
            try:
                if UsdShade.ConnectableAPI.HasConnectedSource(attr):
                    UsdShade.ConnectableAPI.DisconnectSource(attr)
                    cleared = True
            except Exception:  # noqa: BLE001
                pass
        try:
            if attr.HasAuthoredConnections():
                attr.ClearConnections()
                cleared = True
        except Exception:  # noqa: BLE001
            pass
        return cleared

    def _disconnect_attribute(
        self,
        prim: Usd.Prim,
        attr: Usd.Attribute,
        *,
        apply_to_selection: bool = True,
    ) -> None:
        if not self._ensure_edit_target():
            return
        name = attr.GetName()
        targets = self._selected_prims() if apply_to_selection else [prim]
        if not targets:
            targets = [prim]
        cleared_n = 0
        for p in targets:
            a = p.GetAttribute(name)
            if not a or not a.IsValid():
                continue
            if not attr_connection_paths(a):
                continue
            if self._clear_attr_connections(a):
                path = str(p.GetPath())
                self._dirty_prims.add(path)
                self._dirty_attrs.add((path, name))
                cleared_n += 1
        if not cleared_n:
            self._set_status(f"No connection on {name} for selection", "error")
            return
        self._set_status(
            f"Unsaved edit: disconnected {name} on {cleared_n} prim(s)", "dirty"
        )
        self._refresh_panels_after_edit()

    def _context_focus_tree_item(
        self, tree: QtWidgets.QTreeWidget, item: QtWidgets.QTreeWidgetItem
    ) -> None:
        """Focus ``item`` for a context menu without collapsing a multi-selection.

        Plain ``setCurrentItem(item)`` uses ClearAndSelect and drops multi-select.
        """
        no_update = QtCore.QItemSelectionModel.NoUpdate
        if item.isSelected():
            # Keep every selected row; only move the current/focus item.
            tree.setCurrentItem(item, 0, no_update)
            return
        # Clicked outside the selection → become the sole selection.
        tree.blockSignals(True)
        try:
            tree.clearSelection()
            item.setSelected(True)
            tree.setCurrentItem(item, 0, no_update)
        finally:
            tree.blockSignals(False)
        # Selection changed while blocked — refresh panels once.
        self._on_prim_selected()

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        self._context_focus_tree_item(self.tree, item)
        path = item.data(0, ROLE_PRIM_PATH) or ""
        prims = self._selected_prims()
        n = len(prims)
        any_imageable = any(UsdGeom.Imageable(p) for p in prims)

        menu = QtWidgets.QMenu(self)
        if n > 1:
            menu.addSection(f"{n} prims selected")
        act_active = menu.addAction("Set Active")
        act_inactive = menu.addAction("Set Inactive")
        menu.addSeparator()
        act_vis = menu.addAction("Set Visible")
        act_invis = menu.addAction("Set Invisible")
        act_reset_vis = menu.addAction("Reset Visibility")
        act_reset_vis.setToolTip("Clear authored visibility — inherit (no override)")
        menu.addSeparator()
        act_copy = menu.addAction("Copy primPath")
        act_reload = menu.addAction("Reload hierarchy")

        for act in (act_vis, act_invis, act_reset_vis):
            act.setEnabled(any_imageable)
        if not prims:
            for act in (act_active, act_inactive, act_vis, act_invis, act_reset_vis):
                act.setEnabled(False)

        chosen = _qt_menu_exec(menu, self.tree.viewport().mapToGlobal(pos))
        if chosen == act_active:
            self._set_active_state(True, prims)
        elif chosen == act_inactive:
            self._set_active_state(False, prims)
        elif chosen == act_vis:
            self._set_visibility_state("visible", prims)
        elif chosen == act_invis:
            self._set_visibility_state("invisible", prims)
        elif chosen == act_reset_vis:
            self._set_visibility_state("reset", prims)
        elif chosen == act_copy:
            paths = [str(p.GetPath()) for p in prims] or [path]
            QtWidgets.QApplication.clipboard().setText("\n".join(paths))
        elif chosen == act_reload:
            self._rebuild_tree()

    def _attr_menu(self, pos):
        item = self.attr_tree.itemAt(pos)
        if not item:
            return
        # Preserve multi-select among attribute rows (and hierarchy prims).
        no_update = QtCore.QItemSelectionModel.NoUpdate
        if item.isSelected():
            self.attr_tree.setCurrentItem(item, 0, no_update)
        else:
            self.attr_tree.blockSignals(True)
            try:
                self.attr_tree.clearSelection()
                item.setSelected(True)
                self.attr_tree.setCurrentItem(item, 0, no_update)
            finally:
                self.attr_tree.blockSignals(False)
        kind = item.data(0, ROLE_ATTR_KIND) or item.text(0)
        name = item.data(0, ROLE_ATTR_NAME) or item.text(1)
        has_conn = bool(item.data(0, ROLE_ATTR_HAS_CONN))
        prim, attr, attr_name = self._resolve_attr_from_item(item)
        prims = self._selected_prims()
        n_prims = len(prims)
        if attr and not has_conn:
            has_conn = bool(attr_connection_paths(attr))

        menu = QtWidgets.QMenu(self)
        if n_prims > 1:
            menu.addSection(f"Apply to {n_prims} selected prims")

        act_edit = None
        act_disc = None
        if kind in ("attr", "connect") and attr:
            label = "Edit Value…" if n_prims <= 1 else f"Edit Value… ({n_prims} prims)"
            act_edit = menu.addAction(label)
            act_disc = menu.addAction(
                "Disconnect" if n_prims <= 1 else f"Disconnect ({n_prims} prims)"
            )
            act_disc.setEnabled(has_conn or n_prims > 1)

        act_copy_name = menu.addAction("Copy Name")
        act_copy_val = menu.addAction("Copy Value")
        chosen = _qt_menu_exec(menu, self.attr_tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if act_edit is not None and chosen == act_edit and attr and prim:
            self._edit_attribute_value(prim, attr, apply_to_selection=True)
        elif act_disc is not None and chosen == act_disc and attr and prim:
            self._disconnect_attribute(prim, attr, apply_to_selection=True)
        elif chosen == act_copy_name:
            QtWidgets.QApplication.clipboard().setText(str(attr_name or name))
        elif chosen == act_copy_val:
            # Value column index depends on Kind visibility: always col 3 in tree
            QtWidgets.QApplication.clipboard().setText(item.text(3))

    def _meta_menu(self, pos):
        """Context menu on the bottom Meta table (active / visibility / copy)."""
        index = self.meta_table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        name_item = self.meta_table.item(row, 0)
        if name_item is None:
            return
        # Keep multi-row selection when right-clicking an already-selected row.
        sm = self.meta_table.selectionModel()
        already = sm is not None and sm.isRowSelected(row, self.meta_table.rootIndex())
        if not already:
            self.meta_table.clearSelection()
            self.meta_table.selectRow(row)
        name = name_item.data(ROLE_ATTR_NAME) or name_item.text()
        prims = self._selected_prims()
        n_prims = len(prims)
        prim = self._selected_prim()

        menu = QtWidgets.QMenu(self)
        if n_prims > 1:
            menu.addSection(f"Apply to {n_prims} selected prims")

        act_active = act_inactive = None
        act_vis = act_invis = act_reset_vis = None
        if name == "active" and prim:
            act_active = menu.addAction("Set Active")
            act_inactive = menu.addAction("Set Inactive")
        elif name == "visibility" and prim:
            act_vis = menu.addAction("Set Visible")
            act_invis = menu.addAction("Set Invisible")
            act_reset_vis = menu.addAction("Reset Visibility")

        act_copy_name = menu.addAction("Copy Name")
        act_copy_val = menu.addAction("Copy Value")
        chosen = _qt_menu_exec(menu, self.meta_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if act_active is not None and chosen == act_active:
            self._set_active_state(True, prims)
        elif act_inactive is not None and chosen == act_inactive:
            self._set_active_state(False, prims)
        elif act_vis is not None and chosen == act_vis:
            self._set_visibility_state("visible", prims)
        elif act_invis is not None and chosen == act_invis:
            self._set_visibility_state("invisible", prims)
        elif act_reset_vis is not None and chosen == act_reset_vis:
            self._set_visibility_state("reset", prims)
        elif chosen == act_copy_name:
            QtWidgets.QApplication.clipboard().setText(str(name))
        elif chosen == act_copy_val:
            val_item = self.meta_table.item(row, 2)
            QtWidgets.QApplication.clipboard().setText(
                val_item.text() if val_item else ""
            )

    # -- save / export -----------------------------------------------------
    def save_flattened(self):
        if not self._stage:
            QtWidgets.QMessageBox.warning(self, "Export", "Compose a stage first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Flattened USD",
            os.getcwd(),
            "USD ASCII (*.usda);;USD Crate (*.usdc);;USD (*.usd)",
        )
        if not path:
            return
        try:
            self.session.export_flattened(path)
            self._set_status(f"Wrote flattened: {path}", "normal")
            QtWidgets.QMessageBox.information(
                self, "Exported", f"Flattened layer written:\n{path}"
            )
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self, "Export failed", f"{exc}\n\n{traceback.format_exc()}"
            )

    def save_referenced(self):
        """Export compose root + edits, keeping links to source layer files."""
        if not self._stage or not self._root_layer:
            QtWidgets.QMessageBox.warning(self, "Export", "Compose a stage first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Referenced USD",
            os.getcwd(),
            "USD ASCII (*.usda);;USD Crate (*.usdc);;USD (*.usd)",
        )
        if not path:
            return
        try:
            self.session.export_referenced(path)
            self.session.clear_dirty()
            self._mirror_session()
            self._refresh_dirty_fonts()
            self._set_status(f"Wrote referenced: {path}", "normal")
            QtWidgets.QMessageBox.information(
                self,
                "Exported",
                "Referenced layer written (sublayers / refs preserved):\n"
                f"{path}",
            )
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self, "Export failed", f"{exc}\n\n{traceback.format_exc()}"
            )

    def save_payload(self):
        """Export BASE as sublayer + stronger slot files as payloads."""
        paths = [p for p in self._layer_paths() if _clean_path(p)]
        if not paths:
            QtWidgets.QMessageBox.warning(
                self, "Export", "Load / compose at least one layer first."
            )
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Payload USD",
            os.getcwd(),
            "USD ASCII (*.usda);;USD Crate (*.usdc);;USD (*.usd)",
        )
        if not path:
            return
        try:
            self.session.export_payload(paths, path)
            self.session.clear_dirty()
            self._mirror_session()
            self._refresh_dirty_fonts()
            self._set_status(f"Wrote payload: {path}", "normal")
            QtWidgets.QMessageBox.information(
                self,
                "Exported",
                "Payload layer written (BASE sublayer + payloads):\n"
                f"{path}",
            )
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self, "Export failed", f"{exc}\n\n{traceback.format_exc()}"
            )

    def save_layered(self):
        """Main USDA linking all enabled slots; optional per-layer pack folders."""
        entries: list[tuple[str, str]] = []
        for slot in self.slots:
            src = _clean_path(slot.value())
            if not src:
                continue
            entries.append((slot.pack_name(), src))
        if not entries:
            QtWidgets.QMessageBox.warning(
                self, "Export", "Enable / fill at least one layer slot first."
            )
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Export Layered USDA")
        dlg.setMinimumWidth(480)
        form = QtWidgets.QVBoxLayout(dlg)

        path_row = QtWidgets.QHBoxLayout()
        path_edit = QtWidgets.QLineEdit(
            os.path.join(os.getcwd(), "layered.usda")
        )
        browse = QtWidgets.QPushButton("Browse…")

        def _browse_out():
            start = path_edit.text().strip() or os.getcwd()
            chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
                dlg,
                "Export Layered USDA",
                start,
                "USD ASCII (*.usda);;USD Crate (*.usdc);;USD (*.usd)",
            )
            if chosen:
                path_edit.setText(chosen)

        browse.clicked.connect(_browse_out)
        path_row.addWidget(path_edit, 1)
        path_row.addWidget(browse)
        form.addWidget(QtWidgets.QLabel("Output main USDA:"))
        form.addLayout(path_row)

        pack_cb = QtWidgets.QCheckBox("Pack assets to the same folder")
        pack_cb.setChecked(True)
        pack_cb.setToolTip(
            "Create one folder per layer (using the pack name field),\n"
            "plain-copy each USDA into it, and link with relative @ paths\n"
            "from the main file (e.g. @./layerName/file.usda@)."
        )
        form.addWidget(pack_cb)
        form.addWidget(
            QtWidgets.QLabel(
                "Pack names come from the layer row fields (editable). "
                "Stack order is TOP → BASE (strongest first)."
            )
        )

        _ok = getattr(QtWidgets.QDialogButtonBox, "Ok", None) or getattr(
            QtWidgets.QDialogButtonBox.StandardButton, "Ok"
        )
        _cancel = getattr(QtWidgets.QDialogButtonBox, "Cancel", None) or getattr(
            QtWidgets.QDialogButtonBox.StandardButton, "Cancel"
        )
        buttons = QtWidgets.QDialogButtonBox(_ok | _cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addWidget(buttons)

        _dlg_exec = getattr(dlg, "exec", None) or dlg.exec_
        if _dlg_exec() != QtWidgets.QDialog.Accepted:
            return
        path = path_edit.text().strip()
        if not path:
            return
        pack_assets = pack_cb.isChecked()
        try:
            written = self.session.export_layered(
                entries, path, pack_assets=pack_assets
            )
            self.session.clear_dirty()
            self._mirror_session()
            self._refresh_dirty_fonts()
            kind = "packed layered" if pack_assets else "layered"
            self._set_status(f"Wrote {kind}: {written}", "normal")
            msg = (
                f"Layered USDA written:\n{written}\n\n"
                + (
                    "Assets packed beside the main file "
                    "(one folder per layer, relative @ links)."
                    if pack_assets
                    else "Sublayers linked with relative @ paths (no copies)."
                )
            )
            QtWidgets.QMessageBox.information(self, "Exported", msg)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self, "Export failed", f"{exc}\n\n{traceback.format_exc()}"
            )

    def save_edit_layer(self):
        layer = self._edits_layer or self._edit_target_layer
        if not layer:
            self._set_status("No edit layer — compose first.", "error")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save edit / delta layer",
            os.getcwd(),
            "USD ASCII (*.usda);;USD Crate (*.usdc);;USD (*.usd)",
        )
        if not path:
            return
        try:
            export_layer(layer, path)
            self._clear_dirty()
            self._refresh_dirty_fonts()
            self._set_status(f"Wrote edit layer: {path}", "normal")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Save failed: {exc}", "error")


def main():
    # High-DPI before QApplication (PySide2 only; always on / deprecated in Qt6)
    if QtCore.__name__.startswith("PySide2"):
        _aa = getattr(QtCore.Qt, "AA_EnableHighDpiScaling", None)
        if _aa is not None:
            QtWidgets.QApplication.setAttribute(_aa, True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    themes.apply(app, "dark")
    app_icon = _usd_logo_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    win = StageExplorer()
    win.show()
    # Optional CLI USD paths → fill from base upward.
    # Ignore launcher tool tokens (e.g. argv "usd-lamina") — those are not files.
    args = []
    for a in sys.argv[1:]:
        if a.startswith("-"):
            continue
        low = a.lower()
        if low.endswith(_USD_SUFFIXES) or os.path.isfile(a):
            args.append(a)
    if args:
        # last CLI arg = base (bottom), earlier = stronger
        # Map onto slots so last goes to slot 5, previous to 4, …
        use = args[-NUM_LAYERS:]
        # pad left so last aligns to BASE
        padded = [""] * (NUM_LAYERS - len(use)) + use
        for slot, path in zip(win.slots, padded):
            if path:
                slot.path.setText(os.path.abspath(path))
        win.compose()
    _app_exec = getattr(app, "exec", None) or app.exec_
    return _app_exec()


if __name__ == "__main__":
    sys.exit(main())
