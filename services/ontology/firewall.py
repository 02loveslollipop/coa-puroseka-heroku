"""Fail-closed SQL firewall for untrusted model output.

Only the reviewed SELECT grammar, built-in functions/types and source tables
may reach PostgreSQL. Unknown syntax is rejected rather than passed through.
"""

from sqlglot import ErrorLevel, exp, parse
from sqlglot.optimizer.scope import Scope, traverse_scope
from shared.database import schema_name
from shared.query_errors import QueryRejected

ALLOWED_FUNCTIONS = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "coalesce",
    "nullif",
    "lower",
    "upper",
    "length",
    "char_length",
    "concat",
    "concat_ws",
    "replace",
    "trim",
    "substring",
    "to_timestamp",
    "to_char",
    "date_part",
    "date_trunc",
    "extract",
    "round",
    "abs",
    "cast",
    "try_cast",
    "greatest",
    "least",
    "distinct",
    "jsonb_extract_path_text",
    "jsonb_array_length",
    "jsonb_typeof",
    "json_extract",
    "json_extract_scalar",
    "jsonb_extract",
    "jsonb_extract_scalar",
    "jsonb_contains",
    "array_agg",
    "string_agg",
    "group_concat",
    "array_size",
    "current_date",
    "current_timestamp",
    "unix_to_time",
    "time_to_str",
    "timestamp_trunc",
    "at_time_zone",
    "lag",
    "lead",
    "row_number",
    "rank",
    "dense_rank",
    "first_value",
    "last_value",
    "floor",
    "ceil",
    "ceiling",
    "and",
    "or",
    "not",
    "case",
    "if",
    "exists",
    "array",
}


# Exact classes: adding a parser feature never implicitly grants permission.
ALLOWED_NODES = {
    "Select",
    "From",
    "Join",
    "Table",
    "TableAlias",
    "Identifier",
    "Column",
    "Star",
    "Alias",
    "Literal",
    "Null",
    "Boolean",
    "Var",
    "Where",
    "Group",
    "Having",
    "Order",
    "Ordered",
    "Limit",
    "Offset",
    "Distinct",
    "Subquery",
    "With",
    "CTE",
    "Union",
    "Intersect",
    "Except",
    "Paren",
    "Tuple",
    "Bracket",
    "Slice",
    "Window",
    "WindowSpec",
    "Filter",
    "WithinGroup",
    "Neg",
    "Add",
    "Sub",
    "Mul",
    "Div",
    "Mod",
    "Pow",
    "EQ",
    "NEQ",
    "GT",
    "GTE",
    "LT",
    "LTE",
    "Is",
    "In",
    "Between",
    "Like",
    "ILike",
    "Escape",
    "DPipe",
    "DataType",
    "DataTypeParam",
    "Interval",
    "AtTimeZone",
    "JSONPath",
    "JSONPathRoot",
    "JSONPathKey",
    "JSONPathSubscript",
    "JSONPathWildcard",
}
ALLOWED_TYPES = {
    exp.DataType.Type.BOOLEAN,
    exp.DataType.Type.SMALLINT,
    exp.DataType.Type.INT,
    exp.DataType.Type.BIGINT,
    exp.DataType.Type.DECIMAL,
    exp.DataType.Type.FLOAT,
    exp.DataType.Type.DOUBLE,
    exp.DataType.Type.TEXT,
    exp.DataType.Type.VARCHAR,
    exp.DataType.Type.CHAR,
    exp.DataType.Type.DATE,
    exp.DataType.Type.TIME,
    exp.DataType.Type.TIMETZ,
    exp.DataType.Type.TIMESTAMP,
    exp.DataType.Type.TIMESTAMPTZ,
    exp.DataType.Type.INTERVAL,
    exp.DataType.Type.JSON,
    exp.DataType.Type.JSONB,
    exp.DataType.Type.ARRAY,
}


def validate_sql(statement, tables):
    if (
        not isinstance(statement, str)
        or not statement.strip()
        or len(statement) > 16000
    ):
        raise QueryRejected("Query must contain between 1 and 16000 characters")
    try:
        trees = parse(statement, read="postgres", error_level=ErrorLevel.RAISE)
    except RecursionError as exc:
        raise QueryRejected("Query is too complex") from exc
    except Exception as exc:
        raise QueryRejected("SQL could not be parsed") from exc
    if len(trees) != 1 or not isinstance(trees[0], exp.Select):
        raise QueryRejected("Only one SELECT statement is permitted")
    tree = trees[0]
    for count, node in enumerate(tree.walk(), 1):
        if count > 1000 or node.depth > 64:
            raise QueryRejected("Query is too complex")
        if isinstance(node, exp.Func):
            name = (
                node.name.lower()
                if isinstance(node, exp.Anonymous)
                else node.sql_name().lower()
            )
            if name not in ALLOWED_FUNCTIONS:
                raise QueryRejected("Only approved built-in functions are permitted")
        elif type(node).__name__ not in ALLOWED_NODES:
            # Includes DML/DDL inside CTEs, SELECT INTO, locks, custom operators,
            # qualified functions, commands, parameters and unknown extensions.
            raise QueryRejected("Only approved read-only SELECT syntax is permitted")
        if isinstance(node, exp.DataType) and (
            node.this not in ALLOWED_TYPES or node.args.get("kind")
        ):
            raise QueryRejected("Only approved built-in data types are permitted")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise QueryRejected("Recursive queries are not permitted")

    resolved = set()
    try:
        for scope in traverse_scope(tree):
            for table in scope.tables:
                resolved.add(id(table))
                # Resolve each CTE in its own scope; a nested CTE cannot grant
                # access to a physical private table in an outer query.
                source = scope.sources.get(table.alias_or_name)
                if isinstance(source, Scope) and not table.db and not table.catalog:
                    continue
                if (
                    not isinstance(table.this, exp.Identifier)
                    or table.catalog
                    or table.db not in ("", schema_name())
                    or table.name not in tables
                ):
                    raise QueryRejected("Only the imported data tables may be queried")
                table.set("db", exp.Identifier(this=schema_name()))
        if any(id(table) not in resolved for table in tree.find_all(exp.Table)):
            raise QueryRejected("Every table must resolve to an approved source")
        return tree.sql(
            dialect="postgres", comments=False, unsupported_level=ErrorLevel.RAISE
        )
    except QueryRejected:
        raise
    except Exception as exc:
        raise QueryRejected("Query uses unsupported SQL syntax") from exc
