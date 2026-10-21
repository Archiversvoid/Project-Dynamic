"""
theme.py - Dynamic's Dark / Light theming, in one place.

HOW IT WORKS
    The app's source is written in its DARK colors (BG_DARK, CARD_BG,
    "#2A2A2A", ...). Those stay the single source of truth. Light mode is
    produced by MAPPING each color at the moment it is used:

      * widgets   _ss(widget, qss)   stores the dark-source stylesheet on the
                  widget and applies the mapped one. restyle() re-applies it
                  after a switch, so widgets already on screen recolor in place.
      * painters  _qc("#...")        instead of QColor("#...")

    Never call widget.setStyleSheet() directly in app code - that widget would
    not follow the theme. Use _ss(widget, qss).

LIGHT PALETTE
    The two whites come from the reference picture:
      LIGHT_BG    #F1F3F4   page background
      LIGHT_FG    #FFFFFF   sidebar, cards, popups (raised surfaces)
    plus the picture's third gray, #DEE1E6, for inner surfaces and borders, and
    a few derived grays for text.
"""
import colorsys
import re
import sys
from pathlib import Path

_mode = "dark"

LIGHT_BG = "#F1F3F4"
LIGHT_FG = "#FFFFFF"
LIGHT_INNER = "#DEE1E6"


def mode():
    return _mode


def is_light():
    return _mode == "light"


def set_mode(m):
    """Returns True if the mode actually changed."""
    global _mode
    m = "light" if m == "light" else "dark"
    changed = m != _mode
    _mode = m
    return changed


# ------------------------------------------------------------ which theme?
def system_prefers_light():
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
        except Exception:
            pass
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import Qt
        if QGuiApplication.instance() is None:
            return False
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light
    except Exception:
        return False


def resolve(setting):
    """'Default' | 'Dark' | 'Light'  ->  'dark' | 'light'.
    Default follows the Windows app-theme setting (dark if it can't tell)."""
    s = (setting or "Default").strip().lower()
    if s == "light":
        return "light"
    if s == "dark":
        return "dark"
    return "light" if system_prefers_light() else "dark"


# --------------------------------------------------------- color mapping
# Colors that mean the same thing in both themes: ink on accent fills, video black.
_KEEP = {"#000000", "#0B0B0B", "#000"}

# (role, dark color) -> light color. Anything not listed falls through to the
# rules below, so a color added to the app later still lands somewhere sensible.
_EXPLICIT = {
    ("bg", "#142C28"): "#D5F0EB",      # teal-tinted active pill
    ("bg", "#1B3D37"): "#BFE6DF",      # teal-tinted badge
    ("bg", "#2A1515"): "#FCE8E6",      # error panel
    ("border", "#4A2020"): "#F5C6C2",  # error panel border
    ("text", "#00BFA5"): "#00897B",     # accent as TEXT needs a deeper teal on white
    ("text", "#FF5555"): "#D93025",
    ("text", "#FF8888"): "#C5221F",
    ("text", "#EF4444"): "#D93025",
}


def _hex6(h):
    h = h.strip().upper()
    if len(h) == 4:
        h = "#" + "".join(ch * 2 for ch in h[1:])
    return h


def _neutral(g, role):
    """Map a neutral gray (0-255). None = leave as is."""
    if role == "text":
        if g >= 0xF0: return "#202124"
        if g >= 0xC0: return "#3C4043"
        if g >= 0x80: return "#5F6368"
        if g >= 0x50: return "#9AA0A6"
        if g >= 0x19: return "#BDC1C6"
        return None                      # near-black ink: keep
    if role == "border":
        if g <= 0x20: return "#E8EAED"
        if g <= 0x3C: return "#DADCE0"
        return "#BDC1C6"
    # surfaces
    if g <= 0x14: return LIGHT_BG        # page
    if g <= 0x20: return LIGHT_FG        # sidebar / cards
    if g <= 0x26: return LIGHT_BG        # hover on a white row
    if g <= 0x30: return "#E8EAED"       # inner placeholder / chip
    if g <= 0x40: return LIGHT_INNER     # inputs, active segments
    if g <= 0x6E: return "#BDC1C6"
    return None                          # already a light fill (e.g. selection)


def _from_hls(h, l, s):
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def _colored(r, g, b, role):
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if role == "bg" or (role is None and l < 0.4):
        if l < 0.3:                       # dark tint -> pastel tint
            return _from_hls(h, min(0.94, max(0.78, 0.97 - 1.2 * l)), min(s, 0.6))
        return None
    if role == "border":
        return _from_hls(h, 0.80, min(s, 0.5)) if l < 0.3 else None
    if l > 0.55:                          # bright colored text -> deeper
        return _from_hls(h, l * 0.55, s)
    return None


def map_hex(h, role=None):
    """role: 'bg' | 'text' | 'border' | None (unknown: judged by brightness)."""
    if _mode != "light":
        return h
    key = _hex6(h)
    if key in _KEEP or key == "#000000":
        return h
    hit = _EXPLICIT.get((role, key))
    if hit:
        return hit
    r, g, b = int(key[1:3], 16), int(key[3:5], 16), int(key[5:7], 16)
    if max(r, g, b) - min(r, g, b) <= 12:
        avg = (r + g + b) // 3
        eff = role or ("text" if avg >= 100 else "bg")
        res = _neutral(avg, eff)
    else:
        res = _colored(r, g, b, role)
    return res or h


