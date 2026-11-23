"""Answer exported understanding requests with any OpenAI-compatible chat endpoint.

    ccb export understanding --tier single --out requests/
    export OPENAI_API_KEY=...                    # the key is read from the environment and written nowhere
    python examples/openai_compatible.py requests/ --model <model> --out responses.jsonl
    ccb grade understanding --tier single responses.jsonl

Each request becomes one chat completion: the system prompt, then one user message with the prompt text followed by
the images in order. The final text of the model goes to the responses file. A request that is already in the
responses file is skipped, so an interrupted run can be started again. Only the standard library is used.

This covers the two one-turn tiers, single and few. In the full tier the model looks through a whole drawing set with
a page-viewing tool (docs/PROTOCOL.md); sending every page in one message is a different setting, so the script
refuses full-tier requests unless --all-pages-at-once is given.
"""
import argparse
import base64
import concurrent.futures
import json
import os
import sys
import threading
import urllib.error
import urllib.request


def call(base_url, key, model, req, folder, extra, timeout):
    content = [{"type": "text", "text": req["prompt"]}]
    for rel in req["images"]:
        with open(os.path.join(folder, rel), "rb") as f:
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(f.read()).decode()}})
    body = {"model": model, "messages": [{"role": "system", "content": req["system"]}, {"role": "user", "content": content}], **extra}
    http = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(http, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"] or ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("requests", help="folder written by ccb export understanding")
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True, help="responses file (appended to)")
    ap.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1800, help="seconds per request")
    ap.add_argument("--extra", default="{}", help='JSON merged into the request body, e.g. \'{"reasoning_effort":"high"}\'')
    ap.add_argument("--limit", type=int, help="answer only the first N open requests")
    ap.add_argument("--all-pages-at-once", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("set OPENAI_API_KEY")
    with open(os.path.join(a.requests, "requests.jsonl"), encoding="utf-8") as f:
        reqs = [json.loads(line) for line in f if line.strip()]
    if any(r["tier"] == "full" for r in reqs) and not a.all_pages_at_once:
        sys.exit("full-tier requests: see the note at the top of this file")
    done = set()
    if os.path.isfile(a.out):
        with open(a.out, encoding="utf-8") as f:
            done = {json.loads(line)["id"] for line in f if line.strip()}
    todo = [r for r in reqs if r["id"] not in done][:a.limit]
    extra, lock, failed = json.loads(a.extra), threading.Lock(), 0

    def work(req):
        try:
            return req["id"], call(a.base_url, key, a.model, req, a.requests, extra, a.timeout), None
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError, OSError) as e:      # left open for the next start
            return req["id"], None, f"{type(e).__name__}: {e}"

    with open(a.out, "a", encoding="utf-8") as out, concurrent.futures.ThreadPoolExecutor(a.concurrency) as pool:
        for rid, text, err in pool.map(work, todo):
            if err:
                failed += 1
                print(f"{rid}: {err}", file=sys.stderr)
                continue
            with lock:
                out.write(json.dumps({"id": rid, "response": text}, ensure_ascii=False) + "\n")
                out.flush()
    print(f"{len(todo) - failed} answered, {failed} failed, {len(done)} already there -> {a.out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
