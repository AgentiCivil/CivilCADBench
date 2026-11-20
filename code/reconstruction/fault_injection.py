"""Fault injection on a ground truth: apply one fault at a time and score the result against the untouched file.

    python fault_injection.py <gt_dir> <out.json>        # gt_dir holds gt.dxf and meta.json

Ten cases: the unchanged file, a 3 mm shift of the whole page, two faults that only move entities between layers
(every printed pixel stays the same), and six content faults (10% of the texts, dimensions or lines dropped or edited).
The fault definitions are those used for the paper; only the command line was changed (paths are arguments).
"""
import collections, json, os, random, re, sys, tempfile, warnings
warnings.filterwarnings("ignore")
import logging; logging.disable(logging.WARNING)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ezdxf
import score as SC
gt_dir, out = os.path.abspath(sys.argv[1]), sys.argv[2]
meta = json.load(open(gt_dir + "/meta.json", encoding="utf-8"))
sheet = meta["task_id"]
ox, oy = meta["gt_offset"]
LINES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "SPLINE", "ELLIPSE"}
TXT = {"TEXT", "MTEXT"}

def digit(s, r):
    ix = [i for i, c in enumerate(s) if c.isdigit()]
    if not ix:
        return s
    i = r.choice(ix)
    return s[:i] + str((int(s[i]) + 1) % 10) + s[i + 1:]

def apply(name, d, r):
    msp = d.modelspace()
    es = list(msp)
    pick = lambda types: [e for e in es if e.dxftype() in types]
    def sample(xs, f=.1):
        return r.sample(xs, max(1, int(len(xs) * f))) if xs else []
    if name == "all_on_one":
        for e in es: e.dxf.layer = "S-结构轮廓"
    elif name == "swap_outline_rebar":
        m = {"S-钢筋": "S-结构轮廓", "S-结构轮廓": "S-钢筋"}
        for e in es: e.dxf.layer = m.get(e.dxf.layer, e.dxf.layer)
    elif name == "drop_text_10":
        for e in sample(pick(TXT)): msp.delete_entity(e)
    elif name == "edit_text_digit_10":
        for e in sample(pick({"TEXT"})): e.dxf.text = digit(e.dxf.text, r)
    elif name == "drop_dim_10":
        for e in sample(pick({"DIMENSION"})): msp.delete_entity(e)
    elif name == "edit_dim_digit_10":
        for e in sample(pick({"DIMENSION"})):
            b = e.get_geometry_block()
            if b is None: continue
            for v in b:
                if v.dxftype() == "TEXT": v.dxf.text = digit(v.dxf.text, r)
                elif v.dxftype() == "MTEXT": v.text = digit(v.text, r)
    elif name == "drop_lines_10":
        for e in sample(pick(LINES)): msp.delete_entity(e)
    elif name == "jitter_lines_10_3mm":
        for e in sample(pick(LINES)): e.translate(3, 0, 0)
    elif name == "shift_all_3mm":
        for e in es: e.translate(3, 3, 0)
    elif name != "self":
        raise SystemExit(name)

FAULTS = ["self", "shift_all_3mm", "all_on_one", "swap_outline_rebar", "drop_text_10", "edit_text_digit_10", "drop_dim_10", "edit_dim_digit_10", "drop_lines_10", "jitter_lines_10_3mm"]
res = {"sheet": sheet, "faults": {}}
with tempfile.TemporaryDirectory() as t:
    for name in FAULTS:
        d = ezdxf.readfile(gt_dir + "/gt.dxf")
        for n in meta["classes"]:
            if n not in d.layers: d.layers.add(n)
        for e in d.modelspace():
            if hasattr(e, "translate"): e.translate(-ox, -oy, 0)
        r = random.Random(f"{sheet}:{name}")
        apply(name, d, r)
        p = f"{t}/{name}.dxf"
        d.saveas(p)
        s = SC.score(gt_dir, p, f"{t}/s_{name}")
        res["faults"][name] = {"overall": float(s["overall"]), "components": {k: float(v["value"]) for k, v in s["components"].items()}}
json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
for k, v in res["faults"].items(): print(f"{k:22s} {v['overall']:6.1f}")
