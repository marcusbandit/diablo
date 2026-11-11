"""The hold on other sound while Diablo speaks aloud (speakers.SoundHold, tts.Speaker).

A World stands in for playerctl, busctl and pactl, so gvoice's real media.py plans,
applies and undoes against it. What the tests assert is what Spotify and the other
players would have heard.
"""

import io
import json
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import speakers, tts

FULL = 65536
MEDIA = speakers.gvoice_media()


class World:
    """A desktop with players and PipeWire streams, speaking the commands media.py runs."""

    def __init__(self):
        self.players = {}
        self.streams = {}
        self.log = []

    def player(self, name, status="Playing", pid=None):
        self.players[name] = {"status": status, "pid": pid or 1000 + len(self.players)}
        return self

    def stream(self, index, app, pid, volume=FULL, muted=False, corked=False):
        self.streams[index] = {"app": app, "pid": pid, "volume": [volume, volume], "muted": muted, "corked": corked}
        return self

    def volume(self, index):
        return self.streams[index]["volume"][0]

    def commands(self, word):
        return [entry for entry in self.log if entry[0] == word]

    def run(self, cmd, timeout=2.0):
        if cmd[0] == "playerctl":
            if cmd[1] == "-a":
                return "".join(f"{n}\t{n}\t{p['status']}\n" for n, p in self.players.items())
            name, verb = cmd[2], cmd[3]
            if verb == "status":
                return self.players[name]["status"] + "\n"
            self.log.append((verb, name))
            self.players[name]["status"] = {"pause": "Paused", "play": "Playing"}[verb]
            return ""
        if cmd[0] == "busctl":
            if "GetConnectionUnixProcessID" in cmd:
                name = cmd[-1].rsplit(".", 1)[1]
                return f"u {self.players[name]['pid']}\n"
            return 's "Player"\n'
        if cmd[0] == "pactl":
            if cmd[1:4] == ["-f", "json", "list"]:
                return json.dumps([{
                    "index": i,
                    "corked": s["corked"],
                    "mute": s["muted"],
                    "volume": {"front-left": {"value": s["volume"][0]}, "front-right": {"value": s["volume"][1]}},
                    "properties": {"application.name": s["app"], "application.process.binary": s["app"],
                                   "application.process.id": str(s["pid"]), "media.name": "x"},
                } for i, s in self.streams.items()])
            if cmd[1] == "set-sink-input-volume":
                self.log.append(("volume", int(cmd[2]), [int(v) for v in cmd[3:]]))
                self.streams[int(cmd[2])]["volume"] = [int(v) for v in cmd[3:]]
            if cmd[1] == "set-sink-input-mute":
                self.log.append(("mute", int(cmd[2]), cmd[3]))
                self.streams[int(cmd[2])]["muted"] = cmd[3] == "1"
        return ""


class FakeTimer:
    """threading.Timer that only fires when a test says so."""

    instances = []

    def __init__(self, seconds, fn):
        self.seconds, self.fn, self.cancelled, self.started = seconds, fn, False, False
        self.daemon = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn()


