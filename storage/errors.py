"""Every error Module 1 can raise. The CLI catches StorageError once and prints
the message; no stack trace ever reaches the user."""


class StorageError(Exception):
    """Base class. str(err) is the user-facing message."""


class PageError(StorageError):
    """Bad slot number, dead slot, record too large for a page."""


class BufferPoolError(StorageError):
    """No unpinned frame, unpin of a page that is not pinned."""


class CatalogError(StorageError):
    """Unknown table, duplicate table, bad schema string."""


class LoadError(StorageError):
    """Bad CSV field. The message names the line and the column."""


def read_text(path):
    """Read a text file whatever wrote it. Windows PowerShell's `>` writes UTF-16 and
    Out-File -Encoding utf8 adds a BOM; both are decoded here, so a plan or query
    saved from PowerShell loads the same as one saved anywhere else."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")
