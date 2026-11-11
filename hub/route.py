"""Deterministic tier router: picks the brain tier for every turn before the brain runs.

Ported from the Jorbis voice tree router (2026-10-29). No network, no LLM: regex
features over the text, stage 1 picks a subject, stage 2 is a small tree per
subject that picks a tier. About 100 microseconds a turn.

    features(text) -> SUBJECT_TREE -> subject -> DECISION_TREES[subject] -> Decision(tier, reason)

Tiers map to a claude model and effort through settings (route.tier.<name> =
"model|effort"), so a cheap "turn off the lights" runs on haiku and "prove this
theorem" on opus, all inside the one warm session (brains switches the model
with a control request, no respawn).

Precedence in route():
1. Explicit phrases ("think hard", "use opus", "ultrathink", Danish too) win and
   skip the ceiling.
2. A short follow-up ("yes do it") inherits the conversation's last tier for 15
   minutes, so the build that was just discussed is not done on haiku.
3. The tree tier, capped at route.max_tier (default chat) and floored at
   route.min_tier (default none).

Settings: route.enabled (on/off; off still computes and logs the decision),
route.max_tier, route.min_tier, route.tier.<tier>. Log: ~/.local/state/diablo/route.jsonl.
Trees are data at the bottom of this file; `python -m hub.route show` prints them,
`python -m hub.route classify TEXT` explains one line.
"""

import json
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import settings

TIERS = ("fast", "voice", "standard", "chat", "deep")
TIER_DEFAULTS = {
    "fast": "claude-haiku-5-5|low",
    "voice": "claude-sonnet-5-5|low",
    "standard": "claude-sonnet-5-5|medium",
    "chat": "claude-opus-5-5|medium",
    "deep": "claude-opus-5-5|high",
}
STICKY_SECONDS = 900
LOG = Path.home() / ".local" / "state" / "diablo" / "route.jsonl"


def rank(tier):
    return TIERS.index(tier)


def enabled():
    return str(settings.get("route.enabled") or "on").strip().lower() not in ("0", "off", "false", "no")


def _setting_tier(key, default=None):
    raw = str(settings.get(key) or "").strip().lower()
    return raw if raw in TIERS else default


def model_effort(tier):
    """(model, effort) for a tier, from route.tier.<tier> or the defaults."""
    raw = str(settings.get(f"route.tier.{tier}") or TIER_DEFAULTS[tier])
    model, _, effort = raw.partition("|")
    default_model, _, default_effort = TIER_DEFAULTS[tier].partition("|")
    return model.strip() or default_model, effort.strip() or default_effort


# =========================================================== features
def _alt(*parts):
    body = "|".join(p.replace(" ", r"\s+") for p in parts)
    return re.compile(r"(?<!\w)(?:" + body + r")(?!\w)")


_WAKE_RE = re.compile(r"^(?:(?:hey|hi|ok|okay|hej|yo)\s+)?(?:diablo|dia)\b[\s,.!:]*")
_PREFIX_RE = re.compile(r"^\[[^\]]*\]\s*")
_PUNCT_RE = re.compile(r"[^\w\s'?%+*/=-]")
_POLITE_RE = re.compile(
    r"^(?:please |pls |so )?(?:can|could|would|will|wont|won't) you(?: please)?\b|^please\b|"
    r"^(?:kan|vil|gider|kunne|ville) du\b|^(?:i'd|i would) like you to\b|^i want you to\b|^i need you to\b"
)
_WH = frozenset((
    "what", "what's", "whats", "who", "who's", "when", "where", "where's", "why", "how", "how's", "which",
    "is", "are", "am", "do", "does", "did", "was", "were", "have", "has", "should", "shall",
    "hvad", "hvem", "hvornår", "hvor", "hvorfor", "hvordan", "hvilken", "hvilket", "hvilke",
    "er", "har", "skal", "må", "kan", "vil",
))
_IMPERATIVE = frozenset((
    "turn", "switch", "set", "add", "remind", "play", "pause", "stop", "start", "show", "open", "close",
    "tell", "give", "find", "search", "look", "read", "send", "write", "make", "build", "fix", "create",
    "check", "restart", "reboot", "move", "delete", "remove", "install", "update", "put", "dim", "mute",
    "call", "schedule", "implement", "research", "explain", "summarize", "summarise", "compare",
    "buy", "order", "go", "let", "lets", "let's", "change", "deploy", "run", "kill", "edit", "draft",
    "wake", "suspend", "upgrade", "port", "prove", "derive", "solve", "calculate", "help", "plan", "figure", "design", "debug",
    "bevis", "udled", "løs", "beregn", "hjælp", "planlæg",
    "tænd", "sluk", "sæt", "tilføj", "afspil", "vis", "åbn", "luk", "fortæl", "giv", "søg",
    "læs", "skriv", "lav", "byg", "ret", "fiks", "opret", "genstart", "flyt", "slet", "installer",
    "opdater", "dæmp", "mind", "køb", "bestil", "forklar", "sammenlign", "undersøg", "skru", "kør",
))

