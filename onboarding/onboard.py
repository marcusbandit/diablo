import argparse
import getpass
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tomllib
import urllib.request

import httpx

CONFIG_PATH = pathlib.Path.home() / ".config/diablo/config.toml"
PIPER_DIR = pathlib.Path.home() / ".local/share/diablo/piper"

PIPER_URL = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
VOICE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium"
VOICE_URLS = [
    (f"{VOICE_BASE}/en_GB-alan-medium.onnx?download=true", "voice.onnx"),
    (f"{VOICE_BASE}/en_GB-alan-medium.onnx.json?download=true", "voice.onnx.json"),
]

PROVIDERS = {
    "1": ("fireworks", "https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/glm-5p3-flash"),
    "2": ("groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "3": ("openai", "https://api.openai.com/v1", "gpt-4o-mini"),
    "4": ("openrouter", "https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct"),
    "5": ("together", "https://api.together.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "6": ("mistral", "https://api.mistral.ai/v1", "mistral-large-latest"),
    "7": ("deepseek", "https://api.deepseek.com/v1", "deepseek-chat"),
}

LOCAL_TEMPLATE = """hub_url = "{hub_url}"
hub_token = "{hub_token}"
gvoice_base = "http://127.0.0.1:8756"
gvoice_token_path = "~/.local/share/gvoice/token"
mic_device = "{mic_device}"
sample_rate = 16000
silero_path = "~/.local/share/diablo/silero_vad.onnx"
piper_dir = "~/.local/share/diablo/piper"
min_speech_s = 0.4
silence_tail_s = 0.7
max_utterance_s = 15.0
rms_floor = 0.0015
gate_calls_per_5min = 30
deaf_after_tts_s = 1.5
"""


def heading(text):
    print(f"\n== {text}")


def ask(label, default=""):
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def ask_secret(label, default=""):
    value = getpass.getpass(f"{label}: ") or default
    return value.strip()


def existing_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as handle:
            return tomllib.load(handle)
    return {}


def deps_check():
    heading("Dependencies")
    ok = True
    checks = [
        ("nvidia-smi", ["nvidia-smi", "-L"]),
        ("pactl", ["pactl", "info"]),
        ("ssh homehub", ["ssh", "-o", "ConnectTimeout=5", "homehub", "true"]),
    ]
    for name, cmd in checks:
        passed = subprocess.run(cmd, capture_output=True).returncode == 0
        print(f"{name}: {'ok' if passed else 'missing'}")
        ok = ok and passed
    if not ok:
        print("fix the missing pieces and rerun")
        sys.exit(1)


def choose_provider(old):
    heading("Brain provider")
    print("any openai-compatible provider works, the model can be swapped later at any time")
    for key, (name, _, default_model) in PROVIDERS.items():
        print(f"  {key}) {name} (default model {default_model})")
    print("  8) custom base url")
    old_provider = old.get("_provider_name", "")
    default_pick = next((k for k, v in PROVIDERS.items() if v[0] == old_provider), "")
    pick = ask("provider", default_pick)
    while pick not in PROVIDERS and pick != "8":
        pick = input("pick a number: ").strip()
    if pick == "8":
        base = ask("base url (openai-compatible, ends in /v1)")
        model = ask("default model name")
        key = ask_secret("api key")
        name = "custom"
    else:
        name, base, default_model = PROVIDERS[pick]
        model = ask("model", old.get("_llm_model", default_model))
        key = ask_secret(f"{name} api key", old.get("_llm_api_key", ""))
    gate = ask("gate model, judges if the mic heard you, enter to use the same model", model)
    return {"name": name, "base": base, "model": model, "key": key, "gate": gate}


def choose_transcription(llm):
    heading("Telegram voice note transcription")
    print("runs on the always-on hub when your pc is off")
    key = ask_secret("groq api key for whisper (free tier at console.groq.com, enter to reuse the brain key if it is groq)")
    if not key and llm["name"] == "groq":
        key = llm["key"]
    model = ask("transcription model", "whisper-large-v3-turbo")
    return {
        "transcription_base_url": "https://api.groq.com/openai/v1",
        "transcription_api_key": key,
        "transcription_model": model,
        **choose_local_whisper(),
    }


STT_TOKEN = pathlib.Path.home() / ".config/diablo-stt/token"
STT_PORT = 8757


def tailnet_address():
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.split()[0] if out.split() else ""


def choose_local_whisper():
    heading("Voice notes on your own GPU")
    print("while this pc is on, telegram voice notes are transcribed here by whisper on the gpu,")
    print("as fast as groq and without your voice leaving your machines. when the pc is off or")
    print("busy with a game, groq takes over on its own. it costs about 1.3 GB of vram, only for")
    print("ten minutes after a note.")
    if shutil.which("nvidia-smi") is None:
        print("this box has no nvidia gpu, so it cannot serve whisper; run onboarding on the gpu box")
        print("to turn this on. voice notes keep using groq, and the hub's local setting is left alone")
        return {}
    if not STT_TOKEN.is_file():
        if not ask("install the local whisper service now? y/n, n skips", "y").lower().startswith("y"):
            print("skipped. run deploy/stt-install.sh later, then onboarding again, to turn it on")
            return {}
        subprocess.run([str(pathlib.Path(__file__).resolve().parent.parent / "deploy/stt-install.sh")], check=False)
    if not STT_TOKEN.is_file():
        print("the install did not finish, voice notes keep using groq for now")
        return {}
    token = STT_TOKEN.read_text().strip()
    address = tailnet_address()
    if not address:
        print("this box has no tailnet address, so the hub cannot reach it; voice notes keep using groq")
        return {}
    url = f"http://{address}:{STT_PORT}/v1"
    try:
        answer = httpx.get(f"{url}/health", headers={"Authorization": f"Bearer {token}"}, timeout=5)
        print("local whisper answers, voice notes will use it" if answer.status_code == 200
              else f"local whisper answered {answer.status_code}; the hub falls back to groq until it is healthy")
    except httpx.HTTPError:
        print("local whisper is not answering yet; check systemctl --user status diablo-stt")
    return {"transcription.local.base_url": url, "transcription.local.api_key": token}


def choose_tts(old):
    heading("Diablo's voice")
    print("speaks on the desktop and replies to telegram voice notes")
    print("  1) cartesia sonic, premium quality")
    print("  2) openai tts")
    print("  3) none, local piper only")
    pick = ask("voice provider", old.get("_tts_provider", "1"))
    while pick not in ("1", "2", "3"):
        pick = input("pick 1, 2 or 3: ").strip()
    values = {"tts_provider": "none", "tts_api_key": "", "tts_model": "", "tts_voice_id": ""}
    if pick == "1":
        values["tts_provider"] = "cartesia"
        values["tts_api_key"] = ask_secret("cartesia api key", old.get("_tts_api_key", ""))
        values["tts_model"] = ask("cartesia model", "sonic-latest")
        values["tts_voice_id"] = pick_cartesia_voice(values["tts_api_key"], old.get("_tts_voice_id", ""))
    elif pick == "2":
        values["tts_provider"] = "openai"
        values["tts_api_key"] = ask_secret("openai api key", old.get("_tts_api_key", ""))
    print("tip: set a monthly spend cap for this key in the provider dashboard")
    return values


def pick_cartesia_voice(api_key, existing):
    if existing:
        reuse = ask(f"keep voice {existing}? enter to keep, type a name to change", "")
        if not reuse:
            return existing
    try:
        response = httpx.get(
            "https://api.cartesia.ai/voices",
            headers={"X-API-Key": api_key},
            timeout=30,
        )
        response.raise_for_status()
        voices = response.json().get("data", [])
    except (httpx.HTTPError, ValueError):
        print("could not list voices, paste a voice id later with the settings api")
        return ask("cartesia voice id", existing)
    for voice in voices[:20]:
        print(f"  {voice.get('name', '?')}: {voice.get('id', '?')}")
    wanted = ask("voice name to use")
    for voice in voices:
        if voice.get("name", "").lower() == wanted.lower():
            return voice["id"]
    return ask("no exact match, paste a voice id", existing)


def deploy_hub(hub_token):
    heading("Hub on homehub")
    subprocess.run(["ssh", "homehub", "mkdir -p diablo-hub"], check=True)
    script = subprocess.run(
        ["bash", "deploy/hub-install.sh"],
        env={**os.environ, "HUB_TOKEN": hub_token},
        check=False,
    )
    if script.returncode != 0:
        print("warning: hub install script failed, check the output above")
        return False
    return True


def push_settings(hub_url, hub_token, values):
    heading("Pushing settings to the hub")
    headers = {"X-Diablo-Token": hub_token}
    failed = False
    for key, value in values.items():
        try:
            response = httpx.put(
                f"{hub_url.rstrip('/')}/v1/settings/{key}",
                headers=headers,
                json={"value": str(value)},
                timeout=10,
            )
            response.raise_for_status()
        except (httpx.HTTPError, ValueError) as error:
            failed = True
            print(f"failed to set {key}: {error}")
    if failed:
        print("some settings did not land, rerun onboarding or use the settings api")
    else:
        print("all settings stored on the hub, keys live there now, not in any repo")


def download(url, target):
    print(f"downloading {url.rsplit('/', 1)[-1].split('?')[0]}")
    report = [0]

    def hook(blocks, block_size, total):
        done = blocks * block_size
        if done - report[0] > 25_000_000 or (total and done >= total):
            report[0] = done
            print(f"  {done / 1e6:.0f} MB")

    urllib.request.urlretrieve(url, target, reporthook=hook)


def install_piper():
    heading("Piper fallback voice")
    if (PIPER_DIR / "piper").exists() and (PIPER_DIR / "voice.onnx").exists():
        print("already installed")
        return
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    tarball = "/tmp/opencode/piper.tar.gz"
    download(PIPER_URL, tarball)
    tmp = pathlib.Path("/tmp/opencode/piper-extract")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    subprocess.run(["tar", "-xzf", tarball, "-C", str(tmp)], check=True)
    extracted = tmp / "piper"
    for item in extracted.iterdir():
        target = PIPER_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    for url, name in VOICE_URLS:
        download(url, PIPER_DIR / name)
    print(f"piper ready in {PIPER_DIR}")


def install_service():
    heading("Voice service")
    unit_dir = pathlib.Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2("deploy/diablo-voice.service", unit_dir / "diablo-voice.service")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "diablo-voice.service"], check=True)
    print("enabled diablo-voice.service, start it with: systemctl --user start diablo-voice")


