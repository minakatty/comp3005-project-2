"""Schemas and the record format (M1 spec Section 2).

A column is a tuple (name, type, length):
    ("a", "INT", 0)            4 bytes, signed, little-endian two's complement
    ("b", "VARCHAR", 20)       2-byte unsigned length L, then L bytes of UTF-8
    ("avg", "FLOAT", 0)        8-byte IEEE double  } never stored in a table; only
    ("sum", "LONG", 0)         8-byte signed int   } Module 2 temp files use them,
                                                     for AVG and SUM/COUNT results.

A record is the concatenation of its field encodings in schema order. There is no
record header and no null bitmap, because the project has no NULLs.
"""
import struct

from .errors import CatalogError, PageError

_INT = struct.Struct("<i")
_LEN = struct.Struct("<H")
_FLT = struct.Struct("<d")
_LNG = struct.Struct("<q")

INT_MIN, INT_MAX = -2 ** 31, 2 ** 31 - 1


def parse_schema(text):
    """Parse "a:INT,b:VARCHAR(20),c:INT" by hand (no regular expressions)."""
    if not text or not text.strip():
        raise CatalogError("schema is empty")
    cols, seen = [], set()
    for part in text.split(","):
        part = part.strip()
        if ":" not in part:
            raise CatalogError(f"column '{part}' needs the form name:TYPE")
        name, typ = (x.strip() for x in part.split(":", 1))
        if not name or not (name[0].isalpha() or name[0] == "_") or \
                not all(ch.isalnum() or ch == "_" for ch in name):
            raise CatalogError(f"bad column name '{name}'")
        if name in seen:
            raise CatalogError(f"column '{name}' appears twice")
        seen.add(name)
        up = typ.upper()
        if up == "INT":
            cols.append((name, "INT", 0))
        elif up.startswith("VARCHAR(") and up.endswith(")"):
            num = up[len("VARCHAR("):-1].strip()
            if not num.isdigit():
                raise CatalogError(f"bad VARCHAR length in '{part}'")
            n = int(num)
            if not 1 <= n <= 255:
                raise CatalogError(f"VARCHAR length must be 1..255, got {n} in '{part}'")
            cols.append((name, "VARCHAR", n))
        else:
            raise CatalogError(f"unknown type '{typ}' (use INT or VARCHAR(n))")
    return cols


def schema_text(cols):
    return ",".join(f"{n}:INT" if t == "INT" else f"{n}:{t}" if t == "FLOAT"
                    else f"{n}:VARCHAR({ln})" for n, t, ln in cols)


def schema_json(cols):
    """The shape scan --json requires: [["a","INT"],["b","VARCHAR",20]]."""
    return [[n, t] if t != "VARCHAR" else [n, t, ln] for n, t, ln in cols]


def encode(cols, values):
    """Tuple -> bytes. Raises PageError naming the column on a bad value."""
    out = bytearray()
    for (name, typ, n), v in zip(cols, values):
        if typ == "INT":
            if not isinstance(v, int) or isinstance(v, bool) or not INT_MIN <= v <= INT_MAX:
                raise PageError(f"column '{name}': {v!r} is not a 32-bit INT")
            out += _INT.pack(v)
        elif typ == "VARCHAR":
            b = str(v).encode("utf-8")
            if len(b) > n:
                raise PageError(f"column '{name}': value is {len(b)} bytes, longer than VARCHAR({n})")
            out += _LEN.pack(len(b)) + b
        elif typ == "LONG":
            out += _LNG.pack(v)
        else:
            out += _FLT.pack(float(v))
    if len(values) != len(cols):
        raise PageError(f"tuple has {len(values)} values, schema has {len(cols)} columns")
    return bytes(out)


def decode(cols, data, off=0):
    """bytes -> tuple. `data` may be a page buffer with the record at `off`."""
    vals = []
    for _, typ, _ in cols:
        if typ == "INT":
            vals.append(_INT.unpack_from(data, off)[0])
            off += 4
        elif typ == "VARCHAR":
            ln = _LEN.unpack_from(data, off)[0]
            off += 2
            vals.append(bytes(data[off:off + ln]).decode("utf-8"))
            off += ln
        elif typ == "LONG":
            vals.append(_LNG.unpack_from(data, off)[0])
            off += 8
        else:
            vals.append(_FLT.unpack_from(data, off)[0])
            off += 8
    return tuple(vals)


def encoded_length(cols, values):
    """Length of encode(cols, values) without building it (used for memory accounting)."""
    total = 0
    for (_, typ, _), v in zip(cols, values):
        if typ == "INT":
            total += 4
        elif typ == "VARCHAR":
            total += 2 + len(str(v).encode("utf-8"))
        else:
            total += 8
    return total


def max_record_length(cols):
    return sum(4 if t == "INT" else 8 if t in ("FLOAT", "LONG") else 2 + n for _, t, n in cols)
