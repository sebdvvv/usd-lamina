"""USD attribute helpers."""

from __future__ import annotations

import ast
from typing import Any

from pxr import Sdf, Usd

try:
    from pxr import UsdShade
except ImportError:  # pragma: no cover
    UsdShade = None  # type: ignore


def _shade_connect_label(src: Any) -> str:
    """Format UsdShade connection source as a short string."""
    if not src:
        return "<unconnected>"
    try:
        source, base_name, source_type = src
        path = (
            source.GetPath().pathString
            if hasattr(source, "GetPath")
            else str(source)
        )
        return f"{path}.{source_type}:{base_name}"
    except Exception:  # noqa: BLE001
        return repr(src)


def attr_time_samples(attr: Usd.Attribute) -> list[float]:
    """Return authored time-sample times (empty if none / error)."""
    if not attr:
        return []
    try:
        samples = attr.GetTimeSamples()
        return [float(t) for t in samples] if samples else []
    except Exception:  # noqa: BLE001
        return []


def attr_effective_time(
    attr: Usd.Attribute,
    preferred: Usd.TimeCode | None = None,
) -> Usd.TimeCode:
    """Pick a time that yields a value."""
    if preferred is not None and not preferred.IsDefault():
        return preferred
    try:
        if attr.Get(Usd.TimeCode.Default()) is not None:
            return Usd.TimeCode.Default()
    except Exception:  # noqa: BLE001
        pass
    samples = attr_time_samples(attr)
    if samples:
        return Usd.TimeCode(samples[0])
    try:
        stage = attr.GetPrim().GetStage()
        if stage is not None:
            return Usd.TimeCode(stage.GetStartTimeCode())
    except Exception:  # noqa: BLE001
        pass
    return preferred if preferred is not None else Usd.TimeCode.Default()


def get_attr_value(
    attr: Usd.Attribute,
    time: Usd.TimeCode | None = None,
) -> tuple[Any, Usd.TimeCode]:
    """``(value, time_used)`` — resolves time samples when default is empty."""
    if not attr:
        return None, Usd.TimeCode.Default()
    tc = attr_effective_time(attr, time)
    try:
        val = attr.Get(tc)
    except Exception:  # noqa: BLE001
        return None, tc
    if val is None:
        for t in attr_time_samples(attr):
            try:
                val = attr.Get(Usd.TimeCode(t))
            except Exception:  # noqa: BLE001
                continue
            if val is not None:
                return val, Usd.TimeCode(t)
    return val, tc


def format_attr_value(
    attr: Usd.Attribute,
    time: Usd.TimeCode | None = None,
) -> str:
    if not attr:
        return "<no value>"
    has_default = False
    try:
        has_default = attr.HasAuthoredValueOpinion() and attr.Get(
            Usd.TimeCode.Default()
        ) is not None
    except Exception:  # noqa: BLE001
        pass
    samples = attr_time_samples(attr)
    if not has_default and not samples and not attr.HasValue():
        return "<no value>"
    try:
        val, tc = get_attr_value(attr, time)
        if val is None:
            if samples:
                return f"<no value @ default; {len(samples)} time sample(s)>"
            return "<no value>"
        text = repr(val)
        if not tc.IsDefault():
            text += f"  @ t={tc.GetValue():g}"
        if len(samples) > 1:
            text += f"  ({len(samples)} samples)"
        elif len(samples) == 1 and tc.IsDefault():
            text += f"  (+sample @ t={samples[0]:g})"
        return text
    except Exception as exc:  # noqa: BLE001
        return f"<error: {exc}>"


def format_attr_display(
    attr: Usd.Attribute,
    time: Usd.TimeCode | None = None,
) -> str:
    """Value for the attr tree, showing connections when authored."""
    conns: list[str] = []
    if attr:
        try:
            if attr.HasAuthoredConnections():
                conns = [str(p) for p in attr.GetConnections()]
        except Exception:  # noqa: BLE001
            pass
        if not conns and UsdShade is not None:
            try:
                if UsdShade.ConnectableAPI.HasConnectedSource(attr):
                    src = UsdShade.ConnectableAPI.GetConnectedSource(attr)
                    if src:
                        conns = [_shade_connect_label(src)]
            except Exception:  # noqa: BLE001
                pass
    if conns:
        link = ", ".join(conns)
        if attr.HasAuthoredValueOpinion():
            return f"{format_attr_value(attr, time)}  ← {link}"
        return f"← {link}"
    return format_attr_value(attr, time)


def parse_attr_input(attr: Usd.Attribute, raw: str) -> Any:
    """Parse dialog text into a value matching ``attr``'s USD type."""
    text = (raw or "").strip()
    tn = attr.GetTypeName()
    if tn in (Sdf.ValueTypeNames.String, Sdf.ValueTypeNames.Token):
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
            try:
                return ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return text[1:-1]
        return text
    if tn == Sdf.ValueTypeNames.Bool:
        low = text.lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        return bool(ast.literal_eval(text))
    int_types = [
        Sdf.ValueTypeNames.Int,
        Sdf.ValueTypeNames.Int64,
        Sdf.ValueTypeNames.UInt,
        Sdf.ValueTypeNames.UInt64,
        Sdf.ValueTypeNames.UChar,
    ]
    float_types = [
        Sdf.ValueTypeNames.Float,
        Sdf.ValueTypeNames.Double,
    ]
    half = getattr(Sdf.ValueTypeNames, "Half", None)
    if half is not None:
        float_types.append(half)
    if tn in int_types:
        return int(text, 0) if text.lower().startswith("0x") else int(text)
    if tn in float_types:
        return float(text)
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def attr_value_for_dialog(
    attr: Usd.Attribute,
    time: Usd.TimeCode | None = None,
) -> str:
    """Current value as editable dialog text (friendly for scalars)."""
    val, _tc = get_attr_value(attr, time)
    if val is None:
        return ""
    tn = attr.GetTypeName()
    if tn in (Sdf.ValueTypeNames.String, Sdf.ValueTypeNames.Token):
        return str(val)
    if tn == Sdf.ValueTypeNames.Bool:
        return "true" if val else "false"
    return repr(val) if not isinstance(val, (int, float)) else str(val)
