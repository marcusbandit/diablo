import itertools
import json
import random
import unittest
from unittest import mock

from hub import diagram, pointing

SCREEN = {"x": 2560, "y": 0, "w": 1920, "h": 1080}


def chain(*labels):
    beats = []
    for i, label in enumerate(labels):
        beat = {"say": f"Line {i}.", "nodes": [{"id": f"n{i}", "label": label}]}
        if i:
            beat["edges"] = [{"from": f"n{i - 1}", "to": f"n{i}"}]
        beats.append(beat)
    return {"title": "A chain", "beats": beats}


def laid_out(arguments, screen=SCREEN):
    beats, notes = diagram.plan(arguments, screen, show="t1")
    nodes = {m["text"]: m["box"] for b in beats for m in b["marks"] if m["kind"] == "node"}
    edges = [m for b in beats for m in b["marks"] if m["kind"] == "edge"]
    return beats, notes, nodes, edges


def overlap(a, b, gap=0):
    return a["x"] < b["x"] + b["w"] + gap and b["x"] < a["x"] + a["w"] + gap and a["y"] < b["y"] + b["h"] + gap and b["y"] < a["y"] + a["h"] + gap


def on_border(point, box, shape, tol=0.6):
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    dx, dy = abs(point["x"] - cx), abs(point["y"] - cy)
    if shape == "diamond":
        return abs(dx / (box["w"] / 2) + dy / (box["h"] / 2) - 1) < 0.02
    inside = dx <= box["w"] / 2 + tol and dy <= box["h"] / 2 + tol
    edge = abs(dx - box["w"] / 2) <= tol or abs(dy - box["h"] / 2) <= tol
    return inside and edge


