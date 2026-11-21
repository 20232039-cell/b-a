"""One-time supervisor. Read aggregate meters, resume stalled workers and report totals."""
import json
import os
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from pathlib import Path

PLAN = "layer-rank-credit-20261122"
PHASES = {"similar": 120, "outfit": 70, "review": 30}
# Each worker reserves one maximum-size request before sending.
MIN_REQUEST = {"similar": .014592, "outfit": .014592, "review": .04864}
CUTOFF = datetime(2026,11,22,3,50,tzinfo=timezone.utc).timestamp()
PRIVATE = "20232039-cell/layer-brand-agent"
REPO = "20232039-cell/b-a"
REF = "codex/layer-rank-credit-20261122"


def api(path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": "Bearer " + os.environ[token], "User-Agent": "layer-credit-watch",
               "Accept": "application/vnd.github.raw+json" if token == "DATA_TOKEN" else "application/vnd.github+json"}
    req = Request("https://api.github.com/repos/" + path, data=data, headers=headers)
    for attempt in range(5):
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            if e.code == 404 and token == "DATA_TOKEN":
                return {}
            if e.code < 500 or attempt == 4:
                raise
        except OSError:
            if attempt == 4:
                raise
        time.sleep(5 * (attempt + 1))


def dispatch(phase):
    api(REPO + "/actions/workflows/coord-score.yml/dispatches", "JOB_TOKEN",
        {"ref": REF, "inputs": {"phase": phase}})
    print("Queued", phase, flush=True)


def main():
    start = time.time()
    observed = {p: start for p in PHASES}
    previous = {}
    last_queue = {}
    while True:
        now = time.time()
        done = True
        totals = [0., 0., 0]
        report = ["# LAYER recommendation comparisons", "",
                  "| Dataset | Valid comparisons | Metered USD | Unresolved ceiling USD | Maximum USD |",
                  "|---|---:|---:|---:|---:|"]
        for phase, cap in PHASES.items():
            path = PRIVATE + "/contents/ledger.json?ref=" + quote("results/" + PLAN + "-" + phase, safe="")
            row = api(path, "DATA_TOKEN")
            known = row.get("known_cost_nano", 0)/1e9
            uncertain = row.get("uncertain_nano", 0)/1e9
            reserved = row.get("reserved_nano", 0)/1e9
            valid = row.get("valid", 0)
            totals[0] += known
            totals[1] += uncertain
            totals[2] += valid
            report.append(f"| {phase} | {valid:,} | {known:.4f} | {uncertain:.4f} | {cap} |")
            probe = (known, uncertain, valid, row.get("next"))
            if previous.get(phase) != probe:
                previous[phase], observed[phase] = probe, now
            if reserved or cap-known-uncertain >= MIN_REQUEST[phase]:
                done = False
                if (now < CUTOFF-360 and now-observed[phase] > 20*60
                        and now-last_queue.get(phase,0) > 40*60):
                    dispatch(phase)
                    last_queue[phase] = now
        msg = f"{totals[2]:,} valid comparisons, USD {totals[0]:.4f} metered; unresolved ceiling USD {totals[1]:.4f}"
        print(time.strftime("%H:%M UTC",time.gmtime()), msg, flush=True)
        report += ["",msg]
        if done or now >= CUTOFF+600 or now-start > 5.4*3600:
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as f:
                f.write("\n".join(report)+"\n")
            if not done and now < CUTOFF:
                dispatch("monitor")
            break
        time.sleep(180)


if __name__ == "__main__":
    main()
