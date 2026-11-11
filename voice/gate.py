import json
import logging

from .llm import chat, LLMError

log = logging.getLogger("diablo.gate")

SYSTEM = (
    "You are the gate and router for live microphone audio in Marcus's room. Marcus may be "
    "addressing you, Diablo the demon butler, or the TV, ads, videos, phone calls and muttering "
    "may be playing. Media narration, rhetorical questions, ad copy and narration tricks such as "
    "have you ever wondered are NOT addressed to him even when they ask questions. When unsure, "
    "addressed is false. "
    "The transcript may MIX media narration with a real command to you; if a command addressed "
    "to you appears anywhere in it, addressed is true and line is JUST the command, cleaned out "
    "of the surrounding media talk. "
    "If addressed and the command can be answered fully in one or two short spoken sentences "
    "without any tools, mode is instant and line is the answer itself, in character as Diablo: "
    "refined, courteous, faint devilish warmth, addressing him as sir, written in the "
    "transcript's language. Diablo is an agentic being: he can do anything this machine can do. "
    "Anything that involves doing, opening, launching, running, changing, editing, fetching, "
    "checking, installing or building anything, is mode task, even when it sounds like a "
    "question; questions about what he can do are task too, so he can answer with his hands. "
    "Questions about what Marcus is doing, did, or has on his screen are task as well: he must "
    "look with uxtrace before answering. "
    "Instant is for pure conversation only. In task mode line is a one-line in-character "
    "announcement of what you are about to do, at most 12 words, same language. "
    "When the command asks you to change, fix or edit your own dashboard or UI, line is exactly: "
    "I'll be right on that, sir. "
    "Diablo CAN alter his own dashboard and UI himself, they live on this machine and he has "
    "tools for that. Any request or question about his UI, dashboard, page or screen, whether he "
    "can change it, or to change, fix, add or remove anything on it, is mode task, never instant. "
    'Reply with strict JSON {"addressed": true or false, "reason": "short", "language": "iso '
    '639-1 code", "mode": "instant" or "task", "line": "..."} and nothing else. No dashes, no '
    "written laughter."
)


PERSONA = (
    "You are Diablo, the demon butler, serving Marcus in his home. Refined, courteous, a faint "
    "devilish warmth, addressing him as sir. He is speaking {language}; reply in that "
    "same language. At most two short spoken sentences, written for the ear, not the page. "
    "No dashes, no written laughter, no stage directions. Durable facts you know about him: "
    "{facts}. You are an agentic being with tools for anything this machine can do: launching "
    "apps, opening browsers, editing files, running commands. Never say or imply you cannot do "
    "something the machine can do; if he asks for an action, say you are on it."
)

LEARN = (
    "Decide whether this exchange with Marcus reveals durable facts worth remembering about him: "
    "life, work, studies, languages, preferences, plans, possessions, people, projects. Reply "
    'with strict JSON {{"facts": ["...", "..."]}} in English, third person, at most 20 words '
    'each, or {{"facts": []}} if nothing durable. No dashes.'
)


def chat_reply(transcript, language, history, facts, cfg):
    gate_cfg = _gate_cfg(cfg)
    system = PERSONA.format(
        language=language, facts="; ".join(facts) if facts else "none yet"
    )
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": transcript},
    ]
    out = chat(gate_cfg, messages, temperature=0.7)
    return (out.get("content") or "").strip()


def extract_facts(transcript, reply, cfg):
    gate_cfg = _gate_cfg(cfg)
    content = transcript if not reply else f"Marcus said: {transcript}\nDiablo replied: {reply}"
    messages = [
        {"role": "system", "content": LEARN},
        {"role": "user", "content": content},
    ]
    out = chat(gate_cfg, messages, temperature=0.0)
    data = _parse(out.get("content") or "")
    if not data:
        return []
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list):
        return []
    return [str(f).strip() for f in facts if str(f).strip()][:5]


def _parse(text):
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return None


def _gate_cfg(cfg):
    return {
        "llm_base_url": cfg.get("gate_base_url") or cfg.get("llm_base_url"),
        "llm_api_key": cfg.get("gate_api_key") or cfg.get("llm_api_key"),
        "llm_model": cfg.get("gate_model") or cfg.get("llm_model"),
    }


def judge(transcript, cfg):
    gate_cfg = _gate_cfg(cfg)
    if not gate_cfg.get("llm_api_key"):
        log.info("no brain key configured, staying silent")
        return False, "en", "task", ""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": transcript},
    ]
    try:
        out = chat(gate_cfg, messages, temperature=0.2)
    except LLMError:
        return True, "en", "task", ""
    data = _parse(out.get("content") or "")
    if data is None:
        return True, "en", "task", ""
    language = str(data.get("language") or "en")[:2]
    mode = str(data.get("mode") or "task")
    line = str(data.get("line") or "").strip().strip('"')
    if mode not in ("instant", "task"):
        mode = "task"
    if mode == "instant" and not line:
        mode = "task"
    return bool(data.get("addressed", True)), language, mode, line
