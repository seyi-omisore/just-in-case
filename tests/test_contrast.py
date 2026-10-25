"""Every text and background pair the site uses meets WCAG AA, in all three themes.

Reads the color tokens straight from web/static/style.css, so changing a color
there is checked here. Run: pytest -q
"""

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "web" / "static" / "style.css").read_text()
THEMES = {"light": r":root", "dark": r'\[data-theme="dark"\]', "contrast": r'\[data-theme="contrast"\]'}

# (foreground, background): regular text needs 4.5 to 1.
TEXT_PAIRS = [
    *[("ink", bg) for bg in ("wall", "paper", "enamel", "primary-tint", "success-tint", "accent-tint", "note")],
    *[("ink-soft", bg) for bg in ("wall", "paper", "enamel", "primary-tint", "note")],
    *[("link", bg) for bg in ("wall", "paper", "enamel", "primary-tint")],
    ("on-primary", "primary"), ("on-primary", "primary-hover"),
    *[("ok", bg) for bg in ("wall", "paper", "enamel", "success-tint")],
    ("route-person", "accent-tint"), ("on-accent", "accent"),
    ("strip-ink", "strip"),
]
# Icons, borders that must be seen, and focus rings need 3 to 1 (WCAG 1.4.11).
UI_PAIRS = [
    ("strip-icon", "strip"),
    ("primary", "paper"), ("primary", "wall"),
    ("edge", "paper"), ("edge", "wall"),
    ("success", "paper"), ("success", "enamel"),
    ("focus", "paper"), ("focus", "wall"),
    *[(s, "paper") for s in ("sit-ride", "sit-key", "sit-pet", "sit-mail", "sit-other")],
    *[("sit-ink", s) for s in ("sit-ride", "sit-key", "sit-pet", "sit-mail", "sit-other")],
]


def tokens(theme: str) -> dict[str, str]:
    """Light tokens, with the theme's own tokens laid on top."""
    def read(selector):
        out = {}
        for body in re.findall(selector + r"\s*\{([^}]*)\}", CSS):
            out.update(dict(re.findall(r"--([a-z-]+)\s*:\s*([^;]+);", body)))
        return out
    found = read(THEMES["light"])
    if theme != "light":
        found.update(read(THEMES[theme]))
    return found


def color(theme: str, name: str) -> str:
    t = tokens(theme)
    value = t[name].strip()
    for _ in range(5):  # follow var(--x) aliases
        m = re.fullmatch(r"var\(--([a-z-]+)\)", value)
        if not m:
            break
        value = t[m.group(1)].strip()
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", value), f"{theme} --{name} is not a plain color: {value}"
    return value


def luminance(hex_color: str) -> float:
    def channel(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(hex_color[i:i + 2]) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("fg,bg", TEXT_PAIRS)
def test_text_contrast_is_at_least_4_5(theme, fg, bg):
    r = ratio(color(theme, fg), color(theme, bg))
    assert r >= 4.5, f"{theme}: --{fg} on --{bg} is {r:.2f} to 1"


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("fg,bg", UI_PAIRS)
def test_icons_and_borders_contrast_is_at_least_3(theme, fg, bg):
    r = ratio(color(theme, fg), color(theme, bg))
    assert r >= 3, f"{theme}: --{fg} on --{bg} is {r:.2f} to 1"


def test_high_contrast_uses_only_black_white_and_yellow():
    allowed = {"#000000", "#FFFFFF", "#FFFF00"}
    body = "".join(re.findall(THEMES["contrast"] + r"\s*\{([^}]*)\}", CSS))
    used = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", body)}
    assert used <= allowed, used - allowed


def test_coral_accent_is_never_used_for_text():
    rules = re.findall(r"([^{}]+)\{([^}]*)\}", CSS)
    offenders = [sel.strip() for sel, body in rules
                 if re.search(r"(^|[;\s])color:\s*var\(--(accent|orange|vermillion)\)", body)
                 and ".icon" not in sel]  # a small decorative icon beside words is allowed
    assert not offenders, offenders


def test_soft_tiles_and_the_red_phone_icon_are_readable():
    for theme in THEMES:
        for fg in ("ink", "ink-soft", "link", "ok"):
            assert ratio(color(theme, fg), color(theme, "soft")) >= 4.5, (theme, fg)
        for bg in ("soft", "paper"):
            assert ratio(color(theme, "alert-icon"), color(theme, bg)) >= 3, (theme, bg)


def test_the_seasonal_tip_icon_shows_on_its_note():
    for theme in THEMES:
        assert ratio(color(theme, "season-icon"), color(theme, "note")) >= 3, theme


def test_settings_section_tiles_show_their_icons():
    for theme in THEMES:
        assert ratio(color(theme, "on-season"), color(theme, "season-icon")) >= 3, theme
        assert ratio(color(theme, "ink"), color(theme, "blue-tint")) >= 4.5, theme
        assert ratio(color(theme, "ink-soft"), color(theme, "blue-tint")) >= 4.5, theme
