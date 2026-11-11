import time

import pytest

from hub import route


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    store = {}
    monkeypatch.setattr(route.settings, "get", lambda key: store.get(key, ""))
    route._last.clear()
    return store


CASES = [
    ("turn off the lights", "home_control", "fast"),
    ("sluk lyset i stuen", "home_control", "fast"),
    ("pause the music", "home_control", "fast"),
    ("dim the lights and then play my focus playlist on spotify", "home_control", "voice"),
    ("what's the weather like tomorrow", "info_lookup", "voice"),
    ("hvad er klokken", "info_lookup", "voice"),
    ("where is my phone", "info_lookup", "voice"),
    ("remind me to call mom at 5", "reminder_schedule", "voice"),
    ("mind mig om at købe mælk i morgen", "reminder_schedule", "voice"),
    ("check my inbox", "mail_calendar", "standard"),
    ("har jeg nogen nye mails", "mail_calendar", "standard"),
    ("what's 12 times 12", "study_math", "voice"),
    ("explain eigenvalues to me", "study_math", "standard"),
    ("prove that the square root of two is irrational", "study_math", "chat"),
    ("is banditbox online", "dev_system", "voice"),
    ("restart the hub service", "dev_system", "voice"),
    ("fix the crash in banditshell", "dev_system", "chat"),
    ("build me a script that backs up the vault to nemurenai every night and then pings me on telegram", "dev_system", "chat"),
    ("research the best budget mechanical keyboards", "research", "standard"),
    ("sammenlign de bedste elbiler til under 300000 kroner grundigt", "research", "chat"),
    ("order a pizza", "purchase", "standard"),
    ("hey diablo", "smalltalk", "fast"),
    ("tak skal du have", "smalltalk", "fast"),
    ("tell me a joke", "smalltalk", "fast"),
    ("yes do it", "other", "voice"),
    ("ja gør det", "other", "voice"),
    ("This is my voice. Please fix the issue that I just mentioned.", "dev_system", "chat"),
    ("in scratch, mark the ticker tape todo as done", "reminder_schedule", "voice"),
]


@pytest.mark.parametrize("text,most", [
    ("Given the deep mind stirs.", "voice"),
    ("Useful to know in case your game, in case your build might use glitches.", "standard"),
    ("So you've got all those papers, and all these two small things, and it's all been written about at length by people who spent years on it", "standard"),
    ("Quick check from the setup: which machines are on right now, and how many T3 threads are running? one line", "voice"),
])
def test_overheard_or_status_talk_never_reaches_opus(text, most):
    decision = route.route(text, conv=text)
    assert route.rank(decision.tier) <= route.rank(most), (decision.tier, decision.path, decision.adjusted)


@pytest.mark.parametrize("text,subject,tier", CASES)
def test_both_stages(text, subject, tier):
    decision = route.route(text, conv=text, handoff=False)
    assert (decision.subject, decision.tier) == (subject, tier), decision.path


def test_deep_is_reached_when_the_ceiling_allows_it(fake_settings):
    fake_settings["route.max_tier"] = "deep"
    text = "build me a script that backs up the vault to nemurenai every night and then pings me on telegram"
    assert route.route(text, handoff=False).tier == "deep"


def test_every_subject_is_covered_and_trees_stay_small():
    assert {subject for _, subject, _ in CASES} == set(route.SUBJECTS)
    assert route.depth(route.SUBJECT_TREE) <= 3
    for tree in route.DECISION_TREES.values():
        assert route.depth(tree) <= 3
        assert all(leaf.reason for leaf in route.leaves(tree))


def test_tree_dsl_rejects_unknown_features():
    with pytest.raises(ValueError):
        route.split("bad", [("nonsense > 1", route.go("fast", "x"))], else_=route.go("voice", "y"))
    with pytest.raises(ValueError):
        route.go("turbo", "x")


@pytest.mark.parametrize("text", [
    "think hard about my thesis plan",
    "use opus and turn off the lights",
    "ultrathink: redesign the hub",
    "tænk dig godt om, hvordan skal jeg planlægge eksamen",
])
def test_explicit_phrases_win_and_skip_the_ceiling(text):
    decision = route.route(text)
    assert decision.explicit and decision.tier == "deep"
    assert decision.model == "claude-opus-5-5" and decision.effort == "high"


def test_negated_explicit_is_not_explicit():
    assert not route.route("don't think hard, just turn off the lights").explicit


def test_ceiling_and_floor(fake_settings):
    fake_settings["route.max_tier"] = "standard"
    assert route.route("prove that the square root of two is irrational").tier == "standard"
    fake_settings["route.min_tier"] = "voice"
    assert route.route("turn off the lights").tier == "voice"


def test_followup_inherits_the_last_tier_within_the_window():
    now = time.time()
    assert route.route("fix the crash in banditshell", conv="c", now=now, handoff=False).tier == "chat"
    decision = route.route("yes do it", conv="c", now=now + 30, handoff=False)
    assert decision.tier == "chat" and decision.adjusted
    assert route.route("yes do it", conv="c", now=now + 30 + route.STICKY_SECONDS + 1).tier == "voice"
    assert route.route("yes do it", conv="other", now=now).tier == "voice"


def test_tier_models_come_from_settings(fake_settings):
    assert route.model_effort("fast") == ("claude-haiku-5-5", "low")
    fake_settings["route.tier.fast"] = "claude-sonnet-5-5|low"
    assert route.route("turn off the lights").model == "claude-sonnet-5-5"


def test_flag(fake_settings):
    assert route.enabled()
    fake_settings["route.enabled"] = "off"
    assert not route.enabled()


