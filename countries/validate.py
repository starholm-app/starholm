"""Check country card records against schema.json, with no outside packages.

    python3 -I countries/validate.py [--date YYYY-MM-DD]

It reads schema.json and every <iso2>.json beside it. It checks the schema keywords the schema uses (type, required,
properties, additionalProperties, items, minItems, maxItems, minLength, maxLength, pattern, $ref), then these rules the
schema cannot say: the file name matches the code, every fact's source ids exist, every source is used, no more than
five wallet numbers, and 'checked' on the record equals the latest source date. With --date, every source must have
been checked on that day. Exit status 1 when anything fails.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _types(value):
    out = set()
    if value is None:
        out.add("null")
    elif isinstance(value, bool):
        out.add("boolean")
    elif isinstance(value, str):
        out.add("string")
    elif isinstance(value, int):
        out |= {"integer", "number"}
    elif isinstance(value, float):
        out.add("number")
    elif isinstance(value, list):
        out.add("array")
    elif isinstance(value, dict):
        out.add("object")
    return out


def check(value, schema, root, path, errors):
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        check(value, node, root, path, errors)
        return
    want = schema.get("type")
    if want is not None:
        want = want if isinstance(want, list) else [want]
        if not (_types(value) & set(want)):
            errors.append(f"{path}: expected {'/'.join(want)}, got {type(value).__name__}")
            return
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if len(value) > schema.get("maxLength", 10**9):
            errors.append(f"{path}: longer than {schema['maxLength']} ({len(value)})")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if len(value) > schema.get("maxItems", 10**9):
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                check(item, schema["items"], root, f"{path}[{i}]", errors)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                check(item, props[key], root, f"{path}.{key}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected field {key}")


def facts(record):
    for i, item in enumerate(record.get("call", [])):
        yield f"call[{i}]", item
    extra = record.get("extra", {})
    for key in ("call_notes", "alerts", "first_steps", "authorities", "hazards"):
        for i, item in enumerate(extra.get(key, [])):
            yield f"extra.{key}[{i}]", item
    if "climate" in extra:
        yield "extra.climate", extra["climate"]


def rules(record, filename, date, errors):
    code = record.get("code", "")
    if filename != code.lower() + ".json":
        errors.append(f"file name {filename} does not match code {code}")
    ids = [s.get("id") for s in record.get("sources", [])]
    if len(ids) != len(set(ids)):
        errors.append("duplicate source ids")
    used = set()
    for where, item in facts(record):
        for sid in item.get("sources", []):
            used.add(sid)
            if sid not in ids:
                errors.append(f"{where}: unknown source {sid}")
    for sid in ids:
        if sid not in used:
            errors.append(f"source {sid} is not used by any fact")
    if sum(1 for n in record.get("call", []) if n.get("wallet")) > 5:
        errors.append("more than five wallet numbers")
    dates = [s.get("checked", "") for s in record.get("sources", [])]
    if dates and record.get("checked") != max(dates):
        errors.append(f"checked {record.get('checked')} is not the latest source date {max(dates)}")
    if date:
        for s in record.get("sources", []):
            if s.get("checked") != date:
                errors.append(f"source {s.get('id')} checked {s.get('checked')}, not {date}")


def main(argv):
    date = argv[argv.index("--date") + 1] if "--date" in argv else None
    with open(os.path.join(HERE, "schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    failed = False
    for name in sorted(os.listdir(HERE)):
        if not re.fullmatch(r"[a-z]{2}\.json", name):
            continue
        with open(os.path.join(HERE, name), encoding="utf-8") as f:
            record = json.load(f)
        errors = []
        check(record, schema, schema, "$", errors)
        if not errors:
            rules(record, name, date, errors)
        print(("FAIL " if errors else "ok   ") + name)
        for e in errors:
            print("     " + e)
        failed = failed or bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
