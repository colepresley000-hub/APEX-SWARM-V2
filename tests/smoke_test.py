#!/usr/bin/env python3
"""
Refactor safety net for main.py.

Purpose: main.py is being decomposed into modules. This test imports the live
FastAPI app and verifies nothing was lost in the move:
  1. `main:app` imports cleanly (catches broken imports / syntax after a split)
  2. Every route present in tests/route_snapshot.txt still registers
     (catches a route accidentally dropped when moving code to a new module)

Run it after EVERY extraction step:
    venv/bin/python tests/smoke_test.py

Exit code 0 = safe to proceed, 1 = something regressed (do not deploy).

If you intentionally add/remove routes, regenerate the snapshot with:
    venv/bin/python tests/smoke_test.py --update-snapshot
"""
import os
import sys

# Isolate from the real DB — use a throwaway SQLite file.
os.environ.setdefault("DATABASE_PATH", "/tmp/_apex_smoke.db")

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "route_snapshot.txt")
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def current_routes():
    """Import the app and return the set of 'METHOD /path' strings."""
    import main  # noqa: this is the import-cleanliness check
    rows = set()
    for r in main.app.routes:
        methods = getattr(r, "methods", None)
        if not methods:
            continue
        for m in sorted(methods):
            if m in ("HEAD", "OPTIONS"):
                continue
            rows.add(f"{m} {r.path}")
    return rows


def load_snapshot():
    if not os.path.exists(SNAPSHOT):
        return set()
    with open(SNAPSHOT) as f:
        return {line.strip() for line in f if line.strip()}


def write_snapshot(rows):
    with open(SNAPSHOT, "w") as f:
        f.write("\n".join(sorted(rows)) + "\n")


def main_check(update=False):
    try:
        routes = current_routes()
    except Exception as e:  # import failure = hard fail
        import traceback
        traceback.print_exc()
        print(f"\n❌ FAIL: could not import main:app — {e}")
        return 1

    print(f"✅ main:app imported — {len(routes)} routes registered")

    if update:
        write_snapshot(routes)
        print(f"✅ snapshot updated → {SNAPSHOT} ({len(routes)} routes)")
        return 0

    baseline = load_snapshot()
    if not baseline:
        write_snapshot(routes)
        print(f"ℹ️  no snapshot found — created one with {len(routes)} routes")
        return 0

    missing = baseline - routes
    added = routes - baseline

    if missing:
        print(f"\n❌ FAIL: {len(missing)} route(s) from the snapshot are GONE:")
        for r in sorted(missing):
            print(f"     - {r}")
        if added:
            print(f"\n   (also {len(added)} new route(s) appeared:)")
            for r in sorted(added):
                print(f"     + {r}")
        print("\n   A refactor likely dropped a route. Fix before deploying.")
        print("   If this change is intentional, rerun with --update-snapshot")
        return 1

    if added:
        print(f"ℹ️  {len(added)} new route(s) added since snapshot (not a failure):")
        for r in sorted(added):
            print(f"     + {r}")
        print("   Run --update-snapshot to bless them once confirmed intentional.")

    print(f"✅ all {len(baseline)} snapshot routes still present — safe to proceed")
    return 0


if __name__ == "__main__":
    sys.exit(main_check(update="--update-snapshot" in sys.argv))
