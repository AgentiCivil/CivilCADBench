"""Grade model responses on the understanding sample with the typed, deterministic graders of score_v2.py.

    python grade.py single <single_sheet/items.jsonl> <responses.jsonl>
    python grade.py multi  <multi_sheet/U01> <responses.jsonl> [few_sheet|full_set]
    python grade.py demo   <data/understanding>            # gold answers and perturbed answers through the graders

responses.jsonl holds one JSON object per line: {"id": "<item, parent or probe id>", "response": "<final text of the model>"}.

Items and parents (six options, one to four correct) are graded by exact match of the selected set, read from the last
line that starts with 答案： (score_v2.choice_set_detail). Probes are short answers: the answer is the value of the last
{"answer": ...} object in the response, or, if there is none, a final JSON value of exactly the expected shape
(score_v2.bare_json_answer); it is then checked by the grader named in the probe (quantity with unit, named slots, label,
chainage, endpoint pair). No LLM judge is involved anywhere.

For a multi-sheet unit the script also reports the composition metrics of the paper. A solution path is a set of probes
that suffices to decide the parent. M = parents with at least one fully answered path, K = parents with a path whose
probes are all correct, J = those among K whose parent is also correct. Local coverage LC = K / M, compositional accuracy
CA = J / K.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_v2 as S  # noqa: E402

KEYS = ["A", "B", "C", "D", "E", "F"]


def rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def probe_answer(probe, text):
    """value of the last {"answer": ...} object in the text, else a bare final JSON value of the expected shape"""
    raw = (text or "").rstrip()
    if raw.endswith("```"):
        raw = raw[:-3].rstrip()
    for i in range(len(raw) - 1, -1, -1):
        if raw[i] != "{":
            continue
        try:
            v = json.loads(raw[i:])
        except ValueError:
            continue
        if isinstance(v, dict) and "answer" in v:
            return v["answer"]
    return S.bare_json_answer(probe, text)


def grade_choice(item, text):
    return S.choice_set_detail({"option_keys": KEYS, "gold_answer": item["answer"]}, text)["correct"]


def grade_probe(probe, text):
    return S.grade_detail(probe, probe_answer(probe, text))["correct"]


def single(items_path, resp):
    items = rows(items_path)
    done = [it for it in items if it["id"] in resp]
    ok = sum(grade_choice(it, resp[it["id"]]) for it in done)
    return {"items": len(items), "answered": len(done), "correct": ok,
            "accuracy": round(ok / len(items), 4) if items else None}


def multi(unit_dir, resp, tier="few_sheet"):
    parents, probes = rows(os.path.join(unit_dir, "parents.jsonl")), rows(os.path.join(unit_dir, "probes.jsonl"))
    pc = {p["id"]: grade_choice(p, resp[p["id"]]) for p in parents if p["id"] in resp}
    qc = {q["id"]: grade_probe(q, resp[q["id"]]) for q in probes if q["id"] in resp and tier in q["tiers"]}
    m = k = j = 0
    for p in parents:
        paths = [path for path in p["solution_paths"] if all(q in qc for q in path)]
        if p["id"] not in pc or not paths:
            continue
        m += 1
        local = any(all(qc[q] for q in path) for path in paths)
        k += local
        j += local and pc[p["id"]]
    role = {q["id"]: q["role"] for q in probes}
    by = {r: [v for q, v in qc.items() if role[q] == r] for r in ("read", "bind")}
    return {"tier": tier, "parents": len(parents), "parent_accuracy": round(sum(pc.values()) / len(parents), 4),
            "probe_accuracy": {r: round(sum(v) / len(v), 4) if v else None for r, v in by.items()},
            "M": m, "K": k, "J": j, "LC": round(k / m, 4) if m else None, "CA": round(j / k, 4) if k else None}


def demo(root):
    """Gold answers must all pass; an answer with one letter flipped, one digit changed or a wrong unit must fail."""
    out = {"gold_pass": 0, "gold_total": 0, "perturbed_fail": 0, "perturbed_total": 0}
    choice = rows(os.path.join(root, "single_sheet", "items.jsonl"))
    probes = []
    for u in sorted(os.listdir(os.path.join(root, "multi_sheet"))):
        d = os.path.join(root, "multi_sheet", u)
        if os.path.isdir(d):
            choice += rows(os.path.join(d, "parents.jsonl"))
            probes += rows(os.path.join(d, "probes.jsonl"))
    for it in choice:
        out["gold_total"] += 1
        out["gold_pass"] += grade_choice(it, "分析略。\n答案：" + ",".join(it["answer"]))
        flipped = sorted(set(it["answer"]) ^ {"A"}) or ["B"]
        out["perturbed_total"] += 1
        out["perturbed_fail"] += not grade_choice(it, "答案：" + ",".join(flipped))
    for q in probes:
        g = q["gold_answer"]
        out["gold_total"] += 1
        out["gold_pass"] += grade_probe(q, "推理略。" + json.dumps({"answer": g}, ensure_ascii=False))
        bad = json.loads(json.dumps(g))
        if q["grader_id"] == "quantity":
            bad["value"] = bad["value"] + 1
        elif q["grader_id"] == "slots":
            key = sorted(bad)[0]
            bad[key] = dict(bad[key], value=bad[key]["value"] + 1) if isinstance(bad[key], dict) else str(bad[key]) + "x"
        elif q["grader_id"] == "endpoints":
            bad = [bad[0], str(bad[1]) + "x"]
        else:
            bad = str(bad) + "x"
        out["perturbed_total"] += 1
        out["perturbed_fail"] += not grade_probe(q, json.dumps({"answer": bad}, ensure_ascii=False))
    return out


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "demo":
        print(json.dumps(demo(sys.argv[2]), ensure_ascii=False))
    else:
        responses = {r["id"]: r["response"] for r in rows(sys.argv[3])}
        if mode == "single":
            print(json.dumps(single(sys.argv[2], responses), ensure_ascii=False))
        else:
            print(json.dumps(multi(sys.argv[2], responses, sys.argv[4] if len(sys.argv) > 4 else "few_sheet"), ensure_ascii=False))
