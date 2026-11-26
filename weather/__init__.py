"""天气插件后端 / Weather plugin backend."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = PACKAGE_ROOT.parent
DATA_DIR = PLUGIN_ROOT / "data"
LOTTIE_DIR = PLUGIN_ROOT / "assets" / "lottie"

__all__ = ["DATA_DIR", "LOTTIE_DIR", "PACKAGE_ROOT", "PLUGIN_ROOT"]
