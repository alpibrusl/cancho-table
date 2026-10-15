#!/usr/bin/env python3
"""Write schemas/table.v2.json, the JSON Schema (Draft 2020-12) of `table`: the
shape of a file, or a page of selected columns (v1, of the shape alone, is retired).

Adapted from cancho-tools' scripts/schemas.py: the parts every tool shares --
the envelope, the error object, the repair kinds, `text_or_bytes` -- are
written here once, and the file is self-contained because it is embedded in
the binary by scripts/manifest.py and printed by `table introspect`.

    python3 scripts/schemas.py           # write
    python3 scripts/schemas.py --check   # exit 1 if a committed file differs
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIALECT = "https://json-schema.org/draft/2020-12/schema"

NAT = {"type": "integer", "minimum": 0}
CODES = ["GENERAL_ERROR", "INVALID_ARGS", "NOT_FOUND", "PERMISSION_DENIED",
         "CONFLICT", "PRECONDITION_FAILED"]


def obj(props, required=None):
    return {
        "type": "object",
        "properties": props,
        "required": list(props) if required is None else required,
        "additionalProperties": False,
    }


def common_defs():
    return {
        "text_or_bytes": {
            "description": "UTF-8 text as a string; other bytes as base64 (RFC 4648 section 4, padded).",
            "oneOf": [
                {"type": "string"},
                obj({"b64": {"type": "string", "pattern": "^[A-Za-z0-9+/]*={0,2}$"}}),
            ],
        },
        "repair": {
            "oneOf": [
                {"type": "null"},
                obj({"kind": {"const": "retry"},
                     "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1}}),
                obj({"kind": {"const": "none"}, "reason": {"type": "string"}}),
                obj({"kind": {"const": "choose"},
                     "options": {"type": "array", "items": obj(
                         {"argv": {"type": "array", "items": {"type": "string"}}})}}),
            ]
        },
        "error": obj({
            "code": {"enum": CODES},
            "rule": {"type": "string", "pattern": "^[a-z]+\\.[a-z0-9-]+$"},
            "message": {"type": "string"},
            "hint": {"type": ["string", "null"]},
            "repair": {"$ref": "#/$defs/repair"},
            "detail": {"type": "object"},
        }),
        "meta": obj({"version": {"type": "string"}}),
    }


def document(tool, data):
    props = {
        "ok": {"type": "boolean"},
        "command": {"const": tool},
        "schema": {"const": tool + ".v2"},
        "data": {"$ref": "#/$defs/data"},
        "error": {"$ref": "#/$defs/error"},
        "errors": {"type": "array", "items": {"$ref": "#/$defs/error"}, "minItems": 1},
        "meta": {"$ref": "#/$defs/meta"},
    }
    defs = common_defs()
    defs["data"] = data
    return {
        "$schema": DIALECT,
        "$id": "https://github.com/alpibrusl/cancho-table/schemas/%s.v2.json" % tool,
        "title": "%s.v2" % tool,
        "description": "One JSON object on one line. ok is false exactly when errors is present; error is its first element. data is absent when the read stopped before the end of the input.",
        "type": "object",
        "properties": props,
        "required": ["ok", "command", "schema", "meta"],
        "additionalProperties": False,
        "allOf": [
            {"if": {"properties": {"ok": {"const": True}}},
             "then": {"not": {"anyOf": [{"required": ["error"]}, {"required": ["errors"]}]}},
             "else": {"required": ["error", "errors"]}},
        ],
        "$defs": defs,
    }


TB = {"$ref": "#/$defs/text_or_bytes"}

NULLABLE_NAT = {"oneOf": [{"type": "null"}, NAT]}
RULE = {"type": "string", "pattern": "^[a-z]+\\.[a-z0-9-]+$"}


def explain_document():
    """`--explain` (docs/query.md section 15): the plan, said and not run. Every key is always present (a value that does not apply is null), except `rows_held` (only for
    sorted rows) and the bounds, which are the ones in force."""
    column = obj({"ref": TB,
                  "position": {"oneOf": [{"type": "null"}, {"type": "integer", "minimum": 1}]},
                  "name": {"oneOf": [{"type": "null"}, TB]}})
    type_ = {"type": "string", "pattern": "^(text|int|float|dec\\((0|[1-9]|1[0-8])\\))$"}
    return obj({"explain": obj({
        "source": obj({
            "kind": {"enum": ["file", "stdin"]},
            "path": TB,
            "reader": {"enum": ["file", "blocks"]},
            "block_bytes": NAT,
            "format": {"enum": ["csv", "jsonl"]},
            "delimiter": {"enum": [",", "\t", ";", None]},
            # JSON lines only (docs/readers.md section 12): the declared columns, what an absent path is, the tape's nodes
            "fields": {"type": "array", "items": TB},
            "absent": {"enum": ["refuse", "empty"]},
            "max_json_nodes": {"type": "integer", "minimum": 1},
            "size": NULLABLE_NAT,
            "header_read": {"type": "boolean"},
            "columns": NULLABLE_NAT,
            "bytes_read": NAT,
        }, required=["kind", "path", "reader", "block_bytes", "format", "delimiter", "size", "header_read", "columns", "bytes_read"]),
        "plan": obj({
            "form": {"enum": ["shape", "rows", "groups", "report"]},
            "select": {"type": "array", "items": column},
            "where": {"type": "array", "items": obj({
                "column": column,
                "type": type_,
                "op": {"enum": ["=", "!=", "<", "<=", ">", ">=", "contains", "in"]},
                "values": {"type": "array", "items": TB, "minItems": 1},
            })},
            "group": {"type": "array", "items": obj({"column": column, "type": type_})},
            "aggregates": {"type": "array", "items": obj({
                "function": {"enum": ["count", "sum", "mean", "min", "max", "distinct"]},
                "column": {"oneOf": [{"type": "null"}, column]},
                "type": {"oneOf": [{"type": "null"}, type_]},
                "scale": NULLABLE_NAT,
            })},
            "order_by": {"type": "array", "items": obj({"column": column, "type": type_, "descending": {"type": "boolean"}})},
            "sort": {"oneOf": [{"type": "null"}, TB]},
            "top": NULLABLE_NAT,
            "limit": NULLABLE_NAT,
            "from": NAT,
        }),
        "output": obj({
            "format": {"enum": ["json", "csv", "text"]},
            "columns": {"oneOf": [{"type": "null"}, {"type": "array", "items": TB}]},
        }),
        "work": obj({
            "class": {"enum": ["shape", "type report", "grouped single pass", "sorted rows", "stream rows", "join: build then stream"]},
            "reads": {"enum": ["all rows", "until the page is full", "the whole build file, then the probe until the page is full", "the whole build file, then all the probe rows"]},
            "rows_held": NULLABLE_NAT,
            "bounds": obj({
                "max_rows": NAT, "max_line_bytes": NAT, "max_bytes": NAT, "max_groups": NAT, "max_distinct": NAT, "max_sort_rows": NAT, "max_state_bytes": NAT, "max_join_rows": NAT,
            }, required=["max_rows", "max_line_bytes"]),
            "threads": obj({"requested": {"type": "integer", "minimum": 1}, "effective": {"type": "integer", "minimum": 1}, "reason": {"oneOf": [{"type": "null"}, {"type": "string"}]}}),
        }, required=["class", "reads", "bounds", "threads"]),
        "refusals": obj({"possible": {"type": "array", "items": RULE}}),
        "notes": {"type": "array", "items": TB},
        # a join (docs/joins.md section 8.4): the sides, the key, the columns, what the build can hold, the bound and the work
        "join": obj({
            "type": {"enum": ["inner"]},
            "probe": TB,
            "build": TB,
            "role_choice": {"type": "string"},
            "keys": {"type": "array", "items": obj({"probe": TB, "build": TB, "type": {"enum": ["text"]},
                                                    "probe_position": {"oneOf": [{"type": "null"}, {"type": "integer", "minimum": 1}]},
                                                    "build_position": {"type": "integer", "minimum": 1}})},
            "output_columns": NULLABLE_NAT,
            "prefix": TB,
            "using": {"type": "array", "items": TB},
            "build_state": obj({"file_bytes": NULLABLE_NAT, "header_bytes": NAT, "accounted_at_most": NULLABLE_NAT, "budget": NAT, "may_pass_bound": {"type": "boolean"},
                                "counted": {"type": "string"}, "swap": obj({"offered": {"type": "boolean"}, "reason": {"type": "string"}})}),
            "max_join_rows": NAT,
            "order": {"type": "string"},
            "work": obj({"class": {"type": "string"}, "first_output_after": {"type": "string"},
                         "threads": obj({"requested": {"type": "integer", "minimum": 1}, "effective": {"type": "integer", "minimum": 1}, "reason": {"type": "string"}})}),
        }),
    }, required=["source", "plan", "output", "work", "refusals", "notes"])})


def discover_document():
    """`--discover` (docs/readers.md section 12.7): what the first lines of a JSON-lines input hold. Never a plan."""
    kinds = obj({k: {"type": "integer", "minimum": 1} for k in ["string", "int", "number", "bool", "null", "object", "array", "missing"]}, required=[])
    return obj({"discover": obj({
        "fields": {"type": "array", "items": obj({"path": TB, "kinds": kinds, "first_line": {"type": "integer", "minimum": 1}})},
        "lines_examined": NAT,
        "complete": {"type": "boolean"},
        "paths_truncated": {"type": "boolean"},
        "undeclarable": NAT,
        "arrays": {"type": "array", "items": TB},
    })})


SCHEMAS = {
    "table": document("table", {"oneOf": [
        # `table FILE`: the shape.
        obj({
            "headers": {"type": "array", "items": TB},
            "column_count": NAT,
            "row_count": NAT,
            "truncated": {"type": "boolean"},
        }),
        # `--select`, `--where` or `--group`: a page of rows, each the fields in the order
        # of `columns`; `next` resumes with `--from`; `group_count` is the number of
        # groups of a grouping, before `--top` and the page.
        obj({
            "group_count": NAT,
            "columns": {"type": "array", "items": TB},
            "rows": {"type": "array", "items": {"type": "array", "items": TB}},
            "row_count": NAT,
            "truncated": {"type": "boolean"},
            "next": {"oneOf": [{"type": "null"}, obj({"from": NAT})]},
        }, required=["columns", "rows", "row_count", "truncated", "next"]),
        # `--explain`: the plan, not its answer.
        explain_document(),
        # `--input-format jsonl --discover`: the report.
        discover_document(),
    ]}),
}


def render(schema):
    return json.dumps(schema, indent=2, sort_keys=False) + "\n"


def main():
    check = "--check" in sys.argv[1:]
    out = ROOT / "schemas"
    out.mkdir(exist_ok=True)
    stale = []
    for tool, schema in SCHEMAS.items():
        path = out / ("%s.v2.json" % tool)
        text = render(schema)
        if check:
            if not path.exists() or path.read_text() != text:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(text)
    if stale:
        print("stale schemas (run scripts/schemas.py): " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
