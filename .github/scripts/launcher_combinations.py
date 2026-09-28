#!/usr/bin/env python3
"""Enumerate, scope, plan and launch the repository's launcher combinations.

enumerate prints launcher paths and fragment references, followed by
seven-column combo records: kind, launcher, launch words, the options a
launch names copies of with `--join`, joined option, join words, placement. It covers every state of every axis the launcher and its
fragments declare, every copy an axis that runs as copies can add with
`stack join`, plain and under every selection of its own axes, and every
state of the axes of each copy the file deploys, written as the
`NAME.axis=option` launch words that select them. A `one_or_more` axis the
file leaves without a copy is filled by every launch, one of its options
named with `--join`. A declared core_nodes list requests local placement in
CI.

scope decides what a change can reach from the files it touches: every
combination, the combinations whose resolved plan the change moves, or
nothing.

plan previews every combination through peppy stack resolve, its join
included. Constraints classify refused combinations. The skip file
classifies unavailable hardware and rollout dependencies. A joined copy
comes up beside the copies the file deploys. A resolve whose report says the
link rules went unchecked fails the plan, naming the caches to fill; where
LINK_RULES_UNPROVABLE says why this machine's caches are short, those
combinations pass, stay out of the plan, and are named in the run summary.
Of the launchable combinations in scope it keeps the launches that, between
them, run every configured node instance and every pair of instances that run
side by side.

launch runs the planned launches one after the other on the running daemon,
resetting the stack between them, holds each joined copy to the instances
its preview promised, every one of them running, and reports each one's
outcome.

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
COMBINATION_CEILING = 4096

# Both constraint refusals (`... requires ..., which this selection (...) does
# not satisfy` and `... forbids ..., which this selection (...) has`) carry this
# phrase; no other resolution error does. It is what separates a combination
# refused by design from a launcher that is broken.
CONSTRAINT_REFUSAL_MARK = "which this selection"

# A copy named on the command line is composed as a join, so it is refused
# where it writes a stack instance differently from how that instance runs
# (`joining NAME would change INSTANCE, which already runs`); the refusal
# carries this phrase. Such a copy runs where the launcher's file deploys
# it, whose selections this check covers as launch words; it is not a broken
# launcher.
JOIN_CHANGE_MARK = "would change"

# `stack resolve` holds the flat plan to the launch-time link rules only where
# this machine's caches hold every deployed node's manifest, and writes a
# report line to stderr opening with this phrase when they do not. A run whose
# caches are cold resolves every combination without checking one, so the line
# fails the plan and names the caches to fill.
LINK_RULES_UNCHECKED_MARK = "link rules not checked"

# The environment variable the step that fills the caches sets when it knows
# they are short: its value says why, in one clause, and a resolve reporting
# the link rules unchecked is then the expected outcome. The private hub needs
# a deploy key a fork's pull request is not given, which is the case it names
# today.
LINK_RULES_UNPROVABLE = "LINK_RULES_UNPROVABLE"

# The name every previewed and launched copy joins under in CI. A copy name is
# unique on the stack, and the simulation launchers deploy `alpha`.
COPY_NAME = "bravo"

CARDINALITIES = ("one", "zero_or_one", "one_or_more", "zero_or_more")

# The cardinalities whose axis runs as named copies.
COPY_CARDINALITIES = ("one_or_more", "zero_or_more")


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
        return self.cardinality in ("zero_or_one", "zero_or_more")

    @property
    def repeatable(self):
        """Runs as named copies: the file's `deployments`, then `stack join`."""
        return self.cardinality in COPY_CARDINALITIES


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
    copies its file deploys, the options it sets up without listing a copy,
    and its declared core-node placement."""

    axes: list[Axis]
    references: list[str]
    copies: list[Copy]
    declares_core_nodes: bool
    #: The options of repeatable axes whose entry lists no copy: a launch
    #: starts one with `--join OPTION:NAME`.
    unlisted: tuple = ()


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
    """The copies a launcher's `deployments` run, and the options it sets
    up without listing one: one copy per `instances` entry of a repeatable
    axis, each carrying what its `with` selects on the axes of the option it
    runs, the entry's `with` under the copy's own; and, unlisted, each
    option of a repeatable axis whose entry lists no copy, which `--join
    OPTION:NAME` starts with the launch."""
    repeatable = {axis.name for axis in axes if axis.repeatable}
    copies = []
    unlisted = []
    for entry in document.get("deployments", []):
        pair = deployed_option(entry, label)
        if pair is None or pair[0] not in repeatable:
            continue
        axis, option = pair
        instances = entry.get("instances", [])
        if not isinstance(instances, list):
            raise Json5Error(
                f"{label}: axis `{axis}` runs as named copies: "
                f'`{{ {axis}: "{option}", instances: [{{ instance_id: "alpha" }}] }}`'
            )
        if not instances:
            unlisted.append(option)
        for instance in instances:
            name = instance.get("instance_id") if isinstance(instance, dict) else None
            if not isinstance(name, str):
                raise Json5Error(f"{label}: a copy of `{axis}` has no `instance_id`")
            selected = {**entry.get("with", {}), **instance.get("with", {})}
            copies.append(Copy(name, axis, option, selected))
    return copies, tuple(unlisted)


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
            raise Json5Error(f"{label}: use cardinality: {', '.join(CARDINALITIES)}")
        if scope == "fragment" and cardinality in COPY_CARDINALITIES:
            raise Json5Error(
                f"{label}: component `{name}` declares {cardinality} inside a fragment; copies "
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
        copies, unlisted = deployed_copies(document, axes, path)
        return Launcher(
            axes,
            sorted(self.references),
            copies,
            bool(document.get("core_nodes")),
            unlisted,
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
    """One launch: the words over the stack's axes, the options `--join
    OPTION:NAME` starts a copy of with the launch, and, joined afterwards,
    at most one copy."""

    words: list
    join_option: str | None = None
    join_words: list = field(default_factory=list)
    launch_joins: tuple = ()


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


def launcher_selections(axes, copies=(), unlisted=()):
    """Every launch of the launcher, peppy's order: each stack selection
    bare, then with each deployed copy's own axes selected by launch word,
    then with each option the file sets up without listing a copy started
    by `--join OPTION:NAME`, then with every copy a repeatable axis can add
    joined onto it. A `one_or_more` axis the file leaves without a copy is
    filled by every launch, which names one of its options with `--join`,
    each option in turn; a join onto that stack would run under the name the
    launch's copy holds, so the joins are left out."""
    stack = [axis for axis in axes if not axis.repeatable]
    deployed = [words for copy in copies for words in copy_selections(axes, copy)]
    joined = [
        (option, selection)
        for axis in axes
        if axis.repeatable
        for option in axis.options
        for selection in join_selections(axis.nested.get(option, []))
    ]
    held = [
        axis
        for axis in axes
        if axis.cardinality == "one_or_more" and all(copy.axis != axis.name for copy in copies)
    ]
    held_options = {option for axis in held for option in axis.options}
    others = [option for option in unlisted if option not in held_options]
    combinations = []
    for selection in selections_of(stack):
        if not held:
            combinations.append(Combination(selection))
            for words in deployed:
                combinations.append(Combination(selection + words))
            for option in unlisted:
                combinations.append(Combination(selection, launch_joins=(option,)))
            for option, own in joined:
                combinations.append(Combination(selection, option, own))
            continue
        for names in itertools.product(*(axis.options for axis in held)):
            combinations.append(Combination(selection, launch_joins=names))
            for words in deployed:
                combinations.append(Combination(selection + words, launch_joins=names))
            for option in others:
                combinations.append(Combination(selection, launch_joins=names + (option,)))
    return combinations


def render_words(selection):
    return ",".join(f"{axis}={option}" for axis, option in selection if option)


# ---------------------------------------------------------------------------
# The inventory: every launcher and every combination it admits
# ---------------------------------------------------------------------------


def combination_label(name, words, join_option, join_words, local, launch_joins=()):
    """A combination's display name: the launcher, its selection, the copies
    it starts with `--join`, its join, its placement."""
    label = f"{name} ({words})" if words else name
    for option in launch_joins:
        label += f" --join {option}:{COPY_NAME}"
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
    #: The copies the launcher's file deploys, which a join comes up beside.
    file_copies: tuple = ()
    #: The options `--join OPTION:NAME` starts a copy of with the launch.
    launch_joins: tuple = ()

    @property
    def key(self):
        return (self.launcher, self.words, self.launch_joins, self.join_option, self.join_words)

    @property
    def launch_key(self):
        """The combination `stack launch` alone brings up: this one without
        its join."""
        return (self.launcher, self.words, self.launch_joins, "", "")

    @property
    def names_a_copy(self):
        """The combination names a copy on the command line, with the launch
        or on a `stack join` after it."""
        return bool(self.launch_joins or self.join_option)

    @property
    def label(self):
        return combination_label(
            self.launcher, self.words, self.join_option, self.join_words, self.local,
            self.launch_joins,
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
    combinations = launcher_selections(launcher.axes, launcher.copies, launcher.unlisted)
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
            combination.launch_joins,
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
                "combo\t{}\t{}\t{}\t{}\t{}\t{}".format(
                    candidate.launcher,
                    candidate.words,
                    ",".join(candidate.launch_joins),
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
    #: The copy runs only where the launcher's file deploys it.
    FILE_ONLY = "file-only"
    #: It resolves, but this machine's caches lack a manifest the link rules
    #: read, so the resolve proves less than it looks like it proves.
    UNCHECKED = "unchecked"
    #: It does not resolve and nothing above explains why.
    BROKEN = "broken"


@dataclass(frozen=True)
class Resolution:
    verdict: Verdict
    detail: str
    #: The flattened launcher, where the combination resolves.
    plan: dict | None = None

    def fingerprint(self, candidate):
        """Everything a launch of the combination depends on: two trees
        giving a combination the same fingerprint launch the same thing."""
        return canonical([self.verdict.value, self.detail, self.plan, candidate.local])


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


def copy_instances(plan, name):
    """The ids of the instances a flattened launcher deploys for one copy.
    `core_node` is where the plan places an instance, which is the copy for
    a copy's instances and the core node a launcher declares for the rest,
    so a copy is read off the name it was joined under."""
    return sorted(
        instance.get("instance_id", "")
        for _node, instance in plan_instances(plan)
        if instance.get("core_node") == name
    )


def joined_into(plan, name):
    """What a joined copy is wired into beyond its own instances: what the
    copy comes up beside. A simulated robot's initializer and backbone link
    into the simulation's instance, and a copy that drives real hardware is
    wired into nothing outside itself."""
    own = set(copy_instances(plan, name))
    instances = plan_instances(plan)
    deployer_of = {instance.get("instance_id", ""): deployer for deployer, instance in instances}
    hosts = {
        target
        for _deployer, instance in instances
        if instance.get("instance_id", "") in own
        for target in wired_into(instance)
    } - own
    return frozenset(deployer_of[host] for host in hosts)


def plan_instances(plan):
    """Every instance a flattened launcher deploys, and what deploys it: the
    node a deployment names, or the deployment's own source where it names
    none, which is how a deployment publishing MCP exposures reaches the
    stack. Two of those are two hosts, and a copy wired into one is proven
    apart from a copy wired into the other."""
    instances = []
    for deployment in plan.get("deployments", []):
        source = deployment.get("source", {}) if isinstance(deployment, dict) else {}
        name = source.get("name") if isinstance(source, dict) else None
        deployer = name or canonical(source)
        instances += [(deployer, instance) for instance in deployment.get("instances", [])]
    return instances


def wired_into(instance):
    """The instances one instance links to. A link names one instance, a list
    of them, or a vacancy, which names none; a link onto a pairing slot names
    the slot after the instance it is on (`simulation_inst/arms`)."""
    targets = []
    for key, value in (instance.get("links") or {}).items():
        if isinstance(value, str):
            targets.append(value)
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            targets += value
        elif not (isinstance(value, dict) and "vacant" in value):
            raise SystemExit(
                f"the link `{key}` of {instance.get('instance_id', 'an instance')} is "
                f"neither an instance, a list of them, nor a vacancy: {value!r}"
            )
    return {target.split("/", 1)[0] for target in targets}


def read_node_reasons(path):
    """The skip file, which says of a node what no launcher can: what the
    runner it would launch on lacks. Node name to reason."""
    reasons = {}
    for entry in load_json5(path, path) or []:
        if not isinstance(entry, dict) or "node" not in entry:
            raise Json5Error(f"{path}: an entry has no `node`")
        reasons[entry["node"]] = entry.get("reason", "no reason given")
    return reasons


def previewed_words(join_words):
    """A previewed join's own selection, as the launch words that name its
    copy: `bravo.axis=option`, the way a word selects the own axis of a copy
    the file deploys."""
    return ",".join(f"{COPY_NAME}.{word}" for word in join_words.split(",") if word)


def resolve_command(path, words, join_option, join_words, launch_joins=()):
    """The preview of one combination: the launch, and each copy it names
    with `--join` under that copy's own words."""
    argv = ["peppy", "stack", "resolve", path]
    selection = ",".join(part for part in (words, previewed_words(join_words)) if part)
    if selection:
        argv += ["--with", selection]
    named = (*launch_joins, join_option) if join_option else launch_joins
    for option in named:
        argv += ["--join", f"{option}:{COPY_NAME}"]
    return argv


def resolve_candidate(root, candidate, skips):
    """Previews one combination through `peppy stack resolve` and says what
    a runner can do with it."""
    argv = resolve_command(
        candidate.path,
        candidate.words,
        candidate.join_option,
        candidate.join_words,
        candidate.launch_joins,
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
        if candidate.names_a_copy and JOIN_CHANGE_MARK in output:
            return Resolution(Verdict.FILE_ONLY, output)
        return Resolution(Verdict.BROKEN, output)
    unchecked = [
        line for line in resolve.stderr.splitlines() if LINK_RULES_UNCHECKED_MARK in line
    ]
    if unchecked:
        return Resolution(Verdict.UNCHECKED, " ".join(unchecked))
    plan = _Parser(resolve.stdout, "resolved").parse_document()
    nodes = deployed_nodes(plan)
    hits = [(node, skips[node]) for node in sorted(nodes) if node in skips]
    if hits:
        detail = "; ".join(f"deploys {node}: {reason}" for node, reason in hits)
        return Resolution(Verdict.SKIPPED, detail, plan)
    # A joined copy comes up beside the copies the file deploys.
    return Resolution(Verdict.LAUNCH, "-", plan)


def resolve_inventory(root, inventory, skips):
    """Every combination of `inventory` resolved, keyed like its candidate."""
    return {
        candidate.key: resolve_candidate(root, candidate, skips)
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
# and every launcher is launched at least once. A stack instance's
# configuration is its own: the links a copy adds to it are the copy's, so
# a server every robot is listed on is one configuration however many
# robots fill its sets, and each robot's instances count as themselves.
# What separates two combinations a launch cannot tell apart is already
# held by `peppy repo index --check` and by the resolve of every
# combination.
#
# The plain join is the one spelling a launch does tell apart. It writes no
# word, so the daemon alone decides what the copy is, from the launcher's
# entry for the option and the option's fragment, and the launch job holds
# what comes up to the preview. A join spelling the same selection out proves
# none of that. So each option of each launcher is joined plain in at least
# one launch, once for each set of instances the copy comes up wired into:
# standing a joined robot beside the ones already standing is each
# simulation's own work, so what one of them proves says nothing about the
# next. Where the joined option is the one the file deploys, the two copies
# carry the same settings and the same preferred port among them, which is
# the pair a join with words of its own may never form.


def instance_configurations(plan, copies):
    """Every node instance a flattened launcher deploys, as the canonical
    text of its source and its configuration: a copy's instance whole, a
    stack instance without the links into `copies`, which are the copies'
    own."""
    copy_of = {
        instance.get("instance_id", ""): instance.get("core_node")
        for _deployer, instance in plan_instances(plan)
    }
    configurations = set()
    for deployment in plan.get("deployments", []):
        for instance in deployment.get("instances", []):
            if instance.get("core_node") not in copies:
                instance = without_links_into(instance, copies, copy_of)
            configurations.add(canonical([deployment.get("source"), instance]))
    return configurations


def without_links_into(instance, copies, copy_of):
    """The instance with every link target inside `copies` dropped, and a
    slot left with none dropped with it. `copy_of` says where the plan
    places each instance, which is the copy for a copy's instances."""
    def outside(target):
        return copy_of.get(target.split("/", 1)[0]) not in copies

    links = {}
    for slot, value in (instance.get("links") or {}).items():
        if isinstance(value, str):
            if outside(value):
                links[slot] = value
        elif isinstance(value, list):
            kept = [target for target in value if not isinstance(target, str) or outside(target)]
            if kept:
                links[slot] = kept
        else:
            links[slot] = value
    return {**instance, "links": links} if links or "links" in instance else instance


def running_states(candidate, resolutions):
    """The sets of instances a combination's launch has running together,
    one after the other. A launch without a join has one: the resolved plan.
    A launch with a join has two: the launch itself, which is the same
    combination without its join, then the stack with the joined copy beside
    the file's copies."""
    resolution = resolutions[candidate.key]
    plan = resolution.plan
    copies = candidate.file_copies + ((COPY_NAME,) if candidate.launch_joins else ())
    if not candidate.join_option:
        return [instance_configurations(plan, copies)]
    launched = resolutions.get(candidate.launch_key)
    if launched is None or launched.plan is None:
        raise SystemExit(
            f"{candidate.label} resolves with its join, but the launch it joins, "
            f"{combination_label(candidate.launcher, candidate.words, '', '', candidate.local)}, "
            "does not resolve"
        )
    return [
        instance_configurations(launched.plan, copies),
        instance_configurations(plan, copies + (COPY_NAME,)),
    ]


def coverage_units(candidate, states, hosts):
    """What a launch of the combination proves: its launcher launches, each
    configured instance comes up, each two of them come up side by side, and
    a plain join gives the copy what its preview says, beside the file's
    copies, wired into the `hosts` this launch stands them in."""
    units = {("launcher", candidate.launcher)}
    if candidate.join_option and not candidate.join_words:
        units.add(("plain join", candidate.launcher, candidate.join_option, tuple(sorted(hosts))))
    for option in candidate.launch_joins:
        units.add(("launch join", candidate.launcher, option))
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
    #: The instances the preview gives the copy named bravo, started with
    #: the launch or joined after it.
    join_instances: list
    #: The options `--join OPTION:NAME` starts a copy of with the launch.
    launch_joins: list = field(default_factory=list)

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
            copy_instances(resolution.plan, COPY_NAME) if candidate.names_a_copy else [],
            list(candidate.launch_joins),
        )


def write_plan(launches, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump([asdict(launch) for launch in launches], handle, separators=(",", ":"))


def read_plan(path):
    with open(path, encoding="utf-8") as handle:
        return [Launch(**entry) for entry in json.load(handle)]


def candidates_in_scope(scope, candidates, resolutions, base_root, skips):
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
        resolve_inventory(base_root, base_inventory, skips),
    )


def command_plan(root, scope_path, base_root, skips_path, plan_path):
    skips = read_node_reasons(skips_path)
    scope = read_scope(scope_path)
    inventory = read_inventory(root)
    candidates = [candidate for launcher in inventory for candidate in launcher.candidates]
    resolutions = resolve_inventory(root, inventory, skips)
    unprovable = os.environ.get(LINK_RULES_UNPROVABLE, "").strip()
    for candidate in candidates:
        resolution = resolutions[candidate.key]
        if resolution.verdict is Verdict.BROKEN:
            print(resolution.detail, file=sys.stderr)
            raise SystemExit(
                f"{candidate.label} does not resolve and the launcher's constraints "
                "do not refuse it either"
            )
        if resolution.verdict is Verdict.UNCHECKED and not unprovable:
            raise SystemExit(
                f"{candidate.label}: {resolution.detail}; register the repositories "
                "holding those nodes and run `peppy repo refresh` before planning"
            )

    in_scope = candidates_in_scope(scope, candidates, resolutions, base_root, skips)

    launchable = [
        candidate for candidate in in_scope if resolutions[candidate.key].verdict is Verdict.LAUNCH
    ]
    units_by_key = {
        candidate.key: coverage_units(
            candidate,
            running_states(candidate, resolutions),
            joined_into(resolutions[candidate.key].plan, COPY_NAME)
            if candidate.join_option
            else frozenset(),
        )
        for candidate in launchable
    }
    selected = select_launches(units_by_key)
    by_key = {candidate.key: candidate for candidate in launchable}
    write_plan([Launch.of(by_key[key], resolutions[key]) for key in selected], plan_path)

    append_to_env_file(
        "GITHUB_STEP_SUMMARY",
        plan_summary(
            candidates, in_scope, resolutions, by_key, selected, units_by_key, unprovable
        ),
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


def unchecked_note(candidates, resolutions, unprovable):
    """What a run whose caches are short owes its reader: the one reason, and
    every combination no link rule was checked over. Every combination of the
    inventory counts, in or out of scope, so a green run says what it left
    unproven whatever the change reaches."""
    unchecked = [
        candidate
        for candidate in candidates
        if resolutions[candidate.key].verdict is Verdict.UNCHECKED
    ]
    if not unchecked:
        return ""
    return (
        f"\n### 🔓 Link rules not checked ({len(unchecked)})\n\n"
        f"`peppy stack resolve` checked no link rule over these plans: {unprovable}. "
        "They resolve; none of them is launched.\n\n"
        + "".join(f"- {candidate.label}\n" for candidate in unchecked)
    )


def plan_summary(candidates, in_scope, resolutions, launchable, selected, units_by_key, unprovable):
    """The run summary's account of the plan: what launches, what each
    launch stands for, what is left out and why. A `diff` block is the one
    construct a step summary renders in color, so the launches are green `+`
    lines, spelled like the launch job's log groups."""
    parts = [
        launches_headline(in_scope, launchable, selected),
        unchecked_note(candidates, resolutions, unprovable),
    ]

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
        (Verdict.FILE_ONLY, "🧷 Copies only the launcher's file deploys", "refusal"),
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


class JoinedCopyNotRunning(LaunchFailed):
    """An instance of the joined copy is not running once the join returned:
    a robot the engine took out during the join leaves its initializer
    finished, and the join reports nothing of it."""

    def __init__(self, name, states):
        super().__init__(
            f"copy `{name}` is not running whole after its join: "
            + ", ".join(f"{instance_id} is {state}" for instance_id, state in sorted(states.items()))
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


def copy_instance_ids(listing, name):
    """The ids of the instances one copy minted, from `peppy stack list --json`."""
    return sorted(
        instance_id
        for core_node in json.loads(listing)["core_nodes"]
        for copy in core_node["copies"]
        if copy["name"] == name
        for instance_id in copy["instance_ids"]
    )


def copy_instance_states(listing, name):
    """What each instance one copy minted is doing, by id, from `peppy stack
    list --json`: the state peppy reports, `unhealthy` for a running instance
    that did not answer its health probe, and `missing` for one no stack
    lists."""
    states = {}
    for core_node in json.loads(listing)["core_nodes"]:
        for node in (core_node.get("stack") or {}).get("nodes", []):
            for instance in node["instances"]:
                state = instance["state"]
                if state == "running" and not instance.get("healthy", True):
                    state = "unhealthy"
                states[instance["instance_id"]] = state
    return {
        instance_id: states.get(instance_id, "missing")
        for instance_id in copy_instance_ids(listing, name)
    }


def launch_command(launch):
    argv = ["peppy", "stack", "launch", launch.launcher]
    if launch.words:
        argv += ["--with", launch.words]
    for option in launch.launch_joins:
        argv += ["--join", f"{option}:{COPY_NAME}"]
    if launch.local:
        argv.append("--local")
    return argv + BUILD_IDLE_TIMEOUT


def join_command(launch):
    argv = ["peppy", "stack", "join", f"{launch.join_option}:{launch.join_name}"]
    if launch.join_words:
        argv += ["--with", launch.join_words]
    return argv + BUILD_IDLE_TIMEOUT


def launch_start_to_end(launch, run):
    """Launches one combination and returns once every node has signalled
    ready, then joins its copy where it plans one, beside the copies the file
    deploys. The copy the launch names with `--join` and the joined copy are
    held to the instances the preview gave them, every one of them running,
    so a copy that comes up as another robot than the one planned fails the
    launch, and a copy whose robot the engine took out during the join fails
    it too."""
    checked(run, launch_command(launch))
    if launch.launch_joins:
        hold_copy_to_preview(launch, run)
    if launch.join_option:
        checked(run, join_command(launch))
        hold_copy_to_preview(launch, run)
        checked(run, ["peppy", "stack", "list"])
        checked(run, ["peppy", "stack", "remove", launch.join_name])
    checked(run, ["peppy", "stack", "list"])


def hold_copy_to_preview(launch, run):
    """The copy named bravo runs the instances its preview gave it, every
    one of them running."""
    listing = checked(run, ["peppy", "stack", "list", "--json"], capture=True)
    minted = copy_instance_ids(listing.stdout, COPY_NAME)
    if minted != launch.join_instances:
        raise JoinedCopyDiffers(COPY_NAME, launch.join_instances, minted)
    stalled = {
        instance_id: state
        for instance_id, state in copy_instance_states(listing.stdout, COPY_NAME).items()
        if state != "running"
    }
    if stalled:
        raise JoinedCopyNotRunning(COPY_NAME, stalled)


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


def launch_and_reset(launch, run):
    """One launch inside its log group, and the reset that hands the next
    launch an empty stack. Returns the outcome and whether the stack is
    empty again."""
    workflow_command("group", launch.label)
    try:
        launch_start_to_end(launch, run)
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


def launch_all(launches, run):
    """Every planned launch, one after the other on the one daemon. A failed
    launch does not stop the ones after it: the stack is reset and the run
    goes on, so a red run names every failing combination. A stack that does
    not reset is the exception: nothing launched onto it could be trusted,
    so the remaining launches are reported as not launched."""
    outcomes = []
    for index, launch in enumerate(launches):
        outcome, stack_is_empty = launch_and_reset(launch, run)
        outcomes.append(outcome)
        if not stack_is_empty:
            reason = f"the stack did not reset after {launch.label}"
            outcomes += [
                Outcome(later.label, Status.NOT_LAUNCHED, reason)
                for later in launches[index + 1 :]
            ]
            break
    return outcomes


def command_launch(plan_path):
    outcomes = launch_all(read_plan(plan_path), run_peppy)
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
    plan_parser.add_argument("--plan", required=True, help="where to write the launches")

    launch_parser = subcommands.add_parser(
        "launch", help="launch every planned combination start to end on the running daemon"
    )
    launch_parser.add_argument("--plan", required=True, help="the launches `plan` wrote")

    args = parser.parse_args()
    try:
        if args.command == "enumerate":
            command_enumerate(args.root)
        elif args.command == "scope":
            command_scope(args.root, args.changed_files, args.base_root, args.scope)
        elif args.command == "plan":
            command_plan(args.root, args.scope, args.base_root, args.skips, args.plan)
        else:
            command_launch(args.plan)
    except Json5Error as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
