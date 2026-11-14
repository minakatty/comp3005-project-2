"""Write the five buffer-pool traces used in REPORT_M1.md into traces/.

The course traces are not published; the professor's instruction is to write our
own, using M1 spec Section 5.3 as the guide. Each trace tests one behaviour:

    trace1.txt  the spec's worked trace, 1 2 3 1 4 2 (Section 5.3, expected counts known)
    trace2.txt  hot/cold: 80% of fetches go to 20 hot pages, 20% to 400 cold pages
    trace3.txt  sequential scan of an 80-page file, repeated ten times (Section 8)
    trace4.txt  uniform random over 200 pages: no locality at all
    trace5.txt  a hot set of 10 pages, interrupted every so often by a one-pass scan of
                100 other pages (does a scan flush the hot set?)

Deterministic: a fixed seed, so the files are identical on every machine.

    python tools/make_traces.py traces
"""
import os
import random
import sys


def traces():
    rnd = random.Random(3005)
    t2 = [rnd.randint(0, 19) if rnd.random() < 0.8 else rnd.randint(20, 419) for _ in range(5000)]
    t4 = [rnd.randint(0, 199) for _ in range(5000)]
    t5 = []
    for rep in range(10):
        t5 += [rnd.randint(0, 9) for _ in range(200)]          # hot set
        t5 += list(range(1000 + rep * 100, 1100 + rep * 100))  # a one-pass scan of new pages
    return {
        "trace1.txt": [1, 2, 3, 1, 4, 2],
        "trace2.txt": t2,
        "trace3.txt": list(range(80)) * 10,
        "trace4.txt": t4,
        "trace5.txt": t5,
    }


def main(out):
    os.makedirs(out, exist_ok=True)
    for name, ids in traces().items():
        with open(os.path.join(out, name), "w", newline="\n") as f:
            f.write("\n".join(map(str, ids)) + "\n")
    print(f"wrote 5 traces to {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "traces")
