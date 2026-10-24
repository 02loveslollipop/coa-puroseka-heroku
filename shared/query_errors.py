"""Safe SQL diagnostics shared across the query service boundary."""


class QueryRejected(ValueError):
    """The read-only allowlist rejected a query; never relax it for a retry."""


class QueryError(ValueError):
    """A syntax or schema error that one bounded model correction may fix."""


def query_diagnostic(exc):
    # PostgreSQL messages can include row values and connection details. Return
    # only fixed descriptions and narrowly matched identifier diagnostics.
    import re

    descriptions = {
        "42703": "Undefined column; use only columns in the supplied physical schema.",
        "42P01": "Undefined table or alias; check FROM/JOIN and CTE alias scope.",
        "42702": "Ambiguous column; qualify the column with its table alias.",
        "42883": "Undefined function or operator; check PostgreSQL argument types.",
        "42803": "Invalid grouping; group or aggregate all selected expressions.",
        "42601": "Invalid PostgreSQL syntax.",
        "42804": "Incompatible SQL expression types.",
        "42846": "Unsupported type cast.",
        "42P10": "Invalid DISTINCT ON or ORDER BY expression.",
        "42P20": "Invalid window expression; compute window functions in a CTE before filtering their results.",
        "22012": "Division by zero; guard the denominator with NULLIF.",
        "22P02": "Invalid value conversion; check column types before casting.",
    }
    message = descriptions.get(exc.sqlstate)
    if message is None:
        if (exc.sqlstate or "").startswith(("22", "42")):
            return "Invalid SQL expression; check identifiers, types, grouping and window expressions against the supplied schema."
        return None
    primary = exc.diag.message_primary or ""
    if re.fullmatch(
        r'(?:column [a-zA-Z_][\w.]* does not exist|column "[a-zA-Z_][\w.]*" must appear in the GROUP BY clause or be used in an aggregate function|missing FROM-clause entry for table "[a-zA-Z_]\w*")',
        primary,
    ):
        message = primary + ". " + message
    return message
