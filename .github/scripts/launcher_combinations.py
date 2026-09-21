#!/usr/bin/env python3
"""Enumerate, scope, plan and launch the repository's launcher combinations.

enumerate prints launcher paths and fragment references, followed by
six-column combo records: kind, launcher, launch words, joined option, join
words, placement. It covers every state of every axis the launcher and its
fragments declare, every copy a `zero_or_more` axis can add with
`stack join`, plain and under every selection of its own axes, and every
state of the axes of each copy the file deploys, written as the
`NAME.axis=option` launch words that select them. A declared core_nodes list
requests local placement in CI.

scope decides what a change can reach from the files it touches: every
combination, the combinations whose resolved plan the change moves, or
nothing.

plan previews every combination through peppy stack resolve, its join
included. Constraints classify refused combinations. The skip file
classifies unavailable hardware and rollout dependencies. A joined copy
comes up beside the copies the file deploys, except where the stack deploys
a node the single-robot file names: there the file's copies make way for it
first, and a join onto a stack that links to one of them is reported, since
that copy cannot make way. Of the launchable combinations in scope it keeps
the launches that, between them, run every configured node instance and
every pair of instances that run side by side.

launch runs the planned launches one after the other on the running daemon,
resetting the stack between them, holds each joined copy to the instances
its preview promised, and reports each one's outcome.

The JSON5 subset reader reports unsupported syntax with its file and line.
"""

import argparse
import enum
import itertools
import json
import os
import posixpath
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field

# peppy's own cross-combination check refuses to enumerate a selection space
# larger than this (daemon-config-internal, COMBINATION_CEILING); the CI holds
# itself to the same ceiling so a launcher peppy's check escalates is escalated
# here too rather than launching combinations for hours.
COMBINATION_CEILING = 2048

# Both constraint refusals (`... requires ..., which this selection (...) does
# not satisfy` and `... forbids ..., which this selection (...) has`) carry this
# phrase; no other resolution error does. It is what separates a combination
# refused by design from a launcher that is broken.
CONSTRAINT_REFUSAL_MARK = "which this selection"

# A join refused because the copy writes a stack instance differently from
# how it runs (`joining NAME would change INSTANCE, which already runs`)
# carries this phrase. Such a copy runs only when the file deploys it at
# launch, which the repository check covers; it is not a broken launcher.
JOIN_CHANGE_MARK = "would change"

# The name every previewed and launched copy joins under in CI. A copy name is
# unique on the stack, and the simulation launchers deploy `alpha`.
COPY_NAME = "bravo"

CARDINALITIES = ("one", "zero_or_one", "zero_or_more")


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
# The launcher model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Axis:
    """One component axis, with the axes each of its options' fragments
    declare in turn."""

    name: str
    cardinality: str
    options: list[str]
    #: The option the document deploys on this axis, if it deploys one.
    deployed: str | None = None
    #: Per option, the axes its fragments declare.
    nested: dict[str, list["Axis"]] = field(default_factory=dict)

    @property
    def allows_unfilled(self):
        return self.cardinality != "one"

    @property
    def repeatable(self):
        """Runs as named copies: the file's `deployments`, then `stack join`."""
        return self.cardinality == "zero_or_more"


@dataclass(frozen=True)
class Copy:
    """A copy the file deploys: its name, the axis and option it runs, and
    the options its `with` selects on that option's own axes."""

    name: str
    axis: str
    option: str
    selected: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Launcher:
    """One launcher: its axes, the fragment files its options compose, the
    copies its file deploys, and its declared core-node placement."""

    axes: list[Axis]
    references: list[str]
    copies: list[Copy]
    declares_core_nodes: bool


def deployed_option(entry, label):
    """The `{ <axis>: "<option>" }` pair one `deployments` entry deploys, or
    None where the entry deploys a node under `source`."""
    if not isinstance(entry, dict):
        raise Json5Error(f"{label}: a `deployments` entry is an object")
    if "source" in entry:
        return None
    keys = [key for key in entry if key not in ("instances", "with", "arguments", "adjustments")]
    if len(keys) != 1 or not isinstance(entry[keys[0]], str):
        raise Json5Error(
            f"{label}: a `deployments` entry deploys a node under `source` or one "
            "component's option, `{ <component>: \"<option>\" }`"
        )
    return keys[0], entry[keys[0]]


def option_entries(document, label):
    """The `{ <axis>: "<option>" }` entries of a document's `deployments`,
    as an axis-to-option map, beside the node entries."""
    pairs = (deployed_option(entry, label) for entry in document.get("deployments", []))
    return dict(pair for pair in pairs if pair)


def deployed_copies(document, axes, label):
    """The copies a launcher's `deployments` run: one per `instances` entry
    of a repeatable axis, each carrying what its `with` selects on the axes
    of the option it runs, the entry's `with` under the copy's own."""
    repeatable = {axis.name for axis in axes if axis.repeatable}
    copies = []
    for entry in document.get("deployments", []):
        pair = deployed_option(entry, label)
        if pair is None or pair[0] not in repeatable:
            continue
        axis, option = pair
        instances = entry.get("instances")
        if not isinstance(instances, list) or not instances:
            raise Json5Error(
                f"{label}: axis `{axis}` runs as named copies: "
                f'`{{ {axis}: "{option}", instances: [{{ instance_id: "alpha" }}] }}`'
            )
        for instance in instances:
            name = instance.get("instance_id") if isinstance(instance, dict) else None
            if not isinstance(name, str):
                raise Json5Error(f"{label}: a copy of `{axis}` has no `instance_id`")
            selected = {**entry.get("with", {}), **instance.get("with", {})}
            copies.append(Copy(name, axis, option, selected))
    return copies


