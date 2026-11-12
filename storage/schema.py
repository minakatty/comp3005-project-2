"""Schemas and record encoding. INT = 4 bytes signed LE. VARCHAR(n) = 1 length byte + bytes. No NULLs."""
import struct


def parse_schema(s):
    cols = []
    for part in s.split(","):
        name, typ = part.strip().split(":")
        typ = typ.strip().upper()
        if typ == "INT":
            cols.append((name.strip(), "INT", 4))
        elif typ.startswith("VARCHAR(") and typ.endswith(")"):
            n = int(typ[8:-1])
            if not 1 <= n <= 255:
                raise ValueError("VARCHAR length must be 1..255")
            cols.append((name.strip(), "VARCHAR", n))
        else:
            raise ValueError(f"unknown type {typ}")
    return cols


def schema_to_string(cols):
    return ",".join(f"{n}:INT" if t == "INT" else f"{n}:VARCHAR({m})" for n, t, m in cols)


def encode(cols, row):
    if len(row) != len(cols):
        raise ValueError("wrong number of values")
    out = bytearray()
    for (name, t, m), v in zip(cols, row):
        if t == "INT":
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name}: expected INT")
            out += struct.pack("<i", v)
        else:
            b = str(v).encode("utf-8")
            if len(b) > m:
                raise ValueError(f"{name}: value longer than VARCHAR({m})")
            out.append(len(b))
            out += b
    return bytes(out)


def decode(cols, b):
    row, p = [], 0
    for _, t, _ in cols:
        if t == "INT":
            row.append(struct.unpack_from("<i", b, p)[0])
            p += 4
        else:
            ln = b[p]
            row.append(b[p + 1:p + 1 + ln].decode("utf-8"))
            p += 1 + ln
    return tuple(row)


def max_record_size(cols):
    return sum(4 if t == "INT" else 1 + m for _, t, m in cols)


def theoretical_per_page(cols, avg_varchar_len=None):
    """Hand-calculation helper: records/page = floor((4096-8) / (record_size + 4))."""
    size = sum(4 if t == "INT" else 1 + (avg_varchar_len if avg_varchar_len is not None else m)
               for _, t, m in cols)
    return (4096 - 8) // (size + 4)