_HOME_RE = _alt(
    r"lights?", r"lamps?", r"bulbs?", "hue", r"plugs?", "tv", "volume", "louder", "quieter",
    "brightness", r"bright(?:er)?", r"dim(?:mer)?", r"darker", r"speakers?", "spotify", "music",
    r"songs?", "playlist", "pause", "skip", "next track",
    "turn (?:it |them |that |this )?(?:on|off|up|down)", "switch (?:it |them )?(?:on|off)",
    r"lys(?:et|ene)?", r"lamper?(?:n|ne)?", r"lampen", "fjernsyn(?:et)?", "lydstyrke(?:n)?",
    r"højttaler(?:en|ne)?", "musik(?:ken)?", r"tænd(?:e)?", r"sluk(?:ke)?", r"skru (?:op|ned)",
    r"lysere", r"mørkere", r"dæmp(?:e)?", r"warmer", r"cooler", r"colder", r"varmere", r"koldere",
    r"red", r"orange", r"yellow", r"green", r"cyan", r"blue", r"purple", r"pink", r"magenta",
    r"rød", r"gul", r"grøn", r"blå", r"lilla",
)
_INFO_RE = _alt(
    r"weather", r"vejr(?:et)?", r"temperature", r"temperatur(?:en)?", r"rain(?:ing)?", r"regn(?:er)?",
    r"forecast", r"what time", r"time is it", r"klokken", r"what day", r"today's date",
    r"dato(?:en)?", r"news", r"nyheder(?:ne)?", r"how far", r"distance", r"directions?", r"get to",
    r"how do i get", r"vej(?:en)? til", r"who (?:is|was|are)", r"hvem (?:er|var)", r"when (?:is|does|did)",
    r"what (?:is|are|was|does)", r"what's", r"hvad (?:er|betyder)", r"define", r"definition", r"meaning of",
    r"betyder", r"translate", r"oversæt", r"lectures?", r"forelæsning(?:er)?", r"skema(?:et)?",
    r"timetable", r"bus", r"train", r"tog(?:et)?", r"flights?", r"capital of", r"population",
    r"how (?:old|tall|big|long|many|much)", r"sunrise", r"sunset", r"solnedgang",
    r"where is my phone", r"where's my phone", r"battery", r"batteri(?:et)?",
)
_REMINDER_RE = _alt(
    r"remind(?:er|ers|ing)?", r"påmind(?:else|elsen|elser)?", r"mind mig", r"husk(?:e)? mig", r"husk at",
    r"alarms?", r"wake me", r"væk mig", r"timer", r"nedtælling", r"to-?do(?:s| list)?", r"to do list",
    r"add (?:a |an |the )?(?:new )?(?:task|todo|to-do)", r"tilføj (?:en )?(?:ny )?opgave", r"countdown",
    r"don't let me forget",
)
_MAIL_RE = _alt(
    r"e-?mails?", r"mails?", r"inbox", r"indbakke(?:n)?", r"gmail", r"outlook",
    r"calendar", r"kalender(?:en)?", r"invites?", r"invitations?", r"google drive",
    r"drive (?:folder|file)s?", r"google (?:docs?|slides|sheets)", r"meeting request", r"unread", r"ulæst(?:e)?",
)
_MATH_RE = _alt(
    r"maths?", r"matematik(?:ken)?", r"equations?", r"ligning(?:en|er)?", r"integral(?:s|et)?",
    r"integrate", r"derivative(?:s)?", r"differentiat\w*", r"afled\w*", r"solve", r"løs", r"matri(?:x|ces)",
    r"matrice(?:n|r)?", r"eigen\w*", r"vectors?", r"vektor(?:er|en)?", r"probabilit(?:y|ies)",
    r"sandsynlighed\w*", r"statistics", r"statistik", r"calculus", r"limit of", r"grænseværdi\w*",
    r"polynomials?", r"logarithms?", r"log of", r"square root", r"kvadratrod(?:en)?", r"sqrt",
    r"homework", r"lektier(?:ne)?", r"physics", r"fysik", r"chemistry", r"kemi", r"theorem",
    r"exam", r"eksamen", r"assignment", r"afleveringsopgave", r"formula", r"formel(?:en)?",
    r"sine?", r"cosine?", r"tangent", r"modulo", r"factorial", r"fakultet",
)
_HARD_MATH_RE = _alt(
    r"prove", r"proofs?", r"bevis(?:e|et)?", r"derive", r"derivation", r"udled\w*", r"step by step",
    r"trin for trin", r"explain why", r"forklar hvorfor", r"walk me through",
)
_MATH_OPS_RE = re.compile(
    r"(?<!\w)(?:times|multiplied|divided|plus|minus|over|squared|cubed|to the power|percent|procent|"
    r"ganget|gange|delt|i anden|i tredje)(?!\w)|[+*/=^%]|\d\s*x\s*\d"
)
_NUMBER_RE = re.compile(
    r"\d+(?:[.,]\d+)?|(?<!\w)(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
    r"seventy|eighty|ninety|hundred|thousand|million|nul|fem|seks|syv|otte|ni|ti|tyve|hundrede|tusind)(?!\w)"
)
_DEV_RE = _alt(
    r"code", r"coding", r"kode(?:n)?", r"scripts?", r"functions?", r"bugs?", r"errors?", r"fejl(?:en)?",
    r"traceback", r"crash(?:ed|es|ing)?", r"servers?", r"services?", r"systemd", r"daemon", r"repos?",
    r"repository", r"git", r"github", r"commits?", r"branch", r"deploy\w*", r"docker", r"podman", r"python",
    r"rust", r"kotlin", r"javascript", r"typescript", r"api", r"endpoints?", r"database", r"sqlite",
    r"logs?", r"config\w*", r"ssh", r"terminal", r"process(?:es)?", r"workers?", r"apps?", r"apk",
    r"android", r"website", r"frontend", r"backend", r"ui", r"wake ?word", r"tts", r"stt", r"prompt",
    r"claude", r"sonnet", r"haiku", r"opus", r"fable", r"t3", r"threads?", r"fleet", r"hub",
    r"banditbox", r"homehub", r"nemurenai", r"kangaeru", r"diablo-pi",
    r"raspberry", r"banditshell", r"hyprland", r"quickshell", r"syncthing", r"tailscale", r"tailnet",
    r"vault", r"obsidian", r"brain", r"skema", r"files?", r"fil(?:en|er|erne)?", r"folders?",
    r"directory", r"programs?", r"programmet", r"hjemmeside(?:n)?", r"tjeneste(?:n|r)?",
    r"router", r"model(?:s)?", r"agent(?:s)?", r"telegram", r"bot", r"pr", r"pull request", r"issues?",
    r"build", r"ci", r"release", r"emulator",
)
_CODE_RE = re.compile(
    r"\b[\w-]+\.(?:py|js|ts|sh|json|jsonl|md|yaml|yml|toml|service|timer|html|css|qml|kt|rs|env|txt)\b|"
    r"(?:^|\s)[~/][\w.-]*/[\w.-]+|(?:^|\s)--[a-z][\w-]+|\b[a-z]+_[a-z_]+(?:\(\))?|```"
)
_BUILD_RE = _alt(
    r"build", r"implement\w*", r"refactor\w*", r"fix(?:es|ing)?", r"debug\w*", r"repair", r"patch",
    r"rewrite", r"redesign", r"develop", r"integrate", r"automate", r"migrate", r"port (?:it|this|that|the)",
    r"write (?:a|an|the|me|some|it)", r"add (?:a|an|the|support|some|new)", r"create",
    r"make (?:a|an|it|the|me|sure|so|them)", r"set (?:it |this |that |them )?up", r"configure", r"deploy",
    r"install", r"upgrade", r"upload", r"change (?:the|how|it|its|your)", r"improve",
    r"update (?:the|it|your)", r"extend", r"clean up",
    r"byg(?:ge)?", r"implementer\w*", r"kod(?:e|er)", r"skriv", r"ret(?:te)?", r"fiks(?:e)?", r"reparer",
    r"tilføj", r"lav(?:e)?", r"opret(?:te)?", r"opsæt", r"sæt (?:det )?op", r"konfigurer", r"udvikl\w*",
    r"forbedr\w*", r"opdater\w*", r"ændr(?:e)?", r"installer\w*",
)
_OPS_RE = _alt(
    r"restart", r"reboot", r"genstart\w*", r"status", r"wake (?:up )?(?:the )?\w+", r"shut ?down",
    r"suspend", r"is (?:it|the \w+|\w+) (?:running|down|up|working|alive|on|online|offline)",
    r"are you (?:working|down|busy|running|there)", r"what are you working on", r"working on",
    r"check (?:if|whether|the|on|that)", r"tjek", r"kører (?:den|det)", r"virker (?:den|det)",
    r"which (?:boxes|machines|computers) are (?:on|up|online)", r"usage", r"quota",
)
_RESEARCH_RE = _alt(
    r"research", r"investigate", r"look into", r"find out (?:about|why|how|whether|if|what)",
    r"compare", r"comparison", r"sammenlign\w*", r"undersøg\w*", r"analy[sz]e", r"analysis", r"analyse",
    r"deep dive", r"overview of", r"literature", r"papers?", r"essay", r"write ?up", r"report on",
    r"rapport", r"pros and cons", r"fordele og ulemper", r"best (?:way|option|laptop|phone|\w+ for)",
    r"recommend\w*", r"anbefal\w*", r"which (?:one )?is better", r"summari[sz]e", r"opsummer\w*",
    r"find (?:me )?(?:papers|sources|studies)",
)
_DEPTH_RE = _alt(
    r"in depth", r"in-depth", r"thorough(?:ly)?", r"detailed", r"comprehensive", r"deep dive",
    r"full report", r"everything about", r"all the options", r"grundig(?:t)?", r"detaljeret",
    r"uddybende", r"properly", r"ordentligt",
)
_PURCHASE_RE = _alt(
    r"buy", r"buying", r"purchase", r"order (?:me|a|an|some|the|new|more|pizza)", r"pizza",
    r"køb(?:e)?", r"bestil(?:le)?",
)
_SMALLTALK_RE = _alt(
    r"hello", r"hi", r"hey", r"yo", r"good (?:morning|evening|night|afternoon)", r"godmorgen", r"godnat",
    r"god aften", r"hej", r"how are you", r"how's it going", r"hvordan går det", r"thanks?", r"thank you",
    r"tak", r"joke", r"vittighed", r"who are you", r"what's up", r"nice", r"cool", r"love you", r"goodbye",
    r"bye", r"farvel", r"ses", r"good job", r"well done", r"godt gået", r"sweet", r"awesome",
)
_FOLLOWUP_RE = _alt(
    r"yes", r"yeah", r"yep", r"yup", r"ok", r"okay", r"sure", r"go ahead", r"do it", r"do that",
    r"that one", r"the (?:first|second|third|last) one", r"continue", r"carry on", r"go on", r"exactly",
    r"sounds good", r"please do", r"no", r"nope", r"ja", r"jep", r"nej", r"gør det", r"fortsæt",
    r"præcis", r"kør", r"what about", r"and (?:the|that|this)", r"same", r"again", r"igen",
)
_TIME_RE = _alt(
    r"today", r"tomorrow", r"tonight", r"this (?:morning|evening|afternoon|week)", r"next (?:week|month)",
    r"in \d+ (?:minutes?|hours?|days?)", r"at \d+", r"o'?clock", r"monday", r"tuesday", r"wednesday",
    r"thursday", r"friday", r"saturday", r"sunday", r"i dag", r"i morgen", r"i aften", r"i nat",
    r"mandag", r"tirsdag", r"onsdag", r"torsdag", r"fredag", r"lørdag", r"søndag", r"kl",
    r"om \d+ minutter", r"morning", r"evening",
)
_MULTI_RE = _alt(
    r"and then", r"then", r"after that", r"afterwards", r"and also", r"as well as", r"finally",
    r"first", r"step by step", r"og så", r"og derefter", r"derefter", r"bagefter", r"først",
    r"til sidst", r"og bagefter",
)
_ACTION_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(sorted((re.escape(v) for v in _IMPERATIVE), key=len, reverse=True)) + r")(?!\w)"
)
_EXPLICIT_RE = _alt(
    r"think (?:really )?hard(?:er)?", r"think (?:it through|carefully|deeply)", r"ultrathink",
    r"use opus", r"with opus", r"on opus", r"opus (?:for this|please)", r"ask your deep mind",
    r"tænk (?:dig )?(?:godt )?om", r"tænk grundigt", r"tænk så grundigt",
)
_REQUEST_RE = re.compile(
    r"(?<!\w)(?:please|can you|could you|would you|will you|i want you to|i need you to|i'd like you to|"
    r"make sure|go ahead|kan du|vil du|gider du|kunne du|jeg vil have at du)(?!\w)"
)
_NEGATED_RE = re.compile(r"(?<!\w)(?:don't|dont|do not|no need to|ikke|lad være med at)\s+(?:\w+\s+){0,2}$")

