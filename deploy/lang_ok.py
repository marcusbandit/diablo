"""Language gate for Diablo's replies: English, Japanese, or Danish, nothing else.

Reads text on stdin, exits 0 when the reply may pass, 1 when it is the wrong
language. Detection is conservative: when uncertain, the reply passes, because
dropping a good reply is worse than a rare slip through. Japanese is allowed
whenever kana is present; Danish counts Norwegian and Swedish as kin.
"""

import sys

ALLOWED = {"en", "da", "no", "sv"}

# Common English and Danish signals that beat a bad short-text guess.
SIGNS = (
    " the ", " and ", " is ", " it ", " my ", " sir", " lord", " not ",
    " that ", " this ", " have ", " will ", " jeg ", " ikke", " og ",
    " har ", " det ", " til ", " med ", " for ",
)


def has_kana(text):
    return any("\u3040" <= ch <= "\u30ff" for ch in text)


def has_han(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def wrong_script(text):
    for ch in text:
        if (
            "\u0400" <= ch <= "\u04ff"   # Cyrillic
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
        ):
            return True
    return False


def detect(text):
    try:
        from langdetect import DetectorFactory, detect_langs
        DetectorFactory.seed = 0
        return detect_langs(text)
    except Exception:
        return []


def main():
    text = sys.stdin.read().strip()
    if not text or len(text) < 12:
        return 0
    if has_kana(text):
        return 0
    if wrong_script(text):
        return 1
    padded = f" {text.lower()} "
    if any(sign in padded for sign in SIGNS):
        return 0
    # Han without kana is usually Chinese: judge it directly, any length.
    if has_han(text):
        guesses = detect(text)
        bad = bool(guesses) and guesses[0].lang not in ALLOWED and guesses[0].prob >= 0.9
        if bad:
            print(f"wrong language: {guesses[0].lang}", file=sys.stderr)
        return 1 if bad else 0
    if len(text) < 40:
        return 0
    guesses = detect(text)
    if not guesses:
        return 0
    top = guesses[0]
    if top.lang in ALLOWED:
        return 0
    if any(g.lang in ALLOWED and g.prob >= 0.4 for g in guesses):
        return 0
    if top.prob < 0.9:
        return 0
    print(f"wrong language: {top.lang} ({top.prob:.2f})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
