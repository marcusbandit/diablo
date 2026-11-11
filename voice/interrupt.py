import random
import re

RESUME_LINES = [
    "As for what you asked for before, sir.",
    "Returning to your earlier command, sir.",
    "Picking up where we left off, sir.",
    "To resume where I was cut short, sir.",
    "Back to the matter at hand, sir.",
    "If I may continue, sir.",
    "Resuming my previous report, sir.",
    "As I was saying, sir.",
    "Allow me to finish my earlier thought, sir.",
    "Onward with what you requested before, sir.",
]

BARGE_ACKS = [
    "I am sorry, sir.",
    "Yes, sir?",
    "Stopping, sir.",
    "Listening, sir.",
]

_LAUGHTER = re.compile(
    r"\b(?:ku+f[u*]+|ke+ke+|he+he+|ha+ha+|mwah+a+)\b[.,!?;:~]*\s*",
    re.IGNORECASE,
)
_STAGE_DIRECTIONS = re.compile(r"\*[^*]{0,60}\*")
_TILDES = re.compile(r"~+")


def pick():
    return random.choice(RESUME_LINES)


def barge_ack():
    return random.choice(BARGE_ACKS)


def remainder(text, elapsed, duration):
    if duration <= 0 or elapsed <= 0:
        return ""
    if elapsed >= duration * 0.9:
        return ""
    cut = len(text) * (elapsed / duration)
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        start = text.find(sentence)
        if start >= cut and sentence.strip():
            return text[start:].strip()
    return ""


_PUA = re.compile(r"[\ue000-\uf8ff]")

# His master understands English, Japanese, and Danish, nothing else.
# Danish counts Norwegian and Swedish as kin, since detection mixes them.
ALLOWED_LANGS = {"en", "da", "no", "sv"}

# Common English and Danish signals that beat a bad short-text guess.
_SIGNS = (
    " the ", " and ", " is ", " it ", " my ", " sir", " lord", " not ",
    " that ", " this ", " have ", " will ", " jeg ", " ikke", " og ",
    " har ", " det ", " til ", " med ", " for ",
)


def _has_kana(text):
    return any("\u3040" <= ch <= "\u30ff" for ch in text)


def _has_han(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _wrong_script(text):
    return any(
        "\u0400" <= ch <= "\u04ff"    # Cyrillic
        or "\uac00" <= ch <= "\ud7af"  # Hangul
        or "\u0600" <= ch <= "\u06ff"  # Arabic
        or "\u0590" <= ch <= "\u05ff"  # Hebrew
        or "\u0e00" <= ch <= "\u0e7f"  # Thai
        or "\u0370" <= ch <= "\u03ff"  # Greek
        or "\u0900" <= ch <= "\u097f"  # Devanagari
        or "\u0980" <= ch <= "\u09ff"  # Bengali
        or "\u0a00" <= ch <= "\u0a7f"  # Gurmukhi
        or "\u0a80" <= ch <= "\u0aff"  # Gujarati
        or "\u0b00" <= ch <= "\u0b7f"  # Oriya
        or "\u0b80" <= ch <= "\u0bff"  # Tamil
        or "\u0c00" <= ch <= "\u0c7f"  # Telugu
        or "\u0c80" <= ch <= "\u0cff"  # Kannada
        or "\u0d00" <= ch <= "\u0d7f"  # Malayalam
        for ch in text
    )


LANG_NAMES = {
    "en": "english", "da": "danish", "no": "norwegian", "sv": "swedish",
    "de": "german", "fr": "french", "es": "spanish", "it": "italian",
    "nl": "dutch", "pt": "portuguese", "hi": "hindi", "ur": "urdu",
    "ar": "arabic", "ru": "russian", "uk": "ukrainian", "pl": "polish",
    "tr": "turkish", "zh": "chinese", "zh-tw": "chinese", "ko": "korean",
    "ja": "japanese", "fi": "finnish", "el": "greek", "he": "hebrew",
    "th": "thai", "vi": "vietnamese", "id": "indonesian", "ro": "romanian",
    "hu": "hungarian", "cs": "czech", "sk": "slovak", "bg": "bulgarian",
    "mk": "macedonian", "sr": "serbian", "hr": "croatian", "sl": "slovenian",
    "lt": "lithuanian", "lv": "latvian", "et": "estonian", "ka": "georgian",
    "hy": "armenian", "az": "azerbaijani", "kk": "kazakh", "bn": "bengali",
    "ta": "tamil", "te": "telugu", "ml": "malayalam", "kn": "kannada",
    "gu": "gujarati", "pa": "punjabi", "mr": "marathi", "ne": "nepali",
    "fa": "persian", "sw": "swahili", "tl": "tagalog", "ca": "catalan",
}


def rejected_language(text):
    """None when the utterance may pass, else the name of the offending tongue."""
    if not wrong_language(text):
        return None
    guesses = _detect_langs(text)
    if guesses:
        return LANG_NAMES.get(guesses[0].lang, guesses[0].lang)
    return "unknown"


def _detect_langs(text):
    try:
        from langdetect import DetectorFactory, detect_langs
        DetectorFactory.seed = 0
        return detect_langs(text)
    except Exception:
        return []


def wrong_language(text):
    """True when the text is confidently in a language he must not speak."""
    text = text.strip()
    if len(text) < 12 or _has_kana(text):
        return False
    if _wrong_script(text):
        return True
    padded = f" {text.lower()} "
    if any(sign in padded for sign in _SIGNS):
        return False
    # Han without kana is usually Chinese: judge it directly, any length.
    if _has_han(text):
        guesses = _detect_langs(text)
        return bool(guesses) and guesses[0].lang not in ALLOWED_LANGS and guesses[0].prob >= 0.9
    if len(text) < 40:
        return False
    guesses = _detect_langs(text)
    if not guesses:
        return False
    top = guesses[0]
    if top.lang in ALLOWED_LANGS:
        return False
    if any(g.lang in ALLOWED_LANGS and g.prob >= 0.4 for g in guesses):
        return False
    return top.prob >= 0.9


def scrub(text):
    text = _LAUGHTER.sub("", text)
    text = _STAGE_DIRECTIONS.sub("", text)
    text = _TILDES.sub("", text)
    text = _PUA.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" \"'").strip()
