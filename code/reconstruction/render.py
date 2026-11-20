"""Monochrome plot renderer for DXF files (mimics monochrome.ctb + layer lineweights).

Usage:
    python render.py in.dxf out.png [--dpi 150] [--extents x0 y0 x1 y1]
    python render.py in.dxf out.pdf          # vector PDF (for debugging only)

1 drawing unit is rendered as 1 mm on paper.
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

warnings.filterwarnings("ignore")

import ezdxf
from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf
from ezdxf.addons.drawing.config import (
    BackgroundPolicy,
    ColorPolicy,
    Configuration,
    LineweightPolicy,
)
from ezdxf.fonts import fonts

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = next(
    (d for d in (os.path.join(HERE, "fonts"), os.path.join(os.path.dirname(HERE), "fonts")) if os.path.isdir(d)),
    os.path.join(HERE, "fonts"),
)
CJK_FONT = "STFANGSO.ttf"

_fonts_ready = False


def _setup_fonts() -> None:
    global _fonts_ready
    if _fonts_ready:
        return
    if os.path.isdir(FONT_DIR):
        fonts.font_manager.build(folders=[FONT_DIR], support_dirs=False)
    _fonts_ready = True


def _substitute_fonts(doc) -> None:
    """Map every text style to a CJK-capable TTF (SHX / missing TTF -> STFangsong)."""
    for style in doc.styles:
        style.dxf.font = CJK_FONT
        if style.dxf.hasattr("bigfont"):
            style.dxf.bigfont = ""


_UESC = __import__("re").compile(r"\\[Uu]\+([0-9A-Fa-f]{4})")
# Rebar symbol codes of the SHX fonts common in Chinese structural drawings (tssdeng.shx and others): %%130 HPB300, %%131 HRB335, %%132 HRB400, %%128/%%129 diameter, and so on.
# ezdxf does not know these codes and would draw "%%132" literally; they are all shown as Φ (norm_text in the scorer compares them as φ as well).
_REBAR = __import__("re").compile(r"(?<!%)%%(12[89]|13[0-9])")


def _decode_unicode_escapes(doc) -> None:
    """ezdxf does not decode AutoCAD's \\U+XXXX escapes (for example \\U+2205 = ∅) when plotting, so they are replaced by the real characters first.
    Covers TEXT/MTEXT/ATTRIB in model space and in every block definition (including dimension blocks *D), and the override text of dimensions."""
    def fix(s):
        # FangSong has no ∅ glyph; it is shown as Φ, as is usual in Chinese drafting
        if not s or ("+" not in s and "%%1" not in s):
            return s
        s = _REBAR.sub("Φ", s)
        return _UESC.sub(lambda m: chr(int(m.group(1), 16)), s).replace("∅", "Φ")
    spaces = [doc.modelspace()] + list(doc.blocks)
    for sp in spaces:
        for e in sp:
            t = e.dxftype()
            try:
                if t in ("TEXT", "ATTRIB", "ATTDEF"):
                    e.dxf.text = fix(e.dxf.text)
                elif t == "MTEXT":
                    e.text = fix(e.text)
                elif t == "DIMENSION" and e.dxf.hasattr("text"):
                    e.dxf.text = fix(e.dxf.text)
            except Exception:
                pass


def render(doc, out_path: str, dpi: int = 150, extents=None, margin_mm: float = 5.0):
    _setup_fonts()
    _substitute_fonts(doc)
    _decode_unicode_escapes(doc)
    msp = doc.modelspace()
    if extents is None:
        ext = bbox.extents(msp, fast=True)
        x0, y0, x1, y1 = ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y
    else:
        x0, y0, x1, y1 = extents
    cfg = Configuration(
        color_policy=ColorPolicy.BLACK,
        background_policy=BackgroundPolicy.WHITE,
        lineweight_policy=LineweightPolicy.ABSOLUTE,
        lineweight_scaling=1.0,
        min_lineweight=0.13,
    )
    ctx = RenderContext(doc)
    backend = pymupdf.PyMuPdfBackend()
    Frontend(ctx, backend, config=cfg).draw_layout(msp, finalize=True)
    page = layout.Page(
        (x1 - x0) + 2 * margin_mm,
        (y1 - y0) + 2 * margin_mm,
        layout.Units.mm,
        margins=layout.Margins.all(margin_mm),
    )
    settings = layout.Settings(scale=1.0, fit_page=False)
    # fix the rendered window to the requested extents
    from ezdxf.math import BoundingBox2d

    settings.content_rotation = 0
    backend_bbox = BoundingBox2d([(x0, y0), (x1, y1)])
    if out_path.lower().endswith(".pdf"):
        data = backend.get_pdf_bytes(page, settings=settings, render_box=backend_bbox)
    else:
        data = backend.get_pixmap_bytes(
            page, fmt="png", settings=settings, dpi=dpi, render_box=backend_bbox
        )
    with open(out_path, "wb") as f:
        f.write(data)
    return (x0, y0, x1, y1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dxf")
    ap.add_argument("out")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--extents", type=float, nargs=4)
    a = ap.parse_args(argv)
    doc = ezdxf.readfile(a.dxf)
    ext = render(doc, a.out, dpi=a.dpi, extents=a.extents)
    print("rendered", a.out, "extents", [round(v, 2) for v in ext])


if __name__ == "__main__":
    sys.exit(main())
