"""Eval runner.

  python3 -m evals              full suite: every scenario x every seed in config,
                                pooled scorecard, results file, compare to last run
  python3 -m evals --quick      seed 42, normal_day + kim_goes_quiet, per-run table
  python3 -m evals --compare X  compare against suite X instead of the previous one

Exit code is nonzero when any hard gate fails (tuning or hold-out) or any
tuning scored eval fails. Hold-out scored evals are reported, never blocking."""
import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

from clinic import config, director
from clinic.config import ROOT, DEFAULT_PATH
from clinic.scenarios import SCENARIOS
from . import fidelity, finance, gates, scored
from .common import Result, open_run

GATES = ["G1", "G2", "G3", "G4", "G5"]
PLAN = {
    "normal_day":           GATES + ["E1", "E2", "E4", "E9", "E10", "F1", "E11"],
    "kim_goes_quiet":       ["G1", "G2!", "G3", "G4", "G5", "E4", "E9", "E10", "F1", "E11"],
    "walkin_surge":         GATES + ["E1", "E2i", "E3", "E4", "E10", "F1", "E11"],
    "luis_day":             GATES + ["E4", "E5", "E10", "F1", "E11"],
    "pat_day":              GATES + ["E4", "E5", "E10", "F1", "E11"],
    "understaffed_opening": GATES + ["E1", "E3", "E4", "E10", "F1", "E11"],
    "luis_surge":           GATES + ["E1", "E3", "E4", "E5", "E10", "F1", "E11"],
}
EXACT = ("G1", "G2", "G3", "G4", "G5", "E9", "E10", "F1")   # pass/fail per seed, pooled as "n/N seeds pass"
GRADERS = {
    "G1": gates.g1_urgent_reply,
    "G2": gates.g2_no_stale_numbers,
    "G2!": lambda st, cfg: gates.g2_no_stale_numbers(st, cfg, must_exercise=True),
    "G3": gates.g3_no_unapproved_queue_change,
    "G4": gates.g4_no_names_or_clinical_terms,
    "G5": gates.g5_urgent_line_and_freshness,
    "E1": scored.e1_coverage,
    "E2": scored.e2_width,
    "E2i": lambda st, cfg: scored.e2_width(st, cfg, report_only=True),
    "E3": scored.e3_false_reassurance,
    "E4": scored.e4_declines,
    "E5": scored.e5_beats_staff,
    "E9": scored.e9_chen_clock,
    "E10": scored.e10_finance_reconciles,
    "F1": fidelity.f1_persona_fidelity,
    "E11": scored.e11_survey_tracks,
}
SUITES = ROOT / "runs" / "suites"


def grade(db_path, scenario, cfg, seed=None, baseline_path=None):
    st = open_run(db_path)
    base = open_run(baseline_path) if baseline_path else None
    out = []
    for k in PLAN[scenario]:
        r = GRADERS[k](st, cfg, baseline=base) if k == "E10" else GRADERS[k](st, cfg)
        r.seed = seed
        out.append(r)
    st.conn.close()
    if base:
        base.conn.close()
    return out


def _job(args):
    """One scenario x seed: the run, its no-product baseline (for the finance
    pane's walkaway comparison), grades and finance counts."""
    scenario, seed, cfg_path, out_dir = args
    cfg = config.load(cfg_path)
    path = director.run(scenario, seed, cfg, db_path=str(Path(out_dir) / f"{scenario}_s{seed}.sqlite"))
    bpath = director.run(scenario, seed, cfg, db_path=str(Path(out_dir) / f"{scenario}_s{seed}_baseline.sqlite"), product=False)
    results = [asdict(r) for r in grade(path, scenario, cfg, seed, bpath)]
    st, bst = open_run(path), open_run(bpath)
    fin = finance.counts(st, cfg, bst)
    st.conn.close()
    bst.conn.close()
    return scenario, seed, results, fin


# --- pooling -------------------------------------------------------------
def _ratio(rs):
    num = sum(r.pool.get("num", 0) for r in rs)
    den = sum(r.pool.get("den", 0) for r in rs)
    return num, den, (num / den if den else 0.0)


