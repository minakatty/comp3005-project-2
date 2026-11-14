"""One error format for everything in Module 3 (M3 spec Section 6):

    error: <category>: <message> at line L, column C

categories: lexical, syntax, name, ambiguity, type, aggregate, schema
"""

CATEGORIES = ("lexical", "syntax", "name", "ambiguity", "type", "aggregate", "schema")


class SQLError(Exception):
    def __init__(self, category, message, line=None, col=None):
        assert category in CATEGORIES, category
        super().__init__(message)
        self.category, self.message, self.line, self.col = category, message, line, col

    def render(self):
        where = f" at line {self.line}, column {self.col}" if self.line is not None else ""
        return f"error: {self.category}: {self.message}{where}"

    def __str__(self):
        return self.render()
