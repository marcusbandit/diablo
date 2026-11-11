from room import config
from room.context import Player, Settler, Snapshot, classify

APPS = config.DEFAULTS["apps"]
YT = "https://www.youtube.com/watch?v=x"
ZEN = " \u2014 Zen Browser"  # real Zen titles end with an em dash; written as an escape here


def state(**kw):
    return classify(Snapshot(**kw), APPS)[0]


def yt(status="Playing"):
    return (Player("firefox.instance_1_194", status, YT, "A video"),)


def test_fullscreen_youtube_playing_is_cinema():
    assert state(cls="zen", title="A video - YouTube" + ZEN, fullscreen=True, players=yt()) == "cinema"


def test_fullscreen_youtube_paused_is_intermission():
    assert state(cls="zen", title="A video - YouTube" + ZEN, fullscreen=True, players=yt("Paused")) == "intermission"


def test_anibeam_fullscreen_playing_is_cinema():
    players = (Player("anibeam", "Playing", "file:///anime/ep1.mkv"),)
    assert state(cls="com.marcusrosado.AniBeam", fullscreen=True, players=players) == "cinema"


def test_video_app_without_mpris_fullscreen_is_cinema():
    assert state(cls="mpv", fullscreen=True) == "cinema"


def test_windowed_video_is_watching():
    assert state(cls="zen", title="A video - YouTube" + ZEN, players=yt()) == "watching"


def test_music_never_counts_as_video():
    spotify = (Player("spotify", "Playing", "https://open.spotify.com/track/1"),)
    ytm = (Player("firefox.instance_1", "Playing", "https://music.youtube.com/watch?v=1"),)
    assert state(cls="com.t3tools.T3Code", players=spotify) == "focus"
    assert state(cls="zen", title="Song - YouTube Music", fullscreen=True, players=ytm) != "cinema"


def test_other_apps_players_do_not_make_the_browser_cinema():
    anibeam = (Player("anibeam", "Playing", "file:///a.mkv"),)
    assert state(cls="zen", title="GitHub" + ZEN, fullscreen=True, players=anibeam) == "focus"


def test_coding_is_focus():
    assert state(cls="com.t3tools.T3Code", title="T3 Code (Nightly)") == "focus"
    assert state(cls="foot", title="nvim") == "focus"
    assert state(cls="zen", title="marcusbandit/room - GitHub" + ZEN) == "focus"


def test_fullscreen_editor_stays_focus():
    assert state(cls="com.t3tools.T3Code", fullscreen=True) == "focus"


def test_discord_is_everyday_until_a_call():
    assert state(cls="vesktop", title="Discord") == "everyday"
    assert state(cls="vesktop", title="Discord", call="Discord") == "call"


def test_call_does_not_beat_cinema():
    assert state(cls="zen", title="x - YouTube", fullscreen=True, players=yt(), call="Discord") == "cinema"


def test_unknown_fullscreen_app_is_a_game():
    assert state(cls="steam_app_504230", fullscreen=True) == "game"
    assert state(cls="SomeGame", fullscreen=True) == "game"
    assert state(cls="Celeste") == "game"


def test_lock_is_away():
    assert state(cls="zen", fullscreen=True, players=yt(), locked=True) == "away"


def test_reason_names_the_app_and_site():
    _, reason = classify(Snapshot(cls="zen", title="x - YouTube", fullscreen=True, players=yt()), APPS)
    assert reason == "Zen is fullscreen and YouTube is playing"


def test_settler_holds_short_flips_back():
    s = Settler(3.0)
    assert s.feed("focus", "", 0.0) and s.active == "focus"
    assert not s.feed("everyday", "", 1.0)
    assert not s.feed("focus", "", 2.0)  # back before it settled
    assert not s.feed("everyday", "", 3.0)
    assert not s.feed("everyday", "", 5.9)
    assert s.feed("everyday", "", 6.0) and s.active == "everyday"


def test_settler_cinema_comes_fast_and_leaves_slow():
    s = Settler(3.0)
    s.feed("focus", "", 0.0)
    s.feed("cinema", "", 10.0)
    assert s.feed("cinema", "", 11.0) and s.active == "cinema"
    s.feed("everyday", "", 20.0)
    assert not s.feed("everyday", "", 25.0)
    assert s.feed("everyday", "", 26.0)


def test_settler_pause_and_play():
    s = Settler(3.0)
    s.feed("cinema", "", 0.0)
    s.feed("intermission", "", 10.0)
    assert not s.feed("intermission", "", 11.5)
    assert s.feed("intermission", "", 12.0)
    s.feed("cinema", "", 20.0)
    assert s.feed("cinema", "", 20.5)


def test_lock_applies_at_once():
    s = Settler(3.0)
    s.feed("cinema", "", 0.0)
    assert s.feed("away", "", 5.0)


def test_hyprland_fullscreen_modes():
    from room.signals import is_fullscreen
    assert not is_fullscreen(0) and not is_fullscreen(1) and not is_fullscreen(None)
    assert is_fullscreen(2) and is_fullscreen(3) and is_fullscreen(True) and not is_fullscreen(False)
