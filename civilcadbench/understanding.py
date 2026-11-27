"""Answer the understanding requests with an OpenAI-compatible chat endpoint.

single, few   one chat completion per request: the system prompt, then one user message with the prompt text and the
              images in order.
full          a tool loop per request. The model gets the question and the page count and opens pages itself with
              view_page (a whole page or a crop); calculate does arithmetic. The loop keeps a budget of steps, tool
              calls, page views and generated tokens, and closes with one last turn without tools when it runs out.

The key is passed in by the caller, who reads it from the environment. It is sent to the endpoint and written nowhere.
Only the standard library and Pillow are used.
"""
from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import io
import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# the limits of the Full-Set runs of the paper (16 steps, 24 page views, 40 tool calls, 8,192 tokens per step, 65,536 in all)
BUDGET = {"steps": 16, "views": 24, "tool_calls": 40, "step_tokens": 8192, "total_tokens": 65536}
FINAL_TOKENS = 4096             # kept back for the closing turn
MAX_PIXELS = 100_000_000        # pixels of page images delivered to the model in one request
EDGE = 1568                     # long edge of a delivered page image
ATTEMPTS, BACKOFF = 3, (2, 4)   # a call is repeated after HTTP 429 or 5xx only
STEP_TIMEOUT, EPISODE_TIMEOUT, ONE_TURN_TIMEOUT = 300, 1800, 1800

