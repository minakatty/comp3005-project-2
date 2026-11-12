"""Data sources for the evaluator (Project 2, week one, item 5).

Operators no longer own relations held in a dict. They are handed a *provider*:

    provider.schema(name) -> Schema            (KeyError if the table is unknown)
    provider.scan(name)   -> iterator of rows  (a fresh iterator on every call)
    name in provider      -> bool
    provider[name]        -> Relation          (schema + rows, built from scan)

Week one keeps the evaluator materialising (a Relation is still a list of rows);
Module 2 replaces that with the open/next/close iterator interface. What matters
now is that the *source* of base rows is injectable, so the same operators run
over test fixtures or over the M1 heap files.
"""
from .relation import Relation
from .schema import Attribute, ColType, Schema

_TYPE_MAP = {"INT": ColType.NUMBER, "VARCHAR": ColType.STRING}


class _ProviderBase:
    def __contains__(self, name):
        try:
            self.schema(name)
            return True
        except KeyError:
            return False

    def __getitem__(self, name):
        return Relation(self.schema(name), list(self.scan(name)))


class DictProvider(_ProviderBase):
    """Wraps the old {name: Relation} dict, so existing tests and the CLI keep working."""

    def __init__(self, relations):
        self.relations = relations

    def schema(self, name):
        return self.relations[name].schema

    def scan(self, name):
        return iter(self.relations[name].rows)


class StorageProvider(_ProviderBase):
    """Adapter over the M1 storage engine. `db` needs:
         db.catalog.get_table(name) -> {"schema": [(col, "INT"|"VARCHAR", n), ...], ...}
         db.catalog.list_tables()   -> [names]
         db.heap(name).scan()       -> iterator of (rid, tuple)
    INT maps to NUMBER and VARCHAR to STRING, so declared types replace inference."""

    def __init__(self, db):
        self.db = db

    def schema(self, name):
        if name not in self.db.catalog.list_tables():
            raise KeyError(name)
        cols = self.db.catalog.get_table(name)["schema"]
        return Schema([Attribute(name, c) for c, _, _ in cols],
                      [_TYPE_MAP[t] for _, t, _ in cols])

    def scan(self, name):
        for _, row in self.db.heap(name).scan():
            yield row


def as_provider(source):
    """Accept either a plain dict of Relations (legacy) or a provider."""
    return DictProvider(source) if isinstance(source, dict) else source
