"""2D / orthographic viewport helpers (no Qt)."""

from __future__ import annotations

import math

from pxr import Usd, UsdGeom

_MAP_VIEWS = ("Top", "Front", "Side")
MAP_VIEWS = _MAP_VIEWS


def _stage_up_axis(stage: Usd.Stage) -> str:
    try:
        return str(UsdGeom.GetStageUpAxis(stage) or UsdGeom.Tokens.y)
    except Exception:  # noqa: BLE001
        return "Y"


def _nice_scale_step(raw: float) -> float:
    """Pick a 1/2/5×10^n step near ``raw`` (world units)."""
    if raw <= 0:
        return 1.0
    exp = math.floor(math.log10(raw))
    base = 10**exp
    for m in (1.0, 2.0, 5.0, 10.0):
        if m * base >= raw:
            return m * base
    return 10.0 * base


def _project_world_to_map(
    x: float,
    y: float,
    z: float,
    up_axis: str,
    view: str = "Top",
) -> tuple[float, float]:
    """World → map plane for Top / Front / Side.

    Qt GraphicsView has +Y downward; the second return value is negated so
    world elevation / depth reads upward on screen.
    """
    v = (view or "Top").strip().lower()
    z_up = str(up_axis).upper().startswith("Z")
    if v == "top":
        return float(x), float(-y)
    if v == "front":
        return float(x), float(-z)
    if z_up:
        return float(-z), float(-y)
    return float(-y), float(-z)
