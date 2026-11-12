"""Writes five SYNTHETIC trace files for testing the measurement pipeline.
These are NOT the instructor's traces. Replace them with the published ones for the report."""
import os, random, sys

out = sys.argv[1] if len(sys.argv) > 1 else "traces_demo"
os.makedirs(out, exist_ok=True)
rnd = random.Random(42)

def write(name, ids):
    with open(os.path.join(out, name), "w") as f:
        f.write("\n".join(str(i) for i in ids) + "\n")

write("demo_seq_loop.txt", [i for _ in range(10) for i in range(40)])            # repeated scan, 40 pages
write("demo_hot_set.txt", [rnd.choice(range(8)) if rnd.random() < 0.8 else rnd.randrange(8, 200) for _ in range(2000)])
write("demo_uniform.txt", [rnd.randrange(100) for _ in range(2000)])
write("demo_scan_plus_hot.txt", [x for r in range(15) for x in (list(range(100, 130)) + [0, 1, 2, 3] * 5)])
write("demo_working_set.txt", [rnd.randrange(12) for _ in range(1500)])
print("wrote 5 demo traces to", out)
