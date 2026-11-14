"""The integrated system (Project 2 handout Section 6): SQL in, rows out.

    python dbms.py --db DIR [--frames 32]            interactive prompt
    python dbms.py --db DIR --file script.sql        run a script, then exit

Statements end with ';'. Besides the SQL subset of Module 3 the shell accepts

    EXPLAIN <query>             the physical plan with estimated rows and cost
    EXPLAIN ANALYZE <query>     runs it; estimated against actual rows per node, I/O
    ANALYZE <table>             statistics for Module 4
    SET frames = <n>            the memory budget B for later statements
    LOAD <table> FROM <file>    bulk load a CSV file (Module 1 loader)
    \\q, quit, exit              leave

The pipeline is wiring only, the four module tools still work on their own:
    M3 compile_sql -> M4 optimize -> M2 execute -> M1 pages through the buffer pool.
Every query runs on a freshly opened buffer pool of exactly B frames, so its page
counts are reproducible and never depend on what ran before it.
"""
import sys
import time

from execution.engine import execute
from execution.errors import ExecError, PlanError
from optimizer.estimate import Stats
from optimizer.logical import OptError
from optimizer.optimize import optimize
from optimizer.stats import analyze
from ralang.errors import RAError
from sqlfront.compiler import catalog_from_db, compile_sql
from sqlfront.errors import SQLError
from storage.db import Database
from storage.errors import StorageError


