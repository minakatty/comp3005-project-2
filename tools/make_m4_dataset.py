"""The dataset and 20-query workload for the Module 4 report. The course does not
publish one, so this is mine (DESIGN_M4.md §6).

Two kinds of correlation are planted on purpose:
  Emp.city, Emp.state, Emp.region   one-to-one: state and region are functions of city
  Emp.age, Emp.salary               salary rises with age

    python tools/make_m4_dataset.py --db db_m4 --workload workload_m4

"""
import argparse
import os
import random
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.db import Database            # noqa: E402
from storage.record import parse_schema    # noqa: E402

N_EMP, N_ORD, N_DEPT, N_PROJ = 4000, 3000, 50, 400

QUERIES = [
    # single table
    "SELECT name, age FROM Emp WHERE age > 60",
    "SELECT name FROM Emp WHERE city = 'c3'",
    "SELECT name FROM Emp WHERE city = 'c3' AND state = 's3'",
    "SELECT eid FROM Emp WHERE age > 60 AND salary > 60000",
    "SELECT did, COUNT(*) AS n, AVG(salary) AS avg_sal FROM Emp WHERE age > 30 GROUP BY did HAVING COUNT(*) > 20",
    "SELECT DISTINCT city FROM Emp",
    "SELECT name, salary FROM Emp ORDER BY salary DESC LIMIT 10",
    # two tables
    "SELECT e.name, d.dname FROM Emp e JOIN Dept d ON e.did = d.did WHERE d.budget > 40000",
    "SELECT e.name, o.amount FROM Emp e JOIN Orders o ON e.eid = o.eid WHERE o.amount > 900",
    "SELECT e.name, o.amount FROM Emp e, Orders o WHERE e.eid = o.eid AND e.age < 25",
    "SELECT d.dname, COUNT(*) AS n FROM Emp e JOIN Dept d ON e.did = d.did GROUP BY d.dname",
    "SELECT p.pname, d.dname FROM Proj p JOIN Dept d ON p.did = d.did WHERE p.pid < 50",
    # the correlated query (Section 7, "where it breaks")
    "SELECT e.name, e.pad, o.amount FROM Emp e JOIN Orders o ON e.eid = o.eid "
    "WHERE e.city = 'c3' AND e.state = 's3' AND e.region = 'r3'",
    # three and four tables
    "SELECT e.name, d.dname, p.pname FROM Emp e JOIN Dept d ON e.did = d.did JOIN Proj p ON p.did = d.did "
    "WHERE e.age > 65 AND p.pid < 40",
    "SELECT e.name, o.amount, d.dname FROM Emp e, Orders o, Dept d WHERE e.eid = o.eid AND e.did = d.did "
    "AND d.budget > 45000",
    "SELECT o.oid, p.pname FROM Orders o JOIN Emp e ON o.eid = e.eid JOIN Dept d ON e.did = d.did "
    "JOIN Proj p ON p.did = d.did WHERE o.amount > 990 AND p.pid < 20",
    # set operations and subqueries
    "SELECT city FROM Emp WHERE age < 22 UNION SELECT city FROM Emp WHERE age > 68",
    "SELECT did FROM Emp WHERE salary > 70000 INTERSECT SELECT did FROM Dept WHERE budget > 20000",
    "SELECT t.did, t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t JOIN Dept d "
    "ON t.did = d.did WHERE d.budget > 30000",
    "SELECT e.name FROM Emp e JOIN Emp m ON e.mgr = m.eid WHERE m.age > 65",
]


def build(path):
    if os.path.exists(path):
        shutil.rmtree(path)
    rnd = random.Random(3005)
    db = Database(path, 64)
    db.create_table("Emp", parse_schema("eid:INT,name:VARCHAR(16),age:INT,salary:INT,did:INT,mgr:INT,"
                                        "city:VARCHAR(8),state:VARCHAR(8),region:VARCHAR(8),pad:VARCHAR(80)"))
    db.create_table("Dept", parse_schema("did:INT,dname:VARCHAR(16),budget:INT"))
    db.create_table("Orders", parse_schema("oid:INT,eid:INT,amount:INT,pad:VARCHAR(200)"))
    db.create_table("Proj", parse_schema("pid:INT,did:INT,pname:VARCHAR(16)"))
    h = db.heap("Emp")
    for i in range(N_EMP):
        age = rnd.randint(20, 69)
        c = rnd.randint(0, 9)
        h.insert((i, f"emp{i}", age, age * 1000 + rnd.randint(0, 9000), rnd.randint(0, N_DEPT - 1),
                  rnd.randint(0, N_EMP - 1), f"c{c}", f"s{c}", f"r{c}", "x" * 80))
    h = db.heap("Dept")
    for d in range(N_DEPT):
        h.insert((d, f"dept{d}", d * 1000))
    h = db.heap("Orders")
    for o in range(N_ORD):
        h.insert((o, rnd.randint(0, N_EMP - 1), rnd.randint(1, 1000), "y" * 200))
    h = db.heap("Proj")
    for p in range(N_PROJ):
        h.insert((p, rnd.randint(0, N_DEPT - 1), f"proj{p}"))
    for t, n in (("Emp", N_EMP), ("Dept", N_DEPT), ("Orders", N_ORD), ("Proj", N_PROJ)):
        db.catalog.update_counts(t, db.count_data_pages(t), n)
    db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db_m4")
    ap.add_argument("--workload", default="workload_m4")
    a = ap.parse_args()
    build(a.db)
    os.makedirs(a.workload, exist_ok=True)
    for i, q in enumerate(QUERIES, 1):
        with open(os.path.join(a.workload, f"q{i:02d}.sql"), "w") as f:
            f.write(q + ";\n")
    print(f"built {a.db} and {len(QUERIES)} queries in {a.workload}")


if __name__ == "__main__":
    main()