def read_axes(document, label, scope, read_option):
    """The axes a document declares, `read_option(axis, option, spec)`
    returning the axes the option's fragments declare."""
    components = document.get("components", [])
    if not isinstance(components, list):
        raise Json5Error(f"{label}: `components` is a list")
    deployed = option_entries(document, label)
    axes = []
    for component in components:
        if not isinstance(component, dict) or "name" not in component:
            raise Json5Error(f"{label}: a component of `components` has no `name`")
        name = component["name"]
        for refused in ("default", "optional", "components"):
            if refused in component:
                raise Json5Error(
                    f"{label}: component `{name}` declares `{refused}`; the option a document "
                    "starts with is a `deployments` entry, cardinality says how many may run, "
                    "and an option's fragment declares its own components"
                )
        options = component.get("options", {})
        if not isinstance(options, dict) or not options:
            raise Json5Error(f"{label}: component `{name}` declares no options")
        cardinality = component.get("cardinality", "one")
        if cardinality not in CARDINALITIES:
            raise Json5Error(f"{label}: use cardinality: one, zero_or_one, or zero_or_more")
        if scope == "fragment" and cardinality == "zero_or_more":
            raise Json5Error(
                f"{label}: component `{name}` declares zero_or_more inside a fragment; copies "
                "are the launcher's to deploy"
            )
        nested = {option: read_option(name, option, spec) for option, spec in options.items()}
        axes.append(Axis(name, cardinality, list(options), deployed.get(name), nested))
    for axis_name in deployed:
        if not any(axis.name == axis_name for axis in axes):
            raise Json5Error(f"{label}: `deployments` deploys `{axis_name}`, which is not a component")
    return axes


def fragment_parts(spec):
    """An option's fragment parts in order: paths and inline bodies."""
    parts = spec if isinstance(spec, list) else [spec]
    for part in parts:
        if not isinstance(part, (str, dict)):
            raise Json5Error("an option is a fragment path, an inline fragment, or a list of both")
    return parts


class Reader:
    """Reads a launcher and every fragment it composes, collecting the
    repository-relative path of each fragment file."""

    def __init__(self, root):
        self.root = root
        self.references = set()

    def launcher(self, path):
        document = load_json5(os.path.join(self.root, path), path)
        if not isinstance(document, dict):
            raise Json5Error(f"{path}: a launcher document is an object")
        directory = posixpath.dirname(path)

        def read_option(axis, option, spec):
            return self.option_axes(spec, directory, f"{path} option {axis}.{option}", depth=1)

        axes = read_axes(document, path, "launcher", read_option)
        return Launcher(
            axes,
            sorted(self.references),
            deployed_copies(document, axes, path),
            bool(document.get("core_nodes")),
        )

    def option_axes(self, spec, directory, label, depth):
        """The axes an option's fragments declare, their own options read
        one level down; a fragment two levels down declares none."""
        axes = []
        for part in fragment_parts(spec):
            if isinstance(part, str):
                reference = posixpath.normpath(posixpath.join(directory, part))
                self.references.add(reference)
                body = load_json5(os.path.join(self.root, reference), reference)
                body_directory = posixpath.dirname(reference)
                body_label = reference
            else:
                body, body_directory, body_label = part, directory, f"inline fragment of {label}"
            if not isinstance(body, dict):
                raise Json5Error(f"{body_label}: a fragment document is an object")
            if depth > 1:
                if body.get("components"):
                    raise Json5Error(
                        f"{body_label}: a fragment two levels below the launcher declares no components"
                    )
                continue

            def read_option(axis, option, nested_spec):
                return self.option_axes(nested_spec, body_directory, f"{body_label} option {axis}.{option}", depth + 1)

            axes.extend(read_axes(body, body_label, "fragment", read_option))
        return axes


def read_launcher(root, path):
    """One launcher and every fragment it composes."""
    return Reader(root).launcher(path)


# ---------------------------------------------------------------------------
# enumerate
# ---------------------------------------------------------------------------


def axis_states(axis):
    """Every state of one axis, peppy's order: options in declaration
    order, unfilled last where the cardinality allows."""
    states = list(axis.options)
    if axis.allows_unfilled:
        states.append(None)
    return states


def is_fixed(axis):
    """A `one` axis with a single option, deployed, holds that option in
    every launch and is no choice to write out; its option's axes stay in
    reach."""
    return axis.deployed is not None and axis.cardinality == "one" and len(axis.options) == 1


def selections_of(axes, head=()):
    """Every combination of the states of `axes` and of the axes their
    selected options bring in reach, as lists of `(axis, option)`."""
    selections = [list(head)]
    for axis in axes:
        extended = []
        for selection in selections:
            for option in axis_states(axis):
                entry = selection + ([] if is_fixed(axis) else [(axis.name, option)])
                nested = axis.nested.get(option, []) if option else []
                extended.extend(selections_of(nested, entry))
        selections = extended
    return selections


@dataclass(frozen=True)
class Combination:
    """One launch: the words over the stack's axes and, joined afterwards,
    at most one copy."""

    words: list
    join_option: str | None = None
    join_words: list = field(default_factory=list)


def copy_selections(axes, copy):
    """Every selection of a deployed copy's own axes other than the one it
    already runs, as the `NAME.axis=option` launch words that select it."""
    own = next((axis.nested.get(copy.option, []) for axis in axes if axis.name == copy.axis), [])
    runs = {axis.name: copy.selected.get(axis.name, axis.deployed) for axis in own}
    return [
        [(f"{copy.name}.{axis}", option) for axis, option in selection]
        for selection in selections_of(own)
        if any(option != runs.get(axis) for axis, option in selection)
    ]


