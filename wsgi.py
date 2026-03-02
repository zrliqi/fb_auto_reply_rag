from pathlib import Path
import sys
import os
import importlib

# Ensure repository root is on sys.path for Render/Gunicorn import resolution.
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

create_app = None
import_errors = []

for module_name in ("bot_app", "src.bot_app"):
    try:
        module = importlib.import_module(module_name)
        create_app = getattr(module, "create_app", None)
        if create_app is not None:
            break
    except Exception as exc:
        import_errors.append(f"{module_name}: {exc}")

if create_app is None:
    cwd = os.getcwd()
    try:
        root_files = sorted(os.listdir(BASE_DIR))
    except Exception:
        root_files = []
    raise RuntimeError(
        "Unable to import create_app from bot_app package. "
        f"cwd={cwd}, base_dir={BASE_DIR}, files={root_files}, errors={import_errors}"
    )

app = create_app()
