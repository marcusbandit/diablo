"""Colour maths: kelvin and mirek, sRGB hex to CIE xy for Hue, and back to RGB for previews."""


def mirek(kelvin):
    return round(1_000_000 / max(1000, kelvin))


def kelvin(mirek_value):
    return round(1_000_000 / max(1, mirek_value))


def hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"not a colour: {hex_color!r}")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def _expand(c):
    return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92


def _compress(c):
    c = max(0.0, c)
    return 1.055 * c ** (1 / 2.4) - 0.055 if c > 0.0031308 else 12.92 * c


def hex_to_xy(hex_color):
    """sRGB to CIE 1931 xy with the wide-gamut D65 matrix Philips documents for Hue."""
    r, g, b = (_expand(c) for c in hex_to_rgb(hex_color))
    x = r * 0.649926 + g * 0.103455 + b * 0.197109
    y = r * 0.234327 + g * 0.743075 + b * 0.022598
    z = g * 0.053077 + b * 1.035763
    total = x + y + z
    if total == 0:
        return (0.3227, 0.329)  # white point; black has no chromaticity
    return (round(x / total, 4), round(y / total, 4))


def xy_to_rgb(xy, brightness=1.0):
    """CIE xy back to display RGB, normalised so the brightest channel is `brightness`."""
    x, y = xy
    if y <= 0:
        return (0.0, 0.0, 0.0)
    big_y = 1.0
    big_x = big_y / y * x
    big_z = big_y / y * (1 - x - y)
    r = big_x * 1.656492 - big_y * 0.354851 - big_z * 0.255038
    g = -big_x * 0.707196 + big_y * 1.655397 + big_z * 0.036152
    b = big_x * 0.051713 - big_y * 0.121364 + big_z * 1.011530
    rgb = [_compress(c) for c in (r, g, b)]
    top = max(rgb) or 1
    return tuple(c / top * brightness for c in rgb)


def kelvin_to_rgb(k, brightness=1.0):
    """Approximate colour of a black body, for drawing lamp previews (Tanner Helland's fit)."""
    import math

    t = k / 100
    if t <= 66:
        r = 255
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255
    return tuple(max(0, min(255, c)) / 255 * brightness for c in (r, g, b))


def white_rgb(k, brightness=1.0):
    """How a white bulb at k kelvin looks to a person in the room. The black body curve alone
    draws 2700 K as orange, but eyes adapt and see warm white, so mix a third of white in."""
    r, g, b = kelvin_to_rgb(k)
    return tuple((c * 0.65 + 0.35) * brightness for c in (r, g, b))


def preview_hex(target):
    """What a lamp target looks like on screen: off is near black, brightness scales lightness."""
    if not target.on:
        return "#14171b"
    # Perceived lightness rises fast at the low end, so a 5 % lamp still reads as lit.
    level = 0.28 + 0.72 * (max(0.0, min(100.0, target.bri)) / 100) ** 0.5
    if target.xy:
        return rgb_to_hex(xy_to_rgb(target.xy, level))
    if target.mirek:
        return rgb_to_hex(white_rgb(kelvin(target.mirek), level))
    return rgb_to_hex((level, level * 0.93, level * 0.82))