def join_selections(own):
    """Every join of one option, as the selections its join words write out.
    The plain join comes first: it writes no word, so the copy is whatever
    the launcher's entry for the option and the option's fragment give it,
    and it is the join an operator types. Every selection of the option's own
    axes that writes a word follows, in peppy's order. A selection leaving
    every axis unfilled writes none, so it is the plain join."""
    selections = selections_of(own)
    plain = [selection for selection in selections if not render_words(selection)] or [[]]
    return plain + [selection for selection in selections if render_words(selection)]


def launcher_selections(axes, copies=()):
    """Every launch of the launcher, peppy's order: each stack selection
    bare, then with each deployed copy's own axes selected by launch word,
    then with every copy a repeatable axis can add joined onto it."""
    stack = [axis for axis in axes if not axis.repeatable]
    deployed = [words for copy in copies for words in copy_selections(axes, copy)]
    joined = [
        (option, selection)
        for axis in axes
        if axis.repeatable
        for option in axis.options
        for selection in join_selections(axis.nested.get(option, []))
    ]
    combinations = []
    for selection in selections_of(stack):
        combinations.append(Combination(selection))
        for words in deployed:
            combinations.append(Combination(selection + words))
        for option, own in joined:
            combinations.append(Combination(selection, option, own))
    return combinations


def render_words(selection):
    return ",".join(f"{axis}={option}" for axis, option in selection if option)


# ---------------------------------------------------------------------------
# The inventory: every launcher and every combination it admits
# ---------------------------------------------------------------------------


def combination_label(name, words, join_option, join_words, local):
    """A combination's display name: the launcher, its selection, its join,
    its placement."""
    label = f"{name} ({words})" if words else name
    if join_option:
        label += f" + join {join_option}"
        if join_words:
            label += f" ({join_words})"
    if local:
        label += " [--local]"
    return label


@dataclass(frozen=True)
class Candidate:
    """One combination of one launcher, spelled the way `peppy` takes it:
    launch words, the option joined afterwards, the join's own words."""

    launcher: str
    path: str
    words: str
    join_option: str
    join_words: str
    #: The launcher declares `core_nodes`, which CI places on the one daemon.
    local: bool
    #: The copies the launcher's file deploys, which a join comes up beside
    #: or, on a stack that stands one robot, takes the place of.
    file_copies: tuple

    @property
    def key(self):
        return (self.launcher, self.words, self.join_option, self.join_words)

    @property
    def launch_key(self):
        """The combination `stack launch` alone brings up: this one without
        its join."""
        return (self.launcher, self.words, "", "")

    @property
    def label(self):
        return combination_label(
            self.launcher, self.words, self.join_option, self.join_words, self.local
        )


@dataclass(frozen=True)
class LauncherInventory:
    """One launcher of the repository index: its file, the fragment files
    its options compose, and every combination of its axes."""

    name: str
    path: str
    references: list
    candidates: list

    @property
    def files(self):
        return {self.path, *self.references}


def read_index(root):
    """The launchers `peppy_repository.json5` lists, name to path."""
    index = load_json5(os.path.join(root, "peppy_repository.json5"), "peppy_repository.json5")
    launchers = index.get("launchers") if isinstance(index, dict) else None
    if not isinstance(launchers, dict) or not launchers:
        raise Json5Error("peppy_repository.json5: lists no `launchers`")
    paths = {}
    for name, entry in launchers.items():
        if not isinstance(entry, dict) or "path" not in entry:
            raise Json5Error(f"peppy_repository.json5: launcher `{name}` has no `path`")
        paths[name] = entry["path"]
    return paths


def read_launcher_inventory(root, name, path):
    launcher = read_launcher(root, path)
    combinations = launcher_selections(launcher.axes, launcher.copies)
    if len(combinations) > COMBINATION_CEILING:
        raise Json5Error(
            f"{path}: the selection space has {len(combinations)} combinations, "
            f"more than the {COMBINATION_CEILING} this check enumerates"
        )
    file_copies = tuple(copy.name for copy in launcher.copies)
    candidates = [
        Candidate(
            name,
            path,
            render_words(combination.words),
            combination.join_option or "",
            render_words(combination.join_words),
            launcher.declares_core_nodes,
            file_copies,
        )
        for combination in combinations
    ]
    return LauncherInventory(name, path, launcher.references, candidates)


def read_inventory(root):
    """Every launcher the index lists, in its order."""
    return [
        read_launcher_inventory(root, name, path) for name, path in read_index(root).items()
    ]


def read_base_inventory(root):
    """The inventory of the tree a pull request branched from. That tree is
    read as a reference, not as the thing under test: a launcher of it this
    reader refuses is left out, so every combination of the launcher's
    current file counts as changed."""
    try:
        index = read_index(root)
    except Json5Error:
        return []
    inventory = []
    for name, path in index.items():
        try:
            inventory.append(read_launcher_inventory(root, name, path))
        except Json5Error:
            continue
    return inventory


def command_enumerate(root):
    for launcher in read_inventory(root):
        print(f"launcher\t{launcher.name}\t{launcher.path}\t{','.join(launcher.references)}")
        for candidate in launcher.candidates:
            print(
                "combo\t{}\t{}\t{}\t{}\t{}".format(
                    candidate.launcher,
                    candidate.words,
                    candidate.join_option,
                    candidate.join_words,
                    "local" if candidate.local else "-",
                )
            )


# ---------------------------------------------------------------------------
# scope: what a change can reach
# ---------------------------------------------------------------------------