FEATURE_NAMES = (
    "words", "question", "imperative", "polite", "build", "ops", "home", "info", "reminder", "mail",
    "math", "hard_math", "math_ops", "numbers", "arithmetic", "dev", "code", "research", "depth",
    "purchase", "smalltalk", "followup", "time", "multi_step", "actions", "explicit", "images", "request",
)


def normalize(text):
    """(lowered text with punctuation kept, cleaned word text), wake word and [origin] prefix stripped."""
    low = " ".join(str(text or "").replace("’", "'").replace("‘", "'").lower().split())
    low = _PREFIX_RE.sub("", low, count=1)
    low = _WAKE_RE.sub("", low, count=1)
    clean = " ".join(_PUNCT_RE.sub(" ", low).split())
    return low, clean


def explicit(text):
    """True when the text asks for the deep tier in so many words, and not negated."""
    _, t = normalize(text)
    for match in _EXPLICIT_RE.finditer(t):
        if not _NEGATED_RE.search(t[:match.start()]):
            return True
    return False


def features(text, images=False):
    low, t = normalize(text)
    words = t.split()
    first = words[0] if words else ""
    polite = bool(_POLITE_RE.match(t))
    numbers = len(_NUMBER_RE.findall(t))
    math_ops = len(_MATH_OPS_RE.findall(low))
    actions = len(_ACTION_RE.findall(t))
    return {
        "words": len(words),
        "polite": polite,
        "question": (low.rstrip().endswith("?") or first in _WH) and not polite,
        "imperative": polite or first in _IMPERATIVE,
        "build": len(_BUILD_RE.findall(t)),
        "ops": len(_OPS_RE.findall(t)),
        "home": len(_HOME_RE.findall(t)),
        "info": len(_INFO_RE.findall(t)),
        "reminder": len(_REMINDER_RE.findall(t)),
        "mail": len(_MAIL_RE.findall(t)),
        "math": len(_MATH_RE.findall(t)),
        "hard_math": len(_HARD_MATH_RE.findall(t)),
        "math_ops": math_ops,
        "numbers": numbers,
        "arithmetic": numbers >= 2 and math_ops >= 1,
        "dev": len(_DEV_RE.findall(t)),
        "code": bool(_CODE_RE.search(low)),
        "research": len(_RESEARCH_RE.findall(t)),
        "depth": len(_DEPTH_RE.findall(t)),
        "purchase": len(_PURCHASE_RE.findall(t)),
        "smalltalk": len(_SMALLTALK_RE.findall(t)),
        "followup": bool(_FOLLOWUP_RE.search(t)),
        "time": bool(_TIME_RE.search(t)),
        "multi_step": bool(_MULTI_RE.search(t)) or actions >= 3,
        "actions": actions,
        "explicit": explicit(text),
        "images": bool(images),
        "request": polite or first in _IMPERATIVE or bool(_REQUEST_RE.search(t)),
    }


