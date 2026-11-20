"""Set-level and design-level metrics for the reconstruction track.

The functions below are copied without change from the evaluation code used for the paper; only the imports of the run
harness around them were left out.

  l2_metrics   L2: mean page score over a set (a missing page scores 0) and shared-value recall, the share of
               ground-truth values repeated on two or more pages of the set that the output also repeats.
  l3_metrics   L3: design recall (`diff_recall`), the share of values that are new on the target page relative to the
               reference page and that the output writes; plus content F1 and the copy baseline.

    python set_metrics.py l2 <task_dir> <output_dir>          # output_dir holds page_01.dxf, page_02.dxf, ...
    python set_metrics.py l3 <task_dir> <output.dxf>
"""
from __future__ import annotations

import collections
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
import logging

logging.disable(logging.WARNING)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ezdxf  # noqa: E402
from score import extract, score  # noqa: E402


LAYER_KEYS = ("layer-aware F1@1mm",)


def adjust_overall(sc, layer_trusted=True):
    """On pages whose layer ground truth is not trusted (fallback class > 15%) the layer-aware component is left out and the others are renormalised by their original weights."""
    comps = sc.get("components") or {}
    if layer_trusted or not comps:
        return sc.get("overall", 0)
    kept = {k: v for k, v in comps.items() if k not in LAYER_KEYS}
    w = sum(v["weight"] for v in kept.values()) or 1
    return round(100 * sum(v["weight"] * float(v["value"]) for v in kept.values()) / w, 2)


def content_facts(path):
    """Multiset of displayed dimension values and texts. Uses the extract of score.py (including MTEXT line splitting and merging of overlaid symbols)."""
    r = extract(ezdxf.readfile(path))
    vals = collections.Counter(t["text"] for t in r["texts"])
    vals.update(d["text"] for d in r["dims"] if d["text"])
    return vals


def mf1(p, g):
    inter = sum((p & g).values())
    P, G = sum(p.values()), sum(g.values())
    if P == 0 or G == 0:
        return 0.0
    pr, rc = inter / P, inter / G
    return 0.0 if inter == 0 else 2 * pr * rc / (pr + rc)


def l3_metrics(gt_dxf, sib_dxf, pred_dxf):
    G, A = content_facts(gt_dxf), content_facts(sib_dxf)
    P = content_facts(pred_dxf) if os.path.exists(pred_dxf) else collections.Counter()
    new, old = G - A, A - G
    hit = sum(min(c, max(0, P[v] - A[v] if v in A else P[v])) for v, c in new.items())
    stale = sum(min(c, max(0, P[v] - G[v])) for v, c in old.items())
    return {
        "content_f1": round(mf1(P, G), 4),
        "copy_content_f1": round(mf1(A, G), 4),
        "n_new": sum(new.values()), "diff_recall": round(hit / max(1, sum(new.values())), 4),
        "n_old": sum(old.values()), "stale_rate": round(stale / max(1, sum(old.values())), 4),
        "new_missed": [v for v, c in new.items() if P[v] < c][:15],
        "old_left": [v for v, c in old.items() if P[v] > G[v]][:15],
    }


def l2_metrics(pages, out):
    """L2: mean of the page scores (a missing page counts 0), plus cross-page consistency."""
    per, facts_g, facts_p, layers = [], {}, {}, {}
    for pg in pages:
        pred = os.path.join(out, pg["output"])
        if os.path.exists(pred):
            sc = score(pg["gt"], pred, os.path.join(out, "eval", f"page_{pg['page']:02d}"))
            facts_p[pg["page"]] = content_facts(pred)
            try:
                layers[pg["page"]] = {e.dxf.layer for e in ezdxf.readfile(pred).modelspace()}
            except Exception:
                layers[pg["page"]] = set()
        else:
            sc = {"overall": 0, "valid": False}
            facts_p[pg["page"]] = collections.Counter()
        facts_g[pg["page"]] = content_facts(os.path.join(pg["gt"], "gt.dxf"))
        per.append({"page": pg["page"], "sheet": pg["sheet"], "delivered": os.path.exists(pred),
                    "overall": adjust_overall(sc, pg.get("layer_trusted", True)), "raw_overall": sc.get("overall", 0),
                    "layer_trusted": pg.get("layer_trusted", True), "components": sc.get("components")})
    # shared values: dimension/text values that occur on ≥2 pages of the ground truth; does the model reproduce them on every such page
    df = collections.Counter()
    for c in facts_g.values():
        df.update(set(c))
    shared = {v for v, n in df.items() if n >= 2}
    tot = hit = 0
    for pgn, g in facts_g.items():
        for v in shared:
            if g[v]:
                tot += g[v]; hit += min(g[v], facts_p[pgn][v])
    ls = [l for l in layers.values() if l]
    jac = min((len(a & b) / max(1, len(a | b)) for i, a in enumerate(ls) for b in ls[i + 1:]), default=None)
    return {"pages": per, "mean_overall": round(sum(p["overall"] for p in per) / len(per), 2),
            "delivered": sum(p["delivered"] for p in per), "n_pages": len(per),
            "shared_value_recall": round(hit / max(1, tot), 4), "n_shared_occurrences": tot,
            "layer_set_min_jaccard": None if jac is None else round(jac, 3)}


if __name__ == "__main__":
    kind, task = sys.argv[1], os.path.abspath(sys.argv[2])
    meta = json.load(open(os.path.join(task, "meta.json"), encoding="utf-8"))
    if kind == "l2":
        out = os.path.abspath(sys.argv[3])
        pages = [dict(p, gt=os.path.join(task, p["gt"]), output=os.path.basename(p["output"])) for p in meta["pages"]]
        r = l2_metrics(pages, out)
        print(json.dumps({k: r[k] for k in ("mean_overall", "delivered", "n_pages", "shared_value_recall")}, ensure_ascii=False))
    else:
        r = l3_metrics(os.path.join(task, "gt", "gt.dxf"), os.path.join(task, "ref", "sibling.dxf"), sys.argv[3])
        print(json.dumps({k: r[k] for k in ("diff_recall", "n_new", "content_f1", "copy_content_f1", "stale_rate")}, ensure_ascii=False))
