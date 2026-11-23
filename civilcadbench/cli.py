"""ccb, the command line of CivilCADBench.

    ccb selfcheck                                        score the ground truth against itself
    ccb export understanding --tier single --out DIR     requests for a model, without answers
    ccb grade understanding --tier single RESPONSES      grade the responses of a model
    ccb export workspace --level L1 --out RUN_DIR        one workspace per task for an agent, without ground truth
    ccb score RUN_DIR                                    score every workspace of a run and write summary.json

The benchmark never calls a model. It writes the inputs as files, reads the outputs as files and scores them with the
scorers under code/, which this file imports and does not change. docs/PROTOCOL.md describes the files and the rules.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_R = ROOT / "code" / "reconstruction"
CODE_U = ROOT / "code" / "understanding"
SYSTEM = json.loads((Path(__file__).parent / "prompts.json").read_text(encoding="utf-8"))
TIERS = {"single": None, "few": "few_sheet", "full": "full_set"}
LEVELS = ("L1", "L2", "L3")
FORBIDDEN = {"correct", "why_wrong", "answer", "gold_answer", "tolerance", "grader_id", "solution_paths", "decides",
             "evidence_pages", "decoys", "on_decoy_page", "cross_page", "role"}     # never part of a request


def rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def compact(obj):
    """JSON as the harness of the paper wrote it into the user message."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def load(folder, fn):
    """Import from one of the two code folders. Both have a module called score, so it is not left in sys.modules."""
    held = sys.modules.pop("score", None)
    sys.path.insert(0, str(folder))
    try:
        return fn()
    finally:
        sys.path.remove(str(folder))
        sys.modules.pop("score", None)
        if held is not None:
            sys.modules["score"] = held


def grader():
    def fn():
        import grade
        return grade
    return load(CODE_U, fn)


def scorers():
    def fn():
        from score import score
        from set_metrics import adjust_overall, l2_metrics, l3_metrics
        return score, adjust_overall, l2_metrics, l3_metrics
    return load(CODE_R, fn)


def units(data):
    base = Path(data) / "understanding" / "multi_sheet"
    return [d for d in sorted(base.iterdir()) if d.is_dir()]


def tasks(data, level):
    base = Path(data) / "reconstruction" / level
    return [d for d in sorted(base.iterdir()) if d.is_dir()] if base.is_dir() else []


# ---------------------------------------------------------------- understanding: export

def export_understanding(args):
    data, out, tier = Path(args.data), Path(args.out), args.tier
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} is not empty")
    (out / "images").mkdir(parents=True)
    instr = json.loads((data / "understanding" / "multi_sheet" / "prompts.json").read_text(encoding="utf-8"))
    reqs = []

    def options(item):
        return [{"key": o["key"], "text": o["text"]} for o in item["options"]]

    def link(src, rel):
        dst = out / "images" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copyfile(src, dst)
        return f"images/{rel}"

    if tier == "single":
        base = data / "understanding" / "single_sheet"
        for it in rows(base / "items.jsonl"):
            img = link(base / it["image"], Path(it["image"]).name)
            user = {"image_count": 1, "instruction": instr["parent_instruction"], "question": it["question"], "options": options(it)}
            reqs.append({"id": it["id"], "kind": "item", "tier": tier, "system": SYSTEM["one_turn_choice"],
                         "prompt": compact(user), "images": [img]})
    else:
        key = TIERS[tier]
        for u in units(data):
            pages = sorted((u / "pages").glob("p*.png"))
            parents = rows(u / "parents.jsonl")
            shown = {}
            for p in parents:
                if tier == "few":
                    imgs = [link(u / "pages" / f"p{n:02d}.png", f"{u.name}/p{n:02d}.png") for n in p["few_sheet"]["pages_in_order"]]
                    user = {"image_count": len(imgs)}
                    system = SYSTEM["one_turn_choice"]
                else:
                    imgs = [link(pg, f"{u.name}/{pg.name}") for pg in pages]
                    user = {"document": "document", "page_count": len(imgs)}
                    system = SYSTEM["full_choice"]
                shown[p["id"]] = imgs
                user.update(instruction=instr["parent_instruction"], question=p["question"], options=options(p))
                reqs.append({"id": p["id"], "kind": "parent", "tier": tier, "system": system, "prompt": compact(user), "images": imgs})
            if args.parents_only:
                continue
            for q in rows(u / "probes.jsonl"):
                if key not in q["tiers"]:
                    continue
                imgs = shown[q["parent"]]
                if tier == "few":
                    user = {"image_count": len(imgs), "instruction": instr["probe_instruction_few_sheet"]}
                    system = SYSTEM["one_turn_probe"]
                else:
                    user = {"document": "document", "page_count": len(imgs), "instruction": instr["probe_instruction_full_set"]}
                    system = SYSTEM["full_probe"]
                user.update(question=q["question"], response_spec=q["response_spec"])
                reqs.append({"id": q["id"], "kind": "probe", "tier": tier, "system": system, "prompt": compact(user), "images": imgs})

    def keys(x):
        if isinstance(x, dict):
            return set(x) | set().union(*map(keys, x.values())) if x else set()
        return set().union(*map(keys, x)) if isinstance(x, list) and x else set()

    for r in reqs:          # a request holds the question and nothing that grades it
        user = json.loads(r["prompt"])
        leak = (set(r) | {k for k in user if k != "response_spec"} | keys(user.get("options", []))) & FORBIDDEN
        if leak:
            sys.exit(f"answer field in request {r['id']}: {sorted(leak)}")
    with open(out / "requests.jsonl", "w", encoding="utf-8") as f:
        for r in reqs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    kinds = {k: sum(r["kind"] == k for r in reqs) for k in ("item", "parent", "probe")}
    print(f"{len(reqs)} requests ({', '.join(f'{n} {k}s' for k, n in kinds.items() if n)}) -> {out / 'requests.jsonl'}")
    print("write one line per request to a responses file: {\"id\": ..., \"response\": \"<final text of the model>\"}")