# =========================================================== tree DSL
@dataclass(frozen=True)
class Leaf:
    value: str
    reason: str


@dataclass(frozen=True)
class Split:
    name: str
    rules: tuple
    default: object
    codes: tuple = field(default=(), compare=False, repr=False)


_NO_BUILTINS = {"__builtins__": {}}


def _compile(cond):
    code = compile(cond, f"<route:{cond}>", "eval")
    unknown = set(code.co_names) - set(FEATURE_NAMES)
    if unknown:
        raise ValueError(f"route: unknown feature(s) {sorted(unknown)} in {cond!r}")
    return code


def split(name, rules, *, else_):
    rules = tuple(rules)
    return Split(name, rules, else_, tuple(_compile(cond) for cond, _ in rules))


def subject(name, reason):
    if name not in SUBJECTS:
        raise ValueError(f"route: unknown subject {name!r}")
    return Leaf(name, reason)


def go(tier, reason):
    if tier not in TIERS:
        raise ValueError(f"route: unknown tier {tier!r}")
    return Leaf(tier, reason)


def walk(tree, feats):
    path = []
    node = tree
    while isinstance(node, Split):
        for (cond, child), code in zip(node.rules, node.codes):
            if eval(code, _NO_BUILTINS, feats):  # noqa: S307, conditions are module constants with checked names
                path.append(f"{node.name}: {cond}")
                node = child
                break
        else:
            path.append(f"{node.name}: else")
            node = node.default
    return node, path


