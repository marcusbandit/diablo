import os
import tomllib

DEFAULT_CONFIG_PATH = os.path.expanduser("~/diablo-hub/config.toml")

_config = None


def config_path():
    return os.environ.get("DIABLO_HUB_CONFIG", DEFAULT_CONFIG_PATH)


def load_config(path=None):
    path = path or config_path()
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config
