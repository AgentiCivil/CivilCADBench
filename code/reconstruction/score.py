"""Reconstruction scorer: compares the DXF produced by the agent with the normalised ground-truth DXF.

    python score.py <gt_dir> <pred.dxf> <out_dir>

Writes <out_dir>/score.json and the visualisation pictures. All coordinates are sheet coordinates (origin at the lower left, unit mm).

Scoring v3: a text or dimension component whose ground truth is empty is left out and the remaining weights are
renormalised; texts and displayed dimension values can match each other; a text pair requires identical digit sequences.
Results of v2 and v3 are not pooled.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import unicodedata
import warnings
from difflib import SequenceMatcher

warnings.filterwarnings("ignore")

import ezdxf
import numpy as np
from ezdxf import bbox, path as ezpath
from ezdxf import recover
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render as R  # noqa: E402

TAUS = [0.5, 1.0, 2.0]
MAIN_TAU = 1.0
STEP = 0.25            # sampling step, mm
SCORE_VERSION = "v3"   # v2: rebar symbols written on top of a text are merged back into that text before comparison
# v3: empty ground-truth text/dimension components are left out and the weights renormalised; texts and displayed dimension values can match each other; a text pair requires identical digits
NUM = re.compile(r"\d+(?:\.\d+)?")
TEXT_RADIUS = 6.0      # matching radius of text/dimension centres, mm
TEXT_SIM = 0.8         # threshold of text content similarity
CURVE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
OTHER = "(non-standard layer)"


# ----------------------------------------------------------------- reading and sampling
def load(path):
    try:
        return ezdxf.readfile(path), []
    except Exception:
        doc, auditor = recover.readfile(path)
        return doc, [str(e.message) for e in auditor.errors]


def norm_text(s: str) -> str:
    s = re.sub(r"\\[Uu]\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), s or "")  # \U+2205 -> ∅
    s = re.sub(r"(?<!%)%%(12[89]|13[0-9])", "φ", s)  # rebar symbol codes (%%130/%%132 and so on)
    s = re.sub("[\ud800-\udfff]", "", s)  # bytes that failed to decode (lone surrogates)
    s = unicodedata.normalize("NFKC", s)
    for k in ("%%c", "%%C", "Φ", "∅", "Ø", "ø", "⌀"):
        s = s.replace(k, "φ")
    s = s.replace("%%d", "°").replace("%%D", "°").replace("%%p", "±").replace("%%P", "±")
    s = re.sub(r"\\[A-Za-z][^;\\]*;|[{}]|\\P", "", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def sample_path(p, step=STEP):
    try:
        pts = np.array([(v.x, v.y) for v in p.flattening(0.05)], dtype=float)
    except Exception:
        return np.zeros((0, 2))
    if len(pts) < 2:
        return pts
    out = [pts[:1]]
    for a, b in zip(pts[:-1], pts[1:]):
        d = np.hypot(*(b - a))
        n = max(1, int(math.ceil(d / step)))
        t = np.linspace(0, 1, n + 1)[1:, None]
        out.append(a + (b - a) * t)
    return np.vstack(out)


def entity_paths(e, depth=0):
    """Split an entity into geometric paths (no text)."""
    t = e.dxftype()
    if t in CURVE_TYPES:
        try:
            yield ezpath.make_path(e)
        except Exception:
            pass
    elif t == "HATCH":
        try:
            for p in ezpath.from_hatch(e):
                yield p
        except Exception:
            pass
    # top-level INSERTs were already expanded by the layer rule in extract.walk; an INSERT here can only come from inside a dimension (arrow blocks and the like)
    elif t in ("DIMENSION", "LEADER", "MULTILEADER", "MLEADER", "INSERT") and depth < 4:
        try:
            for v in e.virtual_entities():
                if v.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
                    continue
                yield from entity_paths(v, depth + 1)
        except Exception:
            pass
    elif t == "SOLID" or t == "TRACE" or t == "3DFACE":
        try:
            vs = [e.dxf.get(f"vtx{i}") for i in range(4)]
            vs = [v for v in vs if v is not None]
            yield ezpath.from_vertices(vs, close=True)
        except Exception:
            pass


def text_center(e):
    """Centre of the text bounding box. A special symbol code counts as the one character it displays as; otherwise "%%132" would count as 5 characters and shift the centre by about 3 mm."""
    try:
        if e.dxftype() == "TEXT" and "%%" in e.dxf.text:
            e = e.copy()
            e.dxf.text = re.sub(r"(?<!%)%%(12[89]|13[0-9]|[cCdDpP])", "Φ", e.dxf.text)
        b = bbox.extents([e])
        if b.has_data:
            c = b.center
            return float(c.x), float(c.y)
    except Exception:
        pass
    p = e.dxf.insert
    return float(p.x), float(p.y)


def raw_text(e):
    if e.dxftype() == "TEXT":
        return e.dxf.text
    if e.dxftype() == "MTEXT":
        return e.plain_text()
    if e.dxftype() == "ATTRIB":
        return e.dxf.text
    return ""


GLYPH_GAP = re.compile(r"(\S)\s{2,}(\S)")


def merge_glyphs(texts):
    """Merge rebar symbols written on top of a text back into that text.

    Drafters often write φ as a TEXT of its own, placed over a text that leaves a gap for it such as "20  16", or right before a number.
    Ground truth and model output both go through this step, so "20φ16" scores the same whether written as one text or split.
      - gap host: on the same line (dy≤1.6); or offset vertically but aligned with the gap horizontally (dy≤4.5 and dx≤1.5; the source drawings contain such offsets);
      - number host: on the same line, the symbol within 7 mm to the left of the number, giving "φ16".
    "Same line" and "left" are taken along the text direction.
    Each symbol is used once and each gap is filled once, assigned from nearest to farthest. A symbol that cannot be merged is kept as it is.
    """
    keep = [t for t in texts if t["text"] != "φ"]
    glyphs = [t for t in texts if t["text"] == "φ"]
    if not glyphs:
        return texts
    cands = []
    for gi, g in enumerate(glyphs):
        for ti, t in enumerate(keep):
            wx, wy = t["x"] - g["x"], t["y"] - g["y"]
            d = math.hypot(wx, wy)
            r = math.radians(t.get("rot", 0.0) or 0.0)
            dx = wx * math.cos(r) + wy * math.sin(r)    # along the text direction (also valid for vertical text)
            dy = -wx * math.sin(r) + wy * math.cos(r)   # perpendicular to the text direction
            if GLYPH_GAP.search(str(t.get("raw", ""))) and d <= 12 and (abs(dy) <= 1.6 or (abs(dy) <= 4.5 and abs(dx) <= 1.5)):
                cands.append((0, d, gi, ti))
            elif abs(dy) <= 1.6 and 0 < dx < 7 and re.fullmatch(r"\d+(\.\d+)?", t["text"]):
                cands.append((1, d, gi, ti))
    cands.sort()
    used, raws = set(), {}
    prefixed = set()
    for kind, _, gi, ti in cands:
        if gi in used:
            continue
        t = keep[ti]
        raw = raws.get(ti, str(t.get("raw", "")))
        if kind == 0:
            if not GLYPH_GAP.search(raw):
                continue
            raws[ti] = GLYPH_GAP.sub(r"\1φ\2", raw, count=1)
            t["text"] = norm_text(raws[ti])
        else:
            if ti in prefixed:
                continue
            prefixed.add(ti)
            t["text"] = "φ" + t["text"]
        used.add(gi)
    return keep + [g for i, g in enumerate(glyphs) if i not in used]


def extract(doc, offset=(0.0, 0.0), classes=None):
    R._setup_fonts()
    R._substitute_fonts(doc)
    msp = doc.modelspace()
    pts, cls, texts, dims = [], [], [], []
    type_counts = {}
    ox, oy = offset
    for e in msp:
        type_counts[e.dxftype()] = type_counts.get(e.dxftype(), 0) + 1
    # a multi-line MTEXT is split into one TEXT per line before matching, so that "one MTEXT against several TEXTs" is not counted as missing text
    try:
        from ezdxf.addons import MTextExplode
        mts = list(msp.query("MTEXT"))
        if mts:
            with MTextExplode(msp) as xpl:
                for m in mts:
                    try:
                        xpl.explode(m)
                    except Exception:
                        pass
    except Exception:
        pass

    def layer_class(layer):
        return layer if (classes is None or layer in classes) else OTHER

    expanded = {}

    def walk(ents, parent_layer=None, depth=0):
        """Expand block references. By the CAD rule, an entity on layer 0 inside a block inherits the layer of the block reference; otherwise it keeps its own layer."""
        for e in ents:
            layer = e.dxf.layer
            if parent_layer is not None and layer == "0":
                layer = parent_layer
            if e.dxftype() == "INSERT" and depth < 6:
                expanded["INSERT"] = expanded.get("INSERT", 0) + 1
                try:
                    yield from walk(list(e.virtual_entities()), layer, depth + 1)
                except Exception:
                    pass
                for a in getattr(e, "attribs", []) or []:
                    yield a, layer
                continue
            expanded[e.dxftype()] = expanded.get(e.dxftype(), 0) + 1
            yield e, layer

    for e, eff_layer in walk(list(msp)):
        t = e.dxftype()
        c = layer_class(eff_layer)
        if t in ("TEXT", "MTEXT", "ATTRIB"):
            s = norm_text(raw_text(e))
            if s:
                x, y = text_center(e)
                texts.append({"text": s, "raw": raw_text(e), "x": x - ox, "y": y - oy, "cls": c,
                              "rot": e.dxf.get("rotation", 0.0) if t == "TEXT" else 0.0})
            continue
        if t == "DIMENSION":
            label, lx, ly = None, None, None
            try:
                for v in e.virtual_entities():
                    if v.dxftype() in ("TEXT", "MTEXT"):
                        label = norm_text(raw_text(v))
                        lx, ly = text_center(v)
                        break
            except Exception:
                pass
            if label is None:
                ov = e.dxf.get("text", "")
                if ov and ov != "<>":
                    label = norm_text(ov)
                else:
                    try:
                        label = norm_text(f"{e.get_measurement():g}")
                    except Exception:
                        label = ""
                p = e.dxf.get("text_midpoint") or e.dxf.get("defpoint")
                lx, ly = (p.x, p.y) if p is not None else (0, 0)
            dims.append({"text": label, "x": lx - ox, "y": ly - oy, "cls": c})
        for p in entity_paths(e):
            s = sample_path(p)
            if len(s):
                pts.append(s - np.array([ox, oy]))
                cls.extend([c] * len(s))
    P = np.vstack(pts) if pts else np.zeros((0, 2))
    texts = merge_glyphs(texts)
    return {"pts": P, "cls": np.array(cls, dtype=object), "texts": texts, "dims": dims, "types": type_counts,
            "types_expanded": expanded}


# ----------------------------------------------------------------- registration
def register(pred_pts, gt_pts, iters=30):
    """Estimate the scale and translation (no rotation) from pred to gt with trimmed ICP."""
    if len(pred_pts) < 10 or len(gt_pts) < 10:
        return 1.0, np.zeros(2)
    tree = cKDTree(gt_pts)
    rng = np.random.default_rng(0)
    sub = pred_pts[rng.choice(len(pred_pts), min(20000, len(pred_pts)), replace=False)]
    s, t = 1.0, np.zeros(2)
    for thr in [8.0] * 10 + [3.0] * 10 + [1.5] * (iters - 20):
        q = sub * s + t
        d, idx = tree.query(q)
        m = d < thr
        if m.sum() < 10:
            break
        a, b = sub[m], gt_pts[idx[m]]
        am, bm = a.mean(0), b.mean(0)
        ac, bc = a - am, b - bm
        s_new = float((ac * bc).sum() / max((ac * ac).sum(), 1e-9))
        s_new = min(max(s_new, 0.5), 2.0)
        t = bm - s_new * am
        s = s_new
    return s, t


# ----------------------------------------------------------------- metrics
def prf(p_hit, p_n, r_hit, r_n):
    P = p_hit / p_n if p_n else 0.0
    Rr = r_hit / r_n if r_n else 0.0
    F = 2 * P * Rr / (P + Rr) if P + Rr else 0.0
    return {"precision": P, "recall": Rr, "f1": F}


def geo_metrics(pred, gt, classes):
    out = {"agnostic": {}, "layer_aware": {}, "per_class": {}, "confusion": {}}
    P, G = pred["pts"], gt["pts"]
    if len(P) == 0:
        for tau in TAUS:
            out["agnostic"][str(tau)] = prf(0, 0, 0, len(G))
            out["layer_aware"][str(tau)] = prf(0, 0, 0, len(G))
        for c in classes:
            out["per_class"][c] = {"precision": 0, "recall": 0, "f1": 0, "gt_pts": int((gt["cls"] == c).sum()), "pred_pts": 0}
        return out, None
    tg, tp = cKDTree(G), cKDTree(P)
    dPG, iPG = tg.query(P)   # pred -> nearest gt
    dGP, iGP = tp.query(G)   # gt -> nearest pred
    for tau in TAUS:
        out["agnostic"][str(tau)] = prf((dPG <= tau).sum(), len(P), (dGP <= tau).sum(), len(G))
    # matches within the same class
    same_p = np.zeros(len(P), bool)
    same_g = np.zeros(len(G), bool)
    per = {}
    for c in set(classes) | set(pred["cls"]):
        mp, mg = pred["cls"] == c, gt["cls"] == c
        if mp.sum() and mg.sum():
            dp, _ = cKDTree(G[mg]).query(P[mp])
            dg, _ = cKDTree(P[mp]).query(G[mg])
        else:
            dp, dg = np.full(mp.sum(), np.inf), np.full(mg.sum(), np.inf)
        per[c] = (dp, dg)
    for tau in TAUS:
        hp = sum((per[c][0] <= tau).sum() for c in per)
        hg = sum((per[c][1] <= tau).sum() for c in per)
        out["layer_aware"][str(tau)] = prf(hp, len(P), hg, len(G))
    for c in classes:
        dp, dg = per.get(c, (np.array([]), np.array([])))
        r = prf((dp <= MAIN_TAU).sum(), len(dp), (dg <= MAIN_TAU).sum(), len(dg))
        r.update(gt_pts=int(len(dg)), pred_pts=int(len(dp)))
        out["per_class"][c] = r
    # confusion matrix: class of the nearest pred point within 1 mm of each GT point
    conf = {}
    for gi in np.where(dGP <= MAIN_TAU)[0]:
        key = (gt["cls"][gi], pred["cls"][iGP[gi]])
        conf[key] = conf.get(key, 0) + 1
    miss = {}
    for gi in np.where(dGP > MAIN_TAU)[0]:
        miss[gt["cls"][gi]] = miss.get(gt["cls"][gi], 0) + 1
    out["confusion"] = {"matrix": [[a, b, n] for (a, b), n in conf.items()], "missed": miss}
    vis = {"gt_hit": dGP <= MAIN_TAU, "pred_hit": dPG <= MAIN_TAU}
    return out, vis


def match_texts(pred_list, gt_list, radius=TEXT_RADIUS, sim=TEXT_SIM):
    """Greedy one-to-one pairing: centre distance ≤ radius, identical digit sequences, content similarity ≥ sim."""
    cands = []
    for i, g in enumerate(gt_list):
        ng = NUM.findall(g["text"])
        for j, p in enumerate(pred_list):
            d = math.hypot(g["x"] - p["x"], g["y"] - p["y"])
            if d > radius or NUM.findall(p["text"]) != ng:
                continue
            r = SequenceMatcher(None, g["text"], p["text"]).ratio()
            if r >= sim:
                cands.append((r - d / 100.0, i, j, r))
    cands.sort(reverse=True)
    ug, up, pairs = set(), set(), []
    for _, i, j, r in cands:
        if i in ug or j in up:
            continue
        ug.add(i); up.add(j); pairs.append((i, j, r))
    res = prf(len(pairs), len(pred_list), len(pairs), len(gt_list))
    res.update(gt=len(gt_list), pred=len(pred_list), matched=len(pairs))
    return res, pairs


def match_text_dim(P, gt):
    """Pair within the same class first, then pair the rest across classes (some ground truths have dimensions exploded into lines and texts). A prediction matched across classes counts towards the class it matched."""
    _, T = match_texts(P["texts"], gt["texts"])
    _, D = match_texts(P["dims"], gt["dims"], sim=0.99)
    ut, ud = {j for _, j, _ in T}, {j for _, j, _ in D}
    gt_t_left = [i for i in range(len(gt["texts"])) if i not in {i for i, _, _ in T}]
    gt_d_left = [i for i in range(len(gt["dims"])) if i not in {i for i, _, _ in D}]
    p_t_left = [j for j in range(len(P["texts"])) if j not in ut]
    p_d_left = [j for j in range(len(P["dims"])) if j not in ud]
    _, d2t = match_texts([P["dims"][j] for j in p_d_left], [gt["texts"][i] for i in gt_t_left])
    _, t2d = match_texts([P["texts"][j] for j in p_t_left], [gt["dims"][i] for i in gt_d_left], sim=0.99)
    tp_n = len(P["texts"]) + len(d2t) - len(t2d)
    dp_n = len(P["dims"]) + len(t2d) - len(d2t)
    tm, dm = len(T) + len(d2t), len(D) + len(t2d)
    tres = prf(tm, tp_n, tm, len(gt["texts"]))
    tres.update(gt=len(gt["texts"]), pred=tp_n, matched=tm, cross_matched=len(d2t))
    dres = prf(dm, dp_n, dm, len(gt["dims"]))
    dres.update(gt=len(gt["dims"]), pred=dp_n, matched=dm, cross_matched=len(t2d))
    matched_gt_t = {i for i, _, _ in T} | {gt_t_left[i] for i, _, _ in d2t}
    matched_gt_d = {i for i, _, _ in D} | {gt_d_left[i] for i, _, _ in t2d}
    return tres, dres, T, matched_gt_t, matched_gt_d


def score(gt_dir, pred_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    meta = json.load(open(os.path.join(gt_dir, "meta.json"), encoding="utf-8"))
    classes = list(meta["classes"].keys())
    W, H = meta["page_mm"]
    gdoc = ezdxf.readfile(os.path.join(gt_dir, "gt.dxf"))
    gt = extract(gdoc, offset=meta["gt_offset"], classes=classes)

    result = {"task_id": meta["task_id"], "pred": pred_path, "valid": False}
    if not os.path.exists(pred_path):
        result["error"] = "output.dxf was not produced"
        result["overall"] = 0.0
        json.dump(result, open(os.path.join(out_dir, "score.json"), "w"), ensure_ascii=False, indent=2)
        return result
    try:
        pdoc, recover_errors = load(pred_path)
    except Exception as ex:
        result["error"] = f"the DXF could not be read: {ex}"
        result["overall"] = 0.0
        json.dump(result, open(os.path.join(out_dir, "score.json"), "w"), ensure_ascii=False, indent=2)
        return result
    auditor = pdoc.audit()
    # layer compliance (counted on the original entities, before extract splits MTEXT)
    msp = pdoc.modelspace()
    on_std = sum(1 for e in msp if e.dxf.layer in classes)
    total = max(1, len(msp))
    used_layers = sorted({e.dxf.layer for e in msp})
    result["layer_compliance"] = {"ratio": on_std / total, "used_layers": used_layers,
                                  "nonstandard": [l for l in used_layers if l not in classes]}
    pred = extract(pdoc, classes=classes)
    result["valid"] = True
    result["validity"] = {
        "recover_errors": len(recover_errors),
        "audit_errors": len(auditor.errors),
        "audit_fixes": len(auditor.fixes),
        "entities": int(sum(pred["types"].values())),
        "entity_types": pred["types"],
        "entity_types_expanded": pred["types_expanded"],
        "gt_entity_types": gt["types"],
    }

    # registration
    s, t = register(pred["pts"], gt["pts"])
    raw_geo, _ = geo_metrics(pred, gt, classes)
    reg_pred = dict(pred)
    reg_pred["pts"] = pred["pts"] * s + t
    for k in ("texts", "dims"):
        reg_pred[k] = [dict(x, x=x["x"] * s + t[0], y=x["y"] * s + t[1]) for x in pred[k]]
    reg_geo, vis = geo_metrics(reg_pred, gt, classes)
    use_reg = reg_geo["agnostic"]["1.0"]["f1"] > raw_geo["agnostic"]["1.0"]["f1"] + 1e-6
    geo = reg_geo if use_reg else raw_geo
    P = reg_pred if use_reg else pred
    if not use_reg:
        _, vis = geo_metrics(pred, gt, classes)
    result["registration"] = {
        "scale": s, "tx": float(t[0]), "ty": float(t[1]), "applied": bool(use_reg),
        "coordinate_ok": bool(abs(s - 1) < 0.02 and abs(t[0]) < 2 and abs(t[1]) < 2),
        "raw_geo_f1@1mm": raw_geo["agnostic"]["1.0"]["f1"],
    }
    result["geometry"] = geo

    # texts and dimensions
    tres, dres, tpairs, matched_g, matched_d = match_text_dim(P, gt)
    tres["layer_correct"] = sum(1 for i, j, _ in tpairs if P["texts"][j]["cls"] == "A-文字")
    result["text"] = tres
    result["dimension"] = dres
    result["dimension"]["native_ratio"] = min(1.0, len(P["dims"]) / max(1, len(gt["dims"])))

    g1 = geo["agnostic"]["1.0"]["f1"]
    l1 = geo["layer_aware"]["1.0"]["f1"]
    comp = {
        "geometry F1@1mm": (0.25, g1),
        "layer-aware F1@1mm": (0.30, l1),
        "layer share": (0.10, result["layer_compliance"]["ratio"]),
    }
    if gt["texts"]:   # components with an empty ground truth are left out; the others are renormalised by their original weights
        comp["text F1"] = (0.20, tres["f1"])
    if gt["dims"]:
        comp["dimension F1"] = (0.15, dres["f1"])
    result["components"] = {k: {"weight": w, "value": v} for k, (w, v) in comp.items()}
    result["overall"] = 100 * sum(w * v for w, v in comp.values()) / sum(w for w, _ in comp.values())
    result["score_version"] = SCORE_VERSION
    result["layer_accuracy_given_geometry"] = l1 / g1 if g1 else 0.0

    # text matching details (for inspection)
    result["text_missed_examples"] = [gt["texts"][i]["raw"] for i in range(len(gt["texts"])) if i not in matched_g][:40]
    result["dim_missed_examples"] = [gt["dims"][i]["text"] for i in range(len(gt["dims"])) if i not in matched_d][:40]

    # ---- visualisation
    try:
        R.render(ezdxf.readfile(pred_path) if not recover_errors else pdoc,
                 os.path.join(out_dir, "pred_render.png"), dpi=110, extents=(0, 0, W, H), margin_mm=0)
    except Exception as ex:
        result["render_error"] = str(ex)
    draw_overlay(gt, P, vis, W, H, os.path.join(out_dir, "overlay.png"))
    draw_layers(P, classes, W, H, os.path.join(out_dir, "pred_layers.png"))
    json.dump(result, open(os.path.join(out_dir, "score.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return result


PALETTE = {
    "S-结构轮廓": "#1f2937", "S-虚线": "#7c3aed", "S-中心线": "#0891b2", "S-填充": "#ca8a04",
    "A-尺寸标注": "#2563eb", "A-文字": "#16a34a", "A-符号": "#db2777", "A-表格": "#ea580c", "S-钢筋": "#dc2626", OTHER: "#9ca3af",
}


def _fig(W, H):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(W / 25.4 * 0.9, H / 25.4 * 0.9), dpi=110)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.set_aspect("equal"); ax.axis("off")
    return fig, ax, plt


def draw_overlay(gt, pred, vis, W, H, out):
    fig, ax, plt = _fig(W, H)
    G, P = gt["pts"], pred["pts"]
    if vis is not None:
        ax.scatter(*G[vis["gt_hit"]].T, s=0.15, c="#cbd5e1", linewidths=0)
        ax.scatter(*G[~vis["gt_hit"]].T, s=0.25, c="#dc2626", linewidths=0)
        if len(P):
            ax.scatter(*P[~vis["pred_hit"]].T, s=0.25, c="#2563eb", linewidths=0)
    else:
        ax.scatter(*G.T, s=0.25, c="#dc2626", linewidths=0)
    fig.savefig(out, dpi=110, facecolor="white"); plt.close(fig)


def draw_layers(doc_pts, classes, W, H, out):
    fig, ax, plt = _fig(W, H)
    P, C = doc_pts["pts"], doc_pts["cls"]
    for c in list(classes) + [OTHER]:
        m = C == c
        if m.sum():
            ax.scatter(*P[m].T, s=0.2, c=PALETTE.get(c, "#9ca3af"), linewidths=0)
    for t in doc_pts["texts"]:
        ax.plot(t["x"], t["y"], "s", ms=1.2, c=PALETTE.get(t["cls"], "#9ca3af"))
    fig.savefig(out, dpi=110, facecolor="white"); plt.close(fig)


if __name__ == "__main__":
    r = score(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps({k: r.get(k) for k in ("overall", "registration", "layer_compliance")}, ensure_ascii=False, indent=1,
                     default=str))
