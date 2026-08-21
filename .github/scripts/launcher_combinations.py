#!/usr/bin/env python3
"""Enumerates every launcher this repository publishes and every selection of
its component axes, then classifies each selection for the CI runner.

Two modes:

  enumerate    Reads `peppy_repository.json5` and every launcher it lists,
               and prints the inventory as tab-separated lines: one `launcher`
               line per launcher (its path and every fragment file it
               references, which is what the workflow's diff scoping matches
               a pull request's changed files against), and one `combo` line
               per selection of the component axes, enumerating exactly the
               way peppy's own repository check does: every option of every
               axis in declaration order, plus an unfilled entry last on each
               optional axis. A launcher without `components` yields a single
               empty-selection combo; a launcher declaring `core_nodes` marks
               its combos `local`, since a CI launch wires every declared
               link to the one daemon it owns.

  plan         Reads the selected combos, runs `peppy stack resolve` on each
               launcher file with the selection's `--with` words, and reads
               the verdict. A resolution that succeeds names the nodes the
               selection deploys; one that deploys a node listed in the skips
               file (hardware this machine has none of) is planned as
               skipped, reason quoted. A resolution the launcher's own
               constraints refuse is planned as refused — the constraint
               system working as designed, not a failure. Anything else is a
               launcher this repository cannot even flatten, and fails this
               script after printing the resolution error verbatim. Every
               launchable combination is written to the matrix file as one
               JSON object — the label the launch job is named by, the
               launcher and `--with` words to launch it with, whether it
               needs `--local`, and a disk-key slug — which the workflow
               feeds to its launch job's matrix, one job per combination.

The launcher files are JSON5 (comments, unquoted keys, trailing commas), read
by the small parser below rather than a dependency: the runner's Python ships
no json5 module, and what this parser cannot read it names and rejects, so a
launcher written in syntax it does not understand is a red run with a file and
a line, never a silently mis-read launcher.
"""

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys

# peppy's own cross-combination check refuses to enumerate a selection space
# larger than this (daemon-config-internal, COMBINATION_CEILING); the CI holds
# itself to the same ceiling so a launcher peppy's check escalates is escalated
# here too rather than launching combinations for hours.
COMBINATION_CEILING = 512

# Both constraint refusals (`... requires ..., which this selection (...) does
# not satisfy` and `... forbids ..., which this selection (...) has`) carry this
# phrase; no other resolution error does. It is what separates a combination
# refused by design from a launcher that is broken.
CONSTRAINT_REFUSAL_MARK = "which this selection"


# ---------------------------------------------------------------------------
# A JSON5-subset reader: objects, arrays, strings, numbers, the three literals,
# // and /* */ comments, unquoted keys, trailing commas. Nothing else.
# ---------------------------------------------------------------------------

