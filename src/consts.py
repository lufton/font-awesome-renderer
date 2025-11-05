import tomllib
from pathlib import Path

ASSETS_FOLDER = Path("assets")
BUILD_FOLDER = Path("build")
CONFIG_FOLDER = Path("config")
CONFIG_PATH = CONFIG_FOLDER / "config.json"
OUTPUT_FOLDER = Path("output")
OUTPUT_ICONS_FOLDER = OUTPUT_FOLDER / "icons"
OUTPUT_MANIFEST_PATH = OUTPUT_FOLDER / "manifest.json"
SCALE_FACTOR = 5
STREAM_DECK_CONFIG_PATH = CONFIG_FOLDER / "stream_deck.json"
STREAM_DOCK_CONFIG_PATH = CONFIG_FOLDER / "stream_dock.json"
PROJECT_CONFIG = tomllib.load(open("pyproject.toml", "rb"))["project"]
PROJECT_NAME = PROJECT_CONFIG["name"]
PROJECT_VERSION = PROJECT_CONFIG["version"]