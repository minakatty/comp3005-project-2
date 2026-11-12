"""Writes five SYNTHETIC trace files for testing the measurement pipeline (one page id per line).
These are NOT the instructor's traces. Replace them with the published ones for the report."""
import os, random, sys

out = sys.argv[1] if len(sys.argv) > 1 else "traces_demo"
os.makedirs(out, exist_ok=True)
rnd = random.Random(42)

def write(name, ids):
    with open(os.path.join(out, name), "w") as f:
        f.write("\n".join(str(i) for i in ids) + "\n")

write("trace1.txt", [rnd.choice(range(20)) if rnd.random() < 0.8 else rnd.randrange(20, 400) for _ in range(5000)])  # hot set
write("trace2.txt", [rnd.randrange(300) for _ in range(5000)])                                                      # uniform
write("trace3.txt", [i for _ in range(10) for i in range(80)])                  # 80-page file scanned 10 times, like the spec's trace 3
write("trace4.txt", [x for r in range(20) for x in (list(range(100, 190)) + [0, 1, 2, 3] * 10)])                     # scan + hot pages
write("trace5.txt", [rnd.randrange(40) for _ in range(5000)])                                                       # small working set
print("wrote 5 demo traces to", out)