class ScopeKind(enum.Enum):
    #: Every combination of every launcher.
    EVERYTHING = "everything"
    #: The combinations whose resolved plan differs from the base tree's.
    CHANGED = "changed"
    #: No combination: the change touches documentation alone.
    NOTHING = "nothing"


@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    reason: str


def classify_scope(changed_files, launcher_files):
    """What a run covers, from the files a pull request changes (`None`
    outside a pull request) and the files the launchers are made of, in the
    pull request's tree and in its base.

    A launcher's whole behaviour is the plan its combinations resolve to, so
    a change confined to launcher files reaches exactly the combinations
    whose plan it moves. Nothing narrower can be proven safe for a file
    outside every launcher, so it selects every combination, except a
    Markdown file, which nothing launches.
    """
    if changed_files is None:
        return Scope(ScopeKind.EVERYTHING, "no pull request diff narrows this run")
    if not changed_files:
        return Scope(ScopeKind.EVERYTHING, "the pull request's diff names no file")
    outside = [file for file in changed_files if file not in launcher_files]
    unknown = [file for file in outside if not file.lower().endswith(".md")]
    if unknown:
        return Scope(ScopeKind.EVERYTHING, f"`{unknown[0]}` is outside every launcher")
    if len(outside) == len(changed_files):
        return Scope(
            ScopeKind.NOTHING,
            "this pull request changes only documentation outside every launcher",
        )
    return Scope(
        ScopeKind.CHANGED,
        "this pull request changes launcher files alone, so only the combinations "
        "whose resolved plan differs from the base tree's can behave differently",
    )


def read_changed_files(path):
    if path is None:
        return None
    with open(path, encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle if line.strip()]


def write_scope(scope, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"kind": scope.kind.value, "reason": scope.reason}, handle)


def read_scope(path):
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    return Scope(ScopeKind(document["kind"]), document["reason"])


SCOPE_HEADLINES = {
    ScopeKind.EVERYTHING: "Running every combination",
    ScopeKind.CHANGED: "Running the combinations this pull request changes",
    ScopeKind.NOTHING: "Running nothing",
}


def command_scope(root, changed_files_path, base_root, scope_path):
    launcher_files = set()
    for launcher in read_inventory(root):
        launcher_files |= launcher.files
    if base_root is not None:
        for launcher in read_base_inventory(base_root):
            launcher_files |= launcher.files
    scope = classify_scope(read_changed_files(changed_files_path), launcher_files)
    write_scope(scope, scope_path)

    headline = f"{SCOPE_HEADLINES[scope.kind]}: {scope.reason}."
    print(headline)
    append_to_env_file("GITHUB_STEP_SUMMARY", f"\n{headline}\n")
    planned = "false" if scope.kind is ScopeKind.NOTHING else "true"
    append_to_env_file("GITHUB_OUTPUT", f"any={planned}\n")


def append_to_env_file(variable, text):
    """Appends to the file a GitHub Actions variable names, where there is
    one: the step summary, the step outputs."""
    path = os.environ.get(variable)
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


# ---------------------------------------------------------------------------
# resolve: what each combination would run
# ---------------------------------------------------------------------------


class Verdict(enum.Enum):
    LAUNCH = "launch"
    #: The launcher's own constraints refuse the selection.
    REFUSED = "refused"
    #: A deployed node needs what the runner lacks, per the skip file.
    SKIPPED = "skipped"
    #: The copy runs only where the file deploys it at launch.
    LAUNCH_ONLY = "launch-only"
    #: The stack stands one robot and links to the copy the file deploys, so
    #: `stack remove` keeps it and the join it would make way for cannot run.
    COPY_HELD = "copy-held"
    #: It does not resolve and nothing above explains why.
    BROKEN = "broken"


@dataclass(frozen=True)
class Resolution:
    verdict: Verdict
    detail: str
    #: The flattened launcher, where the combination resolves.
    plan: dict | None = None
    #: The copies of the file that make way for the join: the stack stands
    #: one robot. Empty where the joined copy comes up beside them.
    displaced: tuple = ()

    def fingerprint(self, candidate):
        """Everything a launch of the combination depends on: two trees
        giving a combination the same fingerprint launch the same thing."""
        return canonical(
            [self.verdict.value, self.detail, self.plan, candidate.local, self.displaced]
        )


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sanitize(text, limit=300):
    """One line, no tabs, no pipes (it lands in a markdown table), bounded."""
    line = re.sub(r"^(\[ERROR\] )?Error: ", "", text.strip())
    line = re.sub(r"[\t\r\n]+", " ", line).strip()
    if len(line) > limit:
        line = line[: limit - 3] + "..."
    return line.replace("|", "\\|")


def deployed_nodes(resolved):
    """The nodes a flattened launcher deploys, name to tag."""
    nodes = {}
    for deployment in resolved.get("deployments", []):
        source = deployment.get("source", {}) if isinstance(deployment, dict) else {}
        if isinstance(source, dict) and "name" in source:
            nodes[source["name"]] = source.get("tag", "")
    return nodes


def in_copies(instance_id, copies):
    """Whether an instance is one of the named copies': a copy's instances
    carry ids minted under its name (`alpha_backbone_inst`)."""
    return str(instance_id).startswith(tuple(f"{name}_" for name in copies))