def pool(check_id, rs, cfg):
    """Combine one check's per-seed results into one row."""
    ec = cfg["evals"]
    first = rs[0]
    failing = sorted(r.seed for r in rs if r.passed is False)
    vals = [(r.value, r.seed) for r in rs if r.value is not None]
    spread = None
    if vals:
        v = sorted(x for x, _ in vals)
        spread = {"min": v[0], "median": statistics.median(v), "max": v[-1]}
    row = {"id": check_id, "scenario": first.scenario, "threshold": first.threshold,
           "failing_seeds": failing, "spread": spread, "worst_seed": None, "detail": "",
           "evidence": [f"seed {r.seed}: {e}" for r in rs for e in r.evidence][:12],
           "refs": [(ref or {}) | {"seed": r.seed} for r in rs for ref in r.refs][:12]}
    if check_id in EXACT:
        n_ok = sum(1 for r in rs if r.passed)
        row |= {"score": f"{n_ok}/{len(rs)} seeds pass", "value": n_ok / len(rs), "passed": n_ok == len(rs)}
        ne = [r.seed for r in rs if r.score == "not exercised"]
        if ne:
            row["detail"] = f"not exercised on seeds {ne}"
        row["worst_seed"] = failing[0] if failing else None
        return row
    if check_id in ("E1", "E3", "E5"):
        num, den, rate = _ratio(rs)
        thr = {"E1": ec["E1_coverage"], "E3": ec["E3_max_rate"], "E5": ec["E5_beat_rate"]}[check_id]
        passed = rate <= thr if check_id == "E3" else rate >= thr
        row |= {"score": f"{rate:.0%} pooled ({num}/{den})", "value": rate, "passed": passed}
        if check_id == "E1":
            w = [sum(r.pool["walkin"][i] for r in rs) for i in (0, 1)]
            s = [sum(r.pool["scheduled"][i] for r in rs) for i in (0, 1)]
            row["detail"] = f"walk-ins {w[0]}/{w[1]} ({w[0]/max(1,w[1]):.0%}), scheduled {s[0]}/{s[1]} ({s[0]/max(1,s[1]):.0%})"
    elif check_id == "E2":
        allw = [w for r in rs for w in r.pool["values"]]
        sh = [w for r in rs for w in r.pool["short"]]
        lg = [w for r in rs for w in r.pool["long"]]
        med = statistics.median(allw) if allw else 0
        passed = None if first.passed is None else (bool(allw) and med <= ec["E2_median_width_minutes"])
        cut = ec["E2_short_wait_minutes"]
        row |= {"score": f"median {med:g} min pooled ({len(allw)} ranges)", "value": med, "passed": passed,
                "detail": (f"best guess ≤ {cut} min: median {statistics.median(sh) if sh else 0:g} ({len(sh)}); "
                           f"> {cut} min: median {statistics.median(lg) if lg else 0:g} ({len(lg)}); widest {max(allw) if allw else 0}")}
    elif check_id == "E4":
        a = [sum(r.pool["should"][i] for r in rs) for i in (0, 1)]
        b = [sum(r.pool["shouldnt"][i] for r in rs) for i in (0, 1)]
        ra = a[0] / a[1] if a[1] else None
        rb = b[0] / b[1] if b[1] else None
        thr = ec["E4_decline_accuracy"]
        passed = (ra is None or ra >= thr) and (rb is None or rb >= thr)
        fa = "n/a" if ra is None else f"{ra:.0%}"
        fb = "n/a" if rb is None else f"{rb:.0%}"
        row |= {"score": f"should {fa} ({a[0]}/{a[1]}), shouldn't {fb} ({b[0]}/{b[1]})",
                "value": min(x for x in (ra, rb) if x is not None) if (ra is not None or rb is not None) else None,
                "passed": passed,
                "detail": "minutes open: declined when it should / showed a range when it should"}
    elif check_id == "E11":   # report only: pool every answer, then one correlation
        pairs = [p for r in rs for p in r.pool["pairs"]]
        c = scored._corr(pairs)
        ok = c is not None and len(pairs) >= ec["survey_min_group"]
        row |= {"score": f"r = {-c:.2f} pooled ({len(pairs)} answers)" if ok else f"too few answers ({len(pairs)})",
                "value": -c if ok else None, "passed": None,
                "detail": "minutes past the top of the range vs the \"accurate\" answer; simulated answers come from a formula"}
    if vals:
        worst = (max if check_id in ("E2", "E3") else min)(vals)
        row["worst_seed"] = worst[1]
    return row