@unittest.skipIf(MEDIA is None, "no gvoice checkout")
class HoldCase(unittest.TestCase):
    rules = {"media": {"players": "pause", "others": "nothing", "dim_to": 20,
                       "apps": [{"match": "spotify", "action": "dim", "name": "Spotify", "dim_to": 35}]}}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        settings = root / "settings.json"
        settings.write_text(json.dumps(self.rules))
        self.world = World()
        for patch in (
            mock.patch.object(MEDIA, "run", self.world.run),
            mock.patch.object(MEDIA, "SETTINGS_FILE", settings),
            mock.patch.object(MEDIA, "DIM_SECONDS", 0.0),
            mock.patch.object(MEDIA, "RESTORE_SECONDS", 0.0),
            mock.patch.object(speakers, "HELD_FILE", root / "held.json"),
            mock.patch.object(speakers, "_playerctl", lambda *a, **k: ""),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        FakeTimer.instances = []
        self.hold = speakers.SoundHold(MEDIA, gap=1.5, timer=FakeTimer)

    def spotify(self, volume=FULL):
        self.world.player("spotify", "Playing", pid=4242).stream(7, "spotify", 4242, volume)
        return self


class TestDim(HoldCase):
    def test_gvoice_rules_dim_spotify_and_restore_it_exactly(self):
        self.spotify()
        self.hold.acquire()
        self.assertEqual(self.world.volume(7), round(FULL * 35 / 100))
        self.assertTrue(speakers.HELD_FILE.exists())
        self.hold.release(0)
        self.assertEqual(self.world.volume(7), FULL)
        self.assertFalse(speakers.HELD_FILE.exists())

    def test_dim_mode_uses_its_own_level_over_the_app_rule(self):
        self.spotify()
        self.hold.acquire("dim", 10)
        self.assertEqual(self.world.volume(7), round(FULL * 10 / 100))
        self.hold.release(0)
        self.assertEqual(self.world.volume(7), FULL)

    def test_dim_mode_without_a_level_takes_gvoice_s_dim_to(self):
        self.world.player("zen", "Playing", pid=5000).stream(8, "zen", 5000, 40000)
        self.hold.acquire("dim", None)
        self.assertEqual(self.world.volume(8), round(40000 * 20 / 100))
        self.hold.release(0)
        self.assertEqual(self.world.volume(8), 40000)
        self.assertEqual(self.world.commands("pause"), [])

    def test_a_volume_he_moved_meanwhile_stays_where_he_put_it(self):
        self.spotify()
        self.hold.acquire()
        self.world.streams[7]["volume"] = [FULL // 2, FULL // 2]
        self.hold.release(0)
        self.assertEqual(self.world.volume(7), FULL // 2)

    def test_sound_without_controls_is_dimmed_in_dim_and_pause_modes(self):
        self.world.stream(9, "game", 9000, FULL)
        self.hold.acquire("pause", 25)
        self.assertEqual(self.world.volume(9), round(FULL * 25 / 100))
        self.hold.release(0)
        self.assertEqual(self.world.volume(9), FULL)

    def test_a_corked_stream_is_left_alone(self):
        self.world.player("spotify", "Paused", pid=4242).stream(7, "spotify", 4242, FULL, corked=True)
        self.hold.acquire("dim", 10)
        self.assertEqual(self.world.log, [])
        self.hold.release(0)
        self.assertEqual(self.world.log, [])


class TestPause(HoldCase):
    def test_pause_mode_pauses_what_plays_and_resumes_only_that(self):
        self.world.player("spotify", "Playing", pid=4242).stream(7, "spotify", 4242)
        self.world.player("firefox", "Paused", pid=4300)
        self.hold.acquire("pause")
        self.assertEqual(self.world.players["spotify"]["status"], "Paused")
        self.assertEqual(self.hold.paused, ["spotify"])
        self.hold.release(0)
        self.assertEqual(self.world.players["spotify"]["status"], "Playing")
        self.assertEqual(self.world.players["firefox"]["status"], "Paused")
        self.assertEqual(self.world.commands("play"), [("play", "spotify")])

    def test_a_player_he_stopped_during_the_reply_stays_stopped(self):
        self.world.player("spotify", "Playing", pid=4242).stream(7, "spotify", 4242)
        self.hold.acquire("pause")
        self.world.players["spotify"]["status"] = "Stopped"
        self.hold.release(0)
        self.assertEqual(self.world.players["spotify"]["status"], "Stopped")
        self.assertEqual(self.world.commands("play"), [])

    def test_a_player_he_started_himself_during_the_reply_is_not_paused_twice(self):
        self.world.player("spotify", "Playing", pid=4242).stream(7, "spotify", 4242)
        self.hold.acquire("pause")
        self.world.players["spotify"]["status"] = "Playing"
        self.hold.release(0)
        self.assertEqual(self.world.commands("play"), [])

    def test_sound_without_controls_cannot_pause_so_pause_mode_dims_it(self):
        self.world.stream(9, "game", 9000)
        self.hold.acquire("gvoice")
        self.assertEqual(self.world.volume(9), FULL)
        self.hold.release(0)
        self.world.log.clear()
        self.hold.acquire("pause", 30)
        self.assertEqual(self.world.volume(9), round(FULL * 30 / 100))
        self.hold.release(0)
        self.assertEqual(self.world.volume(9), FULL)


class TestOff(HoldCase):
    def test_gvoice_default_is_the_one_he_set(self):
        self.assertEqual(speakers.speech_mode({}), "gvoice")
        self.assertEqual(speakers.speech_mode({"speech_hold": "DIM"}), "dim")
        self.assertEqual(speakers.speech_mode({"speech_hold": "loud"}), "gvoice")
        self.assertEqual(speakers.speech_mode({"speech_hold": "off"}), "off")

    def test_levels_and_gaps_are_clamped(self):
        self.assertIsNone(speakers.speech_dim_to({}))
        self.assertEqual(speakers.speech_dim_to({"speech_hold_dim_to": "250"}), 100)
        self.assertEqual(speakers.speech_dim_to({"speech_hold_dim_to": "-5"}), 0)
        self.assertEqual(speakers.speech_dim_to({"speech_hold_dim_to": "x"}), None)
        self.assertIsNone(speakers.speech_dim_to({"speech_hold_dim_to": "1e999"}))
        self.assertEqual(speakers.speech_gap({"speech_hold_gap_s": "inf"}), 30.0)
        self.assertEqual(speakers.speech_gap({}), speakers.DEFAULT_GAP_S)
        self.assertEqual(speakers.speech_gap({"speech_hold_gap_s": "0"}), 0.0)
        self.assertEqual(speakers.speech_gap({"speech_hold_gap_s": "999"}), 30.0)
        self.assertEqual(speakers.speech_gap({"speech_hold_gap_s": "soon"}), speakers.DEFAULT_GAP_S)

    def test_dim_and_pause_keep_an_app_he_excluded_out_of_it(self):
        rules = {"players": "pause", "others": "nothing", "dim_to": 20,
                 "apps": [{"match": "spotify", "action": "dim", "dim_to": 35},
                          {"match": "obs", "action": "nothing"}]}
        dimmed = speakers.rules_for(rules, "dim", 15)
        self.assertEqual([(a["match"], a["action"], a.get("dim_to")) for a in dimmed["apps"]],
                         [("spotify", "dim", 15), ("obs", "nothing", None)])
        self.assertEqual(speakers.rules_for(rules, "pause")["apps"][0]["action"], "pause")
        self.assertIs(speakers.rules_for(rules, "gvoice"), rules)


class TestOverlapAndGap(HoldCase):
    def test_overlapping_holds_give_the_sound_back_once(self):
        self.spotify()
        self.hold.acquire()
        self.hold.acquire("dim", 5)
        self.assertEqual(self.world.volume(7), round(FULL * 35 / 100))
        self.hold.release(0)
        self.assertEqual(self.world.volume(7), round(FULL * 35 / 100))
        self.hold.release(0)
        self.assertEqual(self.world.volume(7), FULL)
        restores = [entry for entry in self.world.commands("volume") if entry[2][0] == FULL]
        self.assertEqual(len(restores), 1)

    def test_a_second_holder_does_not_dim_the_already_dimmed_sound_again(self):
        self.spotify()
        self.hold.acquire()
        self.hold.acquire("dim", 5)
        self.hold.release(0)
        self.hold.release(0)
        self.assertEqual(self.world.volume(7), FULL)

    def test_the_hold_waits_out_a_short_gap_before_giving_back(self):
        self.spotify()
        self.hold.acquire()
        self.hold.release()
        timer = FakeTimer.instances[-1]
        self.assertEqual(timer.seconds, 1.5)
        self.assertTrue(timer.started)
        self.assertEqual(self.world.volume(7), round(FULL * 35 / 100))
        timer.fire()
        self.assertEqual(self.world.volume(7), FULL)

    def test_the_next_reply_inside_the_gap_never_lets_the_sound_up(self):
        self.spotify()
        self.hold.acquire()
        settled = len(self.world.log)
        self.hold.release()
        first = FakeTimer.instances[-1]
        self.hold.acquire("dim", 10)
        self.assertTrue(first.cancelled)
        first.fire()
        dimmed = round(FULL * 35 / 100)
        self.assertEqual(self.world.volume(7), dimmed)
        self.assertEqual(len(self.world.log), settled)
        self.hold.release()
        FakeTimer.instances[-1].fire()
        self.assertEqual(self.world.volume(7), FULL)

    def test_a_stale_timer_cannot_release_a_hold_taken_after_it(self):
        self.spotify()
        self.hold.acquire()
        self.hold.release()
        stale = FakeTimer.instances[-1]
        stale.cancelled = False
        self.hold.acquire()
        stale.fn()
        self.assertEqual(self.world.volume(7), round(FULL * 35 / 100))
        self.hold.release(0)
        self.assertEqual(self.world.volume(7), FULL)

    def test_a_timer_that_already_fired_cannot_cut_a_later_gap_short(self):
        self.spotify()
        self.hold.acquire()
        self.hold.release()
        late = FakeTimer.instances[-1]
        self.hold.acquire()
        self.hold.release()
        current = FakeTimer.instances[-1]
        late.fn()
        self.assertEqual(self.world.volume(7), round(FULL * 35 / 100))
        current.fire()
        self.assertEqual(self.world.volume(7), FULL)

    def test_pausing_across_the_gap_resumes_once(self):
        self.world.player("spotify", "Playing", pid=4242).stream(7, "spotify", 4242)
        self.hold.acquire("pause")
        self.hold.release()
        self.hold.acquire("pause")
        self.hold.release()
        FakeTimer.instances[-1].fire()
        self.assertEqual(self.world.commands("pause"), [("pause", "spotify")])
        self.assertEqual(self.world.commands("play"), [("play", "spotify")])

    def test_release_all_gives_it_back_now_whoever_holds_it(self):
        self.spotify()
        self.hold.acquire()
        self.hold.acquire()
        self.hold.release_all()
        self.assertEqual(self.world.volume(7), FULL)
        self.assertFalse(speakers.HELD_FILE.exists())
        self.hold.release(0)
        self.assertEqual(self.hold.users, 0)

    def test_concurrent_holders_leave_one_dim_and_one_restore(self):
        self.spotify()

        def reply():
            for _ in range(20):
                self.hold.acquire()
                self.hold.release(0)

        threads = [threading.Thread(target=reply) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.hold.users, 0)
        self.assertEqual(self.world.volume(7), FULL)
        self.assertFalse(self.hold.taken)


class TestCrash(HoldCase):
    def test_the_crash_file_holds_what_was_taken_while_held(self):
        self.spotify()
        self.hold.acquire()
        saved = json.loads(speakers.HELD_FILE.read_text())
        self.assertEqual([op["op"] for op in saved["ops"]], ["dim"])
        self.assertEqual(saved["ops"][0]["from"], [FULL, FULL])

    def test_a_killed_process_gives_the_dim_back_on_the_next_start(self):
        self.spotify()
        self.hold.acquire()
        self.assertEqual(self.world.volume(7), round(FULL * 35 / 100))
        speakers.Hold.recover(MEDIA)
        self.assertEqual(self.world.volume(7), FULL)
        self.assertFalse(speakers.HELD_FILE.exists())

    def test_a_killed_process_resumes_a_pause_it_made_a_minute_ago(self):
        self.world.player("spotify", "Playing", pid=4242).stream(7, "spotify", 4242)
        self.hold.acquire("pause")
        speakers.Hold.recover(MEDIA)
        self.assertEqual(self.world.players["spotify"]["status"], "Playing")

    def test_an_old_crash_gives_the_volume_back_but_never_starts_music(self):
        self.world.player("spotify", "Playing", pid=4242).stream(7, "spotify", 4242)
        self.world.player("firefox", "Playing", pid=4300).stream(8, "firefox", 4300)
        self.hold.acquire("pause")
        self.world.players["firefox"]["status"] = "Paused"
        saved = json.loads(speakers.HELD_FILE.read_text())
        saved["at"] = time.time() - 7200
        speakers.HELD_FILE.write_text(json.dumps(saved))
        self.world.log.clear()
        speakers.Hold.recover(MEDIA)
        self.assertEqual(self.world.commands("play"), [])

    def test_shutdown_gives_back_a_hold_still_in_its_gap(self):
        self.spotify()
        self.hold.acquire()
        self.hold.release()
        with mock.patch.object(speakers, "_shared", self.hold):
            speakers.shutdown()
        self.assertEqual(self.world.volume(7), FULL)
        self.assertFalse(speakers.HELD_FILE.exists())

    def test_a_failing_apply_gives_back_what_it_took_and_dim_mode_never_pauses(self):
        self.spotify()
        with mock.patch.object(MEDIA, "apply", side_effect=RuntimeError("pactl died")):
            self.hold.acquire("dim", 10)
        self.assertEqual(self.world.commands("pause"), [])
        self.assertFalse(speakers.HELD_FILE.exists())
        self.hold.release(0)


class TestNoGvoice(unittest.TestCase):
    def test_without_gvoice_a_dim_does_nothing_and_a_pause_falls_back_to_players(self):
        with mock.patch.object(speakers, "_playerctl", return_value="spotify Playing") as playerctl, \
                mock.patch.object(speakers, "gvoice_media", return_value=None):
            dim = speakers.SoundHold(None, gap=0)
            dim.acquire("dim")
            dim.release()
            self.assertEqual(playerctl.call_count, 0)
            pause = speakers.SoundHold(None, gap=0)
            pause.acquire("pause")
            pause.release()
            self.assertGreater(playerctl.call_count, 0)


def make_wav(seconds=0.05, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class Holder:
    def __init__(self):
        self.users, self.log, self.during = 0, [], []

    def acquire(self, mode="gvoice", dim_to=None):
        self.users += 1
        self.log.append(("acquire", mode, dim_to))

    def release(self, gap=None):
        self.users -= 1
        self.log.append(("release", gap))


class Capture:
    def __init__(self):
        self.interrupted = threading.Event()

    def begin_playback(self):
        pass

    def end_playback(self):
        pass


class Proc:
    def __init__(self, holder, boom=False):
        holder.during.append(holder.users)
        if boom:
            raise OSError("pw-play missing")

    def poll(self):
        return 0

    def kill(self):
        pass

    def wait(self):
        return 0


class TestSpeakerHolds(unittest.TestCase):
    def speaker(self, cfg=None, boom=False):
        self.holder = Holder()
        speaker = tts.Speaker(cfg or {}, Capture(), sleep=lambda s: None, gap=lambda: 0.0, hold=self.holder,
                              spawn=lambda path: Proc(self.holder, boom))
        speaker._cloud_wav = lambda text, cfg: make_wav()
        return speaker

    def test_a_spoken_reply_holds_other_sound_for_its_whole_length(self):
        speaker = self.speaker()
        speaker.say("good evening", {})
        self.assertEqual(self.holder.during, [1])
        self.assertEqual(self.holder.log, [("acquire", "gvoice", None), ("release", speakers.DEFAULT_GAP_S)])

    def test_a_streamed_reply_is_one_hold_not_one_per_sentence(self):
        speaker = self.speaker()
        speaker.say_stream(iter(["one", "two", "three"]), {})
        self.assertEqual(self.holder.during, [1, 1, 1])
        self.assertEqual([entry[0] for entry in self.holder.log], ["acquire", "release"])

    def test_the_setting_picks_mode_level_and_gap(self):
        speaker = self.speaker()
        speaker.say("hello", {"speech_hold": "dim", "speech_hold_dim_to": "12", "speech_hold_gap_s": "3"})
        self.assertEqual(self.holder.log, [("acquire", "dim", 12), ("release", 3.0)])

    def test_off_holds_nothing(self):
        speaker = self.speaker()
        speaker.say("hello", {"speech_hold": "off"})
        self.assertEqual(self.holder.log, [])
        self.assertEqual(self.holder.during, [0])

    def test_a_failed_player_still_gives_the_sound_back(self):
        speaker = self.speaker(boom=True)
        with self.assertRaises(OSError):
            speaker.say("hello", {})
        self.assertEqual(self.holder.users, 0)
        self.assertEqual([entry[0] for entry in self.holder.log], ["acquire", "release"])

    def test_a_cached_clip_follows_the_setting_the_last_reply_was_given(self):
        speaker = self.speaker()
        speaker.say("hello", {"speech_hold": "off"})
        speaker.play_wav(make_wav())
        self.assertEqual(self.holder.log, [])

    def test_nothing_to_say_holds_nothing(self):
        speaker = self.speaker()
        speaker._cloud_wav = lambda text, cfg: None
        speaker._piper_wav = lambda text, cfg: None
        self.assertEqual(speaker.say("hello", {})[0], "unspoken")
        self.assertEqual(self.holder.log, [])

    def test_muted_and_deaf_hold_nothing(self):
        speaker = self.speaker()
        with mock.patch("voice.state.is_muted", return_value=True):
            speaker.say("hello", {})
        with mock.patch("voice.state.is_deaf", return_value=True):
            speaker.say("hello", {})
        self.assertEqual(self.holder.log, [])

    def test_a_cut_off_reply_gives_the_sound_back(self):
        speaker = self.speaker()
        original = speaker._play

        def cut(wav, interruptible, duration, track=None):
            speaker._killed.set()
            return original(wav, interruptible, duration)

        speaker._play = cut
        state = speaker.say("hello", {})[0]
        self.assertEqual(state, "interrupted")
        self.assertEqual(self.holder.users, 0)

    def test_a_hold_that_breaks_never_stops_him_speaking(self):
        speaker = self.speaker()
        self.holder.acquire = mock.Mock(side_effect=RuntimeError("no pactl"))
        self.assertEqual(speaker.say("hello", {})[0], "done")

    def test_the_gap_keeps_back_to_back_replies_in_one_hold(self):
        world = World().player("spotify", "Playing", pid=4242).stream(7, "spotify", 4242)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(MEDIA, "run", world.run), \
                mock.patch.object(MEDIA, "SETTINGS_FILE", Path(tmp) / "s.json"), \
                mock.patch.object(MEDIA, "DIM_SECONDS", 0.0), mock.patch.object(MEDIA, "RESTORE_SECONDS", 0.0), \
                mock.patch.object(speakers, "HELD_FILE", Path(tmp) / "held.json"):
            Path(tmp, "s.json").write_text(json.dumps({"media": {"players": "dim", "dim_to": 20}}))
            FakeTimer.instances = []
            hold = speakers.SoundHold(MEDIA, gap=1.5, timer=FakeTimer)
            speaker = tts.Speaker({}, Capture(), sleep=lambda s: None, gap=lambda: 0.0, hold=hold,
                                  spawn=lambda path: Proc(Holder()))
            speaker._cloud_wav = lambda text, cfg: make_wav()
            speaker.say("first", {})
            speaker.say("second", {})
            dimmed = round(FULL * 20 / 100)
            self.assertEqual(world.volume(7), dimmed)
            FakeTimer.instances[-1].fire()
            self.assertEqual(world.volume(7), FULL)
            self.assertEqual(len([e for e in world.commands("volume") if e[2][0] == FULL]), 1)


if __name__ == "__main__":
    unittest.main()