def link_targets(value):
    """The `instance` or `instance/slot` targets a flattened link holds: one,
    a list of them, or none where the slot is declared vacant."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [target for target in value if isinstance(target, str)]
    return []


def copy_instances(plan, name):
    """The ids of the instances a flattened launcher deploys for one copy."""
    return sorted(
        instance.get("instance_id", "")
        for deployment in plan.get("deployments", [])
        for instance in deployment.get("instances", [])
        if in_copies(instance.get("instance_id", ""), (name,))
    )


def links_holding_copies(plan, copies):
    """The links from the stack into the named copies, spelled as peppy
    spells them. `stack remove` keeps a copy the stack links to, so a copy
    held this way cannot make way for a join."""
    held = []
    for deployment in plan.get("deployments", []):
        for instance in deployment.get("instances", []):
            instance_id = instance.get("instance_id", "")
            if in_copies(instance_id, copies):
                continue
            for slot, value in (instance.get("links") or {}).items():
                held += [
                    f"{instance_id}.{slot} -> {target}"
                    for target in link_targets(value)
                    if in_copies(target.split("/")[0], copies)
                ]
    return held


def read_node_reasons(path):
    """One of the two files that say of a node what no launcher can: the
    skip file and the single-robot file. Node name to reason."""
    reasons = {}
    for entry in load_json5(path, path) or []:
        if not isinstance(entry, dict) or "node" not in entry:
            raise Json5Error(f"{path}: an entry has no `node`")
        reasons[entry["node"]] = entry.get("reason", "no reason given")
    return reasons


def resolve_command(path, words, join_option, join_words):
    """The preview of one combination: the launch, its join included."""
    argv = ["peppy", "stack", "resolve", path]
    if words:
        argv += ["--with", words]
    if join_option:
        argv += ["--join", join_option, "--join-name", COPY_NAME]
        if join_words:
            argv += ["--join-with", join_words]
    return argv


def resolve_candidate(root, candidate, skips, single_robot):
    """Previews one combination through `peppy stack resolve` and says what
    a runner can do with it."""
    argv = resolve_command(
        candidate.path, candidate.words, candidate.join_option, candidate.join_words
    )
    # peppy's own logging is held to errors, so stdout carries the resolved
    # plan alone and the JSON5 reader below takes it whole.
    resolve = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=root,
        env={**os.environ, "RUST_LOG": "error"},
    )
    if resolve.returncode != 0:
        output = (resolve.stderr + resolve.stdout).strip()
        if CONSTRAINT_REFUSAL_MARK in output:
            return Resolution(Verdict.REFUSED, output)
        if candidate.join_option and JOIN_CHANGE_MARK in output:
            return Resolution(Verdict.LAUNCH_ONLY, output)
        return Resolution(Verdict.BROKEN, output)
    plan = _Parser(resolve.stdout, "resolved").parse_document()
    nodes = deployed_nodes(plan)
    hits = [(node, skips[node]) for node in sorted(nodes) if node in skips]
    if hits:
        detail = "; ".join(f"deploys {node}: {reason}" for node, reason in hits)
        return Resolution(Verdict.SKIPPED, detail, plan)
    # A joined copy comes up beside the copies the file deploys. A stack that
    # stands one robot is the exception: there the join follows the removal
    # of the file's copies, and a copy the stack links to is not removed.
    stands_one_robot = any(node in single_robot for node in nodes)
    displaced = candidate.file_copies if candidate.join_option and stands_one_robot else ()
    held = links_holding_copies(plan, displaced)
    if held:
        return Resolution(Verdict.COPY_HELD, ", ".join(held), plan, displaced)
    return Resolution(Verdict.LAUNCH, "-", plan, displaced)


def resolve_inventory(root, inventory, skips, single_robot):
    """Every combination of `inventory` resolved, keyed like its candidate."""
    return {
        candidate.key: resolve_candidate(root, candidate, skips, single_robot)
        for launcher in inventory
        for candidate in launcher.candidates
    }


def changed_candidates(candidates, resolutions, base_candidates, base_resolutions):
    """The combinations whose launch the change can move: the ones the base
    tree lacks, and the ones it resolves to another fingerprint."""
    base_fingerprints = {
        candidate.key: base_resolutions[candidate.key].fingerprint(candidate)
        for candidate in base_candidates
    }
    return [
        candidate
        for candidate in candidates
        if resolutions[candidate.key].fingerprint(candidate)
        != base_fingerprints.get(candidate.key)
    ]


# ---------------------------------------------------------------------------
# coverage: the launches that stand for every launchable combination
# ---------------------------------------------------------------------------
#
# A launch proves that each node instance it deploys builds, starts and
# signals ready as configured, beside the instances it runs with. Two
# combinations deploying the same configured instances side by side prove
# the same thing, so the run launches a subset: every configuration of a
# node instance any selected combination deploys, and every pair of
# configurations any of them runs side by side, runs in at least one launch,
# and every launcher is launched at least once. What separates two
# combinations a launch cannot tell apart is already held by
# `peppy repo index --check` and by the resolve of every combination.
#
# The plain join is the one spelling a launch does tell apart. It writes no
# word, so the daemon alone decides what the copy is, from the launcher's
# entry for the option and the option's fragment, and the launch job holds
# what comes up to the preview. A join spelling the same selection out proves
# none of that. So each option of each launcher is joined plain in at least
# one launch, beside the copies the file deploys and in their place where
# both happen: beside them the two copies carry the same settings, the same
# preferred port among them, which is the pair a join with words of its own
# may never form.


def instance_configurations(plan, without_copies=()):
    """Every node instance a flattened launcher deploys, as the canonical
    text of its source and its whole configuration, the instances of
    `without_copies` left out."""
    configurations = set()
    for deployment in plan.get("deployments", []):
        for instance in deployment.get("instances", []):
            if in_copies(instance.get("instance_id", ""), without_copies):
                continue
            configurations.add(canonical([deployment.get("source"), instance]))
    return configurations


def running_states(candidate, resolutions):
    """The sets of instances a combination's launch has running together,
    one after the other. A launch without a join has one: the resolved plan.
    A launch with a join has two: the launch itself, which is the same
    combination without its join, then the stack with the joined copy, beside
    the file's copies or, where the stack stands one robot, once they have
    made way for it."""
    resolution = resolutions[candidate.key]
    plan = resolution.plan
    if not candidate.join_option:
        return [instance_configurations(plan)]
    launched = resolutions.get(candidate.launch_key)
    if launched is None or launched.plan is None:
        raise SystemExit(
            f"{candidate.label} resolves with its join, but the launch it joins, "
            f"{combination_label(candidate.launcher, candidate.words, '', '', candidate.local)}, "
            "does not resolve"
        )
    return [
        instance_configurations(launched.plan),
        instance_configurations(plan, without_copies=resolution.displaced),
    ]


def coverage_units(candidate, states, displaced):
    """What a launch of the combination proves: its launcher launches, each
    configured instance comes up, each two of them come up side by side, and
    a plain join gives the copy what its preview says, beside the file's
    copies or in the place of the `displaced` ones."""
    units = {("launcher", candidate.launcher)}
    if candidate.join_option and not candidate.join_words:
        units.add(("plain join", candidate.launcher, candidate.join_option, bool(displaced)))
    for state in states:
        units.update(("configuration", configuration) for configuration in state)
        units.update(("pair", *pair) for pair in itertools.combinations(sorted(state), 2))
    return units


def select_launches(units_by_key):
    """The keys of a small set of combinations whose units are those of all
    of them, in the order given. Greedy: the combination proving the most
    that nothing selected proves yet goes next, the earliest on a tie, so the
    same inventory always selects the same launches."""
    everything = set().union(*units_by_key.values()) if units_by_key else set()
    covered = set()
    selected = set()
    while covered != everything:
        key = max(units_by_key, key=lambda key: len(units_by_key[key] - covered))
        selected.add(key)
        covered |= units_by_key[key]
    return [key for key in units_by_key if key in selected]


def covering_launches(units, selected, units_by_key):
    """The selected launches that, between them, prove what a combination
    left out would have."""
    remaining = set(units)
    covering = []
    for key in selected:
        if remaining & units_by_key[key]:
            covering.append(key)
            remaining -= units_by_key[key]
    return covering


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Launch:
    """One entry of the plan the launch job runs."""

    label: str
    launcher: str
    words: str
    join_option: str
    join_name: str
    join_words: str
    local: bool
    #: The copies of the file that make way for the join, removed before it.
    displaced: list
    #: The instances the join's preview gives the joined copy.
    join_instances: list

    @classmethod
    def of(cls, candidate, resolution):
        return cls(
            candidate.label,
            candidate.launcher,
            candidate.words,
            candidate.join_option,
            COPY_NAME if candidate.join_option else "",
            candidate.join_words,
            candidate.local,
            list(resolution.displaced),
            copy_instances(resolution.plan, COPY_NAME) if candidate.join_option else [],
        )


def write_plan(launches, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump([asdict(launch) for launch in launches], handle, separators=(",", ":"))


def read_plan(path):
    with open(path, encoding="utf-8") as handle:
        return [Launch(**entry) for entry in json.load(handle)]


def candidates_in_scope(scope, candidates, resolutions, base_root, skips, single_robot):
    """The combinations a run of this scope plans."""
    if scope.kind is ScopeKind.EVERYTHING:
        return candidates
    if scope.kind is ScopeKind.NOTHING:
        return []
    if base_root is None:
        raise SystemExit("a run scoped to what changed needs --base-root to compare against")
    base_inventory = read_base_inventory(base_root)
    return changed_candidates(
        candidates,
        resolutions,
        [candidate for launcher in base_inventory for candidate in launcher.candidates],
        resolve_inventory(base_root, base_inventory, skips, single_robot),
    )


def command_plan(root, scope_path, base_root, skips_path, single_robot_path, plan_path):
    skips = read_node_reasons(skips_path)
    single_robot = read_node_reasons(single_robot_path)
    scope = read_scope(scope_path)
    inventory = read_inventory(root)
    candidates = [candidate for launcher in inventory for candidate in launcher.candidates]
    resolutions = resolve_inventory(root, inventory, skips, single_robot)
    for candidate in candidates:
        resolution = resolutions[candidate.key]
        if resolution.verdict is Verdict.BROKEN:
            print(resolution.detail, file=sys.stderr)
            raise SystemExit(
                f"{candidate.label} does not resolve and the launcher's constraints "
                "do not refuse it either"
            )

    in_scope = candidates_in_scope(
        scope, candidates, resolutions, base_root, skips, single_robot
    )

    launchable = [
        candidate for candidate in in_scope if resolutions[candidate.key].verdict is Verdict.LAUNCH
    ]
    units_by_key = {
        candidate.key: coverage_units(
            candidate, running_states(candidate, resolutions), resolutions[candidate.key].displaced
        )
        for candidate in launchable
    }
    selected = select_launches(units_by_key)
    by_key = {candidate.key: candidate for candidate in launchable}
    write_plan([Launch.of(by_key[key], resolutions[key]) for key in selected], plan_path)

    append_to_env_file(
        "GITHUB_STEP_SUMMARY",
        plan_summary(candidates, in_scope, resolutions, by_key, selected, units_by_key),
    )


def markdown_table(title, headers, rows):
    lines = [
        f"\n### {title} ({len(rows)})\n",
        f"| {' | '.join(headers)} |",
        f"| {' | '.join('---' for _ in headers)} |",
    ]
    lines += [f"| {' | '.join(row)} |" for row in rows]
    return "\n".join(lines) + "\n"


def folded(summary, body):
    return f"\n<details><summary>{summary}</summary>\n\n{body}\n</details>\n"


def launches_headline(in_scope, launchable, selected):
    if not in_scope:
        return "\n### 🚀 Launching nothing: this change moves no combination's resolved plan\n"
    if not selected:
        return (
            "\n### 🚀 Launching nothing: every combination this change reaches is "
            "refused or skipped\n"
        )
    return (
        f"\n### 🚀 Launching {len(selected)} of the {len(launchable)} launchable "
        "combinations in scope\n\n"
        "Every configured node instance those combinations deploy, and every pair of "
        "them they run side by side, runs in one of these launches.\n\n```diff\n"
        + "".join(f"+ {launchable[key].label}\n" for key in selected)
        + "```\n"
    )


def plan_summary(candidates, in_scope, resolutions, launchable, selected, units_by_key):
    """The run summary's account of the plan: what launches, what each
    launch stands for, what is left out and why. A `diff` block is the one
    construct a step summary renders in color, so the launches are green `+`
    lines, spelled like the launch job's log groups."""
    parts = [launches_headline(in_scope, launchable, selected)]

    left_out = [key for key in launchable if key not in selected]
    if left_out:
        rows = [
            (
                launchable[key].label,
                "<br>".join(
                    launchable[cover].label
                    for cover in covering_launches(units_by_key[key], selected, units_by_key)
                ),
            )
            for key in left_out
        ]
        parts.append(
            folded(
                f"{len(left_out)} launchable combinations are proven by the launches above",
                markdown_table("🧩 Proven by other launches", ("combination", "launched as part of"), rows),
            )
        )

    in_scope_keys = {candidate.key for candidate in in_scope}
    unchanged = [
        candidate
        for candidate in candidates
        if candidate.key not in in_scope_keys
        and resolutions[candidate.key].verdict is Verdict.LAUNCH
    ]
    if unchanged:
        parts.append(
            folded(
                f"{len(unchanged)} launchable combinations resolve to the plan the base tree "
                "gives them, and are not launched",
                "\n".join(f"- {candidate.label}" for candidate in unchanged),
            )
        )

    for verdict, title, column in (
        (Verdict.REFUSED, "❌ Refused by the launcher's own constraints", "refusal"),
        (Verdict.SKIPPED, "⏭️ Skipped: hardware or rollout dependencies", "deploys"),
        (Verdict.LAUNCH_ONLY, "🧷 Copies the file deploys at launch, refused as a join", "join refusal"),
        (Verdict.COPY_HELD, "🔗 Joins the file's copy cannot make way for", "the stack stands one robot and links to the copy"),
    ):
        rows = [
            (
                combination_label(
                    candidate.launcher, candidate.words, candidate.join_option,
                    candidate.join_words, False,
                ),
                sanitize(resolutions[candidate.key].detail),
            )
            for candidate in in_scope
            if resolutions[candidate.key].verdict is verdict
        ]
        if rows:
            parts.append(markdown_table(title, ("combination", column), rows))
    return "".join(parts)


# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------

# Raised from peppy's 180s default: a release compile of a heavy dependency
# can sit between cargo's progress lines for longer than that on a loaded
# machine, and the fix for a genuinely stuck build is the job's own timeout,
# not a mid-build abort that names no culprit.
BUILD_IDLE_TIMEOUT = ["--node-build-idle-timeout-secs", "900"]

STACK_RESET = ["peppy", "stack", "reset"]


class LaunchFailed(Exception):
    """A launch did not come up start to end as planned."""


class CommandFailed(LaunchFailed):
    """A `peppy` command of a launch exited non-zero."""

    def __init__(self, argv, returncode):
        super().__init__(f"`{' '.join(argv)}` exited {returncode}")


class JoinedCopyDiffers(LaunchFailed):
    """The joined copy does not run the instances its preview gave it."""

    def __init__(self, name, planned, running):
        super().__init__(
            f"copy `{name}` runs {', '.join(running) or 'no instance'}; "
            f"`peppy stack resolve` previewed {', '.join(planned)}"
        )


def run_peppy(argv, capture=False):
    """Runs one command of a launch, its output going to the job's log, or
    returned where `capture` asks for it. A captured command is read as JSON,
    so peppy's own logging is held to errors and stdout carries the document
    alone."""
    print(f"$ {' '.join(argv)}", flush=True)
    env = {**os.environ, "RUST_LOG": "error"} if capture else None
    return subprocess.run(
        argv, text=True, stdout=subprocess.PIPE if capture else None, env=env
    )


