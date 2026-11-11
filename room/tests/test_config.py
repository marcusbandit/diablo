import tomllib

from room import color, config


def test_load_merges_over_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('settle_s = 5\n[scenes.focus]\nbri = 80\n')
    cfg = config.load(p)
    assert cfg["settle_s"] == 5
    assert cfg["scenes"]["focus"] == {"bri": 80, "k": 4300}
    assert cfg["scenes"]["cinema"] == config.DEFAULTS["scenes"]["cinema"]


def test_missing_file_is_defaults(tmp_path):
    assert config.load(tmp_path / "nope.toml") == config.DEFAULTS


def test_save_choices_round_trips(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[scenes.cinema.lamps."Wood lamp"]\nbri = 3\n')
    config.save_choices({"lamps": ["Wood lamp", "The corner"], "bedtime.enabled": True, "night.start": "22:30"}, p)
    data = tomllib.loads(p.read_text())
    assert data["lamps"] == ["Wood lamp", "The corner"]
    assert data["bedtime"]["enabled"] is True
    assert data["night"]["start"] == "22:30"
    assert data["scenes"]["cinema"]["lamps"]["Wood lamp"]["bri"] == 3


def test_env_file(tmp_path):
    p = tmp_path / "keys.env"
    p.write_text('# hue\nexport HUE_BRIDGE=10.0.0.2\nHUE_TOKEN="abc"\n')
    assert config.read_env_file(p) == {"HUE_BRIDGE": "10.0.0.2", "HUE_TOKEN": "abc"}


def test_colour_round_trip():
    xy = color.hex_to_xy("#ff7a2e")
    r, g, b = color.xy_to_rgb(xy)
    assert r == 1.0 and g < 0.8 and b < 0.4
    assert color.mirek(2200) == 455 and color.kelvin(455) == 2198