TOOLS = [
    {"type": "function", "function": {
        "name": "view_page",
        "description": "Read this document page as a PNG. Optional bbox uses displayed page top-left normalized [x0,y0,x1,y1].",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["page"], "properties": {
            "page": {"type": "integer", "minimum": 1},
            "bbox": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number", "minimum": 0, "maximum": 1}}}}}},
    {"type": "function", "function": {
        "name": "calculate",
        "description": "Arithmetic with + - * / parentheses sqrt and ^2. Map units yourself; output is rounded to 10 decimal places.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["expression"], "properties": {
            "expression": {"type": "string", "minLength": 1, "maxLength": 256}}}}},
]
FINALIZE_CHOICE = ("Observation budget is now closed. No tools remain. Give your best supported final answer now, ending "
                   "with the answer line the user message specifies. Do not request more tools.")
FINALIZE_PROBE = ("Observation budget is now closed. No tools remain. Give your best supported final JSON answer now, or "
                  "answer null. Do not request more tools. Return only one JSON object: {\"answer\":\"A\",\"evidence_pages\":[1]} "
                  "for a single choice; use an array of option letters for multiple selection. Use the JSON value null, not "
                  "the string \"null\", if evidence is insufficient. Do not include explanation or markdown.")


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- the two tools

def calculate(expression):
    """+ - * / parentheses sqrt and ^2, by recursive descent; nothing is evaluated as code."""
    if not isinstance(expression, str) or len(expression) > 256:
        raise ToolError("INVALID_EXPRESSION")
    t = re.findall(r"sqrt|[0-9]+(?:\.[0-9]+)?|\.[0-9]+|[+\-*/()^]", expression)
    if "".join(t) != re.sub(r"\s", "", expression):
        raise ToolError("INVALID_EXPRESSION")
    pos, depth = [0], [0]

    def take():
        pos[0] += 1
        return t[pos[0] - 1] if pos[0] <= len(t) else None

    def peek():
        return t[pos[0]] if pos[0] < len(t) else None

    def atom():
        depth[0] += 1
        if depth[0] > 32:
            raise ToolError("EXPRESSION_DEPTH")
        s = take()
        if s in ("+", "-"):
            n = (-1 if s == "-" else 1) * atom()
        elif s == "sqrt":
            if take() != "(":
                raise ToolError("SYNTAX")
            v = total()
            n = math.sqrt(v) if v >= 0 else math.nan
            if take() != ")":
                raise ToolError("SYNTAX")
        elif s == "(":
            n = total()
            if take() != ")":
                raise ToolError("SYNTAX")
        elif s is not None and re.match(r"[0-9.]", s):
            n = float(s)
        else:
            raise ToolError("SYNTAX")
        if peek() == "^":
            take()
            if take() != "2":
                raise ToolError("ONLY_SQUARE_ALLOWED")
            n = n * n
        depth[0] -= 1
        if not math.isfinite(n) or abs(n) > 1e18:
            raise ToolError("NUMERIC_RANGE")
        return n

    def product():
        n = atom()
        while peek() in ("*", "/"):
            op, v = take(), atom()
            if op == "/" and v == 0:
                raise ToolError("DIVISION_BY_ZERO")
            n = n * v if op == "*" else n / v
        return n

    def total():
        n = product()
        while peek() in ("+", "-"):
            op, v = take(), product()
            n = n + v if op == "+" else n - v
        return n

    n = total()
    if pos[0] != len(t) or not math.isfinite(n) or abs(n) > 1e18:
        raise ToolError("INVALID_EXPRESSION")
    return {"value": re.sub(r"\.?0+$", "", f"{n + 0.0:.10f}") or "0"}


def view_page(pages, page, bbox=None):
    """PNG of a page or of a part of it, at most EDGE pixels long, with the numbers that describe it."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None           # the pages are our own files, some of them large
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= len(pages):
        raise ToolError("INVALID_ARGUMENT")
    b = [0, 0, 1, 1] if bbox is None else bbox
    if (not isinstance(b, list) or len(b) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in b)
            or not (0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1)):
        raise ToolError("INVALID_ARGUMENT")
    with Image.open(pages[page - 1]) as src:
        im = src.convert("RGB")
    w, h = im.size
    if bbox is not None:
        x0, y0 = min(int(b[0] * w), w - 1), min(int(b[1] * h), h - 1)
        im = im.crop((x0, y0, max(math.ceil(b[2] * w), x0 + 1), max(math.ceil(b[3] * h), y0 + 1)))
    im.thumbnail((EDGE, EDGE), Image.Resampling.LANCZOS)
    im = im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), {"page": page, "bbox": b, "page_size": [w, h], "output_size": list(im.size)}


# ---------------------------------------------------------------- the endpoint

class Endpoint:
    def __init__(self, base_url, key, model, extra=None, image_detail=None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key, self.model, self.extra, self.image_detail = key, model, dict(extra or {}), image_detail
        self.token_field = "max_completion_tokens"      # older servers know max_tokens only: switched on their first refusal
        self.lock = threading.Lock()

    def clean(self, text):
        return str(text).replace(self.key, "<key>") if self.key else str(text)

    def image(self, png):
        part = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}
        if self.image_detail:
            part["image_url"]["detail"] = self.image_detail
        return part

    def chat(self, messages, timeout, cap=None, tools=None):
        """One chat completion. Repeated after HTTP 429 or 5xx, at most ATTEMPTS times; any other failure is raised."""
        attempt, switched = 0, False
        while True:
            body, field = {**self.extra, "model": self.model, "messages": messages}, self.token_field
            if cap is not None:
                body[field] = cap
            if tools:
                body["tools"] = tools
            http = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
            try:
                with urllib.request.urlopen(http, timeout=timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                detail = e.read(2000).decode("utf-8", "replace")
                if e.code == 400 and cap is not None and field in detail and not switched:
                    with self.lock:         # the server names the field it refused: use the other one from now on
                        if self.token_field == field:
                            self.token_field = "max_tokens" if field == "max_completion_tokens" else "max_completion_tokens"
                    switched = True
                    continue
                if (e.code == 429 or e.code >= 500) and attempt < ATTEMPTS - 1:
                    time.sleep(BACKOFF[attempt])
                    attempt += 1
                    continue
                raise RuntimeError(self.clean(f"HTTP {e.code}: {' '.join(detail.split())[:300]}")) from None


def text_of(message):
    c = message.get("content")
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") in ("text", "output_text"))
    return c or ""


def one_turn(ep, req, images):
    content = [{"type": "text", "text": req["prompt"]}] + [ep.image(Path(p).read_bytes()) for p in images]
    res = ep.chat([{"role": "system", "content": req["system"]}, {"role": "user", "content": content}], ONE_TURN_TIMEOUT)
    return text_of(res["choices"][0]["message"]), {"usage": res.get("usage")}


# ---------------------------------------------------------------- full tier: the tool loop

def has_final(req, text):
    """Does the text already end in an answer of the asked form? Only used to tell a cut-off turn from a finished one."""
    if req["kind"] == "parent":
        return any(re.match(r"^[*#> ]*答案\s*[:：]", line.strip()) for line in text.splitlines())
    return bool(re.search(r"\{\s*\"answer\"\s*:", text))


def tool_loop(ep, req, pages, budget, trace):
    b = budget
    steps = tools = views = pixels = generated = 0
    finalizing, trigger, usage_known, seen, cache = False, None, True, set(), {}
    started = time.time()
    messages = [{"role": "system", "content": req["system"]}, {"role": "user", "content": req["prompt"]}]

    def cap():
        if finalizing:
            return min(FINAL_TOKENS, b["total_tokens"] - generated)
        return min(b["step_tokens"], b["total_tokens"] - FINAL_TOKENS - generated)

    def close(reason):
        nonlocal finalizing, trigger
        finalizing, trigger = True, reason
        messages.append({"role": "user", "content": FINALIZE_CHOICE if req["kind"] == "parent" else FINALIZE_PROBE})

    text, stop = "", "final"
    while True:
        if time.time() - started > EPISODE_TIMEOUT:
            raise TimeoutError(f"no answer within {EPISODE_TIMEOUT} s")
        if cap() < 16 or steps >= b["steps"]:
            stop = "budget"
            break
        this_cap = cap()
        res = ep.chat(messages, STEP_TIMEOUT, this_cap, None if finalizing else TOOLS)
        msg, finish = res["choices"][0]["message"], res["choices"][0].get("finish_reason")
        steps += 1
        used = (res.get("usage") or {}).get("completion_tokens")
        if isinstance(used, int):
            generated += used
        else:
            usage_known = False
        text, calls = text_of(msg), ([] if finalizing else msg.get("tool_calls") or [])
        step = {"step": steps, "cap": this_cap, "generated": used, "finish_reason": finish, "closing_turn": finalizing, "text": text, "calls": []}
        trace.append(step)
        if finalizing:
            stop = "budget"
            break
        can_close = steps < b["steps"] and b["total_tokens"] - generated >= 16
        if not calls:
            if not has_final(req, text) and (finish == "length" or not text.strip()) and can_close:
                if text.strip():
                    messages.append({"role": "assistant", "content": text})
                close("cut_off" if finish == "length" else "empty_output")
                continue
            break
        # the reply is sent back as the server gave it, so that fields some servers ask for (reasoning_content) stay with it
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls", "reasoning_content", "reasoning", "reasoning_details")})
        shown = []
        for c in calls:
            fn, cid = c.get("function") or {}, c.get("id")
            name, err, result, png = fn.get("name"), None, None, None
            if tools >= b["tool_calls"]:
                err = "BUDGET_EXHAUSTED"
            else:
                tools += 1
                if name == "view_page":
                    if views >= b["views"]:
                        err = "VIEW_BUDGET_EXHAUSTED"
                    else:
                        views += 1
            if cid in seen:
                err = "DUPLICATE_TOOL_CALL_ID"
            seen.add(cid)
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                args, err = None, err or "INVALID_ARGUMENT"
            if err is None:
                try:
                    if name == "calculate":
                        if set(args) != {"expression"}:
                            raise ToolError("INVALID_ARGUMENT")
                        result = calculate(args["expression"])
                    elif name == "view_page":
                        if not set(args) <= {"page", "bbox"} or "page" not in args:
                            raise ToolError("INVALID_ARGUMENT")
                        key = json.dumps(args, sort_keys=True)
                        if key not in cache:
                            cache[key] = view_page(pages, args["page"], args.get("bbox"))
                        png, result = cache[key]
                        w, h = result["output_size"]
                        if pixels + w * h > MAX_PIXELS:
                            png = None
                            raise ToolError("IMAGE_BUDGET_EXHAUSTED")
                        pixels += w * h
                    else:
                        raise ToolError("UNKNOWN_TOOL")
                except ToolError as e:
                    err = str(e)
            payload = {"error": "Error: " + err, "tool_call_id": cid} if err else result
            messages.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))})
            step["calls"].append({"name": name, "arguments": args, **({"error": err} if err else {"result": result}),
                                  **({"image_sha256": hashlib.sha256(png).hexdigest()} if png else {})})
            if png:
                shown.append(ep.image(png))
        if shown:       # a tool message carries text only: the images of a turn follow in one user message
            messages.append({"role": "user", "content": [{"type": "text", "text": "Attached image(s) from tool result:"}] + shown})
        if steps >= b["steps"] - 1 or tools >= b["tool_calls"] or cap() < 16:
            if not can_close:
                stop = "budget"
                break
            close("observation_budget")
    return text, {"steps": steps, "tool_calls": tools, "page_views": views, "delivered_pixels": pixels,
                  "generated_tokens": generated if usage_known else None, "stop": stop, "closing_turn": trigger,
                  "seconds": round(time.time() - started, 1)}


# ---------------------------------------------------------------- a tier

def run_tier(ep, reqs, folder, tier, concurrency=4, limit=None, budget=None, say=print):
    """Answer the open requests of a tier and append them to folder/responses.jsonl. Returns (answered, failed, already there).

    reqs: the requests of ccb export, each with "_files", the image files in order. A request whose call fails is not
    written, so the next start takes it up again."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out, done = folder / "responses.jsonl", set()
    if out.is_file():
        with open(out, encoding="utf-8") as f:
            done = {json.loads(line)["id"] for line in f if line.strip()}
    todo = [r for r in reqs if r["id"] not in done][:limit]
    budget = {**BUDGET, **(budget or {})}
    if tier == "full":
        (folder / "traces").mkdir(exist_ok=True)

    def work(req):
        try:
            if tier == "full":
                trace = []
                text, stats = tool_loop(ep, req, req["_files"], budget, trace)
                (folder / "traces" / (req["id"] + ".json")).write_text(
                    json.dumps({"id": req["id"], **stats, "trace": trace}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            else:
                text, stats = one_turn(ep, req, req["_files"])
            return req["id"], text, None
        except Exception as e:          # endpoint errors, time-outs, malformed replies: left open for the next start
            return req["id"], None, ep.clean(f"{type(e).__name__}: {e}")

    ok = failed = 0
    lock = threading.Lock()
    with open(out, "a", encoding="utf-8") as f, open(folder / "errors.log", "a", encoding="utf-8") as errors, \
            concurrent.futures.ThreadPoolExecutor(max(1, concurrency)) as pool:
        for n, fut in enumerate(concurrent.futures.as_completed([pool.submit(work, r) for r in todo]), 1):
            rid, text, err = fut.result()
            with lock:
                if err:
                    failed += 1
                    errors.write(json.dumps({"id": rid, "error": err}, ensure_ascii=False) + "\n")
                    errors.flush()
                    say(f"  {rid}: {err[:200]}")
                else:
                    ok += 1
                    f.write(json.dumps({"id": rid, "response": text}, ensure_ascii=False) + "\n")
                    f.flush()
            if n % max(1, len(todo) // 10) == 0 or n == len(todo):
                say(f"  {tier}: {n}/{len(todo)} requests done")
    if not (folder / "errors.log").stat().st_size:
        (folder / "errors.log").unlink()
    return ok, failed, len(done)