def checked(run, argv, capture=False):
    completed = run(argv, capture)
    if completed.returncode != 0:
        raise CommandFailed(argv, completed.returncode)
    return completed


def running_copy_instances(listing, name):
    """The ids of the instances one copy runs, from `peppy stack list --json`."""
    return sorted(
        instance_id
        for core_node in json.loads(listing)["core_nodes"]
        for copy in core_node["copies"]
        if copy["name"] == name
        for instance_id in copy["instance_ids"]
    )


def launch_command(launch, rebuild):
    argv = ["peppy", "stack", "launch", launch.launcher]
    if launch.words:
        argv += ["--with", launch.words]
    if launch.local:
        argv.append("--local")
    if rebuild:
        argv.append("--rebuild")
    return argv + BUILD_IDLE_TIMEOUT


def join_command(launch):
    argv = ["peppy", "stack", "join", launch.join_option, "-i", launch.join_name]
    if launch.join_words:
        argv += ["--with", launch.join_words]
    return argv + BUILD_IDLE_TIMEOUT


def launch_start_to_end(launch, rebuild, run):
    """Launches one combination and returns once every node has signalled
    ready, then joins its copy where it plans one: beside the copies the file
    deploys, or after the ones the plan says make way for it. The joined copy
    is held to the instances its preview gave it, so a join that comes up as
    another robot than the one planned fails the launch."""
    checked(run, launch_command(launch, rebuild))
    if launch.join_option:
        for copy in launch.displaced:
            checked(run, ["peppy", "stack", "remove", copy])
        checked(run, join_command(launch))
        listing = checked(run, ["peppy", "stack", "list", "--json"], capture=True)
        running = running_copy_instances(listing.stdout, launch.join_name)
        if running != launch.join_instances:
            raise JoinedCopyDiffers(launch.join_name, launch.join_instances, running)
        checked(run, ["peppy", "stack", "list"])
        checked(run, ["peppy", "stack", "remove", launch.join_name])
    checked(run, ["peppy", "stack", "list"])