_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_ESCAPES = {
    '"': '"',
    "'": "'",
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class Json5Error(ValueError):
    """The file is not JSON5 this reader understands, with file and line."""


class _Parser:
    def __init__(self, text, label):
        self.text = text
        self.label = label
        self.pos = 0

    def fail(self, message):
        line = self.text.count("\n", 0, self.pos) + 1
        raise Json5Error(f"{self.label}:{line}: {message}")

    def peek(self):
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def expect(self, char):
        if self.peek() != char:
            self.fail(f"expected {char!r}, found {self.peek()!r}")
        self.pos += 1

    def skip_ws(self):
        while True:
            while self.peek() in (" ", "\t", "\r", "\n"):
                self.pos += 1
            if self.text.startswith("//", self.pos):
                newline = self.text.find("\n", self.pos)
                self.pos = len(self.text) if newline < 0 else newline + 1
            elif self.text.startswith("/*", self.pos):
                end = self.text.find("*/", self.pos + 2)
                if end < 0:
                    self.fail("unterminated block comment")
                self.pos = end + 2
            else:
                return

    def parse_document(self):
        self.skip_ws()
        value = self.parse_value()
        self.skip_ws()
        if self.pos != len(self.text):
            self.fail(f"trailing content after the document: {self.peek()!r}")
        return value

    def parse_value(self):
        self.skip_ws()
        char = self.peek()
        if char == "{":
            return self.parse_object()
        if char == "[":
            return self.parse_array()
        if char in ('"', "'"):
            return self.parse_string()
        match = _IDENT.match(self.text, self.pos)
        if match:
            word = match.group(0)
            self.pos = match.end()
            if word in ("true", "false", "null"):
                return {"true": True, "false": False, "null": None}[word]
            self.fail(f"bare word {word!r} is not a value")
        match = _NUMBER.match(self.text, self.pos)
        if match and char:
            self.pos = match.end()
            number = match.group(0)
            return float(number) if any(c in number for c in ".eE") else int(number)
        self.fail(f"expected a value, found {char!r}")

    def parse_object(self):
        self.expect("{")
        obj = {}
        while True:
            self.skip_ws()
            if self.peek() == "}":
                self.pos += 1
                return obj
            match = _IDENT.match(self.text, self.pos)
            if self.peek() in ('"', "'"):
                key = self.parse_string()
            elif match:
                key = match.group(0)
                self.pos = match.end()
            else:
                self.fail(f"expected a key, found {self.peek()!r}")
            self.skip_ws()
            self.expect(":")
            obj[key] = self.parse_value()
            self.skip_ws()
            if self.peek() == ",":
                self.pos += 1
            elif self.peek() != "}":
                self.fail(f"expected ',' or '}}', found {self.peek()!r}")

    def parse_array(self):
        self.expect("[")
        arr = []
        while True:
            self.skip_ws()
            if self.peek() == "]":
                self.pos += 1
                return arr
            arr.append(self.parse_value())
            self.skip_ws()
            if self.peek() == ",":
                self.pos += 1
            elif self.peek() != "]":
                self.fail(f"expected ',' or ']', found {self.peek()!r}")

    def parse_string(self):
        quote = self.peek()
        self.pos += 1
        out = []
        while True:
            if self.pos >= len(self.text):
                self.fail("unterminated string")
            char = self.text[self.pos]
            if char == quote:
                self.pos += 1
                return "".join(out)
            if char == "\n":
                self.fail("unterminated string (newline before the closing quote)")
            if char == "\\":
                self.pos += 1
                escape = self.peek()
                if escape == "u":
                    hex_digits = self.text[self.pos + 1 : self.pos + 5]
                    if len(hex_digits) != 4 or not all(
                        c in "0123456789abcdefABCDEF" for c in hex_digits
                    ):
                        self.fail("malformed \\u escape")
                    out.append(chr(int(hex_digits, 16)))
                    self.pos += 5
                elif escape in _ESCAPES:
                    out.append(_ESCAPES[escape])
                    self.pos += 1
                else:
                    self.fail(f"unknown escape \\{escape}")
            else:
                out.append(char)
                self.pos += 1


def load_json5(path, label):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        raise Json5Error(f"{label}: cannot read: {error}") from error
    return _Parser(text, label).parse_document()


# ---------------------------------------------------------------------------
# enumerate
# ---------------------------------------------------------------------------


def fragment_references(document):
    """Every `.json5` path string anywhere in the launcher document.

    An option's body names its fragments by path, inline or as a list, and
    nothing else in the document names files; collecting every string that
    ends in `.json5` finds them wherever the schema puts them, so a fragment
    moved to a new key is still scoped by the launchers that reference it.
    """
    if isinstance(document, str):
        return [document] if document.endswith(".json5") else []
    if isinstance(document, list):
        return [path for item in document for path in fragment_references(item)]
    if isinstance(document, dict):
        return [path for item in document.values() for path in fragment_references(item)]
    return []


def axis_selections(axes):
    """Every selection of the axes, peppy's order: options in declaration
    order, an unfilled entry last on optional axes."""
    selections = [[]]
    for name, optional, options in axes:
        extended = []
        for selection in selections:
            for option in options:
                extended.append(selection + [(name, option)])
            if optional:
                extended.append(selection + [(name, None)])
        selections = extended
    return selections


def read_launcher(root, path):
    """The axes, fragment references, and core-node placement of one launcher."""
    document = load_json5(os.path.join(root, path), path)
    if not isinstance(document, dict):
        raise Json5Error(f"{path}: a launcher document is an object")
    components = document.get("components", [])
    if not isinstance(components, list):
        raise Json5Error(f"{path}: `components` is a list")
    axes = []
    for component in components:
        if not isinstance(component, dict) or "name" not in component:
            raise Json5Error(f"{path}: a component of `components` has no `name`")
        options = component.get("options", {})
        if not isinstance(options, dict) or not options:
            raise Json5Error(
                f"{path}: component `{component['name']}` declares no options"
            )
        axes.append(
            (component["name"], bool(component.get("optional", False)), list(options))
        )
    references = fragment_references(document)
    # A fragment path is relative to the launcher's own directory (the same
    # rule peppy's flatten enforces), so the diff scoping compares it against
    # repository-relative changed files.
    launcher_dir = posixpath.dirname(path)
    references = sorted(
        {posixpath.normpath(posixpath.join(launcher_dir, ref)) for ref in references}
    )
    declares_core_nodes = bool(document.get("core_nodes"))
    return axes, references, declares_core_nodes


def command_enumerate(root):
    index = load_json5(
        os.path.join(root, "peppy_repository.json5"), "peppy_repository.json5"
    )
    launchers = index.get("launchers") if isinstance(index, dict) else None
    if not isinstance(launchers, dict) or not launchers:
        raise Json5Error("peppy_repository.json5: lists no `launchers`")
    for name, entry in launchers.items():
        if not isinstance(entry, dict) or "path" not in entry:
            raise Json5Error(f"peppy_repository.json5: launcher `{name}` has no `path`")
        path = entry["path"]
        axes, references, declares_core_nodes = read_launcher(root, path)
        selections = axis_selections(axes)
        if len(selections) > COMBINATION_CEILING:
            raise Json5Error(
                f"{path}: the selection space has {len(selections)} combinations, "
                f"more than the {COMBINATION_CEILING} this check enumerates"
            )
        print(f"launcher\t{name}\t{path}\t{','.join(references)}")
        for selection in selections:
            words = [f"{axis}={option}" for axis, option in selection if option]
            placement = "local" if declares_core_nodes else "-"
            print(f"combo\t{name}\t{','.join(words)}\t{placement}")


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def sanitize(text, limit=300):
    """One line, no tabs, no pipes (it lands in a markdown table), bounded."""
    line = re.sub(r"^(\[ERROR\] )?Error: ", "", text.strip())
    line = re.sub(r"[\t\r\n]+", " ", line).strip()
    if len(line) > limit:
        line = line[: limit - 3] + "..."
    return line.replace("|", "\\|")


def deployed_nodes(resolved):
    """The node names a flattened launcher deploys."""
    nodes = set()
    for deployment in resolved.get("deployments", []):
        source = deployment.get("source", {}) if isinstance(deployment, dict) else {}
        if isinstance(source, dict) and "name" in source:
            nodes.add(source["name"])
    return nodes


def combination_label(name, words, placement):
    """The launch job's display name: the launcher, its selection, its placement."""
    label = f"{name} ({words})" if words else name
    if placement == "local":
        label += " [--local]"
    return label


def disk_key(name, words, placement):
    """A slug naming this combination: the per-combination sticky-disk key.

    Keyed on the combination itself rather than its position in the list, so
    adding a launcher shifts nobody's disk and a warm run stays warm.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{name} {words} {placement}").strip("-")
    return slug


def command_plan(root, combos_path, skips_path, matrix_path):
    skips = {}
    for entry in load_json5(skips_path, skips_path) or []:
        if not isinstance(entry, dict) or "node" not in entry:
            raise Json5Error(f"{skips_path}: an entry has no `node`")
        skips[entry["node"]] = entry.get("reason", "no reason given")

    inventory = load_json5(os.path.join(root, "peppy_repository.json5"),
                           "peppy_repository.json5")
    paths = {
        name: entry["path"]
        for name, entry in inventory["launchers"].items()
    }

    planned = []
    matrix = []
    with open(combos_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            fields = line.split("\t")
            if len(fields) != 4 or fields[0] != "combo" or fields[1] not in paths:
                raise SystemExit(f"{combos_path}: not a combo line of this repository: {line!r}")
            _, name, words, placement = fields
            argv = ["peppy", "stack", "resolve", paths[name]]
            if words:
                argv += ["--with", words]
            resolve = subprocess.run(argv, capture_output=True, text=True, cwd=root)
            if resolve.returncode == 0:
                nodes = deployed_nodes(_Parser(resolve.stdout, "resolved").parse_document())
                hits = [(node, skips[node]) for node in sorted(nodes) if node in skips]
                if hits:
                    detail = "; ".join(f"deploys {node} — {reason}" for node, reason in hits)
                    planned.append((name, words, placement, "skipped", detail))
                else:
                    planned.append((name, words, placement, "launch", "-"))
                    matrix.append(
                        {
                            "label": combination_label(name, words, placement),
                            "launcher": name,
                            "words": words,
                            "local": placement == "local",
                            "key": disk_key(name, words, placement),
                        }
                    )
            else:
                output = (resolve.stderr + resolve.stdout).strip()
                if CONSTRAINT_REFUSAL_MARK in output:
                    planned.append((name, words, placement, "refused", sanitize(output)))
                else:
                    print(output, file=sys.stderr)
                    raise SystemExit(
                        f"{paths[name]} with `{words or 'defaults'}` does not resolve "
                        "and the launcher's constraints do not refuse it either"
                    )

    with open(matrix_path, "w", encoding="utf-8") as handle:
        json.dump(matrix, handle, separators=(",", ":"))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n### Launcher combinations\n\n")
            handle.write("| launcher | selection | verdict | detail |\n")
            handle.write("| --- | --- | --- | --- |\n")
            for name, words, _, verdict, detail in planned:
                handle.write(
                    f"| {name} | {words or 'defaults'} | {verdict} | {detail} |\n"
                )

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            launchable = any(verdict == "launch" for _, _, _, verdict, _ in planned)
            handle.write(f"launchable={'true' if launchable else 'false'}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command", required=True)

    enumerate_parser = subcommands.add_parser(
        "enumerate", help="print the launcher and combination inventory"
    )
    enumerate_parser.add_argument("--root", default=".")

    plan_parser = subcommands.add_parser(
        "plan", help="classify selected combinations for this machine"
    )
    plan_parser.add_argument("--root", default=".")
    plan_parser.add_argument("--combos", required=True)
    plan_parser.add_argument("--skips", required=True)
    plan_parser.add_argument("--matrix", required=True)

    args = parser.parse_args()
    try:
        if args.command == "enumerate":
            command_enumerate(args.root)
        else:
            command_plan(args.root, args.combos, args.skips, args.matrix)
    except Json5Error as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