def depth(tree):
    if isinstance(tree, Leaf):
        return 0
    return 1 + max([depth(child) for _, child in tree.rules] + [depth(tree.default)])


def leaves(tree):
    if isinstance(tree, Leaf):
        return [tree]
    found = []
    for _, child in tree.rules:
        found += leaves(child)
    return found + leaves(tree.default)


def render(tree, indent=""):
    if isinstance(tree, Leaf):
        return f"{indent}-> {tree.value}   # {tree.reason}"
    lines = [f"{indent}[{tree.name}]"]
    for cond, child in tree.rules:
        if isinstance(child, Leaf):
            lines.append(f"{indent}  if {cond}  -> {child.value}   # {child.reason}")
        else:
            lines.append(f"{indent}  if {cond}:")
            lines.append(render(child, indent + "    "))
    if isinstance(tree.default, Leaf):
        lines.append(f"{indent}  else  -> {tree.default.value}   # {tree.default.reason}")
    else:
        lines.append(f"{indent}  else:")
        lines.append(render(tree.default, indent + "    "))
    return "\n".join(lines)


# =========================================================== the trees
SUBJECTS = (
    "smalltalk", "home_control", "info_lookup", "reminder_schedule", "mail_calendar",
    "study_math", "dev_system", "research", "purchase", "other",
)

# Stage 1: what is the turn about? First true wins, so the specific intents
# come first: "remind me to order pizza" is a reminder, not a purchase.
SUBJECT_TREE = split("subject", [
    ("words == 0", subject("smalltalk", "only his name, or a bare picture")),
    ("reminder", split("reminder?", [
        ("dev and build and not time", subject("dev_system", "changes how reminders work")),
    ], else_=subject("reminder_schedule", "remind/alarm/todo words"))),
    ("purchase and not (dev and build)", subject("purchase", "buy/order/pizza words")),
    ("mail", subject("mail_calendar", "mail/calendar/drive words")),
    ("home and (not dev or words <= 6)", subject("home_control", "lights/music/volume words")),
    ("dev or code", split("dev?", [
        ("research and not build", subject("research", "investigate a technical topic")),
    ], else_=subject("dev_system", "code/server/fleet words"))),
    ("research", subject("research", "research/compare/investigate words")),
    ("math or hard_math or arithmetic", subject("study_math", "math/study words or arithmetic")),
    ("info or (question and words <= 12)", subject("info_lookup", "weather/time/facts or a short question")),
    ("followup and words <= 6", subject("other", "short follow-up: depends on the conversation")),
    ("smalltalk and words <= 10", subject("smalltalk", "greeting/thanks/chit-chat")),
], else_=subject("other", "no subject keywords"))