class Status(enum.Enum):
    PASSED = "✅ launched"
    FAILED = "❌ failed"
    NOT_LAUNCHED = "⏸️ not launched"


@dataclass(frozen=True)
class Outcome:
    label: str
    status: Status
    detail: str = ""


def workflow_command(name, message, **properties):
    """One GitHub Actions workflow command, its data escaped so a label can
    only ever be text."""

    def escaped(text, extra=()):
        for char, code in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A"), *extra):
            text = text.replace(char, code)
        return text

    rendered = ",".join(
        f"{key}={escaped(value, ((':', '%3A'), (',', '%2C')))}" for key, value in properties.items()
    )
    print(f"::{name}{' ' + rendered if rendered else ''}::{escaped(message)}", flush=True)


def launch_and_reset(launch, rebuild, run):
    """One launch inside its log group, and the reset that hands the next
    launch an empty stack. Returns the outcome and whether the stack is
    empty again."""
    workflow_command("group", launch.label)
    try:
        launch_start_to_end(launch, rebuild, run)
        failure = ""
    except LaunchFailed as error:
        failure = str(error)
    try:
        checked(run, STACK_RESET)
        stack_is_empty = True
    except CommandFailed as error:
        failure = f"{failure}; then {error}" if failure else str(error)
        stack_is_empty = False
    workflow_command("endgroup", "")
    if not failure:
        return Outcome(launch.label, Status.PASSED), stack_is_empty
    workflow_command("error", failure, title=launch.label)
    return Outcome(launch.label, Status.FAILED, failure), stack_is_empty


def launch_all(launches, rebuild, run):
    """Every planned launch, one after the other on the one daemon. A failed
    launch does not stop the ones after it: the stack is reset and the run
    goes on, so a red run names every failing combination. A stack that does
    not reset is the exception: nothing launched onto it could be trusted,
    so the remaining launches are reported as not launched."""
    outcomes = []
    for index, launch in enumerate(launches):
        outcome, stack_is_empty = launch_and_reset(launch, rebuild, run)
        outcomes.append(outcome)
        if not stack_is_empty:
            reason = f"the stack did not reset after {launch.label}"
            outcomes += [
                Outcome(later.label, Status.NOT_LAUNCHED, reason)
                for later in launches[index + 1 :]
            ]
            break
    return outcomes


def command_launch(plan_path, rebuild):
    outcomes = launch_all(read_plan(plan_path), rebuild, run_peppy)
    rows = [
        (outcome.label, f"{outcome.status.value} {sanitize(outcome.detail)}".strip())
        for outcome in outcomes
    ]
    passed = sum(outcome.status is Status.PASSED for outcome in outcomes)
    append_to_env_file(
        "GITHUB_STEP_SUMMARY",
        f"\n{passed} of {len(outcomes)} launches came up start to end.\n"
        + markdown_table("Launches", ("combination", "result"), rows),
    )
    if passed != len(outcomes):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command", required=True)

    enumerate_parser = subcommands.add_parser(
        "enumerate", help="print the launcher and combination inventory"
    )
    enumerate_parser.add_argument("--root", default=".")

    scope_parser = subcommands.add_parser(
        "scope", help="decide what a change can reach, from the files it touches"
    )
    scope_parser.add_argument("--root", default=".")
    scope_parser.add_argument("--changed-files", help="the pull request's changed files, one per line")
    scope_parser.add_argument("--base-root", help="the tree the pull request branched from")
    scope_parser.add_argument("--scope", required=True, help="where to write the decision")

    plan_parser = subcommands.add_parser(
        "plan", help="resolve the combinations in scope and pick the launches that cover them"
    )
    plan_parser.add_argument("--root", default=".")
    plan_parser.add_argument("--scope", required=True, help="the decision `scope` wrote")
    plan_parser.add_argument("--base-root", help="the tree the pull request branched from")
    plan_parser.add_argument("--skips", required=True, help="the nodes the runner cannot launch")
    plan_parser.add_argument(
        "--single-robot", required=True, help="the nodes that stand one robot at a time"
    )
    plan_parser.add_argument("--plan", required=True, help="where to write the launches")

    launch_parser = subcommands.add_parser(
        "launch", help="launch every planned combination start to end on the running daemon"
    )
    launch_parser.add_argument("--plan", required=True, help="the launches `plan` wrote")
    launch_parser.add_argument("--rebuild", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "enumerate":
            command_enumerate(args.root)
        elif args.command == "scope":
            command_scope(args.root, args.changed_files, args.base_root, args.scope)
        elif args.command == "plan":
            command_plan(
                args.root, args.scope, args.base_root, args.skips, args.single_robot, args.plan
            )
        else:
            command_launch(args.plan, args.rebuild)
    except Json5Error as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
