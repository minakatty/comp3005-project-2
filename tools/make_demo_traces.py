"""Write five synthetic buffer-pool traces, so tools/m1_measure.py can run before the
published traces are downloaded. THESE ARE NOT THE COURSE TRACES. Only trace 3's
shape is taken from the spec (an 80-page file scanned sequentially ten times).

    python tools/make_demo_traces.py traces_demo
"""
import os
import random
import sys


def main(out):
    os.makedirs(out, exist_ok=True)
    rnd = random.Random(3005)
    traces = {
        "trace1.txt": [rnd.randint(0, 199) for _ in range(5000)],                       # uniform random
        "trace2.txt": [int(rnd.paretovariate(1.2)) % 300 for _ in range(5000)],          # skewed, hot set
        "trace3.txt": list(range(80)) * 10,                                              # repeated scan (spec)
        "trace4.txt": [p for _ in range(20) for p in (list(range(10)) + [rnd.randint(10, 400) for _ in range(30)])],
        "trace5.txt": [p for k in range(50) for p in range(k * 4, k * 4 + 40)],           # sliding window
    }
    for name, ids in traces.items():
        with open(os.path.join(out, name), "w") as f:
            f.write("\n".join(map(str, ids)) + "\n")
    print(f"wrote {len(traces)} synthetic traces to {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "traces_demo")