# Stage 2: one small tree per subject, leaf = tier.
DECISION_TREES = {
    "smalltalk": split("smalltalk", [
        ("images", go("voice", "a picture with a greeting: look at it properly")),
    ], else_=go("fast", "chit-chat: haiku is enough")),
    "home_control": split("home_control", [
        ("multi_step or question or words > 14", go("voice", "several device steps or a device question")),
    ], else_=go("fast", "single device command: haiku")),
    "info_lookup": split("info_lookup", [
        ("depth or words > 25", go("standard", "longer explanation: sonnet medium")),
    ], else_=go("voice", "quick lookup")),
    "reminder_schedule": split("reminder_schedule", [
        ("multi_step and words > 25", go("standard", "long multi-part scheduling")),
    ], else_=go("voice", "file the reminder")),
    "mail_calendar": split("mail_calendar", [
        ("build or multi_step or words > 25", go("chat", "draft or sort mail across several steps: opus medium")),
    ], else_=go("standard", "mail/calendar lookup with the connectors")),
    "study_math": split("study_math", [
        ("arithmetic and words <= 14 and not math and not hard_math", go("voice", "plain arithmetic")),
        ("hard_math or depth or words > 30", go("chat", "proof/derivation or a long problem: opus medium")),
    ], else_=go("standard", "math/study question: sonnet medium")),
    "dev_system": split("dev_system", [
        ("build and (multi_step or depth or words > 20)", go("deep", "long build/fix work: opus high")),
        ("build", go("chat", "build/fix work on the system: opus medium")),
        ("ops or question", go("voice", "status check or quick ops")),
        ("words > 25", go("standard", "long technical talk")),
    ], else_=go("voice", "short technical remark")),
    "research": split("research", [
        ("depth or multi_step or words > 20", go("deep", "in-depth research: opus high")),
    ], else_=go("standard", "research: sonnet medium")),
    "purchase": go("standard", "an order costs money: sonnet medium, asks before paying"),
    "other": split("other", [
        ("followup", go("voice", "follow-up: inherits the last tier when there is one")),
        ("build and multi_step and words >= 15", go("deep", "long multi-step build request")),
        ("words > 60", go("standard", "a long message with no clear subject")),
    ], else_=go("voice", "no strong signal: sonnet low")),
}

for _name in SUBJECTS:
    if _name not in DECISION_TREES:
        raise ValueError(f"route: no stage 2 tree for subject {_name!r}")


# =========================================================== decision
@dataclass
class Decision:
    subject: str
    tier: str
    reason: str
    path: list
    explicit: bool = False
    applied: bool = False
    elapsed_us: int = 0
    id: str = ""
    adjusted: list = field(default_factory=list)
    features: dict = field(default_factory=dict)
    model: str = ""
    effort: str = ""
    source: str = "tree"
    jev: dict = None
    direct: dict = None
    handoff: dict = None

    def log_row(self, **extra):
        row = {
            "id": self.id, "subject": self.subject, "tier": self.tier, "model": self.model,
            "effort": self.effort, "reason": self.reason, "path": list(self.path),
            "explicit": self.explicit, "applied": self.applied, "elapsed_us": self.elapsed_us,
            "source": self.source,
        }
        if self.jev:
            row["jev"] = dict(self.jev)
        if self.direct:
            row["direct"] = dict(self.direct)
        if self.handoff:
            row["handoff"] = dict(self.handoff)
        if self.adjusted:
            row["adjusted"] = list(self.adjusted)
        row.update(extra)
        return row


def classify(text, images=False):
    """Pure decision for one text: no settings, no memory."""
    started = time.perf_counter_ns()
    feats = features(text, images)
    leaf1, path1 = walk(SUBJECT_TREE, feats)
    leaf2, path2 = walk(DECISION_TREES[leaf1.value], feats)
    decision = Decision(subject=leaf1.value, tier=leaf2.value, reason=leaf2.reason,
                        path=path1 + [f"-> {leaf1.value} ({leaf1.reason})"] + path2, features=feats)
    if feats["explicit"]:
        decision.tier, decision.explicit = "deep", True
        decision.reason = "explicit request for the deep tier"
        decision.path = ["explicit"] + decision.path
    decision.elapsed_us = (time.perf_counter_ns() - started) // 1000
    return decision


_last = {}


def _threshold(key, default):
    try:
        return float(settings.get(key) or default)
    except (TypeError, ValueError):
        return default


DIRECT_ACTIONS = ("lights_on", "lights_off", "dim", "brighten", "color", "warmer", "cooler", "again", "undo")
NEEDS_RECENT = ("again", "undo")


def apply_jev(decision, answer, images=False, recent_light=False, needs_context=False):
    """Jev understands the meaning, so a confident jev tier replaces the tree's.

    The tree still wins for explicit phrases and short follow-ups. A direct
    action needs jev sure of it, and either the tree agreeing that the line is
    about the room or a short line right after a light change ("dim it a bit").
    "it" and again/undo need that recent light change to point at.
    """
    if not answer:
        return decision
    decision.jev = answer
    followup = decision.subject == "other" and decision.path[-1].startswith("other: followup")
    if not decision.explicit and not followup and answer["tier_confidence"] >= _threshold("route.jev_threshold", 0.75):
        if answer["tier"] != decision.tier:
            decision.adjusted.append(f"jev {answer['tier_confidence']:.2f}: {decision.tier} -> {answer['tier']}")
        decision.tier, decision.source = answer["tier"], "jev"
        decision.reason = f"jev, confidence {answer['tier_confidence']:.2f} (tree said: {decision.reason})"
    if needs_context:
        if rank(decision.tier) < rank("voice"):
            decision.adjusted.append(f"jev needs more context: {decision.tier} -> voice")
            decision.tier = "voice"
        return decision
    action = answer.get("action")
    if action not in DIRECT_ACTIONS or images or decision.explicit:
        return decision
    if answer["action_confidence"] < _threshold("route.direct_threshold", 0.9):
        return decision
    words = decision.features.get("words", 0)
    # Jev all but certain of one light change is enough on its own: "that's wonderful, but
    # can you warm them up a bit?" is ten words the keyword tree files as a lookup.
    certain = answer["action_confidence"] >= _threshold("route.direct_certain", 0.97) and words <= 16
    if not (decision.subject == "home_control" or (recent_light and words <= 8) or certain):
        return decision
    if action in NEEDS_RECENT and not recent_light:
        return decision
    target = answer.get("target") or "all"
    if answer.get("target_confidence", 0) < 0.7:
        target = "last" if recent_light else "all"
    if target == "last" and not recent_light:
        return decision
    color = answer.get("color") if answer.get("color_confidence", 0) >= 0.7 else "none"
    if action == "color" and (not color or color == "none"):
        return decision
    decision.direct = {"kind": "hue", "action": action, "target": target,
                       "amount": answer.get("amount") or "normal", "color": color}
    return decision


