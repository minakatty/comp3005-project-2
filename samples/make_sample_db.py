"""Build the sample database the sample plans and sample SQL run against.

The course's dataset, plan schema, sample plans and sample catalog are not
published; the professor's instruction is to write our own from the spec
documents. Tables follow the spec's examples:

    R(a INT, b VARCHAR(10), c INT, dept VARCHAR(10), salary INT, name VARCHAR(10))
        M2 Section 2: filter "R.a > 500 and R.b = 'cs'", project R.a, R.c,
        sort R.a asc / R.b desc, group_by R.dept with avg(R.salary), max(R.name)
    S(b VARCHAR(10), c VARCHAR(20))            joined on R.b = S.b
    DR(k INT), DS(k INT)                       M2 2.8: R.b = [1,2,2,3], S.b = [2,2,4]
    Emp, Dept                                  M3 Section 5 and M4 Section 2 examples

Deterministic (fixed seed).   python samples/make_sample_db.py samples_db
"""
import os
import random
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.db import Database            # noqa: E402
from storage.record import parse_schema    # noqa: E402

TABLES = {
    "R": "a:INT,b:VARCHAR(10),c:INT,dept:VARCHAR(10),salary:INT,name:VARCHAR(10)",
    "S": "b:VARCHAR(10),c:VARCHAR(20)",
    "DR": "k:INT",
    "DS": "k:INT",
    "Emp": "eid:INT,name:VARCHAR(20),age:INT,did:INT,salary:INT,mgr:INT,city:VARCHAR(20)",
    "Dept": "did:INT,title:VARCHAR(20),budget:INT,city:VARCHAR(20)",
}


def rows():
    rnd = random.Random(3005)
    subjects = ["cs", "math", "bio", "chem", "phys", "art", "hist", "econ"]
    depts = ["sales", "it", "hr", "ops", "legal"]
    cities = ["ottawa", "toronto", "montreal", "kingston"]
    yield "R", [(i, rnd.choice(subjects), rnd.randint(0, 99), rnd.choice(depts),
                 rnd.randint(30, 120) * 1000, f"n{rnd.randint(0, 999)}") for i in range(1000)]
    yield "S", [(rnd.choice(subjects), f"course{i}") for i in range(200)]
    yield "DR", [(1,), (2,), (2,), (3,)]
    yield "DS", [(2,), (2,), (4,)]
    yield "Emp", [(i, f"emp{i}", rnd.randint(20, 70), rnd.randint(0, 49), rnd.randint(30, 150) * 1000,
                   rnd.randint(0, 9999), rnd.choice(cities)) for i in range(10000)]
    yield "Dept", [(d, f"dept{d}", d * 100, rnd.choice(cities)) for d in range(50)]


def main(path):
    if os.path.exists(path):
        shutil.rmtree(path)
    db = Database(path, 64)
    for name, schema in TABLES.items():
        db.create_table(name, parse_schema(schema))
    for name, data in rows():
        h = db.heap(name)
        for r in data:
            h.insert(r)
        db.catalog.update_counts(name, db.count_data_pages(name), len(data))
    for name in TABLES:
        i = db.catalog.get_table(name)
        print(f"{name}: {i['num_tuples']} tuples, {i['num_pages']} pages")
    db.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples_db")
