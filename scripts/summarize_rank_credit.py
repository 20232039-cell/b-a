"""Combine the private judgment ledgers without calling a model."""
import gzip
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PLAN = "layer-rank-credit-20261122"
REMOTE = "https://github.com/20232039-cell/layer-brand-agent.git"
BRANCH = "results/" + PLAN
CAP = {"similar": 120, "outfit": 70, "review": 30}


def git(root, *args):
    return subprocess.check_output(["git", "-c", "credential.helper=!gh auth git-credential", *args],
                                   cwd=root, text=True)


def load(root, phase):
    git(root, "init", "-q")
    git(root, "remote", "add", "origin", REMOTE)
    branch = f"{BRANCH}-{phase}"
    if not git(root, "ls-remote", "--heads", "origin", branch):
        return {}, [], None
    git(root, "fetch", "-q", "--depth=1", "origin", branch)
    sha = git(root, "rev-parse", "FETCH_HEAD").strip()
    meter = json.loads(git(root, "show", sha + ":ledger.json"))
    def records():
        for name in sorted(git(root,"ls-tree","--name-only",sha).splitlines()):
            if name.endswith(".jsonl"):
                for line in git(root,"show",f"{sha}:{name}").splitlines():
                    yield json.loads(line)
    return meter, records(), sha


def main():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        out = base / "output"
        out.mkdir()
        summary = {"as_of_utc": datetime.now(timezone.utc).isoformat(), "budget_usd": 220,
                   "data": "comparisons.jsonl.gz", "currency": "USD",
                   "cost_basis": "Returned token usage at standard model prices with cache discounts",
                   "split": "anchor ID hashed to 10 buckets; bucket 0 held out across all axes and reviews",
                   "phases": {}}
        import hashlib
        with gzip.open(out / summary["data"], "wt", encoding="utf-8") as f:
            for phase in CAP:
                src = base / phase
                src.mkdir()
                ledger, rows, revision = load(src, phase)
                counts, axes, errors = Counter(), Counter(), Counter()
                for row in rows:
                    if not row.get("choice"):
                        errors[row.get("error", "missing")] += 1
                        continue
                    counts[row["choice"]] += 1
                    axes[row["axis"]] += 1
                    row["phase"] = phase
                    anchor = row["ids"][0].encode()
                    bucket = int.from_bytes(hashlib.sha256(anchor).digest()[:4], "big") % 10
                    row["split"] = "holdout" if not bucket else "train"
                    f.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
                valid = sum(counts.values())
                if valid != ledger.get("valid",0):
                    raise ValueError(f"Meter count and saved judgments differ for {phase}")
                summary["phases"][phase] = {
                    "budget_usd": CAP[phase], "valid": valid, "choices": dict(counts), "axes": dict(axes),
                    "errors": dict(errors), "source_commit": revision,
                    "metered_usd": ledger.get("known_cost_nano",0)/1e9,
                    "uncertain_ceiling_usd": ledger.get("uncertain_nano",0)/1e9,
                    "open_reservations_usd": ledger.get("reserved_nano",0)/1e9,
                }
        summary["metered_usd"] = sum(p["metered_usd"] for p in summary["phases"].values())
        summary["valid"] = sum(p["valid"] for p in summary["phases"].values())
        (out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
        git(out,"init","-q")
        git(out,"remote","add","origin",REMOTE)
        if git(out,"ls-remote","--heads","origin",BRANCH):
            git(out,"fetch","-q","--depth=1","origin",BRANCH)
            git(out,"reset","--mixed","FETCH_HEAD")
        git(out,"config","user.name","layer-runner-bot")
        git(out,"config","user.email","runner@layer.invalid")
        git(out,"add","summary.json",summary["data"])
        git(out,"commit","-qm","Combine one-day recommendation judgments and metered usage")
        git(out,"push","-q","origin",f"HEAD:refs/heads/{BRANCH}")
        print(f"Published {summary['valid']:,} judgments; metered USD {summary['metered_usd']:.4f}")


if __name__ == "__main__":
    main()