FAST_UNIT = """[Unit]
Description=Diablo fast tongue
After=pipewire.service

[Service]
WorkingDirectory=%h/Projects/diablo
ExecStart=%h/Projects/diablo/.venv-voice/bin/python -m pool.panel
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""

STAGEHAND_UNIT = """[Unit]
Description=Diablo stagehand, keeps the stage standing
After=graphical-session.target

[Service]
ExecStart=%h/Projects/diablo/deploy/stagehand.sh
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_pool():
    heading("Pool, fast panel and stage")
    unit_dir = pathlib.Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2("deploy/diablo-pool.service", unit_dir / "diablo-pool.service")
    (unit_dir / "diablo-fast.service").write_text(FAST_UNIT)
    (unit_dir / "diablo-stagehand.service").write_text(STAGEHAND_UNIT)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for name in ("diablo-pool.service", "diablo-fast.service", "diablo-stagehand.service"):
        subprocess.run(["systemctl", "--user", "enable", name], check=False)
    print("the pool and the fast panel are enabled, the stagehand keeps the tmux stage standing")


def pick_mic():
    heading("Microphone")
    out = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True)
    source = out.stdout.strip()
    print(f"system default source: {source or 'unknown'}")
    return ask("device name substring to match, enter uses the system default", "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-hub", action="store_true")
    args = parser.parse_args()
    deps_check()
    old = existing_config()
    hub_token = old.get("hub_token") or secrets.token_hex(32)
    hub_url = ask("hub url", old.get("hub_url", "http://diablo-pi:8755"))
    llm = choose_provider(old)
    transcription = choose_transcription(llm)
    tts = choose_tts(old)
    telegram = ask_secret("telegram bot token (create a bot with @BotFather first)", old.get("_telegram", ""))

    settings = {
        "llm_base_url": llm["base"],
        "llm_api_key": llm["key"],
        "llm_model": llm["model"],
        "gate_model": llm["gate"],
        **transcription,
        **tts,
        "telegram_bot_token": telegram,
    }

    mic_device = pick_mic()
    heading("Config")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        LOCAL_TEMPLATE.format(hub_url=hub_url, hub_token=hub_token, mic_device=mic_device)
    )
    os.chmod(CONFIG_PATH, 0o600)
    print(f"wrote {CONFIG_PATH}")

    if args.skip_hub:
        print("\nskipped hub deploy, settings not pushed, run again without --skip-hub to push them")
    else:
        if deploy_hub(hub_token):
            push_settings(hub_url, hub_token, settings)
    install_piper()
    install_service()
    install_pool()
    print("\nDone. Where to talk to him:")
    print("  out loud:       just say his name, diablo, ...")
    print("  this terminal:  diablo agent in any repo, diablo talk for the tongue")
    print("  telegram:       your bot, any chat")
    print("Start everything: systemctl --user start diablo-voice diablo-fast diablo-pool")
    print("Change the brain anytime: /model <name> on telegram, or the settings api.")


if __name__ == "__main__":
    main()
