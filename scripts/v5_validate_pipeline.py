"""V5 §7/§26 — real-data pipeline validation.

Drives the *real* research pipeline (genome generation -> dataset resolution ->
screening backtest -> status classification -> persistence) against a real copy
of the authoritative DATA tree and reports the authoritative breakdown:

    generated / unique / duplicate / tested / not tested / valid /
    strategy-failed / data-unavailable / data-corrupt / backtest-error /
    other-infrastructure / alive

Nothing here is simulated: the datasets, the feature engine, the backtest
engine, the evolution engine and the database are the production ones, pointed
at the tree named by EVOLUTIONARY_LAB_DATA_ROOT.

Usage:
    EVOLUTIONARY_LAB_DATA_ROOT=/opt/lmsarena-storage/v5_live \\
        python scripts/v5_validate_pipeline.py --nodes 40 [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

# workers must never write feature caches during validation
os.environ.setdefault("LAB_NO_FEATURE_PERSIST", "1")


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, default=40, help="how many fresh nodes to generate")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--json", default="")
    ap.add_argument("--batch", type=int, default=8, help="screening batch size")
    args = ap.parse_args()

    import app.paths as P
    from app import diagnostics as dg
    from app import status as st
    from app.config import get_config
    from app.db.database import get_db

    root = Path(P.DATA_ROOT)
    banner(f"V5 REAL-DATA PIPELINE VALIDATION\ndata root: {root}")

    db = get_db()

    # ---------------------------------------------------------------- baseline
    base_counts = {}
    for row in db.q("SELECT status, data_source, COUNT(*) n FROM strategies GROUP BY status, data_source"):
        base_counts[(row["status"], row["data_source"])] = row["n"]
    total_before = db.one("SELECT COUNT(*) c FROM strategies")["c"]
    # ids are not contiguous (rows were deleted over the lab's history), so the
    # "new nodes" boundary is MAX(id), never COUNT(*)
    max_id_before = db.one("SELECT COALESCE(MAX(id),0) m FROM strategies")["m"]
    print(f"nodes already persisted: {total_before} (max id {max_id_before})")

    # ------------------------------------------------------------ eligibility
    banner("1. DATASET ELIGIBILITY (the gate that produced DATASET UNAVAILABLE)")
    elig = dg.eligibility_report()
    for tf in elig["timeframes"]:
        print(f"  {tf['timeframe']:<4} candidates={tf['candidates']:<3} "
              f"valid={tf['valid']:<3} corrupt={tf['corrupt']:<3} unavailable={tf['unavailable']:<3} "
              f"resolved={(tf['resolved_dataset'] or '-')[:38]:<38} testable={tf['testable']}")
        for reason in tf["reasons"][:2]:
            print(f"        · {reason[:110]}")
    print(f"  testable timeframes: {elig['testable_timeframes']}")
    print(f"  unusable timeframes: {elig['unusable_timeframes']}")

    data_rep = dg.data_report(include_rows=20)
    print(f"  datasets registered: {data_rep['dataset_rows']} · "
          f"usability: {data_rep['by_usability']}")

    # --------------------------------------------------------------- generate
    banner(f"2. GENERATE {args.nodes} REAL NODES THROUGH THE EVOLUTION ENGINE")
    from app.orchestrator.lab import Lab

    lab = Lab()
    lab._datasets_ready = True
    lab._research_blocked = False
    lab._research_completed = False
    lab.evo.active_run_id = None
    cfg = get_config()
    cfg.evolution.screen_batch_size = args.batch

    lab.evo.set_total_node_target(total_before + args.nodes)
    t0 = time.time()
    born = lab.evo.seed_population(args.nodes, args.symbol)
    print(f"  born {born} nodes in {time.time() - t0:.1f}s "
          f"(duplicates blocked: {lab.evo.duplicates_blocked})")

    seeded = lab.db.q("SELECT id, timeframe, status FROM strategies WHERE id > ? ORDER BY id", (max_id_before,))
    by_tf = {}
    for r in seeded:
        by_tf[r["timeframe"]] = by_tf.get(r["timeframe"], 0) + 1
    print(f"  timeframe spread: {dict(sorted(by_tf.items()))}")
    print(f"  (before the timeframe scope fix, timeframes with no dataset produced "
          f"5,960 DATASET UNAVAILABLE nodes)")

    # ---------------------------------------------------------------- screen
    banner("3. SCREEN EVERY GENERATED NODE (real backtests, real features)")
    rounds = 0
    ids = [r["id"] for r in seeded]
    t0 = time.time()
    while rounds < 40:
        rounds += 1
        pending = lab.db.one(
            "SELECT COUNT(*) c FROM strategies WHERE id > ? AND status='BORN'", (max_id_before,))["c"]
        if not pending:
            break
        lab._screen_batch()
    print(f"  {rounds} screening batch(es) in {time.time() - t0:.1f}s")

    # -------------------------------------------------------------- classify
    banner("4. OUTCOME DISTRIBUTION (authoritative, read back from the database)")
    rows = lab.db.q("SELECT * FROM strategies WHERE id > ?", (max_id_before,))
    buckets = {
        "VALID": 0, "LIVE_ELIGIBLE": 0, "LIVE_TESTING": 0, "LIVE_COMPLETED": 0, "MT5_DEMO": 0,
        "TESTING": 0, "PENDING": 0, "NOT_TESTED": 0,
        "STRATEGY_FAILED": 0, "DATA_UNAVAILABLE": 0, "DATA_CORRUPT": 0, "BACKTEST_ERROR": 0,
    }
    tested_rows = not_tested = 0
    for r in rows:
        v = st.v5_status(r)
        buckets[v] = buckets.get(v, 0) + 1
        has_bt = lab.db.one("SELECT COUNT(*) c FROM backtests WHERE strategy_id=?", (r["id"],))["c"]
        if has_bt:
            tested_rows += 1
        else:
            not_tested += 1
    alive = sum(1 for r in rows if st.is_alive(r))
    infra = sum(1 for r in rows if st.is_infrastructure(r))

    print(f"  generated (persisted) : {len(rows)}")
    print(f"  unique genomes        : {len({r['hash'] for r in rows})}")
    print(f"  tested (has backtest) : {tested_rows}")
    print(f"  not tested            : {not_tested}")
    print("  --- V5 status buckets ---")
    for k in ("VALID", "LIVE_ELIGIBLE", "LIVE_TESTING", "LIVE_COMPLETED", "MT5_DEMO",
              "TESTING", "PENDING", "NOT_TESTED",
              "STRATEGY_FAILED", "DATA_UNAVAILABLE", "DATA_CORRUPT", "BACKTEST_ERROR"):
        print(f"    {k:<18} {buckets.get(k, 0)}")
    print(f"  alive/evaluable       : {alive}")
    print(f"  infrastructure skipped: {infra}")
    print(f"  strategy failures     : {buckets.get('STRATEGY_FAILED', 0)}")

    reason_mix = {}
    for r in rows:
        if st.v5_status(r) == "DATA_UNAVAILABLE":
            reason_mix["data"] = reason_mix.get("data", 0) + 1
        elif st.v5_status(r) == "BACKTEST_ERROR":
            reason_mix["worker"] = reason_mix.get("worker", 0) + 1
    print(f"  failure-reason mix    : {reason_mix}")

    print("\n  sample of generated nodes:")
    for r in rows[:6]:
        bt = lab.db.one("""SELECT stage, verdict, metrics FROM backtests
                           WHERE strategy_id=? ORDER BY id DESC LIMIT 1""", (r["id"],))
        m = json.loads(bt["metrics"]) if bt and bt.get("metrics") else {}
        print(f"    Node_{r['id']:<6} {r['timeframe']:<4} {st.v5_status(r):<18} "
              f"trades={m.get('trades', '-'):<5} pf={m.get('profit_factor', '-'):<6} "
              f"ret={m.get('total_return_pct', '-')}")

    # ------------------------------------------------------------ counters
    banner("5. ORCHESTRATOR COUNTERS (what the engine itself recorded)")
    for k in sorted(lab.counters):
        print(f"    {k:<32} {lab.counters[k]}")

    banner("6. §4 DIAGNOSTICS REPORTS")
    dg.invalidate()
    bt_rep = dg.backtest_report()
    print("  backtest counters:")
    for k, v in bt_rep["counts"].items():
        print(f"    {k:<28} {v}")
    print(f"  reconciliation: {bt_rep['reconciliation']}")
    ev_rep = dg.evolution_report()
    print("  evolution counters:")
    for k, v in ev_rep["counts"].items():
        print(f"    {k:<28} {v}")

    result = {
        "data_root": str(root),
        "generated": len(rows),
        "born": born,
        "unique": len({r["hash"] for r in rows}),
        "duplicates_blocked": lab.evo.duplicates_blocked,
        "tested": tested_rows,
        "not_tested": not_tested,
        "alive": alive,
        "infrastructure": infra,
        "buckets": buckets,
        "timeframes": by_tf,
        "counters": dict(lab.counters),
        "eligibility": {
            "testable": elig["testable_timeframes"],
            "unusable": elig["unusable_timeframes"],
        },
        "backtest_counts": bt_rep["counts"],
    }
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\n  written: {args.json}")
    banner("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
