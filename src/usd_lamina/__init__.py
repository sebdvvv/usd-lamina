"""usd-lamina — compose / inspect / edit layered USD stages.

Core (no Qt)::

    from usd_lamina import LayerViewSession

UI launch::

    python -m usd_lamina
"""

from __future__ import annotations

from usd_lamina.core import (
    COMPOSE_MODES,
    MAX_LOAD_FILES,
    NUM_LAYERS,
    LayerViewSession,
    collect_prim_rows,
    find_prims_bound_to_material,
    slot_strength_label,
)

__all__ = [
    "COMPOSE_MODES",
    "MAX_LOAD_FILES",
    "NUM_LAYERS",
    "LayerViewSession",
    "collect_prim_rows",
    "find_prims_bound_to_material",
    "slot_strength_label",
    "main",
]


def main() -> int:
    """Launch the Qt UI."""
    from usd_lamina.ui import main as ui_main

    return int(ui_main() or 0)
