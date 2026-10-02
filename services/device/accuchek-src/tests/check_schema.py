#!/usr/bin/env python3
"""Replay every tests/fixtures/*.trace and validate stdout against the schema.

A trace may carry a "# args: ..." comment with the extra arguments it must be
replayed with (for example --set-time --now "..."). Needs `jsonschema`
(pip install jsonschema). Run from the repository root: make schema-check.
"""
import glob
import json
import os
import shlex
import subprocess
import sys

try:
    import jsonschema
except ImportError:
    sys.exit("check_schema: jsonschema missing, run: pip install jsonschema")

BIN = os.environ.get("ACCUCHEK_BIN", "./accuchek")


def replay_args(path):
    with open(path) as f:
        for line in f:
            if line.startswith("# args:"):
                return shlex.split(line[len("# args:"):])
    return []


def main():
    with open("schema/output.schema.json") as f:
        schema = json.load(f)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    traces = sorted(glob.glob("tests/fixtures/*.trace"))
    if not traces:
        sys.exit("check_schema: no trace in tests/fixtures")
    failures = 0
    readings = 0
    for path in traces:
        run = subprocess.run([BIN, "--replay", path] + replay_args(path),
                             capture_output=True, text=True)
        problems = []
        if run.returncode != 0:
            problems.append(f"exit code {run.returncode}: {run.stderr.strip()}")
        else:
            try:
                output = json.loads(run.stdout)
            except json.JSONDecodeError as e:
                problems.append(f"invalid JSON: {e}")
            else:
                problems += [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                             for e in validator.iter_errors(output)]
                readings += len(output.get("readings", []))
        for p in problems:
            print(f"FAIL {path}: {p}")
        failures += bool(problems)
    print(f"check_schema: {len(traces) - failures}/{len(traces)} traces valid, {readings} readings")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
