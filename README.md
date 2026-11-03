# usd-lamina

**USD L.A.M.I.N.A.** — Layer Assembly · Map · Inspection · Navigation · Authoring

Desktop tool to **compose, inspect, diagnose, and edit layered OpenUSD stages**.

Drop USD files onto a strongest-to-weakest stack (TOP → BASE), compose them as
sublayers / references / payloads, browse prims, attributes, materials, and a
2D scene map, then save or export flattened, referenced, payload, or packed
layered USDA.

<img width="1045" height="702" alt="2026-11-03_14-24-12" src="https://github.com/user-attachments/assets/9e9880d9-564c-42b6-8ba1-a0df0f338dbf" />

## Requirements

- **Python 3.11+**
- **PySide6** (Qt 6) — default. PySide2 still works on Python 3.10 if you need it.
- **OpenUSD** (`pxr`) on `PYTHONPATH` (NVIDIA / Pixar / conda-forge build)

OpenUSD is **not** installed by pip in this project: use the USD build that
matches your DCC, or a public wheel such as `usd-core` if it covers
the modules you need (`Usd`, `Sdf`, `UsdGeom`, `UsdShade`, `UsdUtils`).

## Install

```bash
python -m pip install -e .
usd-lamina
```

Or run from a clone without installing:

**Linux / macOS**

```bash
chmod +x usd-lamina
./usd-lamina
# or:
PYTHONPATH=src python3 -m usd_lamina
```

**Windows**

Double-click `usd-lamina.bat` (uses `pythonw` so no console window). From a terminal:

```bat
usd-lamina.bat
set PYTHONPATH=src && python -m usd_lamina
```

CLI paths fill slots from the **base upward** (last argument = BASE):

```bash
usd-lamina ./strong.usda ./base.usda
```

## Features

- Six-slot layer stack with compose modes: sublayer, reference, payload
- Scene hierarchy, attribute tree, material / shader network
- Orthographic scene map (Top / Front / Side)
- Session edits on an anonymous over layer
- Export: flattened, referenced, payload, packed layered USDA

## License

Apache License 2.0 — see [LICENSE](LICENSE).