# ----------------------------------------------------------- stylesheets
_DECL = re.compile(r'([A-Za-z-]+)(\s*:\s*)([^;{}]*)')
_HEX = re.compile(r'#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b')
_qss_cache = {}


def _role_for(prop):
    p = prop.lower()
    if p in ("color", "selection-color", "placeholder-text-color"):
        return "text"
    if p.startswith("background") or p in ("selection-background-color", "alternate-background-color"):
        return "bg"
    if p.startswith("border") or p.startswith("outline"):
        return "border"
    return None


def qss(src):
    """Dark-source stylesheet -> stylesheet for the current mode."""
    if _mode != "light" or "#" not in src:
        return src
    key = (src,)
    hit = _qss_cache.get(key)
    if hit is not None:
        return hit

    def decl(m):
        val = m.group(3)
        if "#" not in val:
            return m.group(0)
        role = _role_for(m.group(1))
        return m.group(1) + m.group(2) + _HEX.sub(lambda hm: map_hex(hm.group(0), role), val)

    out = _DECL.sub(decl, src)
    if len(_qss_cache) > 2000:
        _qss_cache.clear()
    _qss_cache[key] = out
    return out


def ss(widget, src):
    """Use this instead of widget.setStyleSheet(src)."""
    try:
        widget.setProperty("_qss_src", src)
    except Exception:
        pass
    widget.setStyleSheet(qss(src))


def restyle(widget):
    """Re-apply a widget's stored dark-source stylesheet in the current mode."""
    try:
        src = widget.property("_qss_src")
    except Exception:
        return
    if isinstance(src, str):
        widget.setStyleSheet(qss(src))


_QColor = None


def qc(c, role=None):
    """Use this instead of QColor(c) for any color that should follow the theme."""
    global _QColor
    if _QColor is None:
        from PySide6.QtGui import QColor
        _QColor = QColor
    if isinstance(c, str) and c.startswith("#"):
        return _QColor(map_hex(c, role))
    return _QColor(c)


def accent_text():
    """The teal accent when used as TEXT (deeper in light mode for contrast)."""
    return map_hex("#00BFA5", "text")


# ----------------------------------------------------------------- icons
# Icons that are single-color glyphs. Colored art (check, "trash red") is left alone.
_MONO = {"home", "scissors", "downloads", "settings", "collapse", "expand",
         "arrow-right", "pause", "play", "trash", "retry"}
_icon_base = {}     # name -> (QImage, avg_luminance, avg_saturation)
_icon_cache = {}    # (name, mode) -> QPixmap


def _small_image(name, icons_dir, cache_dir, px=64):
    """The icon decoded at px x px. The first run scales the source down once
    and saves it under cache/icons, so later launches read a tiny PNG instead
    of decoding a huge one every time (that was ~130 ms per icon)."""
    from PySide6.QtGui import QImage
    from PySide6.QtCore import Qt
    src = Path(icons_dir) / (name + ".png")
    if not src.exists():
        return None
    cached = Path(cache_dir) / "icons" / ("%s_%d.png" % (name, px))
    try:
        if cached.exists() and cached.stat().st_mtime >= src.stat().st_mtime:
            img = QImage(str(cached))
            if not img.isNull():
                return img
    except OSError:
        pass
    img = QImage(str(src))
    if img.isNull():
        return None
    img = img.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(cached))
    except Exception:
        pass
    return img


def _analyze(img):
    lum = sat = tot = 0.0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            a = c.alphaF()
            if a < 0.2:
                continue
            r, g, b = c.redF(), c.greenF(), c.blueF()
            lum += a * (0.2126 * r + 0.7152 * g + 0.0722 * b)
            sat += a * (max(r, g, b) - min(r, g, b))
            tot += a
    return (lum / tot, sat / tot) if tot else (0.5, 0.0)


def _tint(img, color):
    from PySide6.QtGui import QImage, QPainter, QColor
    from PySide6.QtCore import Qt
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawImage(0, 0, img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(color))
    p.end()
    return out


def icon_pixmap(name, icons_dir, cache_dir):
    """QPixmap (<=64px) for icons/<name>.png that stays visible in the current
    theme: a single-color glyph too close to the surface it sits on (black on a
    dark sidebar, white on a light one) is recolored; everything else is
    shown exactly as drawn."""
    from PySide6.QtGui import QPixmap
    key = (name, _mode)
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    base = _icon_base.get(name)
    if base is None:
        img = _small_image(name, icons_dir, cache_dir)
        if img is None:
            return QPixmap()
        lum, sat = _analyze(img)
        base = _icon_base[name] = (img, lum, sat)
    img, lum, sat = base
    out = img
    if name in _MONO and sat < 0.12:
        surface = 1.0 if _mode == "light" else 0.09
        if abs(lum - surface) < 0.35:
            out = _tint(img, "#5F6368" if _mode == "light" else "#C4C7CC")
    pm = QPixmap.fromImage(out)
    _icon_cache[key] = pm
    return pm


def icon(name, icons_dir, cache_dir):
    from PySide6.QtGui import QIcon
    pm = icon_pixmap(name, icons_dir, cache_dir)
    return QIcon(pm) if not pm.isNull() else QIcon()
