class StorageError(Exception):
    """Base class for every error the storage engine reports on purpose."""

class SlotError(StorageError): pass            # dead or out-of-range slot
class RecordTooLong(StorageError): pass        # record cannot fit on one page
class LoadError(StorageError): pass            # bad CSV line
class BufferPoolError(StorageError): pass
class PinnedPageError(BufferPoolError): pass   # tried to evict a pinned page
class BufferPoolFull(BufferPoolError): pass    # no unpinned frame available
class PinError(BufferPoolError): pass          # unpin of a page not pinned / not resident
class UnknownTable(StorageError, KeyError):
    def __str__(self):
        return self.args[0] if self.args else "unknown table"