# ---------------------------------------------------------------- understanding: grade

def grade_understanding(args):
    g = grader()
    data, tier = Path(args.data), args.tier
    resp = {r["id"]: r.get("response") or "" for r in rows(args.responses)}
    if tier == "single":
        items = rows(data / "understanding" / "single_sheet" / "items.jsonl")
        res = g.single(str(data / "understanding" / "single_sheet" / "items.jsonl"), resp)
        by = {}
        for it in items:
            b = by.setdefault(it["binding"], [0, 0])
            b[1] += 1
            b[0] += bool(it["id"] in resp and g.grade_choice(it, resp[it["id"]]))
        res = {"tier": tier, **res, "by_binding": {k: {"correct": c, "items": n, "accuracy": round(c / n, 4)} for k, (c, n) in sorted(by.items())}}
    else:
        key = TIERS[tier]
        tot = {"parents": 0, "parents_answered": 0, "parents_correct": 0, "M": 0, "K": 0, "J": 0}
        probes = {"read": [0, 0], "bind": [0, 0]}
        per_unit = {}
        for u in units(data):
            r = g.multi(str(u), resp, key)
            parents = rows(u / "parents.jsonl")
            tot["parents"] += len(parents)
            tot["parents_answered"] += sum(p["id"] in resp for p in parents)
            tot["parents_correct"] += sum(bool(p["id"] in resp and g.grade_choice(p, resp[p["id"]])) for p in parents)
            for k in ("M", "K", "J"):
                tot[k] += r[k]
            for q in rows(u / "probes.jsonl"):
                if key in q["tiers"] and q["id"] in resp:
                    probes[q["role"]][1] += 1
                    probes[q["role"]][0] += bool(g.grade_probe(q, resp[q["id"]]))
            per_unit[u.name] = r
        res = {"tier": tier, **tot, "parent_accuracy": round(tot["parents_correct"] / tot["parents"], 4) if tot["parents"] else None,
               "probe_accuracy": {k: {"correct": c, "answered": n, "accuracy": round(c / n, 4) if n else None} for k, (c, n) in probes.items()},
               "LC": round(tot["K"] / tot["M"], 4) if tot["M"] else None, "CA": round(tot["J"] / tot["K"], 4) if tot["K"] else None,
               "units": per_unit}
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


# ---------------------------------------------------------------- reconstruction and design: workspaces

def export_workspace(args):
    data, out = Path(args.data), Path(args.out)
    levels = LEVELS if args.level == "all" else (args.level,)
    only = set(args.tasks.split(",")) if args.tasks else None
    made = []
    for level in levels:
        for t in tasks(data, level):
            if only and t.name not in only:
                continue
            ws = out / t.name
            if ws.exists():
                sys.exit(f"{ws} exists; a workspace is never overwritten")
            (ws / "tools").mkdir(parents=True)
            task = (t / "TASK.md").read_text(encoding="utf-8").replace("{PYTHON}", args.python)
            (ws / "TASK.md").write_text(task, encoding="utf-8")
            if (t / "input.pdf").is_file():
                shutil.copyfile(t / "input.pdf", ws / "input.pdf")
            if (t / "ref").is_dir():
                shutil.copytree(t / "ref", ws / "ref")
            shutil.copyfile(CODE_R / "render.py", ws / "tools" / "render_preview.py")
            if (CODE_R / "fonts").is_dir():
                shutil.copytree(CODE_R / "fonts", ws / "tools" / "fonts")
            names = {p.name for p in ws.rglob("*")}
            if names & {"gt", "gt.dxf", "meta.json"}:
                sys.exit(f"ground truth in workspace {ws}")
            made.append(t.name)
    if not made:
        sys.exit("no task matches")
    print(f"{len(made)} workspaces -> {out}: {' '.join(made)}")
    print("start your agent inside each folder with the prompt of docs/PROTOCOL.md, then run: ccb score " + str(out))