# --- fingerprint, storage, comparison -------------------------------------
def fingerprint(cfg_path):
    cfg_bytes = Path(cfg_path or DEFAULT_PATH).read_bytes()
    h = hashlib.sha256()
    for f in sorted(list((ROOT / "clinic").rglob("*.py")) + list((ROOT / "evals").rglob("*.py"))):
        h.update(str(f.relative_to(ROOT)).encode())
        h.update(f.read_bytes())
    return {"config": hashlib.sha256(cfg_bytes).hexdigest()[:12], "code": h.hexdigest()[:12]}


def previous_suite(exclude):
    if not SUITES.exists():
        return None
    dirs = sorted(d for d in SUITES.iterdir() if d.is_dir() and (d / "results.json").exists() and d.name != exclude)
    return dirs[-1] if dirs else None


def prune_old(current):
    """Keep every SQLite file for the newest suite. For older suites keep only
    seeds that failed a hard gate, plus the worst seed of each failing eval."""
    for d in SUITES.iterdir():
        if not d.is_dir() or d.name == current or not (d / "results.json").exists():
            continue
        res = json.loads((d / "results.json").read_text())
        keep = set()
        for c in res["checks"]:
            if c["id"].startswith("G"):
                keep |= {f"{c['scenario']}_s{s}.sqlite" for s in c["failing_seeds"]}
            elif c["passed"] is False and c["worst_seed"] is not None:
                keep.add(f"{c['scenario']}_s{c['worst_seed']}.sqlite")
        keep |= {k.replace(".sqlite", "_baseline.sqlite") for k in keep}
        for f in d.glob("*.sqlite"):
            if f.name not in keep:
                f.unlink()


def compare(new, old):
    """Rows of (key, old score, new score, flip) for every check in both."""
    oldmap = {(c["id"], c["scenario"]): c for c in old["checks"]}
    rows = []
    for c in new["checks"]:
        o = oldmap.get((c["id"], c["scenario"]))
        if o is None:
            rows.append((c, None, "new check"))
            continue
        flip = ""
        if o["passed"] is True and c["passed"] is False:
            flip = "PASS → FAIL"
        elif o["passed"] is False and c["passed"] is True:
            flip = "FAIL → PASS"
        rows.append((c, o, flip))
    return rows


# --- printing ----------------------------------------------------------------
def mark(p):
    return "info" if p is None else ("PASS" if p else "FAIL")


def print_rows(rows, title):
    print(f"\n{title}")
    w = (4, 21, 46, 26)
    print(f"{'ID':<{w[0]}} {'scenario':<{w[1]}} {'score':<{w[2]}} {'threshold':<{w[3]}} result")
    print("-" * (sum(w) + 12))
    for c in rows:
        print(f"{c['id']:<{w[0]}} {c['scenario']:<{w[1]}} {c['score'][:w[2]]:<{w[2]}} {c['threshold'][:w[3]]:<{w[3]}} {mark(c['passed'])}")
        pad = " " * (w[0] + w[1] + 2)
        if c["spread"] and c["id"] not in EXACT:
            sp = c["spread"]
            fmtv = (lambda x: f"{x:.0%}") if c["id"] in ("E1", "E3", "E4", "E5") else (lambda x: f"{x:g}")
            print(f"{pad}per seed: min {fmtv(sp['min'])}, median {fmtv(sp['median'])}, max {fmtv(sp['max'])}; worst seed {c['worst_seed']}")
        if c["detail"]:
            print(f"{pad}{c['detail']}")
        if c["passed"] is False:
            if c["failing_seeds"]:
                print(f"{pad}failing seeds: {c['failing_seeds']}")
            for e in c["evidence"][:4]:
                print(f"{pad}  ! {e}")


