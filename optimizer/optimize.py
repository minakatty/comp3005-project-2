"""The optimizer as one function: logical plan -> (rewritten logical plan, physical PNode)."""
from .estimate import Estimator, Stats
from .logical import props, rewrite
from .physical import Planner, naive_physical


def optimize(logical, stats, B, disabled=(), dp_log=None):
    est = Estimator(stats)
    rewritten = rewrite(logical, est, disabled)
    root = Planner(est, B, dp_log).plan(rewritten)
    return rewritten, root


def explain_logical(node, stats, depth=0):
    """A logical tree with estimated rows per node (for the before/after comparison)."""
    from sqlfront.compiler import explain_plan
    est = Estimator(stats)
    lines = []

    def rec(n, d):
        head = explain_plan({k: v for k, v in n.items() if k not in ("child", "left", "right")})[0]
        lines.append(f"{'  ' * d + head:<52} est_rows={props(n, est).rows}")
        for k in ("child", "left", "right"):
            if k in n:
                rec(n[k], d + 1)
    rec(node, depth)
    return lines


__all__ = ["optimize", "explain_logical", "naive_physical", "Stats"]
