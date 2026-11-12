import os
import tomllib
from pathlib import Path

DEFAULTS = {
    "hub_url": "http://diablo-pi:8755",
    "hub_token": "",
    "gvoice_base": "http://127.0.0.1:8756",
    "gvoice_token_path": "~/.local/share/gvoice/token",
    "stt_via": "gvoice",
    "mic_device": "",
    "sample_rate": 16000,
    "silero_path": "~/.local/share/diablo/silero_vad.onnx",
    "piper_dir": "~/.local/share/diablo/piper",
    "min_speech_s": 0.4,
    "silence_tail_s": 1.2,
    "max_utterance_s": 20.0,
    "join_window_s": 0.8,
    "rms_floor": 0.0008,
    "voice_rms_floor": 0.0018,
    "env_decay": 0.94,
    "release_floor": 0.004,
    "meter_ceiling": 0.05,
    "voiceprint": True,
    "voiceprint_min": 0.70,
    "engage_window_s": 45.0,
    "gate_calls_per_5min": 30,
    "home_place": "",
    "home_name": "",
    "home_lat": "",
    "home_lon": "",
    "bus_stop_id": "",
    "bus_stop_name": "",
    "bus_line": "",
    "bus_toward": "",
    "pizza_url": "",
    "camera_device": "/dev/video0",
    "search_url": "https://duckduckgo.com/?q={q}",
}


def load_config():
    cfg = dict(DEFAULTS)
    path = Path(os.path.expanduser("~/.config/diablo/config.toml"))
    if path.exists():
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        for key, value in data.items():
            if key in cfg:
                cfg[key] = value
    for key in ("gvoice_token_path", "silero_path", "piper_dir"):
        cfg[key] = os.path.expanduser(str(cfg[key]))
    return cfg
