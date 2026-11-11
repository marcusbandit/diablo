from . import db, localenv

DEFAULTS = {
    "llm_base_url": "https://api.fireworks.ai/inference/v1",
    "llm_api_key": "",
    "llm_model": "accounts/fireworks/models/glm-5p3-flash",
    "gate_base_url": "",
    "gate_api_key": "",
    "gate_model": "",
    "transcription_base_url": "https://api.groq.com/openai/v1",
    "transcription_api_key": "",
    "transcription_model": "whisper-large-v3-turbo",
    "transcription.backends": "local,cloud",
    "transcription.local.base_url": localenv.get("DIABLO_STT_URL") or f"http://{localenv.host('banditbox')}:8757/v1",
    "transcription.local.model": "large-v3-turbo",
    "transcription.local.health": "on",
    "transcription.local.timeout": "60",
    "transcription.cloud.health": "off",
    "tts_provider": "none",
    "tts_api_key": "",
    "tts_model": "sonic-latest",
    "tts_voice_id": "",
    "speech_rate": "1.0",
    "ack_base_url": "",
    "ack_api_key": "",
    "ack_model": "",
    "rank.imp": "",
    "rank.baron": "",
    "rank.marquis": "",
    "rank.duke": "",
    "rank.lord": "",
    "telegram_bot_token": "",
    "speaking_mode": "off",
    "brain.backend": "claude",
    "brain.claude.model": "claude-sonnet-5-5",
    "brain.claude.effort": "medium",
    "brain.opencode.model": "fireworks-ai/accounts/fireworks/models/glm-5p3-flash",
    "brain.fallback": "api",
    "restart.allowed": "on",
    "restart.min_interval_s": "300",
    "t3.environments": "nemurenai,banditbox,diablo-pi,kangaeru",
    "network.home_subnet": localenv.home_subnet(),
    "network.phone": "",
    "network.kinds": "",
    "fleet.url": localenv.get("DIABLO_FLEET_URL") or f"http://{localenv.host('nemurenai')}:8780",
    "lock.enabled": "on",
    "lock.host": "banditbox",
    "ears.silence_s": "2.0",
    "ears.silence_open_s": "3.5",
    "ears.silence_short_s": "1.0",
    "ears.turn_max_s": "90",
    "ears.adaptive": "1",
}


def get(key):
    value = db.get_setting(key)
    if value is None or value == "":
        return DEFAULTS.get(key, "")
    return value


def get_all():
    merged = dict(DEFAULTS)
    for row in db.get_all_settings():
        merged[row["key"]] = row["value"]
    return merged


def set_setting(key, value):
    db.set_setting(key, value)