def run_score(args):
    score, adjust_overall, l2_metrics, l3_metrics = scorers()
    data, run = Path(args.data), Path(args.run_dir).resolve()
    if not run.is_dir():
        sys.exit(f"{run} is not a folder")
    sdir = run / "_scores"
    say = (lambda *a: None) if getattr(args, "quiet", False) else (lambda *a: print(*a, file=sys.stderr))
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    comp = lambda r, c: 100 * (r.get("components") or {}).get(c, {}).get("value", 0)
    summary, per_task = {}, {}

    l1 = []
    for t in tasks(data, "L1"):
        ws = run / t.name
        if not ws.is_dir():
            continue
        say(f"scoring {t.name}")
        meta = json.loads((t / "gt" / "meta.json").read_text(encoding="utf-8"))
        r = score(str(t / "gt"), str(ws / "output.dxf"), str(sdir / t.name))
        trusted = meta.get("layer_trusted", True)
        row = {"task": t.name, "delivered": (ws / "output.dxf").is_file(), "valid": bool(r.get("valid")),
               "score": round(adjust_overall(r, trusted), 2), "layer_trusted": trusted,
               "components": {k: round(100 * v["value"], 2) for k, v in (r.get("components") or {}).items()}}
        l1.append((row, r))
        per_task[t.name] = row
    if l1:
        summary["L1"] = {
            "tasks": len(l1), "tasks_in_data": len(tasks(data, "L1")), "delivered": sum(a["delivered"] for a, _ in l1),
            "page_score": mean([a["score"] for a, _ in l1]),
            "geometry_f1": mean([comp(r, "geometry F1@1mm") for _, r in l1]),
            "text_f1": mean([comp(r, "text F1") for _, r in l1]),
            "dimension_f1": mean([comp(r, "dimension F1") for a, r in l1 if "dimension F1" in (r.get("components") or {}) or not a["valid"]]),
            "layer_f1": mean([comp(r, "layer-aware F1@1mm") for a, r in l1 if a["layer_trusted"]]),
        }

    l2 = []
    for t in tasks(data, "L2"):
        ws = run / t.name
        if not ws.is_dir():
            continue
        say(f"scoring {t.name}")
        meta = json.loads((t / "meta.json").read_text(encoding="utf-8"))
        outdir = sdir / t.name / "output"       # the scorer writes next to the pages it reads: work on a copy
        if outdir.parent.exists():
            shutil.rmtree(outdir.parent)
        outdir.mkdir(parents=True)
        for p in meta["pages"]:
            src = ws / p["output"]
            if src.is_file():
                shutil.copyfile(src, outdir / Path(p["output"]).name)
        pages = [dict(p, gt=str(t / p["gt"]), output=Path(p["output"]).name) for p in meta["pages"]]
        r = l2_metrics(pages, str(outdir))
        row = {"task": t.name, "pages": r["n_pages"], "delivered": r["delivered"], "set_score": r["mean_overall"],
               "shared_value_recall": round(100 * r["shared_value_recall"], 2),
               "page_scores": {f"page_{p['page']:02d}": p["overall"] for p in r["pages"]}}
        l2.append(row)
        per_task[t.name] = row
    if l2:
        pages = [s for row in l2 for s in row["page_scores"].values()]
        summary["L2"] = {"tasks": len(l2), "tasks_in_data": len(tasks(data, "L2")), "pages": len(pages),
                         "delivered": sum(row["delivered"] for row in l2), "page_score": mean(pages),
                         "shared_value_recall": mean([row["shared_value_recall"] for row in l2])}

    l3 = []
    for t in tasks(data, "L3"):
        ws = run / t.name
        if not ws.is_dir():
            continue
        say(f"scoring {t.name}")
        pred, sib = ws / "output.dxf", t / "ref" / "sibling.dxf"
        r = score(str(t / "gt"), str(pred), str(sdir / t.name))
        c = score(str(t / "gt"), str(sib), str(sdir / t.name / "copy"))
        m = l3_metrics(str(t / "gt" / "gt.dxf"), str(sib), str(pred))
        row = {"task": t.name, "delivered": pred.is_file(), "valid": bool(r.get("valid")),
               "design_recall": round(100 * m["diff_recall"], 2), "page_score": round(r.get("overall", 0), 2),
               "copy_page_score": round(c.get("overall", 0), 2), "vs_copy": round(r.get("overall", 0) - c.get("overall", 0), 2),
               "stale_rate": round(100 * m["stale_rate"], 2)}
        l3.append(row)
        per_task[t.name] = row
    if l3:
        summary["L3"] = {"tasks": len(l3), "tasks_in_data": len(tasks(data, "L3")), "delivered": sum(a["delivered"] for a in l3),
                         "design_recall": mean([a["design_recall"] for a in l3]), "vs_copy": mean([a["vs_copy"] for a in l3]),
                         "page_score": mean([a["page_score"] for a in l3])}

    if not per_task:
        sys.exit(f"no folder of {run} is named after a task of {data}")
    out = {"run": run.name, "summary": summary, "tasks": per_task}
    (run / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    say(f"per-task scores and pictures: {sdir}; summary: {run / 'summary.json'}")


# ---------------------------------------------------------------- selfcheck

def selfcheck(args):
    data = Path(args.data)
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "ground-truth"
        n = 0
        for level in LEVELS:
            for t in tasks(data, level):
                n += 1
                if level == "L2":
                    meta = json.loads((t / "meta.json").read_text(encoding="utf-8"))
                    for p in meta["pages"]:
                        dst = run / t.name / p["output"]
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(t / p["gt"] / "gt.dxf", dst)
                else:
                    (run / t.name).mkdir(parents=True)
                    shutil.copyfile(t / "gt" / "gt.dxf", run / t.name / "output.dxf")
        if n:
            quiet = argparse.Namespace(data=str(data), run_dir=str(run), quiet=True)
            stdout, sys.stdout = sys.stdout, open(os.devnull, "w")
            try:
                run_score(quiet)
            finally:
                sys.stdout.close()
                sys.stdout = stdout
            res = json.loads((run / "summary.json").read_text(encoding="utf-8"))
            full = []
            for name, row in res["tasks"].items():
                s = row.get("score", row.get("set_score", row.get("page_score")))
                good = s == 100 and row.get("shared_value_recall", 100) == 100 and row.get("design_recall", 100) == 100
                full.append(good)
                if not good:
                    print(f"  {name}: {row}")
            ok &= all(full)
            print(f"reconstruction  {sum(full)}/{len(full)} tasks at 100.0 with the ground truth as output")
    g = grader()
    d = g.demo(str(data / "understanding"))
    ok &= d["gold_pass"] == d["gold_total"] and d["perturbed_fail"] == d["perturbed_total"]
    print(f"understanding   {d['gold_pass']}/{d['gold_total']} gold answers pass, {d['perturbed_fail']}/{d['perturbed_total']} perturbed answers fail")
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------- command line

def main(argv=None):
    ap = argparse.ArgumentParser(prog="ccb", description="CivilCADBench: export inputs, grade and score outputs.")
    ap.add_argument("--data", default=str(ROOT / "data"), help="benchmark data folder (default: the sample in this repository)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selfcheck", help="score the ground truth against itself").set_defaults(fn=selfcheck)

    ex = sub.add_parser("export", help="write the inputs of a model or an agent").add_subparsers(dest="what", required=True)
    eu = ex.add_parser("understanding", help="requests.jsonl and images, without answers")
    eu.add_argument("--tier", choices=list(TIERS), required=True)
    eu.add_argument("--out", required=True)
    eu.add_argument("--parents-only", action="store_true", help="few, full: leave out the probes")
    eu.set_defaults(fn=export_understanding)
    ew = ex.add_parser("workspace", help="one folder per reconstruction or design task, without ground truth")
    ew.add_argument("--level", choices=LEVELS + ("all",), default="all")
    ew.add_argument("--tasks", help="comma-separated task ids (default: every task of the level)")
    ew.add_argument("--out", required=True)
    ew.add_argument("--python", default="python", help="interpreter named in TASK.md (default: python)")
    ew.set_defaults(fn=export_workspace)

    gr = sub.add_parser("grade", help="grade responses").add_subparsers(dest="what", required=True)
    gu = gr.add_parser("understanding", help="grade a responses file")
    gu.add_argument("--tier", choices=list(TIERS), required=True)
    gu.add_argument("responses")
    gu.add_argument("--out", help="also write the result to this file")
    gu.set_defaults(fn=grade_understanding)

    sc = sub.add_parser("score", help="score every workspace of a run")
    sc.add_argument("run_dir")
    sc.set_defaults(fn=run_score)

    args = ap.parse_args(argv)
    if not CODE_R.is_dir() or not CODE_U.is_dir():
        sys.exit("the scorers are not next to this package: install from a clone of the repository with `pip install -e .`")
    args.fn(args)


if __name__ == "__main__":
    main()
