"""ccb, the command line of CivilCADBench.

Run a model or an agent and score it:

    ccb download                                               the full benchmark from Hugging Face
    ccb selfcheck                                              score the ground truth against itself
    ccb run understanding --tier all --model M --out RUN_DIR   ask a model the questions and grade the answers
    ccb run agent --level all --agent codex --model M --out RUN_DIR
                                                               start an agent on every task and score the files
    ccb report RUN_DIR                                         the row of the leaderboard for a run

Or bring your own harness: ccb writes the inputs as files, reads the outputs as files and scores them.

    ccb export understanding --tier single --out DIR     requests for a model, without answers
    ccb grade understanding --tier single RESPONSES      grade the responses of a model
    ccb export workspace --level L1 --out RUN_DIR        one workspace per task for an agent, without ground truth
    ccb score RUN_DIR                                    score every workspace of a run and write summary.json

All scores come from the scorers under code/, which this file imports and does not change. docs/PROTOCOL.md describes
the files and the rules.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_R = ROOT / "code" / "reconstruction"
CODE_U = ROOT / "code" / "understanding"
SYSTEM = json.loads((Path(__file__).parent / "prompts.json").read_text(encoding="utf-8"))
TIERS = {"single": None, "few": "few_sheet", "full": "full_set"}
LEVELS = ("L1", "L2", "L3")
HF_REPO = "AgentiCivil/CivilCADBench"
DOWNLOAD = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "civilcadbench" / "CivilCADBench"
COMPLETE = ".ccb_download_complete"
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


def multi_items(data, key=None):
    """Parents of multi_sheet/items.jsonl, each with its probes; with a tier key, only the parents posed in that tier."""
    items = rows(Path(data) / "understanding" / "multi_sheet" / "items.jsonl")
    return [p for p in items if key is None or key in p["tiers"]]


def set_folders(data, tmp, key=None):
    """The graders under code/ read one folder per drawing set, with parents.jsonl and probes.jsonl. This writes
    items.jsonl in that form under tmp/multi_sheet/ and returns the folders, so that the graders run unchanged."""
    by = {}
    for p in multi_items(data, key):
        by.setdefault(p["set"], []).append(p)
    out = []
    for name, parents in sorted(by.items()):
        d = Path(tmp) / "multi_sheet" / name
        d.mkdir(parents=True)
        with open(d / "parents.jsonl", "w", encoding="utf-8") as f:
            for p in parents:
                f.write(json.dumps({k: v for k, v in p.items() if k != "probes"}, ensure_ascii=False) + "\n")
        with open(d / "probes.jsonl", "w", encoding="utf-8") as f:
            for p in parents:
                for q in p["probes"]:
                    f.write(json.dumps({**q, "parent": p["id"]}, ensure_ascii=False) + "\n")
        out.append(d)
    return out


def tasks(data, level):
    base = Path(data) / "reconstruction" / level
    return [d for d in sorted(base.iterdir()) if d.is_dir()] if base.is_dir() else []


# ---------------------------------------------------------------- which data

def resolve_data(arg):
    """--data, then the environment variable CCB_DATA, then a finished `ccb download`, then the sample of the repository."""
    if arg:
        p, source = Path(arg), "--data"
    elif os.environ.get("CCB_DATA"):
        p, source = Path(os.environ["CCB_DATA"]), "CCB_DATA"
    elif (DOWNLOAD / COMPLETE).is_file():
        p, source = DOWNLOAD / "data", "ccb download"
    else:
        p, source = ROOT / "data", "sample"
    if not (p / "understanding").is_dir() and not (p / "reconstruction").is_dir() and (p / "data").is_dir():
        p = p / "data"          # the folder of the dataset was named, the data is one level below
    if not p.is_dir():
        sys.exit(f"data folder {p} not found")
    if p.resolve() == (ROOT / "data").resolve():
        source = "sample"
    return p, source


def counts(data):
    data, c = Path(data), {}
    f = data / "understanding" / "single_sheet" / "items.jsonl"
    c["single"] = len(rows(f)) if f.is_file() else 0
    multi = multi_items(data) if (data / "understanding" / "multi_sheet" / "items.jsonl").is_file() else []
    for tier, key in (("few", "few_sheet"), ("full", "full_set")):
        c[tier] = sum(key in p["tiers"] for p in multi)
        c[tier + "_probes"] = sum(key in q["tiers"] for p in multi if key in p["tiers"] for q in p["probes"])
    for level in LEVELS:
        c[level] = len(tasks(data, level))
    c["L2_pages"] = sum(json.loads((t / "meta.json").read_text(encoding="utf-8"))["n_pages"] for t in tasks(data, "L2"))
    return c


def describe(data, source):
    """Say which data a command uses, on stderr, so that a score on the sample is not taken for a full result."""
    c = counts(data)
    where = "the sample of this repository" if source == "sample" else f"{data} (from {source})"
    print(f"data: {where}\n"
          f"      Single-Sheet {c['single']} | Few-Sheet {c['few']} (+{c['few_probes']} probes) | Full-Set {c['full']} (+{c['full_probes']} probes)"
          f" | L1 {c['L1']} | L2 {c['L2']} ({c['L2_pages']} pages) | L3 {c['L3']}", file=sys.stderr)
    if source == "sample":
        print("      this is the sample, not the full benchmark: get it with `ccb download`", file=sys.stderr)
    return c


def download(args):
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        sys.exit("ccb download needs the package huggingface_hub: pip install huggingface_hub")
    dst = Path(args.dir) if args.dir else DOWNLOAD
    print(f"downloading the dataset {HF_REPO} (about 6 GB in 13,000 files) to {dst}\n"
          "an interrupted download continues when started again; with HF_TOKEN set, the Hub allows a higher request rate")
    snapshot_download(HF_REPO, repo_type="dataset", local_dir=str(dst), max_workers=args.workers)
    if not (dst / "data" / "understanding").is_dir() or not (dst / "data" / "reconstruction").is_dir():
        sys.exit(f"{dst}/data is incomplete")
    (dst / COMPLETE).write_text(time.strftime("%F %T") + "\n", encoding="utf-8")
    describe(dst / "data", "ccb download")
    if args.dir:
        print(f"this is not the default place: pass --data {dst} or set CCB_DATA={dst}")
    else:
        print("ccb uses this data from now on; --data or CCB_DATA name another folder")


# ---------------------------------------------------------------- understanding: export

def build_requests(data, tier, parents_only=False):
    """The requests of a tier, in order. "images" are paths inside an export folder, "_files" the files they come from."""
    data = Path(data)
    instr = json.loads((data / "understanding" / "multi_sheet" / "prompts.json").read_text(encoding="utf-8"))
    reqs = []

    def options(item):
        return [{"key": o["key"], "text": o["text"]} for o in item["options"]]

    if tier == "single":
        base = data / "understanding" / "single_sheet"
        for it in rows(base / "items.jsonl"):
            user = {"image_count": 1, "instruction": instr["parent_instruction"], "question": it["question"], "options": options(it)}
            reqs.append({"id": it["id"], "kind": "item", "tier": tier, "system": SYSTEM["one_turn_choice"], "prompt": compact(user),
                         "images": ["images/" + Path(it["image"]).name], "_files": [base / it["image"]]})
    else:
        key = TIERS[tier]
        base = data / "understanding" / "multi_sheet"
        for p in multi_items(data, key):        # every parent brings its own images: few_sheet.images or full_set.images
            files = [base / rel for rel in p[key]["images"]]
            imgs = ["images/" + rel.removeprefix("images/") for rel in p[key]["images"]]
            if tier == "few":
                user = {"image_count": len(imgs)}
                system = SYSTEM["one_turn_choice"]
            else:
                user = {"document": "document", "page_count": len(imgs)}
                system = SYSTEM["full_choice"]
            user.update(instruction=instr["parent_instruction"], question=p["question"], options=options(p))
            reqs.append({"id": p["id"], "kind": "parent", "tier": tier, "system": system, "prompt": compact(user), "images": imgs, "_files": files})
            if parents_only:
                continue
            for q in p["probes"]:
                if key not in q["tiers"]:
                    continue
                if tier == "few":
                    user = {"image_count": len(imgs), "instruction": instr["probe_instruction_few_sheet"]}
                    system = SYSTEM["one_turn_probe"]
                else:
                    user = {"document": "document", "page_count": len(imgs), "instruction": instr["probe_instruction_full_set"]}
                    system = SYSTEM["full_probe"]
                user.update(question=q["question"], response_spec=q["response_spec"])
                reqs.append({"id": q["id"], "kind": "probe", "tier": tier, "system": system, "prompt": compact(user), "images": imgs, "_files": files})

    def keys(x):
        if isinstance(x, dict):
            return set(x) | set().union(*map(keys, x.values())) if x else set()
        return set().union(*map(keys, x)) if isinstance(x, list) and x else set()

    for r in reqs:          # a request holds the question and nothing that grades it
        user = json.loads(r["prompt"])
        leak = (set(r) | {k for k in user if k != "response_spec"} | keys(user.get("options", []))) & FORBIDDEN
        if leak:
            sys.exit(f"answer field in request {r['id']}: {sorted(leak)}")
    return reqs


def export_understanding(args):
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} is not empty")
    (out / "images").mkdir(parents=True)
    reqs = build_requests(args.data, args.tier, args.parents_only)
    for r in reqs:
        for rel, src in zip(r["images"], r.pop("_files")):
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copyfile(src, dst)
    with open(out / "requests.jsonl", "w", encoding="utf-8") as f:
        for r in reqs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    kinds = {k: sum(r["kind"] == k for r in reqs) for k in ("item", "parent", "probe")}
    print(f"{len(reqs)} requests ({', '.join(f'{n} {k}s' for k, n in kinds.items() if n)}) -> {out / 'requests.jsonl'}")
    print("write one line per request to a responses file: {\"id\": ..., \"response\": \"<final text of the model>\"}")


# ---------------------------------------------------------------- understanding: grade

def grade_tier(data, tier, resp):
    """Grade {request id: final text} for one tier."""
    g = grader()
    data = Path(data)
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
        per_set = {}
        with tempfile.TemporaryDirectory() as tmp:
            for u in set_folders(data, tmp, key):       # the parents posed in this tier, grouped by drawing set
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
                per_set[u.name] = r
        res = {"tier": tier, **tot, "parent_accuracy": round(tot["parents_correct"] / tot["parents"], 4) if tot["parents"] else None,
               "probe_accuracy": {k: {"correct": c, "answered": n, "accuracy": round(c / n, 4) if n else None} for k, (c, n) in probes.items()},
               "LC": round(tot["K"] / tot["M"], 4) if tot["M"] else None, "CA": round(tot["J"] / tot["K"], 4) if tot["K"] else None,
               "sets": per_set}
    return res


def grade_understanding(args):
    res = grade_tier(args.data, args.tier, {r["id"]: r.get("response") or "" for r in rows(args.responses)})
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


# ---------------------------------------------------------------- reconstruction and design: workspaces

def export_task(t, ws, python):
    """The workspace of one task: the task statement, its inputs and the preview tool. Never the ground truth."""
    if ws.exists():
        sys.exit(f"{ws} exists; a workspace is never overwritten")
    (ws / "tools").mkdir(parents=True)
    task = (t / "TASK.md").read_text(encoding="utf-8").replace("{PYTHON}", python)
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


def export_workspace(args):
    data, out = Path(args.data), Path(args.out)
    levels = LEVELS if args.level == "all" else (args.level,)
    only = set(args.tasks.split(",")) if args.tasks else None
    made = []
    for level in levels:
        for t in tasks(data, level):
            if only and t.name not in only:
                continue
            export_task(t, out / t.name, args.python)
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
    if not getattr(args, "quiet", False):
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    say(f"per-task scores and pictures: {sdir}; summary: {run / 'summary.json'}")
    return out


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
            res = run_score(argparse.Namespace(data=str(data), run_dir=str(run), quiet=True))
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
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "single_sheet").mkdir()
        shutil.copyfile(data / "understanding" / "single_sheet" / "items.jsonl", Path(tmp) / "single_sheet" / "items.jsonl")
        set_folders(data, tmp)
        d = g.demo(tmp)
    ok &= d["gold_pass"] == d["gold_total"] and d["perturbed_fail"] == d["perturbed_total"]
    print(f"understanding   {d['gold_pass']}/{d['gold_total']} gold answers pass, {d['perturbed_fail']}/{d['perturbed_total']} perturbed answers fail")
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------- run a model or an agent

def record(run, data, source):
    """run.json: what a run folder holds. A run folder belongs to one data folder."""
    f = Path(run) / "run.json"
    rec = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    here = {"path": str(Path(data).resolve()), "source": source}
    if rec.get("data", here)["path"] != here["path"]:
        sys.exit(f"{run} holds results on other data ({rec['data']['path']}); use another --out")
    rec["data"] = {**here, "counts": counts(data)}
    return rec


def save(run, rec):
    Path(run).mkdir(parents=True, exist_ok=True)
    (Path(run) / "run.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def same_settings(run, old, new, what):
    diff = [k for k in new if k in old and old[k] != new[k]]
    if diff:
        sys.exit(f"{run} holds {what} results with other settings ({', '.join(f'{k}: {old[k]!r}' for k in diff)}); use another --out")


def run_understanding(args):
    from . import understanding as U
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("set OPENAI_API_KEY, and OPENAI_BASE_URL for an endpoint other than api.openai.com")
    base = args.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    try:
        extra = json.loads(args.extra)
        assert isinstance(extra, dict)
    except (ValueError, AssertionError):
        sys.exit("--extra is a JSON object, for example '{\"reasoning_effort\":\"high\"}'")
    run, tiers = Path(args.out), list(TIERS) if args.tier == "all" else [args.tier]
    budget = {"steps": args.steps, "views": args.views, "tool_calls": args.tool_calls,
              "step_tokens": args.step_tokens, "total_tokens": args.total_tokens}
    rec = record(run, args.data, args.data_source)
    settings = {"model": args.model, "extra": extra, "image_detail": args.image_detail}       # the endpoint is not recorded
    old = rec.setdefault("understanding", {})
    same_settings(run, old, settings, "understanding")
    if "full" in tiers:
        same_settings(run, old, {"full_budget": budget}, "Full-Set")
        old["full_budget"] = budget
    old.update(settings)
    save(run, rec)
    ep = U.Endpoint(base, key, args.model, extra, args.image_detail)
    say = lambda *a: print(*a, file=sys.stderr)
    failed = 0
    for tier in tiers:
        reqs = build_requests(args.data, tier, args.parents_only)
        folder = run / "understanding" / tier
        say(f"{tier}: {len(reqs)} requests")
        ok, bad, had = U.run_tier(ep, reqs, folder, tier, args.concurrency, args.limit, budget, say)
        failed += bad
        resp = {r["id"]: r.get("response") or "" for r in rows(folder / "responses.jsonl")}
        res = grade_tier(args.data, tier, resp)
        res = {"tier": tier, "requests": len(reqs), "answered": sum(r["id"] in resp for r in reqs), **{k: v for k, v in res.items() if k != "tier"}}
        (folder / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        say(f"{tier}: {ok} answered now, {bad} failed, {had} already there -> {folder / 'responses.jsonl'}")
    if failed:
        say(f"{failed} requests failed (see errors.log in the tier folders). Run the same command again to take them up; "
            "until then they count as wrong.")
    report(argparse.Namespace(run_dir=str(run), data=args.data, data_source=args.data_source, json=False))
    sys.exit(1 if failed else 0)


def run_agent(args):
    from . import agents as A
    run = Path(args.out)
    levels = LEVELS if args.level == "all" else (args.level,)
    only = set(args.tasks.split(",")) if args.tasks else None
    todo = [(t.name, level, t) for level in levels for t in tasks(args.data, level) if not only or t.name in only]
    if not todo:
        sys.exit("no task matches")
    docker = not args.local
    if not args.agent_cmd:
        if not args.agent or not args.model:
            sys.exit("give --agent and --model, or your own agent with --agent-cmd")
        if docker and args.agent == "claude-code" and not args.env_file:
            sys.exit("Claude Code in a container needs --env-file: a file with ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN or "
                     "ANTHROPIC_API_KEY, one per line, readable by you only")
        if docker and args.agent == "codex" and not args.codex_home:
            sys.exit("Codex in a container needs --codex-home: a folder with the config.toml and auth.json of your endpoint")
        if not docker and not shutil.which("claude" if args.agent == "claude-code" else "codex"):
            sys.exit(f"{'claude' if args.agent == 'claude-code' else 'codex'} is not on the PATH")
    for f in (args.env_file, args.codex_home):
        if f and not Path(f).exists():
            sys.exit(f"{f} not found")
    limits = {"L1": args.limit_page, "L2": args.limit_set, "L3": args.limit_page}
    cfg = {"agent": args.agent, "agent_cmd": args.agent_cmd, "model": args.model, "docker": docker, "limits": limits, "jobs": args.jobs,
           "env_file": str(Path(args.env_file).resolve()) if args.env_file else None,
           "codex_home": str(Path(args.codex_home).resolve()) if args.codex_home else None,
           "image": args.image, "proxy_image": args.proxy_image, "proxy": args.proxy_name,
           "sock": str(Path(args.proxy_socket).expanduser().resolve())}
    rec = record(run, args.data, args.data_source)
    settings = {"agent": "custom command" if args.agent_cmd else args.agent, "command": args.agent_cmd, "model": args.model,
                "isolation": "container" if docker else "none (local run)", "image": args.image if docker else None, "limits_seconds": limits}
    old = rec.setdefault("agent", {})
    same_settings(run, old, settings, "agent")
    old.update(settings)
    save(run, rec)
    say = lambda *a: print(*a, file=sys.stderr)
    if not docker:
        say("LOCAL RUN: the agent works on this machine without a container. Nothing keeps it from the network or from files\n"
            "outside its workspace, the ground truth among them. Do not report these numbers as protocol results.")
    python = args.python or ("python" if docker else sys.executable)
    def stopped(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stopped)       # stop the agents before leaving, whichever way ccb is stopped
    try:
        done = A.run_all(cfg, run, todo, lambda t, ws: export_task(t, ws, python), say)
    except KeyboardInterrupt:
        sys.exit(130)
    old.setdefault("tasks", {}).update(done)
    save(run, rec)
    run_score(argparse.Namespace(data=args.data, run_dir=str(run), quiet=True))
    report(argparse.Namespace(run_dir=str(run), data=args.data, data_source=args.data_source, json=False))


# ---------------------------------------------------------------- report

GROUPS = [("L1", ["Score", "Geom.", "Text", "Dim.", "Layer"]), ("L2", ["Score", "vs. alone", "Shared"]),
          ("L3", ["Recall", "vs. copy"]), ("Understanding", ["Single", "Few", "Full"])]


def report(args):
    """The columns of the leaderboard for one run folder, on a 0 to 100 scale, and what the run covers."""
    run = Path(args.run_dir)
    rec = json.loads((run / "run.json").read_text(encoding="utf-8")) if (run / "run.json").is_file() else {}
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))["summary"] if (run / "summary.json").is_file() else {}
    tiers = {}
    for tier in TIERS:
        f = run / "understanding" / tier / "result.json"
        if f.is_file():
            tiers[tier] = json.loads(f.read_text(encoding="utf-8"))
    if not summary and not tiers:
        sys.exit(f"{run} holds neither summary.json nor understanding results")
    l1, l2, l3 = summary.get("L1", {}), summary.get("L2", {}), summary.get("L3", {})
    pct = lambda r, k: None if r.get(k) is None else 100 * r[k]
    values = {
        "L1": [l1.get("page_score"), l1.get("geometry_f1"), l1.get("text_f1"), l1.get("dimension_f1"), l1.get("layer_f1")],
        "L2": [l2.get("page_score"), None, l2.get("shared_value_recall")],       # vs. alone needs the same pages drawn alone
        "L3": [l3.get("design_recall"), l3.get("vs_copy")],
        "Understanding": [pct(tiers.get("single", {}), "accuracy"), pct(tiers.get("few", {}), "parent_accuracy"), pct(tiers.get("full", {}), "parent_accuracy")],
    }
    cover, partial = [], False
    ran = (rec.get("agent") or {}).get("tasks", {})
    for level, r, unit in (("L1", l1, "pages"), ("L2", l2, "sets"), ("L3", l3, "tasks")):
        if r:
            made = f"{r['delivered']} of {r['pages']} pages delivered" if level == "L2" else f"{r['delivered']} delivered"
            late = sum(v.get("exit_code") == 124 for k, v in ran.items() if k.startswith(level + "-"))
            cover.append(f"{level} {r['tasks']} of {r['tasks_in_data']} {unit} ({made}" + (f", {late} stopped at the time limit)" if late else ")"))
            partial |= r["tasks"] < r["tasks_in_data"]
    for tier, r in tiers.items():
        cover.append(f"{tier} {r['answered']} of {r['requests']} requests answered" if "requests" in r else tier)
        partial |= r.get("answered", 0) < r.get("requests", 0)
    data = rec.get("data") or {"source": getattr(args, "data_source", None)}       # a run of your own harness has no run.json
    out = {"run": run.name, "data": data.get("source"), "partial": bool(partial),
           "model": (rec.get("understanding") or {}).get("model"), "agent": (rec.get("agent") or {}).get("agent"),
           "agent_model": (rec.get("agent") or {}).get("model"), "isolation": (rec.get("agent") or {}).get("isolation"),
           "columns": {g: {n: (None if v is None else round(v, 1)) for n, v in zip(names, values[g])} for g, names in GROUPS}}
    (run / "report.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if getattr(args, "json", False):
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    width = lambda n: max(len(n), 6) + 2
    head1 = "  ".join(g.ljust(sum(width(n) for n in names)) for g, names in GROUPS)
    head2 = "  ".join("".join(n.rjust(width(n)) for n in names) for g, names in GROUPS)
    cell = lambda g, n, v: ("—" if v is None else f"{v:+.1f}" if n.startswith("vs.") else f"{v:.1f}").rjust(width(n))
    line = "  ".join("".join(cell(g, n, v) for n, v in zip(names, values[g])) for g, names in GROUPS)
    who = " | ".join(x for x in (out["model"] and f"model {out['model']}",
                                 out["agent"] and f"agent {out['agent']}" + (f" with {out['agent_model']}" if out["agent_model"] else "")) if x)
    print(f"\n{run}" + (f"   {who}" if who else ""))
    print(head1.rstrip() + "\n" + head2 + "\n" + line)
    print("covers: " + "; ".join(cover))
    notes = []
    if data.get("source") == "sample":
        notes.append("these numbers are on the sample of the repository, not on the full benchmark")
    if partial:
        notes.append("the run is not complete: an open request counts as wrong, a task without a workspace is left out of the means")
    if out["isolation"] and out["isolation"] != "container":
        notes.append("the agent ran on this machine without a container, which the protocol does not allow for reported results")
    if l2:
        notes.append("vs. alone is the L2 page score minus the score of the same pages drawn alone; ccb does not compute it")
    for n in notes:
        print("note: " + n)


# ---------------------------------------------------------------- command line

def main(argv=None):
    ap = argparse.ArgumentParser(prog="ccb", description="CivilCADBench: run a model or an agent, grade and score the outputs.")
    where = "benchmark data folder (default: $CCB_DATA, else what `ccb download` fetched, else the sample in this repository)"
    ap.add_argument("--data", help=where)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def leaf(parent, name, fn, **kw):
        q = parent.add_parser(name, **kw)
        q.add_argument("--data", default=argparse.SUPPRESS, help=where)
        q.set_defaults(fn=fn)
        return q

    dl = sub.add_parser("download", help="get the full benchmark from Hugging Face")
    dl.add_argument("--dir", help=f"where to put it (default: {DOWNLOAD})")
    dl.add_argument("--workers", type=int, default=16, help="files fetched at the same time (default: 16)")
    dl.set_defaults(fn=download)

    leaf(sub, "selfcheck", selfcheck, help="score the ground truth against itself")

    rn = sub.add_parser("run", help="run a model or an agent, then grade or score it").add_subparsers(dest="what", required=True)
    ru = leaf(rn, "understanding", run_understanding, help="ask a model the questions through an OpenAI-compatible endpoint and grade the answers",
              description="Ask a model the questions and grade the answers. The key is read from OPENAI_API_KEY and written nowhere. "
                          "Start the same command again to continue an interrupted run.")
    ru.add_argument("--tier", choices=list(TIERS) + ["all"], required=True)
    ru.add_argument("--model", required=True)
    ru.add_argument("--out", required=True, help="run folder; results go to <out>/understanding/<tier>/")
    ru.add_argument("--base-url", help="endpoint (default: $OPENAI_BASE_URL, else https://api.openai.com/v1)")
    ru.add_argument("--extra", default="{}", help='JSON merged into every request body, e.g. \'{"reasoning_effort":"high"}\'')
    ru.add_argument("--image-detail", choices=["low", "high", "auto", "original"], help="detail field of every image (default: not sent)")
    ru.add_argument("--concurrency", type=int, default=4, help="requests in flight (default: 4)")
    ru.add_argument("--limit", type=int, help="answer only the first N open requests of each tier")
    ru.add_argument("--parents-only", action="store_true", help="few, full: leave out the probes")
    fb = ru.add_argument_group("budget of a Full-Set request (defaults: the limits of the paper)")
    fb.add_argument("--steps", type=int, default=16, help="model turns (default: 16)")
    fb.add_argument("--views", type=int, default=24, help="page views (default: 24)")
    fb.add_argument("--tool-calls", type=int, default=40, help="tool calls, page views included (default: 40)")
    fb.add_argument("--step-tokens", type=int, default=8192, help="generated tokens per turn (default: 8192)")
    fb.add_argument("--total-tokens", type=int, default=65536, help="generated tokens per request (default: 65536)")

    ra = leaf(rn, "agent", run_agent, help="start an agent in a workspace per task and score what it writes",
              description="Start an agent once per reconstruction or design task and score the files it writes. By default each task runs "
                          "in a container with the limits of docs/PROTOCOL.md. Start the same command again to continue an interrupted run.")
    ra.add_argument("--level", choices=LEVELS + ("all",), default="all")
    ra.add_argument("--tasks", help="comma-separated task ids (default: every task of the level)")
    ra.add_argument("--agent", choices=["claude-code", "codex"])
    ra.add_argument("--model")
    ra.add_argument("--agent-cmd", help="your own agent: a shell command run inside each workspace. It gets $CCB_PROMPT, $CCB_TASK, $CCB_LEVEL, "
                                        "$CCB_MODEL and $CCB_WORKSPACE; {prompt}, {task}, {model} and {workspace} in the text are filled in")
    ra.add_argument("--out", required=True, help="run folder: one workspace per task, logs under _logs/")
    ra.add_argument("--local", action="store_true", help="run on this machine without a container (no isolation: not for reported results)")
    ra.add_argument("--env-file", help="container: file with the endpoint and the key of the agent, passed to docker --env-file")
    ra.add_argument("--codex-home", help="container, Codex: folder with config.toml and auth.json")
    ra.add_argument("--image", default="cadbench/agent", help="container image (default: cadbench/agent, built from examples/docker)")
    ra.add_argument("--proxy-image", default="cadbench/proxy", help="image of the allowlist proxy (default: cadbench/proxy, built from examples/docker)")
    ra.add_argument("--proxy-name", default="cadbench-proxy",
                    help="name of the proxy container (default: cadbench-proxy). A running container of this name is used as it is; "
                         "give another name to start a proxy of your own next to it")
    ra.add_argument("--proxy-socket", default=str(Path.home() / ".cache" / "cadbench" / "proxy_sock"),
                    help="folder that holds the socket of the proxy (default: ~/.cache/cadbench/proxy_sock); one folder per proxy container")
    ra.add_argument("--jobs", type=int, default=1, help="tasks at the same time (default: 1)")
    ra.add_argument("--limit-page", type=int, default=7200, help="seconds per L1 or L3 task (default: 7200)")
    ra.add_argument("--limit-set", type=int, default=21600, help="seconds per L2 task (default: 21600)")
    ra.add_argument("--python", help="interpreter named in TASK.md (default: python in a container, the interpreter of ccb with --local)")

    rp = leaf(sub, "report", report, help="the leaderboard columns of a run folder")
    rp.add_argument("run_dir")
    rp.add_argument("--json", action="store_true", help="print report.json instead of the table")

    ex = sub.add_parser("export", help="write the inputs of a model or an agent").add_subparsers(dest="what", required=True)
    eu = leaf(ex, "understanding", export_understanding, help="requests.jsonl and images, without answers")
    eu.add_argument("--tier", choices=list(TIERS), required=True)
    eu.add_argument("--out", required=True)
    eu.add_argument("--parents-only", action="store_true", help="few, full: leave out the probes")
    ew = leaf(ex, "workspace", export_workspace, help="one folder per reconstruction or design task, without ground truth")
    ew.add_argument("--level", choices=LEVELS + ("all",), default="all")
    ew.add_argument("--tasks", help="comma-separated task ids (default: every task of the level)")
    ew.add_argument("--out", required=True)
    ew.add_argument("--python", default="python", help="interpreter named in TASK.md (default: python)")

    gr = sub.add_parser("grade", help="grade responses").add_subparsers(dest="what", required=True)
    gu = leaf(gr, "understanding", grade_understanding, help="grade a responses file")
    gu.add_argument("--tier", choices=list(TIERS), required=True)
    gu.add_argument("responses")
    gu.add_argument("--out", help="also write the result to this file")

    sc = leaf(sub, "score", run_score, help="score every workspace of a run")
    sc.add_argument("run_dir")

    args = ap.parse_args(argv)
    if not CODE_R.is_dir() or not CODE_U.is_dir():
        sys.exit("the scorers are not next to this package: install from a clone of the repository with `pip install -e .`")
    if args.fn is not download:
        args.data, args.data_source = resolve_data(args.data)
        if args.fn is not report:
            describe(args.data, args.data_source)
        args.data = str(args.data)
    args.fn(args)


if __name__ == "__main__":
    main()
