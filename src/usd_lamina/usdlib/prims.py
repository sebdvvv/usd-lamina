"""Prim-level helpers used by usd-lamina."""

from __future__ import annotations

from typing import Any

from pxr import Usd, UsdGeom

from .attr import attr_time_samples, get_attr_value
from .layers import _DEPT_KEYS, _TAG_KEYS, _VERSION_KEYS, _dict_get_ci


def _prim_metadata(prim: Usd.Prim | None) -> dict[str, Any]:
    """Read USD-native prim assetInfo / customData."""
    meta: dict[str, Any] = {
        "version": "",
        "opinion": "",
        "department": "",
        "tags": "",
        "raw": {},
    }
    if prim is None or not prim:
        return meta

    asset_info: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    try:
        asset_info = dict(prim.GetAssetInfo() or {})
    except Exception:  # noqa: BLE001
        pass
    try:
        custom = dict(prim.GetCustomData() or {})
    except Exception:  # noqa: BLE001
        pass
    meta["raw"] = {"assetInfo": asset_info, "customData": custom}

    meta["version"] = (
        _dict_get_ci(asset_info, _VERSION_KEYS)
        or _dict_get_ci(custom, _VERSION_KEYS)
    )
    meta["department"] = (
        _dict_get_ci(custom, _DEPT_KEYS)
        or _dict_get_ci(asset_info, _DEPT_KEYS)
    )
    meta["tags"] = (
        _dict_get_ci(custom, _TAG_KEYS)
        or _dict_get_ci(asset_info, _TAG_KEYS)
    )
    meta["opinion"] = _dict_get_ci(
        custom, ("opinion", "Opinion", "purpose", "role")
    ) or _dict_get_ci(asset_info, ("opinion", "purpose", "role"))

    try:
        kind = prim.GetMetadata("kind")
        if kind and not meta["opinion"]:
            meta["opinion"] = "kind:%s" % kind
    except Exception:  # noqa: BLE001
        pass

    return meta


def _iter_all_prim_children(prim: Usd.Prim):
    """Children including inactive (GetChildren() skips active=false)."""
    if hasattr(prim, "GetAllChildren"):
        return prim.GetAllChildren()
    return prim.GetFilteredChildren(Usd.PrimAllPrimsPredicate)


def prim_query_time(
    prim: Usd.Prim,
    preferred: Usd.TimeCode | None = None,
) -> Usd.TimeCode:
    """Stage/prim time for xforms & cameras (honours time-sampled-only ops)."""
    if preferred is not None and not preferred.IsDefault():
        return preferred
    earliest: float | None = None
    try:
        xformable = UsdGeom.Xformable(prim)
        if xformable:
            for op in xformable.GetOrderedXformOps():
                samples = attr_time_samples(op.GetAttr())
                if samples:
                    t0 = samples[0]
                    earliest = t0 if earliest is None else min(earliest, t0)
    except Exception:  # noqa: BLE001
        pass
    if earliest is not None:
        return Usd.TimeCode(earliest)
    try:
        stage = prim.GetStage()
        if stage is not None:
            return Usd.TimeCode(stage.GetStartTimeCode())
    except Exception:  # noqa: BLE001
        pass
    return Usd.TimeCode.Default()


def prim_visibility_label(prim: Usd.Prim) -> str:
    img = UsdGeom.Imageable(prim)
    if not img:
        return "—"
    attr = img.GetVisibilityAttr()
    if not attr:
        return "—"
    val, _tc = get_attr_value(attr)
    return str(val) if val is not None else "—"


def _classify_map_prim(prim: Usd.Prim) -> str:
    """Return ``camera`` | ``char`` | ``set`` | ``other`` from type / path / kind."""
    if not prim or not prim.IsValid():
        return "other"
    if prim.IsA(UsdGeom.Camera):
        return "camera"
    bits = " ".join(
        [
            str(prim.GetPath()).lower(),
            (prim.GetName() or "").lower(),
            str(prim.GetTypeName() or "").lower(),
        ]
    )
    try:
        kind = str(prim.GetMetadata("kind") or "").lower()
    except Exception:  # noqa: BLE001
        kind = ""
    bits = f"{bits} {kind}"
    try:
        info = prim.GetAssetInfo() or {}
        for k in ("assetType", "type", "entity", "category", "assetName"):
            if k in info:
                bits += f" {str(info[k]).lower()}"
    except Exception:  # noqa: BLE001
        pass

    char_tokens = (
        "character",
        "characters",
        "/char/",
        "/chars/",
        "char_",
        "_char",
        "creature",
    )
    set_tokens = (
        "setdress",
        "/sets/",
        "/set/",
        "setprop",
        "setprops",
        "environment",
        "/env/",
        "layout",
    )
    if any(t in bits for t in char_tokens):
        return "char"
    if any(t in bits for t in set_tokens):
        return "set"
    return "other"