def suite(args, cfg):
    seeds = cfg["run"]["seeds"] if args.seeds is None else list(range(1, args.seeds + 1))
    scenarios = args.scenario or list(PLAN)
    suite_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = SUITES / suite_id
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    jobs = [(sc, s, args.config, str(out)) for sc in scenarios for s in seeds]
    per, fin = {}, {}
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as ex:
        for sc, s, rs, f in ex.map(_job, jobs):
            for r in rs:
                per.setdefault((r["id"], sc), []).append(Result(**r))
            fin[sc] = finance.add(fin.get(sc, {}), f)
    checks = []
    for sc in scenarios:
        for k in PLAN[sc]:
            cid = k.rstrip("!").replace("E2i", "E2")
            row = pool(cid, per[(cid, sc)], cfg)
            row["group"] = "hold-out" if SCENARIOS[sc]["holdout"] else "tuning"
            checks.append(row)
    result = {"suite_id": suite_id, "created": dt.datetime.now().isoformat(timespec="seconds"),
              "seeds": seeds, "scenarios": scenarios, "fingerprint": fingerprint(args.config),
              "elapsed_s": round(time.time() - t0, 1), "checks": checks,
              "finance": {sc: {"counts": fin[sc], "derived": finance.derive(fin[sc], cfg, runs=len(seeds))} for sc in scenarios}}
    (out / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"Clinic wait-time demo — simulation, synthetic data.")
    print(f"Suite {suite_id}: {len(scenarios)} scenarios x {len(seeds)} seeds = {len(jobs)} runs in {result['elapsed_s']}s")
    print(f"Results: {(out / 'results.json').relative_to(ROOT)}")
    gates_rows = [c for c in checks if c["id"].startswith("G")]
    tuning = [c for c in checks if not c["id"].startswith("G") and c["group"] == "tuning"]
    hold = [c for c in checks if not c["id"].startswith("G") and c["group"] == "hold-out"]
    gate_fail = [c for c in gates_rows if c["passed"] is False]
    if gate_fail:
        print_rows(gates_rows, "HARD GATES (one miss on any seed fails the run)")
    else:
        n = len(gates_rows)
        print(f"\nHARD GATES: all {n} gate checks pass on every seed of every scenario, hold-outs included.")
        ne = [c for c in gates_rows if c["detail"]]
        for c in ne:
            print(f"  {c['id']} {c['scenario']}: {c['detail']}")
    print_rows(tuning, "SCORED EVALS — tuning scenarios (pooled across seeds)")
    print_rows(hold, "SCORED EVALS — HOLD-OUT scenarios (never tuned against; reported, not blocking)")

    prev = SUITES / args.compare if args.compare else previous_suite(suite_id)
    print("\nREGRESSION")
    new_failures = []
    if prev and (prev / "results.json").exists():
        old = json.loads((prev / "results.json").read_text())
        fp_o, fp_n = old["fingerprint"], result["fingerprint"]
        changed = [k for k in ("config", "code") if fp_o[k] != fp_n[k]]
        print(f"vs {prev.name}: {'config and code' if len(changed) == 2 else (changed[0] if changed else 'nothing')} changed"
              + ("" if old["seeds"] == seeds else " (different seed list)"))
        rows = compare(result, old)
        flips = [r for r in rows if r[2] in ("PASS → FAIL", "FAIL → PASS")]
        for c, o, f in sorted(flips, key=lambda r: r[2] != "PASS → FAIL"):
            print(f"  {f:<12} {c['id']} {c['scenario']}: {o['score']}  →  {c['score']}")
        moved = [(c, o) for c, o, f in rows if o and not f and c["value"] is not None and o["value"] is not None and abs(c["value"] - o["value"]) > 1e-9]
        for c, o in moved:
            print(f"  changed      {c['id']} {c['scenario']}: {o['score']}  →  {c['score']}")
        if not flips and not moved:
            print("  no score changed")
        for c, o, f in rows:
            if c["passed"] is False and (o is None or o["passed"] is not False):
                new_failures.append(c)
    else:
        print("no previous suite to compare against")
        new_failures = [c for c in checks if c["passed"] is False]
    prune_old(suite_id)

    blocking = gate_fail + [c for c in tuning if c["passed"] is False]
    print(f"\ngates: {'FAIL' if gate_fail else 'PASS'}   tuning evals: "
          f"{'FAIL' if any(c['passed'] is False for c in tuning) else 'PASS'} "
          f"({sum(c['passed'] is False for c in tuning)} failing)   hold-out evals: "
          f"{sum(c['passed'] is False for c in hold)} failing (not blocking)")
    if new_failures:
        print("\nNEW FAILURES since last run — add each to FAILURE_LOG.md:")
        for c in new_failures:
            print(f"  {c['id']} {c['scenario']} ({c['group']}): {c['score']}")
    return 1 if blocking else 0