DESKTOP_WINDOW = ("close_window", "focus_window")
DESKTOP_WORKSPACE = ("workspace", "move_to_workspace")
DESKTOP_MEDIA = ("play", "pause", "next", "previous")
DESKTOP_SOUND = ("volume_up", "volume_down", "mute", "unmute")
DESKTOP_WORDS = 12
# Keys, tabs, scrolling, clicks and window moves: (action, what was done). The ears map each
# to a key combo or a pointer action in voice/commands.py.
KEY_SAID = {
    "focus_left": "moved left", "focus_right": "moved right", "focus_up": "moved up",
    "focus_down": "moved down", "fullscreen": "toggled fullscreen", "new_window": "opened a new window",
    "terminal": "opened a terminal",
    "enter": "pressed enter", "escape": "pressed escape", "tab": "pressed tab",
    "backspace": "pressed backspace", "delete_word": "deleted the last word", "copy": "copied",
    "paste": "pasted", "cut": "cut", "undo": "went back", "redo": "redid it", "select_all": "selected all",
    "save": "saved", "find": "opened find", "new_tab": "opened a new tab", "close_tab": "closed the tab",
    "next_tab": "went to the next tab", "previous_tab": "went to the previous tab",
    "reopen_tab": "reopened the tab", "reload": "reloaded", "scroll_up": "scrolled up",
    "scroll_down": "scrolled down", "page_up": "paged up", "page_down": "paged down",
    "top": "went to the top", "bottom": "went to the bottom", "click": "clicked",
    "double_click": "double clicked", "right_click": "right clicked",
}
# These can send a message or lose work, so they need Jev sure, like closing a window.
DESKTOP_SURE = ("close_window", "close_tab", "enter")
MEDIA_SAID = {"play": "played {}", "pause": "paused {}", "next": "skipped to the next track on {}",
              "previous": "went back a track on {}"}
SOUND_SAID = {"volume_up": "turned the sound up", "volume_down": "turned the sound down",
              "mute": "muted the sound", "unmute": "unmuted the sound"}


def desktop_action(answer, text, desktop):
    """The desktop command for the ears to run, or None when the line is not plainly one.

    Jev must be sure enough of the command (route.desktop_threshold 0.8 to close a window, a tab or press enter,
    route.desktop_light_threshold 0.6 for the rest), the line short, and the
    target clear: a named window jev is unsure of, or a workspace with no number, goes
    to Claude instead of a guess.
    """
    if not answer or not desktop:
        return None
    action = answer.get("desktop")
    known = (DESKTOP_WINDOW + DESKTOP_WORKSPACE + DESKTOP_MEDIA + DESKTOP_SOUND + tuple(KEY_SAID)
             + ("reopen_window", "open_thread", "again"))
    if action not in known:
        return None
    # Closing or sending needs Jev sure; the rest is harmless and undone in a word.
    sure = (_threshold("route.desktop_threshold", 0.8) if action in DESKTOP_SURE
            else _threshold("route.desktop_light_threshold", 0.6))
    if answer.get("desktop_confidence", 0) < sure:
        return None
    if len(str(text or "").split()) > DESKTOP_WORDS:
        return None
    act = {"kind": "desktop", "action": action}
    if action in DESKTOP_WINDOW:
        windows = [w for w in desktop.get("windows") or [] if isinstance(w, dict)]
        choice, sure = answer.get("window") or "focused", answer.get("window_confidence", 0)
        keys = {f"w{index + 1}": w for index, w in enumerate(windows)}
        if choice == "focused":
            focused = next((w for w in windows if w.get("focused")), None)
            if action == "focus_window" or focused is None or sure < 0.5:
                return None
            window = focused
        elif choice in keys and sure >= (0.7 if action == "close_window" else 0.6):
            window = keys[choice]
        else:
            return None
        name = str(window.get("class") or "the window")
        act.update(window=str(window.get("address") or ""), name=name)
        act["did"] = f"closed {name}" if action == "close_window" else f"switched to {name}"
        return act if act["window"] else None
    if action in DESKTOP_WORKSPACE:
        number = answer.get("workspace")
        if not str(number).isdigit() or answer.get("workspace_confidence", 0) < 0.8:
            return None
        act["workspace"] = int(number)
        act["did"] = (f"switched to workspace {number}" if action == "workspace"
                      else f"moved the window to workspace {number}")
        return act
    if action in DESKTOP_MEDIA:
        players = [str(p) for p in desktop.get("players") or []]
        player = answer.get("player")
        if player in players and answer.get("player_confidence", 0) >= 0.7:
            act["player"] = player
        act["did"] = MEDIA_SAID[action].format(player.split(".")[0] if act.get("player") else "the player")
        return act
    if action in KEY_SAID:
        act["did"] = KEY_SAID[action]
        return act
    if action == "open_thread":
        threads = [t for t in desktop.get("threads") or [] if isinstance(t, dict)]
        keys = {f"t{index + 1}": t for index, t in enumerate(threads)}
        chosen = keys.get(answer.get("thread"))
        if chosen is None or answer.get("thread_confidence", 0) < 0.5 or not chosen.get("open"):
            return None
        act.update(link=chosen["open"], name=chosen.get("title"), did=f"opened the thread {chosen.get('title')}")
        return act
    if action == "again":
        # The ears know what they did last; the hub only says to do it once more.
        act["did"] = "did it again"
        return act
    if action == "reopen_window":
        # The ears know what they closed; the hub only says to bring it back.
        act["did"] = "reopened the last app closed"
        return act
    act["did"] = SOUND_SAID[action]
    return act


