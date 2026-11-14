"""exec command line tool (M2 spec Section 4).

    python -m execution run   --db DIR --plan plan.json --frames 32 [--json] [--policy lru|clock]
    python -m execution trace --db DIR --plan plan.json --frames 32

On Windows/Linux you can also use the wrapper:  python exec.py run ...
"""
import json
import sys

from ralang.errors import RAError
from storage.cli import as_int, need, parse_args
from storage.errors import StorageError, read_text

from .engine import run_plan
from .errors import ExecError, PlanError
from .plan import load_plan_text


def _load(opts):
    path = need(opts, "plan")
    try:
        text = read_text(path)
    except OSError:
        raise StorageError(f"cannot open plan file '{path}'")
    return load_plan_text(text, path)


def _fmt(v):
    return f"{v:.4f}" if isinstance(v, float) else str(v)


def main(argv):
    try:
        cmd, opts = parse_args(argv, flags=("json",))
        if cmd not in ("run", "trace"):
            raise StorageError(f"unknown command '{cmd}' (run or trace)")
        B = as_int(opts, "frames", 32, lo=3)
        plan = _load(opts)
        res = run_plan(opts.get("db", "db"), plan, B, opts.get("policy", "lru"), collect=(cmd == "run"))
        if cmd == "trace":
            print("\n".join(res.trace_lines()))
            print(f"-- B={B}, peak frames held={res.peak_frames}, "
                  f"pinned after close={res.leftover_pins}, temp files left={len(res.leftover_temps)}")
        elif opts.get("json"):
            out = res.to_json()
            out["rows"] = [[round(v, 4) if isinstance(v, float) else v for v in r] for r in out["rows"]]
            print(json.dumps(out))
        else:
            print(" | ".join(c[0] for c in res.schema))
            for r in res.rows:
                print(" | ".join(_fmt(v) for v in r))
            print(f"({len(res.rows)} rows) {res.totals}")
        return 0
    except (StorageError, PlanError, ExecError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except RAError as e:
        print(f"error: {e.kind}: {e.message}", file=sys.stderr)
        return 1