def quick(args, cfg):
    """Milestone 1 behavior: one seed, two scenarios, per-run table."""
    seed = args.seed if args.seed is not None else cfg["run"]["seed"]
    scenarios = args.scenario or ["normal_day", "kim_goes_quiet"]
    results, paths = [], {}
    t0 = time.time()
    for sc in scenarios:
        path = director.run(sc, seed, cfg)
        paths[sc] = path
        results += grade(path, sc, cfg, seed)
    print(f"Clinic wait-time demo — simulation, synthetic data. seed={seed}  ({time.time() - t0:.1f}s)")
    for sc in scenarios:
        print(f"  {sc}: {SCENARIOS[sc]['description']}  -> {Path(paths[sc]).relative_to(ROOT)}")
    print()
    rows = sorted(results, key=lambda r: (not r.id.startswith("G"), r.id, r.scenario))
    w = [4, 21, 44, 32, 6]
    print(f"{'ID':<{w[0]}} {'scenario':<{w[1]}} {'score':<{w[2]}} {'threshold':<{w[3]}} result")
    print("-" * (sum(w) + 10))
    for r in rows:
        print(f"{r.id:<{w[0]}} {r.scenario:<{w[1]}} {r.score[:w[2]]:<{w[2]}} {r.threshold[:w[3]]:<{w[3]}} {mark(r.passed)}")
        if r.detail:
            print(f"{'':<{w[0]}} {'':<{w[1]}} {r.detail}")
        for e in r.evidence[:8] if r.passed is False else []:
            print(f"{'':<{w[0]}} {'':<{w[1]}}   ! {e}")
    gate_fail = [r for r in results if r.id.startswith("G") and r.passed is False]
    eval_fail = [r for r in results if not r.id.startswith("G") and r.passed is False]
    print()
    print(f"gates: {'FAIL' if gate_fail else 'PASS'} ({len(gate_fail)} failing)   evals: {'FAIL' if eval_fail else 'PASS'} ({len(eval_fail)} failing)")
    (ROOT / "runs" / "last_results.json").write_text(json.dumps(
        {"seed": seed, "scenarios": scenarios, "results": [asdict(r) for r in results]}, indent=2, ensure_ascii=False))
    return 1 if (gate_fail or eval_fail) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m evals", description="Run the scenarios and grade them.")
    ap.add_argument("--quick", action="store_true", help="one seed (config run.seed), per-run table")
    ap.add_argument("--seed", type=int, default=None, help="with --quick: override the seed")
    ap.add_argument("--seeds", type=int, default=None, help="full suite: use seeds 1..N instead of the config list")
    ap.add_argument("--scenario", action="append", choices=list(SCENARIOS), help="only this scenario (repeatable)")
    ap.add_argument("--compare", default=None, help="suite id under runs/suites to compare against")
    ap.add_argument("--config", default=None, help="path to thresholds.toml")
    args = ap.parse_args(argv)
    cfg = config.load(args.config)
    return quick(args, cfg) if (args.quick or args.seed is not None) else suite(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