def random_graph(seed):
    rng = random.Random(seed)
    count = rng.randint(3, 14)
    ids = [f"k{i}" for i in range(count)]
    beats = [{"say": "s", "nodes": [], "edges": []} for _ in range(rng.randint(3, 8))]
    for i, key in enumerate(ids):
        beats[min(i * len(beats) // count, len(beats) - 1)]["nodes"].append(
            {"id": key, "label": "word " * rng.randint(1, 5), "shape": rng.choice(["box", "round", "diamond"])})
    pairs = [(a, b) for a, b in itertools.permutations(ids, 2)]
    for a, b in rng.sample(pairs, min(len(pairs), rng.randint(2, 20))):
        rng.choice(beats)["edges"].append({"from": a, "to": b})
    return {"title": "Random", "direction": rng.choice(["down", "right"]), "beats": beats}


class Layout(unittest.TestCase):
    def test_ranks_follow_the_longest_path_from_the_sources(self):
        nodes = [{"id": k, "label": k, "shape": "box", "order": i} for i, k in enumerate("abcde")]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "a", "to": "c"}, {"from": "d", "to": "c"},
                 {"from": "c", "to": "e"}]
        self.assertEqual(diagram.ranks_of(nodes, edges), {"a": 0, "b": 1, "c": 2, "d": 0, "e": 3})

    def test_a_cycle_still_gets_ranks(self):
        nodes = [{"id": k, "label": k, "shape": "box", "order": i} for i, k in enumerate("abc")]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}]
        self.assertEqual(diagram.ranks_of(nodes, edges), {"a": 0, "b": 1, "c": 2})

    def test_down_puts_ranks_in_rows_ordered_by_first_appearance_and_centered(self):
        _, _, nodes, _ = laid_out(diagram.SAMPLE)
        self.assertLess(nodes["User program"]["y"], nodes["System call"]["y"])
        self.assertLess(nodes["Kernel"]["y"], nodes["Scheduler"]["y"])
        self.assertEqual(nodes["Scheduler"]["y"], nodes["Device drivers"]["y"])
        self.assertLess(nodes["Scheduler"]["x"], nodes["Device drivers"]["x"])
        mid = SCREEN["x"] + SCREEN["w"] / 2
        for label in ("User program", "System call", "Kernel", "Hardware"):
            box = nodes[label]
            self.assertAlmostEqual(box["x"] + box["w"] / 2, mid, delta=1)
        pair = nodes["Scheduler"]["x"] + nodes["Device drivers"]["x"] + nodes["Device drivers"]["w"]
        self.assertAlmostEqual(pair / 2, mid, delta=1)

    def test_right_puts_ranks_in_columns(self):
        _, _, nodes, _ = laid_out({**chain("One", "Two", "Three"), "direction": "right"})
        self.assertLess(nodes["One"]["x"] + nodes["One"]["w"], nodes["Two"]["x"])
        self.assertAlmostEqual(nodes["One"]["y"], nodes["Three"]["y"], delta=1)

    def test_nodes_fit_their_labels(self):
        _, _, nodes, _ = laid_out(chain("A", "A label of twenty eight ch"))
        self.assertGreater(nodes["A label of twenty eight ch"]["w"], nodes["A"]["w"])
        size = nodes["A label of twenty eight ch"]
        self.assertGreaterEqual(size["w"], len("A label of twenty eight ch") * 17 * 0.6)

    def test_no_overlaps_everything_inside_the_region_and_edges_on_borders(self):
        for seed in range(60):
            arguments = random_graph(seed)
            region = diagram.region_of(SCREEN)
            beats, _, _, _ = laid_out(arguments)
            nodes = [m for b in beats for m in b["marks"] if m["kind"] == "node"]
            shapes = {json.dumps(m["box"]): m["shape"] for m in nodes}
            for a, b in itertools.combinations(nodes, 2):
                self.assertFalse(overlap(a["box"], b["box"], gap=8), f"seed {seed}: {a['text']} and {b['text']}")
            for m in nodes:
                box = m["box"]
                self.assertGreaterEqual(box["x"], region["x"] - 0.5, seed)
                self.assertGreaterEqual(box["y"], region["y"] - 0.5, seed)
                self.assertLessEqual(box["x"] + box["w"], region["x"] + region["w"] + 0.5, seed)
                self.assertLessEqual(box["y"] + box["h"], region["y"] + region["h"] + 0.5, seed)
            for e in (m for b in beats for m in b["marks"] if m["kind"] == "edge"):
                ends = [p for p in (e["start"], e["end"])]
                for point in ends:
                    self.assertTrue(any(on_border(point, json.loads(k), shape) for k, shape in shapes.items()), f"seed {seed}: {point}")

    def test_a_rank_too_wide_for_the_region_wraps_and_stays_inside(self):
        leaves = [{"id": f"l{i}", "label": f"A rather long leaf label {i:02d}"} for i in range(13)]
        for direction in ("down", "right"):
            arguments = {"title": "Star", "direction": direction, "beats": [
                {"say": "s", "nodes": [{"id": "hub", "label": "Hub"}] + leaves, "edges": [{"from": "hub", "to": l["id"]} for l in leaves]}]}
            beats, _, nodes, _ = laid_out(arguments)
            region = diagram.region_of(SCREEN)
            for a, b in itertools.combinations(nodes.values(), 2):
                self.assertFalse(overlap(a, b, gap=4))
            for box in nodes.values():
                self.assertGreaterEqual(box["x"], region["x"] - 0.5)
                self.assertLessEqual(box["x"] + box["w"], region["x"] + region["w"] + 0.5)
                self.assertGreaterEqual(box["y"], region["y"] - 0.5)
                self.assertLessEqual(box["y"] + box["h"], region["y"] + region["h"] + 0.5)
            self.assertGreaterEqual(beats[0]["marks"][1]["size"], 17 * 0.5)

    def test_the_region_is_seventy_percent_of_the_monitor_centered(self):
        region = diagram.region_of(SCREEN)
        self.assertEqual((region["w"], region["h"]), (1920 * 0.7, 1080 * 0.7))
        self.assertAlmostEqual(region["x"] + region["w"] / 2, 2560 + 960)
        self.assertEqual(diagram.region_of(None), diagram.region_of(diagram.DEFAULT_SCREEN))
        self.assertEqual(diagram.region_of({"x": 0, "y": 0, "w": "wide"}), diagram.region_of(diagram.DEFAULT_SCREEN))

    def test_a_skipping_edge_bends_round_the_box_between(self):
        arguments = {"title": "t", "beats": [{"say": "s", "nodes": [{"id": "a", "label": "Top"}, {"id": "b", "label": "Middle"},
                                                                    {"id": "c", "label": "Bottom"}],
                                               "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "a", "to": "c"}]}]}
        _, _, nodes, edges = laid_out(arguments)
        skip = edges[2]
        middle = nodes["Middle"]
        self.assertFalse(diagram.hits((skip["start"]["x"], skip["start"]["y"]), (skip["via"]["x"], skip["via"]["y"]),
                                      (skip["end"]["x"], skip["end"]["y"]), [middle], pad=0))

    def test_the_title_sits_above_the_region(self):
        beats, _, _, _ = laid_out(diagram.SAMPLE)
        title = beats[0]["marks"][0]
        self.assertEqual(title["kind"], "title")
        self.assertLess(title["box"]["y"] + title["box"]["h"], diagram.region_of(SCREEN)["y"])


class Plan(unittest.TestCase):
    def test_every_beat_keeps_the_earlier_ones_and_lingers(self):
        beats, notes, _, _ = laid_out(diagram.SAMPLE)
        self.assertEqual(notes, [])
        self.assertEqual([b["n"] for b in beats], [0, 1, 2, 3, 4])
        self.assertTrue(all(b["keep"] and b["count"] == 5 and b["linger"] == 45 and b["show"] == "t1" for b in beats))
        self.assertEqual([[m["kind"] for m in b["marks"]] for b in beats][:2], [["title", "node"], ["node", "edge"]])

    def test_pieces_are_never_repeated_across_beats(self):
        arguments = chain("One", "Two")
        arguments["beats"][1]["nodes"].append({"id": "n0", "label": "One again"})
        beats, _, _, _ = laid_out(arguments)
        labels = [m["text"] for b in beats for m in b["marks"] if m["kind"] == "node"]
        self.assertEqual(labels, ["One", "Two"])

    def test_an_edge_waits_for_the_beat_that_brings_its_node(self):
        arguments = {"title": "t", "beats": [{"say": "a", "nodes": [{"id": "a", "label": "A"}], "edges": [{"from": "a", "to": "b"}]},
                                             {"say": "b", "nodes": [{"id": "b", "label": "B"}]}]}
        beats, _, _, _ = laid_out(arguments)
        self.assertEqual([m["kind"] for m in beats[1]["marks"]], ["node", "edge"])

    def test_edges_may_name_a_node_by_its_label(self):
        arguments = {"title": "t", "beats": [{"say": "a", "nodes": [{"id": "a", "label": "Kernel"}, {"id": "b", "label": "Disk"}],
                                              "edges": [{"from": "kernel", "to": "Disk"}]}]}
        _, _, _, edges = laid_out(arguments)
        self.assertEqual(len(edges), 1)

    def test_caps(self):
        many = {"title": "x" * 90, "beats": [{"say": f"s{i}", "nodes": [{"id": f"{i}-{j}", "label": "L" * 40} for j in range(2)]}
                                             for i in range(10)]}
        beats, notes, nodes, _ = laid_out(many)
        self.assertEqual(len(beats), diagram.MAX_BEATS)
        self.assertEqual(len([m for b in beats for m in b["marks"] if m["kind"] == "node"]), diagram.MAX_NODES)
        self.assertTrue(all(len(m["text"]) <= 28 for b in beats for m in b["marks"] if m["kind"] == "node"))
        self.assertEqual(len(beats[0]["marks"][0]["text"]), diagram.TITLE)
        self.assertTrue(any("8 beats" in n for n in notes))
        ids = [f"n{i}" for i in range(7)]
        edges = [{"from": a, "to": b} for a, b in itertools.permutations(ids, 2)]
        lots = {"title": "t", "beats": [{"say": "s", "nodes": [{"id": k, "label": k} for k in ids], "edges": edges}]}
        _, notes, _, got = laid_out(lots)
        self.assertEqual(len(got), diagram.MAX_EDGES)
        self.assertTrue(any("20" in n for n in notes))

    def test_bad_input_is_refused_or_cleaned(self):
        self.assertIn("nothing to draw", diagram.plan({"title": "t", "beats": [{"say": "hi"}]}))
        self.assertIn("nothing to draw", diagram.plan({"title": "t", "beats": "no"}))
        self.assertIn("give a title", diagram.plan("no"))
        beats, notes = diagram.plan({"title": "t", "direction": "sideways", "beats": [
            {"say": "a", "nodes": [{"id": "a", "label": "A", "shape": "star"}, 5], "edges": [{"from": "a", "to": "ghost"}, "x"]}]})
        self.assertEqual(beats[0]["marks"][1]["shape"], "box")
        self.assertTrue(any("ghost" in n for n in notes))

    def test_every_beat_survives_the_endpoint_cleaning(self):
        beats, _, _, _ = laid_out(diagram.SAMPLE)
        for beat in beats:
            out = pointing.clean_payload(beat)
            self.assertIsInstance(out, dict)
            self.assertEqual(len(out["marks"]), len(beat["marks"]))
            self.assertTrue(out["keep"])
            self.assertEqual(out["linger"], 45)
        edge = next(m for m in pointing.clean_payload(beats[1])["marks"] if m["kind"] == "edge")
        self.assertEqual(edge["text"], "read, write, fork")
        self.assertIn("box", edge)


class Cleaning(unittest.TestCase):
    def test_diagram_pieces_only_go_out_on_a_keep_beat(self):
        node = {"kind": "node", "text": "Kernel", "box": {"x": 1, "y": 2, "w": 100, "h": 40}}
        plain = pointing.clean_payload({"show": "s", "n": 0, "marks": [node, {"kind": "ring", "box": node["box"]}]})
        self.assertEqual([m["kind"] for m in plain["marks"]], ["ring"])
        self.assertNotIn("keep", plain)
        self.assertNotIn("linger", plain)
        kept = pointing.clean_payload({"show": "s", "n": 0, "keep": True, "marks": [node]})
        self.assertEqual(kept["marks"][0], {"kind": "node", "box": node["box"], "text": "Kernel", "shape": "box"})

    def test_pieces_without_their_parts_are_dropped(self):
        for raw in ({"kind": "node", "box": {"x": 0, "y": 0, "w": 5, "h": 5}}, {"kind": "node", "text": "a"},
                    {"kind": "edge", "start": {"x": 0, "y": 0}}, {"kind": "title", "text": "t"}):
            self.assertIsNone(pointing.clean_mark(raw), raw)

    def test_point_on_screen_beats_never_carry_diagram_pieces(self):
        beats = pointing.clean_beats([{"say": "a", "marks": [{"kind": "node", "text": "x", "box": {"x": 0, "y": 0, "w": 9, "h": 9}}]}])
        self.assertEqual(beats[0]["marks"], [])

    def test_a_long_linger_is_capped(self):
        out = pointing.clean_payload({"show": "s", "n": 0, "keep": True, "linger": 9999, "marks": []})
        self.assertNotIn("linger", out)


class Tool(unittest.TestCase):
    def setUp(self):
        from hub import mcp_server

        self.mcp = mcp_server

    def test_it_is_listed_and_teaches_fast_use(self):
        tool = next(t for t in self.mcp.EXTRA if t["name"] == "draw_diagram")
        for words in ("One call per answer", "3 to 6 beats", "Never write an HTML page", "flow", "architecture", "hierarchy"):
            self.assertIn(words, tool["description"])
        self.assertEqual(tool["inputSchema"]["required"], ["title", "beats"])

    def test_a_call_sends_every_beat_in_order(self):
        sent = []
        text = self.mcp.draw_diagram(diagram.SAMPLE, screen=SCREEN, send=lambda body: sent.append(body))
        self.assertEqual([b["n"] for b in sent], [0, 1, 2, 3, 4])
        self.assertEqual(len({b["show"] for b in sent}), 1)
        self.assertIn("5 beats", text)
        self.assertIn("6 boxes and 6 arrows", text)
        self.assertIn("say the beats' lines in order", text)

    def test_it_goes_through_call_and_reads_the_monitor_from_the_desktop(self):
        posted = []
        done = mock.Mock(stdout='{"class": "x", "screen": {"x": 100, "y": 0, "w": 2560, "h": 1440}}\n', stderr="", returncode=0)
        with mock.patch.object(self.mcp, "screen_post", side_effect=lambda path, body: posted.append((path, body))), \
                mock.patch("hub.desktop.desktop_run", return_value=done), \
                mock.patch.object(self.mcp.lockguard, "refusal", return_value=None), \
                mock.patch.object(self.mcp.character, "refusal", return_value=None):
            text = self.mcp.call("draw_diagram", diagram.SAMPLE)
        self.assertEqual({p for p, _ in posted}, {"/v1/screen/marks"})
        node = posted[0][1]["marks"][1]
        self.assertGreater(node["box"]["x"], 100 + 2560 * 0.15)
        self.assertIn("Drawing", text)

    def test_an_unreachable_desktop_falls_back_to_a_default_region(self):
        from hub import desktop

        with mock.patch("hub.desktop.desktop_run", side_effect=desktop.Unreachable("down")):
            self.assertIsNone(self.mcp.desktop_screen())
        sent = []
        self.mcp.draw_diagram(diagram.SAMPLE, screen={}, send=lambda body: sent.append(body))
        region = diagram.region_of(None)
        node = sent[0]["marks"][1]["box"]
        self.assertGreaterEqual(node["x"], region["x"])

    def test_a_failed_send_stops_and_says_so(self):
        self.assertEqual(self.mcp.draw_diagram(diagram.SAMPLE, screen=SCREEN, send=lambda body: "could not reach the hub: down"),
                         "not drawn: could not reach the hub: down")

    def test_nothing_to_draw_is_said_plainly(self):
        self.assertIn("nothing to draw", self.mcp.draw_diagram({"title": "t", "beats": []}, screen=SCREEN))


if __name__ == "__main__":
    unittest.main()