def test_origin_prefix_and_wake_word_are_ignored():
    assert route.classify("[telegram, Thu 29 Oct 10:00]\nhey diablo turn off the lights").tier == "fast"


def test_latency():
    started = time.perf_counter()
    for text, _, _ in CASES * 10:
        route.classify(text)
    assert (time.perf_counter() - started) / (len(CASES) * 10) < 0.002


def jev(tier, conf=0.95, action="none", action_conf=0.99, target="all", target_conf=0.95):
    return {"tier": tier, "tier_confidence": conf, "action": action, "action_confidence": action_conf,
            "target": target, "target_confidence": target_conf, "ms": 240}


def test_confident_jev_replaces_the_tree_tier():
    decision = route.route("what is the eur to dkk rate right now", conv="j1", jev=jev("standard"))
    assert decision.tier == "standard" and decision.source == "jev"


def test_unsure_jev_leaves_the_tree_tier():
    decision = route.route("what is the eur to dkk rate right now", conv="j2", jev=jev("deep", conf=0.5))
    assert decision.tier == "voice" and decision.source == "tree"


def test_jev_cannot_skip_the_ceiling_or_the_no_ask_guard():
    assert route.route("fix the crash in banditshell", conv="j3", jev=jev("deep"), handoff=False).tier == "chat"
    assert route.route("Given the deep mind stirs.", conv="j4", jev=jev("chat")).tier == "standard"


def test_explicit_and_followups_ignore_jev():
    assert route.route("think hard about my thesis plan", conv="j5", jev=jev("fast"), handoff=False).tier == "deep"
    route.route("fix the crash in banditshell", conv="j6", handoff=False)
    assert route.route("yes do it", conv="j6", jev=jev("fast"), handoff=False).tier == "chat"


def test_direct_light_action_needs_jev_sure_and_the_tree_agreeing():
    direct = route.route("turn off the lights", jev=jev("fast", action="lights_off")).direct
    assert (direct["action"], direct["target"]) == ("lights_off", "all")
    assert route.route("turn the corner lamp on", jev=jev("fast", action="lights_on", target="corner lamp")).direct["target"] == "corner lamp"
    assert route.route("turn off the lights", jev=jev("fast", action="lights_off", action_conf=0.6)).direct is None
    # The tree disagreeing stops a direct change unless Jev is all but certain (route.direct_certain).
    assert route.route("what's the weather", jev=jev("voice", action="lights_off", action_conf=0.95)).direct is None
    assert route.route("turn off the lights", images=True, jev=jev("fast", action="lights_off")).direct is None
    assert route.route("turn off the lights", jev=None).direct is None


def test_short_line_after_a_light_change_can_go_direct():
    answer = jev("fast", action="dim", target="last")
    assert route.route("a bit more", jev=answer).direct is None
    assert route.route("a bit more", jev=answer, recent_light=True).direct["action"] == "dim"


def test_it_and_again_need_a_recent_light_change():
    assert route.route("dim it", jev=jev("fast", action="dim", target="last")).direct is None
    assert route.route("again", jev=jev("fast", action="again")).direct is None
    assert route.route("again", jev=jev("fast", action="again"), recent_light=True).direct["action"] == "again"


def test_color_needs_a_named_color():
    answer = jev("fast", action="color")
    answer.update(color="none", color_confidence=0.9)
    assert route.route("make the lights a nice color", jev=answer).direct is None
    answer.update(color="blue")
    assert route.route("make the lights blue", jev=answer).direct["color"] == "blue"


def test_needs_more_context_goes_to_claude_at_sonnet_or_above():
    decision = route.route("can you please find that document we talked about earlier", jev=jev("fast"), needs_context=True)
    assert decision.direct is None and route.rank(decision.tier) >= route.rank("voice")
    assert route.route("make it blue", jev=jev("fast", action="color"), needs_context=True).direct is None


def test_build_work_is_handed_to_t3_with_a_heavy_worker():
    answer = jev("chat")
    answer.update(work="big_work", work_confidence=0.9)
    decision = route.route("add offline sync to anibeam", conv="h1", jev=answer)
    assert decision.handoff == {"work": "big_work", "model": "claude-opus-5-5", "effort": "high"}
    assert decision.tier == "standard" and decision.model == "claude-sonnet-5-5"


def test_small_changes_get_a_sonnet_worker():
    answer = jev("chat")
    answer.update(work="small_change", work_confidence=0.9)
    assert route.route("fix the typo in the jetlag app title", conv="h2", jev=answer).handoff["model"] == "claude-sonnet-5-5"


def test_jev_saying_none_means_no_handoff():
    answer = jev("chat")
    answer.update(work="none", work_confidence=0.9)
    assert route.route("fix the crash in banditshell", conv="h3", jev=answer).handoff is None


def test_tree_backup_hands_off_build_requests():
    decision = route.route("please fix the crash in banditshell", conv="h4")
    assert decision.handoff and decision.handoff["work"] == "small_change" and decision.tier == "standard"
    assert route.route("what's the weather", conv="h5").handoff is None


def test_no_handoff_for_machine_turns_or_direct_actions():
    assert route.route("please fix the crash in banditshell", conv="h6", handoff=False).handoff is None
    direct = route.route("turn off the lights", jev=jev("fast", action="lights_off"))
    assert direct.direct and direct.handoff is None


def test_jev_certain_of_a_light_change_skips_the_word_rule():
    line = "that's wonderful, but can you warm them up a bit?"
    sure = route.route(line, conv="c1", jev=jev("fast", action="warmer", action_conf=1.0))
    assert sure.direct and sure.direct["action"] == "warmer"
    unsure = route.route(line, conv="c2", jev=jev("fast", action="warmer", action_conf=0.93))
    assert not unsure.direct
