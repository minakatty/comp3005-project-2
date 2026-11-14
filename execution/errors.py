"""Module 2 errors. The CLI prints them as one line and exits 1."""


class ExecError(Exception):
    """Runtime failure: budget exceeded, undefined aggregate, bad tuple."""


class PlanError(Exception):
    """A plan that is malformed or does not conform to the plan schema.
    The message names the offending node."""
