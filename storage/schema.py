"""Schemas and record encoding.
INT        4 bytes signed two's complement, little endian.
VARCHAR(n) 2-byte unsigned length L (L <= n <= 255), then L bytes of UTF-8.
A record is the concatenation of its fields in schema order: no header, no null bitmap."""
import struct


def parse_schema(s):
    cols = []
    for part in s.split(","):
        if ":" not in part:
            raise ValueError(f"bad column definition {part!r}; expected name:TYPE")
        name, typ = part.split(":", 1)
        name, typ = name.strip(), typ.strip().upper()
        if not name:
            raise ValueError("empty column name")
        if typ == "INT":
            cols.append((name, "INT", 4))
        elif typ.startswith("VARCHAR(") and typ.endswith(")"):
            n = int(typ[8:-1])
            if not 1 <= n <= 255:
                raise ValueError("VARCHAR length must be between 1 and 255")
            cols.append((name, "VARCHAR", n))
        else:
            raise ValueError(f"unknown type {typ!r}")
    if len({c[0] for c in cols}) != len(cols):
        raise ValueError("duplicate column name")
    return cols


def schema_to_string(cols):
    return ",".join(f"{n}:INT" if t == "INT" else f"{n}:VARCHAR({m})" for n, t, m in cols)


def encode(cols, row):
    if len(row) != len(cols):
        raise ValueError(f"expected {len(cols)} values, got {len(row)}")
    out = bytearray()
    for (name, t, m), v in zip(cols, row):
        if t == "INT":
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"column {name}: expected INT, got {v!r}")
            out += struct.pack("<i", v)
        else:
            b = str(v).encode("utf-8")
            if len(b) > m:
                raise ValueError(f"column {name}: value of {len(b)} bytes exceeds VARCHAR({m})")
            out += struct.pack("<H", len(b)) + b
    return bytes(out)


def decode(cols, b):
    row, p = [], 0
    for _, t, _ in cols:
        if t == "INT":
            row.append(struct.unpack_from("<i", b, p)[0]); p += 4
        else:
            ln = struct.unpack_from("<H", b, p)[0]
            row.append(b[p + 2:p + 2 + ln].decode("utf-8")); p += 2 + ln
    return tuple(row)


def max_record_size(cols):
    return sum(4 if t == "INT" else 2 + m for _, t, m in cols)


def min_record_size(cols):
    return sum(4 if t == "INT" else 2 for _, t, _ in cols)


def records_per_page(record_len):
    """Spec section 3.3: floor(4088 / (L + 4))."""
    return 4088 // (record_len + 4)