WORKER = {
    "small_change": ("claude-sonnet-5-5", "high"),
    "app_update": ("claude-sonnet-5-5", "high"),
    "no_project": ("claude-sonnet-5-5", "high"),
    "big_work": ("claude-opus-5-5", "high"),
    "new_project": ("claude-opus-5-5", "high"),
}


def plan_handoff(decision, answer):
    """Work for T3 Code: Diablo only writes the brief (sonnet medium), the T3 thread gets the heavy model.

    Jev's work label decides when it is confident; the tree is the backup
    (build words with a real ask, or in-depth research).
    """
    raw_tier = decision.tier
    work = ""
    if answer and answer.get("work_confidence", 0) >= 0.6:
        work = answer.get("work") or ""
        if work not in WORKER:
            return decision
    elif decision.subject == "dev_system" and decision.features.get("build") and decision.features.get("request"):
        work = "big_work" if rank(raw_tier) >= rank("deep") else "small_change"
    elif decision.subject == "research" and rank(raw_tier) >= rank("deep"):
        work = "no_project"
    else:
        return decision
    model, effort = WORKER[work]
    if decision.explicit or rank(raw_tier) >= rank("deep"):
        model, effort = "claude-opus-5-5", "high"
    decision.handoff = {"work": work, "model": model, "effort": effort}
    if decision.tier != "standard":
        decision.adjusted.append(f"handoff to T3 ({work}, {model} {effort}): diablo {decision.tier} -> standard")
        decision.tier = "standard"
    return decision


def route(text, conv="main", images=False, now=None, jev=None, recent_light=False, needs_context=False, handoff=True):
    """classify(), jev's answer, follow-up memory, the ceiling and floor, and the tier's model."""
    started = time.perf_counter_ns()
    now = time.time() if now is None else now
    decision = classify(text, images)
    decision.id = secrets.token_hex(4)
    apply_jev(decision, jev, images, recent_light, needs_context)
    if not decision.explicit:
        feats = decision.features
        if rank(decision.tier) > rank("standard") and not (feats["request"] or feats["question"]):
            decision.adjusted.append(f"no ask or question in the text: {decision.tier} -> standard")
            decision.tier = "standard"
        previous = _last.get(conv)
        followup = decision.subject == "other" and decision.path[-1].startswith("other: followup")
        if followup and previous and now - previous[1] <= STICKY_SECONDS and rank(previous[0]) > rank(decision.tier):
            decision.adjusted.append(f"follow-up keeps the last tier: {decision.tier} -> {previous[0]}")
            decision.tier = previous[0]
        ceiling = _setting_tier("route.max_tier", "chat")
        if rank(decision.tier) > rank(ceiling):
            decision.adjusted.append(f"ceiling {ceiling}: {decision.tier} -> {ceiling}")
            decision.tier = ceiling
        floor = _setting_tier("route.min_tier")
        if floor and rank(decision.tier) < rank(floor):
            decision.adjusted.append(f"floor {floor}: {decision.tier} -> {floor}")
            decision.tier = floor
    if handoff and not decision.direct:
        plan_handoff(decision, jev)
    _last[conv] = (decision.tier, now)
    decision.model, decision.effort = model_effort(decision.tier)
    decision.elapsed_us = (time.perf_counter_ns() - started) // 1000
    return decision


def log(decision, **extra):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as handle:
            handle.write(json.dumps({"ts": time.time(), "type": "decision", **decision.log_row(**extra)}) + "\n")
    except OSError:
        pass


def describe():
    parts = ["STAGE 1: subject", render(SUBJECT_TREE), "", "STAGE 2: tier per subject"]
    for name in SUBJECTS:
        tree = DECISION_TREES[name]
        parts.append(render(tree, "  ") if isinstance(tree, Split) else f"  [{name}]\n  -> {tree.value}   # {tree.reason}")
    return "\n".join(parts)


def _main(argv):
    if len(argv) >= 2 and argv[0] == "classify":
        decision = classify(" ".join(argv[1:]))
        print(f"{decision.subject} -> {decision.tier}  ({decision.reason}, {decision.elapsed_us} us)")
        for step in decision.path:
            print(f"  {step}")
        print("  features: " + ", ".join(f"{k}={v}" for k, v in decision.features.items() if v))
        return 0
    if argv[:1] == ["show"]:
        print(describe())
        return 0
    print("usage: python -m hub.route show | classify TEXT")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
