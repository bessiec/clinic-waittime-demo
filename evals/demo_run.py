"""Record or replay the Milestone 4 demo run: one day played by LLM persona agents.

  python3 -m evals.demo_run            record: live agent calls through the Claude Code CLI
                                       on the owner's subscription (no API key, $0 billed)
  python3 -m evals.demo_run --replay   replay the recording (free, exact) and grade it

The recording lives in demo/ and is kept in git, so anyone can replay and grade the
same day without calling a model. Scenario and seed come from config [actors]."""
import argparse
import sys
import time
from pathlib import Path

from clinic import config, director
from clinic.config import ROOT
from . import fidelity
from .common import open_run
from .runner import grade

DEMO = ROOT / "demo"


def paths(cfg):
    a = cfg["actors"]
    stem = f"{a['demo_scenario']}_s{a['demo_seed']}_llm"
    return DEMO / f"{stem}.sqlite", ROOT / "runs" / "demo" / f"{stem}_replay.sqlite"


def report(db, sc, seed, cfg):
    print(f"\nScorecard ({sc}, seed {seed}, LLM persona agents; one run is an example, not a rate)")
    for r in grade(str(db), sc, cfg, seed):
        mark = {True: "PASS", False: "FAIL", None: "info"}[r.passed]
        print(f"  {mark:4}  {r.id:4} {r.score}" + (f"  ({r.detail})" if r.detail else ""))
        for e in r.evidence[:4]:
            print(f"          - {e}")
    st = open_run(str(db))
    print("\nPersona fidelity")
    for row in fidelity.personas(st, cfg):
        print(f"  {row['status']:8} {row['persona']:7} {row['metric']}  [target: {row['target']}]")
    h = st.one("SELECT COUNT(*) n, SUM(ok) ok, SUM(modeled_usd) usd, SUM(ms) ms FROM llm_calls WHERE source != 'skipped'")
    print(f"\nAgent calls: {h['n']} ({h['ok']} valid). Billed: $0 (subscription). "
          f"At API list prices: ${h['usd'] or 0:.2f} (modeled). Model time: {(h['ms'] or 0) / 60000:.0f} min.")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--config")
    args = ap.parse_args(argv)
    cfg = config.load(args.config)
    sc, seed = cfg["actors"]["demo_scenario"], cfg["actors"]["demo_seed"]
    rec, rep = paths(cfg)
    if args.replay:
        if not rec.exists():
            sys.exit(f"no recording at {rec}; run python3 -m evals.demo_run first")
        rep.parent.mkdir(parents=True, exist_ok=True)
        director.run(sc, seed, cfg, db_path=str(rep), actors="replay", replay_from=str(rec))
        report(rep, sc, seed, cfg)
        return 0
    DEMO.mkdir(exist_ok=True)
    tmp = rec.with_suffix(".recording.sqlite")
    t0 = time.time()
    print(f"Recording {sc} seed {seed} with LLM persona agents (Claude Code CLI, model {cfg['actors']['model']}). "
          "This takes a while; each agent call is a few seconds.", flush=True)
    director.run(sc, seed, cfg, db_path=str(tmp), actors="llm")
    tmp.replace(rec)
    print(f"Saved {rec.relative_to(ROOT)} in {(time.time() - t0) / 60:.0f} min.")
    report(rec, sc, seed, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
