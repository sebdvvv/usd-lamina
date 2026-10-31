"""Filesystem / asset-path helpers."""

from __future__ import annotations

import os
import stat

def clean_path(path: str) -> str:
    """Normalize a filesystem path string (strip quotes / whitespace)."""
    return os.path.normpath(path.strip().strip('"')) if path and path.strip() else ""


def path_key(path: str) -> str:
    """Case-normalized absolute path key for duplicate detection."""
    return os.path.normcase(os.path.abspath(clean_path(path)))


def path_relative_to_dir(abs_path: str, out_dir: str) -> str:
    """Filesystem path → USD-friendly relative path from ``out_dir``."""
    abs_path = os.path.normpath(abs_path)
    out_dir = os.path.normpath(out_dir or os.getcwd())
    try:
        rel = os.path.relpath(abs_path, out_dir)
    except ValueError:
        rel = abs_path
    return rel.replace("\\", "/")


def asset_path_without_args(asset_path: str) -> str:
    """Strip ``:SDF_FORMAT_ARGS:…`` suffix from an Sdf asset path."""
    if not asset_path:
        return ""
    marker = ":SDF_FORMAT_ARGS:"
    if marker in asset_path:
        return asset_path.split(marker, 1)[0]
    return asset_path


def set_path_writable(path: str) -> None:
    """Ensure an existing file is writable so export can overwrite it."""
    if not path or not os.path.isfile(path):
        return
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IWUSR | stat.S_IWRITE)
    except OSError:
        pass