class Shell:
    def __init__(self, path, frames=32, out=sys.stdout):
        self.path = path
        self.B = frames
        self.out = out

    def p(self, *a):
        print(*a, file=self.out)

    def _db(self, frames=None):
        return Database(self.path, frames or self.B)

    # -- one statement --------------------------------------------------------------
    def run(self, text):
        """Run one statement (without its ';'). Returns a dict describing the result,
        for the workload runner; prints the human-readable form."""
        t = text.strip()
        if not t:
            return None
        words = t.split()
        head = words[0].upper()
        try:
            if head == "SET":
                return self._set(t)
            if head == "ANALYZE" and len(words) == 2:
                db = self._db(16)
                try:
                    st = analyze(db, words[1])
                finally:
                    db.close()
                self.p(f"ANALYZE {words[1]}: {st['n_tuples']} tuples, {st['n_pages']} pages")
                return {"kind": "analyze"}
            if head == "LOAD":
                return self._load(words)
            if head == "EXPLAIN":
                rest = t[len(words[0]):].strip()
                if rest.upper().startswith("ANALYZE"):
                    return self.query(rest[len("ANALYZE"):].strip(), analyze_mode=True)
                return self.query(rest, explain_only=True)
            return self.query(t)
        except SQLError as e:
            self.p(e.render())
        except RAError as e:
            self.p(f"error: {e.kind}: {e.message}")
        except (StorageError, PlanError, ExecError, OptError) as e:
            self.p(f"error: {e}")
        return {"kind": "error"}

    def _set(self, t):
        body = t[3:].strip()
        if "=" not in body:
            raise StorageError("usage: SET frames = n")
        k, v = (x.strip() for x in body.split("=", 1))
        if k.lower() != "frames" or not v.isdigit() or int(v) < 3:
            raise StorageError("usage: SET frames = n   (n >= 3)")
        self.B = int(v)
        self.p(f"frames = {self.B}")
        return {"kind": "set"}

    def _load(self, words):
        if len(words) != 4 or words[2].upper() != "FROM":
            raise StorageError("usage: LOAD table FROM file")
        path = words[3].strip("'\"")
        db = self._db()
        try:
            n = db.load_csv(words[1], path)
        finally:
            db.close()
        self.p(f"LOAD {words[1]}: {n} rows")
        return {"kind": "load"}

    def query(self, sql, explain_only=False, analyze_mode=False):
        db = self._db()
        try:
            stmts = compile_sql(sql, catalog_from_db(db))
            if len(stmts) != 1:
                raise SQLError("syntax", "one statement at a time")
            lp = stmts[0]
            if lp["op"] == "create_table":
                cols = [(c[0], c[1], c[2] if len(c) > 2 else 0) for c in lp["columns"]]
                db.create_table(lp["table"], cols)
                self.p(f"CREATE TABLE {lp['table']}")
                return {"kind": "ddl"}
            if lp["op"] == "insert":
                db.insert(lp["table"], tuple(lp["values"]))
                self.p("INSERT 1")
                return {"kind": "insert"}
            _, root = optimize(lp, Stats(db), self.B)
            if explain_only:
                self.p("\n".join(root.explain()))
                return {"kind": "explain"}
        finally:
            db.close()
        db = self._db()                       # a cold pool of exactly B frames
        try:
            t0 = time.perf_counter()
            res = execute(db, root.json(), self.B)
            wall = time.perf_counter() - t0
        finally:
            db.close()
        io = res.totals["page_reads"] + res.totals["page_writes"]
        info = {"kind": "query", "rows": len(res.rows), "page_reads": res.totals["page_reads"],
                "page_writes": res.totals["page_writes"], "io": io, "wall": wall,
                "est_root": root.est.rows, "actual_root": res.root.tuples_out}
        if analyze_mode:
            ops = [o for o, _ in _walk(res.root)]
            pn = list(root.walk())
            for (o, d), p in zip(_walk(res.root), pn):
                est, act = p.est.rows, o.tuples_out
                q = max(est, act, 1) / max(min(est, act), 1)
                self.p(f"{'  ' * d + p.label:<52} est={est:<8} actual={act:<8} q={q:<8.2f} "
                       f"reads={o.page_reads} writes={o.page_writes}")
            self.p(f"-- {len(res.rows)} rows, {io} page I/O, {wall:.3f} s, B = {self.B}")
            info["nodes"] = [(p.label, p.est.rows, o.tuples_out) for p, o in zip(pn, ops)]
            return info
        names = [c[0] for c in res.schema]
        self.p(" | ".join(names))
        for r in res.rows:
            self.p(" | ".join(f"{v:.4f}" if isinstance(v, float) else str(v) for v in r))
        self.p(f"({len(res.rows)} rows, {io} page I/O, {wall:.3f} s)")
        return info

    # -- scripts and the prompt ------------------------------------------------------------
    def run_script(self, text):
        return [self.run(s) for s in split_statements(text)]

    def repl(self):
        buf = ""
        while True:
            try:
                line = input("dbms> " if not buf else "  ... ")
            except EOFError:
                break
            if line.strip() in ("\\q", "quit", "exit") and not buf:
                break
            buf += line + "\n"
            if ";" in line:
                parts = split_statements(buf)
                complete = buf.rstrip().endswith(";")
                for s in (parts if complete else parts[:-1]):
                    self.run(s)
                buf = "" if complete else parts[-1]


def split_statements(text):
    """Split on ';' outside string literals and outside -- comments."""
    out, cur, i, n, in_str = [], [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            cur.append(ch)
            if ch == "'":
                if i + 1 < n and text[i + 1] == "'":
                    cur.append("'")
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            cur.append(ch)
        elif ch == "-" and i + 1 < n and text[i + 1] == "-":
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif ch == ";":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur))
    return [s for s in out if s.strip()]


def _walk(op, d=0):
    yield op, d
    for c in op.children:
        yield from _walk(c, d + 1)


def main(argv):
    opts, i = {}, 0
    while i < len(argv):
        if argv[i] in ("--db", "--frames", "--file") and i + 1 < len(argv):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            print(f"error: unexpected argument '{argv[i]}'", file=sys.stderr)
            return 2
    frames = int(opts.get("frames", "32"))
    sh = Shell(opts.get("db", "db"), frames)
    if "file" in opts:
        from storage.errors import read_text
        try:
            text = read_text(opts["file"])
        except OSError:
            print(f"error: cannot open '{opts['file']}'", file=sys.stderr)
            return 1
        sh.run_script(text)
        return 0
    sh.repl()
    return 0
