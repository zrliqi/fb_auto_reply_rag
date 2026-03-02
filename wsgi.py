from pathlib import Path
import sys

# Ensure repository root is on sys.path for Render/Gunicorn import resolution.
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot_app import create_app

app = create_app()
