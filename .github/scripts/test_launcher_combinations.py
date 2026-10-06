import collections
import contextlib
import io
import json
from pathlib import Path
import posixpath
import re
import tempfile
import unittest
from unittest.mock import patch

import launcher_combinations as combinations


def fleet_axes():
    """A simulation axis the file deploys, a scene commander it leaves off,
    and a robot axis running as copies whose fragment declares a robot
    commander and a recorder."""
    commander = combinations.Axis("robot_commander", "one", ["web_commander", "xr_commander"], deployed="web_commander")
    recorder = combinations.Axis("recorder", "zero_or_one", ["lerobot_recorder"])
    return [
        combinations.Axis("simulation", "one", ["mujoco", "isaac_sim"], deployed="mujoco"),
        combinations.Axis("scene_commander", "zero_or_one", ["web_scene_commander"]),
        combinations.Axis(
            "robot", "zero_or_more", ["openarm_v2"], nested={"openarm_v2": [commander, recorder]}
        ),
    ]


def resolved_plan(*nodes):
    """What `stack resolve` prints for a launcher deploying `nodes`, each a
    `name:tag` running as the one instance `name_inst`."""
    deployments = [
        {"source": {"name": name, "tag": tag}, "instances": [{"instance_id": f"{name}_inst"}]}
        for name, tag in (node.split(":") for node in nodes)
    ]
    return completed(json.dumps({"deployments": deployments}))


def completed(stdout="", returncode=0, stderr=""):
    return combinations.subprocess.CompletedProcess([], returncode, stdout, stderr)


def simulation_axis(*simulations):
    return {"name": "simulation", "options": {simulation: {} for simulation in simulations}}


def simulation_launcher(*simulations):
    """A launcher whose one axis picks a simulation, deploying the first."""
    return {
        "peppy_schema": "launcher/v1",
        "components": [simulation_axis(*simulations)],
        "deployments": [{"simulation": simulations[0]}],
    }


def fleet_launcher(file_copy=None, at_launch=False):
    """A MuJoCo simulation a robot joins as a copy, with or without a
    recorder, the file deploying the copy `file_copy` where one is named, or
    listing the robot without a copy, for `--join` to name at launch."""
    robot = {"components": [
        {"name": "recorder", "cardinality": "zero_or_one", "options": {"lerobot_recorder": {}}},
    ]}
    deployments = [{"simulation": "mujoco"}]
    if file_copy:
        deployments.append({"robot": "openarm_v2", "instances": [{"instance_id": file_copy}]})
    if at_launch:
        deployments.append({"robot": "openarm_v2"})
    return {
        "peppy_schema": "launcher/v1",
        "components": [
            simulation_axis("mujoco"),
            {"name": "robot", "cardinality": "zero_or_more", "options": {"openarm_v2": robot}},
        ],
        "deployments": deployments,
    }


def write_repository(root, launchers):
    root.mkdir()
    (root / "peppy_repository.json5").write_text(json.dumps({
        "launchers": {name: {"path": f"{name}.json5"} for name in launchers},
    }))
    for name, document in launchers.items():
        text = document if isinstance(document, str) else json.dumps(document)
        (root / f"{name}.json5").write_text(text)


class FakeResolve:
    """Stands in for `peppy stack resolve`: answers each tree's combinations
    from `(launcher path, launch words, the options it names a copy of with
    `--join`, that copy's own words)`, an empty plan where a tree names none,
    and keeps the calls it received. A named copy's own words ride the
    selection under its name, so the words carrying that name read back as
    the copy's."""

    def __init__(self, **answers_by_tree):
        self.answers_by_tree = answers_by_tree
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        flags = list(zip(argv[4::2], argv[5::2]))
        scope = f"{combinations.COPY_NAME}."
        selection = [word for word in dict(flags).get("--with", "").split(",") if word]
        key = (
            argv[3],
            ",".join(word for word in selection if not word.startswith(scope)),
            ",".join(value.split(":")[0] for flag, value in flags if flag == "--join"),
            ",".join(word.removeprefix(scope) for word in selection if word.startswith(scope)),
        )
        answers = self.answers_by_tree.get(Path(kwargs["cwd"]).name, {})
        return answers.get(key, resolved_plan())


EVERYTHING = combinations.Scope(combinations.ScopeKind.EVERYTHING, "a test of every combination")
CHANGED = combinations.Scope(combinations.ScopeKind.CHANGED, "a test of a launcher change")


@contextlib.contextmanager
def planned(launchers, answers=None, scope=EVERYTHING, base_launchers=None, base_answers=None,
            unprovable=""):
    """The launchers planned in a repository of their own, `stack resolve`
    answering `answers`, `base_answers` for the base tree a changed scope
    compares against, and `unprovable` for what the step that fills the caches
    reports about them: the fake resolve, the labels of the planned launches,
    the plan and the run summary."""
    with tempfile.TemporaryDirectory() as directory:
        head = Path(directory) / "head"
        write_repository(head, launchers)
        base = None
        if base_launchers is not None:
            base = Path(directory) / "base"
            write_repository(base, base_launchers)
        scope_path = Path(directory) / "scope.json"
        combinations.write_scope(scope, scope_path)
        skips = Path(directory) / "skips.json5"
        skips.write_text('[{ node: "zed_camera", reason: "the runner has no ZED camera" }]')
        plan_path = Path(directory) / "plan.json"
        summary = Path(directory) / "summary.md"
        resolve = FakeResolve(head=answers or {}, base=base_answers or {})
        with patch.object(combinations.subprocess, "run", resolve), \
                patch.dict(combinations.os.environ, {
                    "GITHUB_STEP_SUMMARY": str(summary),
                    combinations.LINK_RULES_UNPROVABLE: unprovable,
                }):
            combinations.command_plan(head, scope_path, base, skips, plan_path)
        plan = combinations.read_plan(plan_path)
        yield resolve, [launch.label for launch in plan], plan, summary.read_text()


#: The stack's one server, every robot is listed on.
SERVER = "robot_control_inst"

#: The launchers running robots as copies, and the robot options of each.
ROBOT_LAUNCHERS = {
    "physical.json5": ("openarm", "so101"),
    "openarm_simulation.json5": ("openarm_sim", "so101_sim"),
    "so101_simulation.json5": ("openarm_sim", "so101_sim"),
    "mcp/simulation_mcp.json5": ("openarm_sim", "so101_sim"),
}
SIMULATION_LAUNCHERS = [path for path in ROBOT_LAUNCHERS if path != "physical.json5"]

#: What every simulation launcher adjusts: the server reads the simulation's
#: clock. The MCP launcher turns rendering on as well.
SERVER_CLOCK = {"target": SERVER, "set_framework": {"clock": "simulation"}}
RENDERING_ON = {"target": "simulation_inst", "set_arguments": {"cameras_enabled": True}}


def family_of(option):
    return "so101" if option.startswith("so101") else "openarm"


def robot_option(document, option):
    """One robot option of a launcher: the files it names and the writes the
    launcher makes under it."""
    axis = next(axis for axis in document["components"] if axis["name"] == "robot")
    return axis["options"][option]


def axis_of(document, name):
    return next(axis for axis in document["components"] if axis["name"] == name)


def option_file(declaring, option_path):
    """The fragment a path-string option names, read from the declaring
    file's directory."""
    root = Path(__file__).resolve().parents[2]
    path = (root / declaring).parent / option_path
    return combinations.load_json5(path, path.relative_to(root).as_posix())


def instance_of(document, instance_id):
    return next(instance for deployment in document["deployments"] if "source" in deployment
                for instance in deployment["instances"] if instance["instance_id"] == instance_id)


def writes_link(adjustment, slot):
    """Whether an adjustment sets, adds to or unsets the link `slot`."""
    return (slot in adjustment.get("set_links", {}) or slot in adjustment.get("add_links", {})
            or slot in adjustment.get("unset_links", []))


#: The robot files: each family's physical and simulated robot.
ROBOT_FILES = {
    "openarm": "openarm/fragments/openarm.json5",
    "openarm_sim": "openarm/fragments/openarm_sim.json5",
    "so101": "so101/fragments/so101.json5",
    "so101_sim": "so101/fragments/so101_sim.json5",
}


class FragmentInventoryTests(unittest.TestCase):
    """The files each directory holds, named one by one, and the set of
    fragment directories. A file that moves, one that comes back and one that
    arrives unannounced all fail here; where a fragment may live and that
    every fragment is referenced are
    `test_fragment_paths_follow_domain_layout_and_are_all_referenced`."""

    def test_every_fragment_directory_holds_what_it_owns(self):
        root = Path(__file__).resolve().parents[2]
        for directory, expected in [
            ("openarm/fragments", {"ai_brain_vla.json5", "cameras.json5", "cameras_sim.json5",
                                   "ker_commander.json5", "lerobot_recorder.json5",
                                   "openarm.json5", "openarm_sim.json5", "web_commander.json5"}),
            ("so101/fragments", {"cameras.json5", "cameras_sim.json5", "lerobot_recorder.json5",
                                 "so101.json5", "so101_leader.json5", "so101_sim.json5"}),
            ("robot_commanders/fragments", {"mcp_commander.json5", "xr_commander.json5"}),
            ("simulation/fragments", {"isaac_sim.json5", "mujoco.json5", "waldo.json5",
                                      "web_scene_commander.json5"}),
            ("mcp/fragments", {"robot_control.json5", "world_control.json5"}),
            ("common/fragments", {"none.json5"}),
        ]:
            with self.subTest(directory=directory):
                held = {path.name for path in (root / directory).glob("*.json5")}
                self.assertEqual(held, expected)
        self.assertEqual(
            sorted(path.relative_to(root).as_posix()
                   for path in root.glob("*/fragments") if path.is_dir()),
            ["common/fragments", "mcp/fragments", "openarm/fragments",
             "robot_commanders/fragments", "simulation/fragments", "so101/fragments"],
        )


def constraints_anywhere(document):
    """Every constraint a document declares, its options' bodies included."""
    found = list(document.get("constraints") or [])
    for axis in document.get("components") or []:
        for spec in (axis.get("options") or {}).values():
            if isinstance(spec, dict):
                found.extend(constraints_anywhere(spec))
    return found


class ConstraintFormTests(unittest.TestCase):
    """Every rule in the hub is written as a requirement. `forbids` names the
    options that exist today and says nothing about one the axis gains later,
    so a rule written that way would admit a new hardware version or a new rig
    without a word. Two options of two optional axes that cannot run together
    are the case `requires` cannot state; the first of those in this hub
    narrows this test."""

    def test_no_document_writes_forbids(self):
        root = Path(__file__).resolve().parents[2]
        for path in sorted(root.rglob("*.json5")):
            if ".git" in path.parts or ".github" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            document = combinations.load_json5(path, relative)
            if not isinstance(document, dict):
                continue
            with self.subTest(path=relative):
                self.assertEqual(
                    [c for c in constraints_anywhere(document) if "forbids" in c], []
                )


class DatasetIdentityTests(unittest.TestCase):
    """What a recorded episode is filed under. The recorder is one fragment
    per family, shared by the physical and the simulated robot, so the
    launcher names the dataset under the robot: a physical OpenArm's by its
    version, a simulated one's by version and engine, an SO-101's by
    the engine alone."""

    ENGINES = [("mujoco", "mujoco"), ("isaac_sim", "isaac"), ("waldo", "waldo")]

    def test_every_dataset_is_named_under_the_robot_that_films_it(self):
        root = Path(__file__).resolve().parents[2]
        openarm_sim = {
            (("hardware_version", gen), ("simulation", engine)): (f"openarm_{gen}_{label}", f"/tmp/lerobot_openarm_{label}")
            for gen in ("v1", "v2") for engine, label in self.ENGINES
        }
        so101_sim = {(("simulation", engine),): (f"so101_{label}", f"/tmp/lerobot_so101_{label}")
                     for engine, label in self.ENGINES}
        expected = {
            "openarm": {(("hardware_version", gen),): (f"openarm_{gen}", f"/tmp/lerobot_openarm_{gen}") for gen in ("v1", "v2")},
            "so101": {(): ("so101", "/tmp/lerobot_so101")},
            "openarm_sim": openarm_sim,
            "so101_sim": so101_sim,
        }
        for path, robots in ROBOT_LAUNCHERS.items():
            document = combinations.load_json5(root / path, path)
            for robot in robots:
                with self.subTest(path=path, robot=robot):
                    written = {}
                    for entry in robot_option(document, robot)["adjustments"]:
                        arguments = entry.get("set_arguments", {})
                        if entry["target"] != "recorder_inst" or "robot_type" not in arguments:
                            continue
                        guard = entry.get("when", {})
                        key = tuple(sorted(guard.items())) if guard else ()
                        self.assertNotIn(key, written, f"{robot} names its dataset twice under {dict(key)}")
                        written[key] = (arguments["robot_type"], arguments["storage_root"])
                    self.assertEqual(written, expected[robot])
        # No fragment names a dataset: the family recorder carries the rate
        # and the limb links alone.
        for path in ["openarm/fragments/lerobot_recorder.json5", "so101/fragments/lerobot_recorder.json5"]:
            with self.subTest(path=path):
                recorder = combinations.load_json5(root / path, path)
                instance = instance_of(recorder, "recorder_inst")
                self.assertEqual(set(instance["arguments"]), {"fps"})
                self.assertNotIn("adjustments", recorder)


class SharedRobotBlockTests(unittest.TestCase):
    """A robot is wired into the same stack surfaces wherever it runs, so the
    launchers that offer one write the same entries under it. A fragment may
    not carry these writes, so each launcher holds its own copy; this is what
    keeps the copies in step."""

    def test_every_launcher_writes_the_same_entries_under_one_robot(self):
        root = Path(__file__).resolve().parents[2]
        blocks = collections.defaultdict(dict)
        for path in SIMULATION_LAUNCHERS:
            document = combinations.load_json5(root / path, path)
            for option in ROBOT_LAUNCHERS[path]:
                entries = robot_option(document, option).get("adjustments", [])
                # The simulation is the launcher's own instance: only the MCP
                # launcher turns its rendering on.
                blocks[option][path] = [
                    entry for entry in entries if entry["target"] != "simulation_inst"
                ]
        for option, per_launcher in sorted(blocks.items()):
            first, *rest = sorted(per_launcher)
            for path in rest:
                with self.subTest(option=option, launcher=path):
                    self.assertEqual(per_launcher[path], per_launcher[first])
        # Rendering is asked for under the robot whose rig runs, except by
        # the MCP launcher, which turns it on for every copy at the top level.
        for path in SIMULATION_LAUNCHERS:
            document = combinations.load_json5(root / path, path)
            for option in ROBOT_LAUNCHERS[path]:
                with self.subTest(option=option, launcher=path):
                    rendering = [entry for entry in robot_option(document, option)["adjustments"]
                                 if entry["target"] == "simulation_inst"]
                    expected = [] if path == "mcp/simulation_mcp.json5" else [
                        RENDERING_ON | {"when": {"camera_rig": "cameras_sim"}}]
                    self.assertEqual(rendering, expected)


class OptionShapeTests(unittest.TestCase):
    """The shapes an option takes, as `peppy` takes them: a path or an
    object. What the daemon refuses the planner refuses, so a launcher that
    would fail at resolve fails while the plan is still being read."""

    def parts(self, spec):
        return combinations.fragment_parts(spec, "l.json5 option robot.arm", 1, "robot")

    def test_a_path_string_names_one_file(self):
        self.assertEqual(self.parts("fragments/arm.json5"), ["fragments/arm.json5"])

    def test_an_object_names_its_files_then_its_body(self):
        body = {"deployments": [{"source": {"name": "arm", "tag": "v1"}, "instances": []}]}
        spec = {
            "fragments": ["fragments/a.json5", "fragments/b.json5"],
            "adjustments": [{"target": "arm_inst", "set_arguments": {"speed": 1}}],
            **body,
        }
        self.assertEqual(
            self.parts(spec), ["fragments/a.json5", "fragments/b.json5", body]
        )

    def test_an_object_of_adjustments_alone_names_no_part(self):
        spec = {"adjustments": [{"target": "arm_inst", "set_arguments": {"speed": 1}}]}
        self.assertEqual(self.parts(spec), [])

    def test_a_list_is_refused_with_the_object_to_write(self):
        with self.assertRaises(combinations.Json5Error) as refusal:
            self.parts(["fragments/a.json5", "fragments/b.json5"])
        self.assertIn("{ fragments: [", str(refusal.exception))

    def test_an_empty_fragments_list_is_refused(self):
        with self.assertRaises(combinations.Json5Error):
            self.parts({"fragments": []})

    def test_an_empty_adjustments_list_is_refused(self):
        with self.assertRaises(combinations.Json5Error):
            self.parts({"fragments": ["fragments/a.json5"], "adjustments": []})

    def test_a_shape_the_daemon_refuses_is_refused(self):
        for spec in (
            ["a.json5"],
            42,
            "",
            "   ",
            {"fragments": "a.json5"},
            {"fragments": ["a.json5", 7]},
            {"fragments": [""]},
            {"fragments": ["a.json5"], "adjustments": {}},
            {"fragments": ["a.json5"], "adjustments": "nope"},
            {"fragments": ["a.json5"], "core_nodes": "notalist"},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x"}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": ""}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x", "set_arguments": {}}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x", "unset_links": []}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x",
                                                       "set_argumnets": {"a": 1}}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x",
                                                       "add_links": {"s": []}}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x",
                                                       "add_links": {"s": ["a", "a"]}}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x",
                                                       "add_links": {"s": [""]}}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x", "when": {},
                                                       "set_arguments": {"a": 1}}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x",
                                                       "set_arguments": {"": 1}}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x",
                                                       "set_links": {"": "y"}}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x",
                                                       "unset_links": [""]}]},
            {"fragments": ["a.json5"], "adjustments": [{"target": "x",
                                                       "when": {"robot": "arm"},
                                                       "set_arguments": {"a": 1}}]},
        ):
            with self.subTest(spec=spec):
                with self.assertRaises(combinations.Json5Error):
                    self.parts(spec)

    def test_an_option_inside_a_body_carries_no_adjustments(self):
        spec = {"adjustments": [{"target": "x", "set_arguments": {"a": 1}}]}
        with self.assertRaises(combinations.Json5Error) as refusal:
            combinations.fragment_parts(spec, "l.json5 option robot.arm", depth=2)
        self.assertIn("an option inside a body does not carry", str(refusal.exception))

    def test_a_misspelled_key_is_refused(self):
        for spec in (
            {"fragment": "fragments/a.json5"},
            {"fragments": ["fragments/a.json5"], "adjustment": [{"target": "arm_inst"}]},
        ):
            with self.assertRaises(combinations.Json5Error) as refusal:
                self.parts(spec)
            self.assertIn("unknown key", str(refusal.exception))


class CombinationsTests(unittest.TestCase):
    def test_every_state_of_every_axis_in_reach_is_enumerated(self):
        combinations_found = combinations.launcher_selections(fleet_axes())
        # Stack: simulation (2) times scene commander (1 + unfilled) = 4, each
        # bare and with every copy: the plain join, then robot commander (2)
        # times recorder (1 + unfilled) = 4.
        self.assertEqual(len(combinations_found), 4 * (1 + 1 + 4))
        bare = [c for c in combinations_found if c.join_option is None]
        self.assertIn([("simulation", "mujoco"), ("scene_commander", None)], [c.words for c in bare])
        joined = [c for c in combinations_found if c.join_option == "openarm_v2"]
        self.assertIn(
            [("robot_commander", "xr_commander"), ("recorder", None)], [c.join_words for c in joined]
        )
        # The plain join writes no word and comes first, so where it runs the
        # same instances as a join that spells them out, it is the one launched.
        self.assertEqual(joined[0].join_words, [])
        self.assertEqual(
            combinations.render_words([("simulation", "mujoco"), ("scene_commander", None)]), "simulation=mujoco"
        )

    def test_the_plain_join_comes_first_and_is_enumerated_once(self):
        recorder = combinations.Axis("recorder", "zero_or_one", ["lerobot_recorder"])
        commander = combinations.Axis("robot_commander", "one", ["web_commander", "xr_commander"], deployed="web_commander")
        # Every selection writes a word: the plain join is one more.
        self.assertEqual(
            [combinations.render_words(selection) for selection in combinations.join_selections([commander])],
            ["", "robot_commander=web_commander", "robot_commander=xr_commander"],
        )
        # The selection leaving every axis unfilled writes no word: it is the
        # plain join, moved first and not repeated.
        self.assertEqual(
            [combinations.render_words(selection) for selection in combinations.join_selections([recorder])],
            ["", "recorder=lerobot_recorder"],
        )
        # An option with no axis of its own joins plain alone.
        self.assertEqual(combinations.join_selections([]), [[]])
        # A `one` axis without a default is named by every join: no plain join.
        version = combinations.Axis("hardware_version", "one", ["v1", "v2"])
        self.assertEqual(
            [combinations.render_words(selection) for selection in combinations.join_selections([version, recorder])],
            ["hardware_version=v1,recorder=lerobot_recorder", "hardware_version=v1",
             "hardware_version=v2,recorder=lerobot_recorder", "hardware_version=v2"],
        )

    def test_a_one_axis_with_a_single_option_is_no_choice(self):
        control = combinations.Axis("control", "one", ["shared"], deployed="shared")
        commander = combinations.Axis("robot_commander", "one", ["web_commander", "xr_commander"], deployed="web_commander")
        found = combinations.selections_of([control, commander])
        self.assertEqual(
            found,
            [[("robot_commander", "web_commander")], [("robot_commander", "xr_commander")]],
        )
        # A single option nothing deploys is a choice the launch has to
        # write out.
        undeployed = combinations.Axis("control", "one", ["shared"])
        self.assertEqual(
            combinations.selections_of([undeployed]), [[("control", "shared")]]
        )

    def test_a_one_axis_selected_by_the_file_brings_its_options_axes_in_reach(self):
        commander = combinations.Axis("robot_commander", "one", ["web_commander", "xr_commander"], deployed="web_commander")
        axes = [combinations.Axis("robot", "one", ["openarm_v2"], deployed="openarm_v2", nested={"openarm_v2": [commander]})]
        found = combinations.launcher_selections(axes)
        # The robot is the launcher's single deployed option, so the words
        # are its commander's alone.
        self.assertEqual(
            [c.words for c in found],
            [[("robot_commander", "web_commander")], [("robot_commander", "xr_commander")]],
        )

    def test_hub_inventory_covers_every_launcher_and_its_copies(self):
        root = Path(__file__).resolve().parents[2]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            combinations.command_enumerate(root)
        lines = output.getvalue().splitlines()

        def combos(launcher):
            return [line.split("\t") for line in lines if line.startswith(f"combo\t{launcher}\t")]

        # A join is plain (1) or under any selection of the robot's axes: an
        # OpenArm has two hardware versions, four commanders, a recorder, a rig
        # (off or on, `none` being the simulated rig's off) and a brain; an
        # SO-101 four commanders, a recorder and a rig. A selection its
        # constraints refuse is still enumerated, and reported refused when
        # it resolves. The physical OpenArm has no plain join: its hardware
        # version has no default, so every join names one.
        openarm, so101 = 2 * 4 * 2 * 2 * 2, 1 + 4 * 2 * 2
        # The simulated SO-101's commander axis is zero_or_one: its unfilled
        # state is the plain join, so no selection is counted twice.
        openarm_sim, so101_sim = 1 + openarm, 4 * 2 * 2
        # The physical launcher: the endpoint unfilled or on, each bare and
        # joined by any physical robot. It lists no robot, so no launch names
        # one.
        physical = combos("physical")
        self.assertEqual(len(physical), 2 * (1 + openarm + so101))
        self.assertIn(["combo", "physical", "robot_control=robot_control", "", "", "", "-"], physical)
        self.assertIn(["combo", "physical", "", "", "", "", "-"], physical)
        self.assertIn(
            ["combo", "physical", "", "", "so101", "robot_commander=xr_commander,recorder=lerobot_recorder,camera_rig=cameras", "-"],
            physical,
        )
        self.assertIn(
            ["combo", "physical", "robot_control=robot_control", "", "openarm", "hardware_version=v2,robot_commander=mcp_commander,camera_rig=cameras,brain=ai_brain_vla", "-"],
            physical,
        )
        self.assertEqual([c for c in physical if c[3]], [])
        # A simulation launcher's stack: MuJoCo, Isaac Sim and Waldo with and
        # without their scene commander (5), the robots' endpoint unfilled or
        # on; each launched with alpha under every selection of its axes (the
        # v2's, or the SO-101's) and joined by any simulated robot.
        stacks = (1 + 2 + 2) * 2
        alpha_openarm, alpha_so101 = 2 * 4 * 2 * 2 * 2, 4 * 2 * 2
        sim = combos("openarm_simulation")
        self.assertEqual(len(sim), stacks * (alpha_openarm + openarm_sim + so101_sim))
        self.assertIn(["combo", "openarm_simulation", "simulation=mujoco,robot_control=robot_control", "", "", "", "-"], sim)
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=mujoco,robot_control=robot_control,alpha.hardware_version=v2,alpha.robot_commander=mcp_commander,alpha.camera_rig=cameras_sim", "", "", "", "-"],
            sim,
        )
        self.assertIn(["combo", "openarm_simulation", "simulation=waldo,robot_control=robot_control", "", "so101_sim", "", "-"], sim)
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=waldo,scene_commander=web_scene_commander", "", "", "", "-"],
            sim,
        )
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=mujoco,robot_control=robot_control", "", "openarm_sim", "hardware_version=v2,robot_commander=mcp_commander,camera_rig=cameras_sim", "-"],
            sim,
        )
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=isaac_sim", "", "openarm_sim", "hardware_version=v2,robot_commander=xr_commander,recorder=lerobot_recorder,camera_rig=cameras_sim", "-"],
            sim,
        )
        references = next(line for line in lines if line.startswith("launcher\topenarm_simulation\t"))
        for reference in [
            "mcp/fragments/robot_control.json5",
            "openarm/fragments/openarm_sim.json5",
            "robot_commanders/fragments/mcp_commander.json5",
            "robot_commanders/fragments/xr_commander.json5",
            "so101/fragments/so101_sim.json5",
            "simulation/fragments/waldo.json5",
        ]:
            self.assertIn(reference, references)
        self.assertNotIn("world_control", references)
        # The so101_simulation launcher: the same selections, joined by any
        # simulated robot, plain and under every selection of its axes.
        so101_launcher = combos("so101_simulation")
        self.assertEqual(len(so101_launcher), stacks * (alpha_so101 + openarm_sim + so101_sim))
        self.assertIn(["combo", "so101_simulation", "simulation=waldo", "", "", "", "-"], so101_launcher)
        self.assertIn(
            ["combo", "so101_simulation", "simulation=waldo,robot_control=robot_control,alpha.robot_commander=so101_leader,alpha.recorder=lerobot_recorder", "", "", "", "-"],
            so101_launcher,
        )
        self.assertIn(["combo", "so101_simulation", "simulation=mujoco,robot_control=robot_control", "", "so101_sim", "", "-"], so101_launcher)
        self.assertIn(
            ["combo", "so101_simulation", "simulation=waldo,robot_control=robot_control", "", "so101_sim", "robot_commander=mcp_commander,camera_rig=cameras_sim", "-"],
            so101_launcher,
        )
        self.assertIn(
            ["combo", "so101_simulation", "simulation=isaac_sim,robot_control=robot_control", "", "openarm_sim", "hardware_version=v2,robot_commander=xr_commander,camera_rig=none", "-"],
            so101_launcher,
        )
        # The simulation_mcp launcher: the same stacks with the world's
        # endpoint on or off as well (the constraint refuses it off Waldo at
        # resolve), each launched bare, with no robot, naming either robot
        # at launch, and joined by any robot, plain and under every
        # selection of its axes. The file lists no copy, so no launch word
        # names one. The entries state the MCP commander and the rendered
        # rig for every copy of their option, so the copy a launch names and
        # the plain join are MCP robots, and the planner picks all of it up
        # from the index and the fragments alone.
        robot_control = combos("simulation_mcp")
        self.assertEqual(len(robot_control), stacks * 2 * (1 + 2 + openarm_sim + so101_sim))
        stack = "simulation=waldo,robot_control=robot_control,world_control=world_control"
        self.assertIn(["combo", "simulation_mcp", stack, "", "", "", "-"], robot_control)
        self.assertIn(["combo", "simulation_mcp", stack, "openarm_sim", "", "", "-"], robot_control)
        self.assertIn(["combo", "simulation_mcp", stack, "so101_sim", "", "", "-"], robot_control)
        self.assertIn(["combo", "simulation_mcp", stack, "", "openarm_sim", "", "-"], robot_control)
        self.assertEqual([c for c in robot_control if "alpha." in c[2]], [])
        self.assertIn(
            ["combo", "simulation_mcp", stack, "", "so101_sim", "robot_commander=mcp_commander,camera_rig=cameras_sim", "-"],
            robot_control,
        )
        self.assertIn(
            ["combo", "simulation_mcp", stack, "", "openarm_sim", "hardware_version=v2,robot_commander=mcp_commander,camera_rig=cameras_sim,brain=ai_brain_vla", "-"],
            robot_control,
        )
        self.assertIn(
            ["combo", "simulation_mcp", "simulation=waldo,scene_commander=web_scene_commander,robot_control=robot_control,world_control=world_control", "", "", "", "-"],
            robot_control,
        )
        self.assertIn(["combo", "simulation_mcp", "simulation=mujoco,robot_control=none,world_control=none", "", "", "", "-"], robot_control)
        self.assertIn(["combo", "simulation_mcp", "simulation=mujoco,robot_control=robot_control,world_control=none", "so101_sim", "", "", "-"], robot_control)
        references = next(line for line in lines if line.startswith("launcher\tsimulation_mcp\t"))
        for reference in [
            "mcp/fragments/robot_control.json5",
            "openarm/fragments/openarm_sim.json5",
            "robot_commanders/fragments/mcp_commander.json5",
            "so101/fragments/so101_sim.json5",
            "simulation/fragments/waldo.json5",
            "mcp/fragments/world_control.json5",
            "common/fragments/none.json5",
        ]:
            self.assertIn(reference, references)
    def test_fragment_files_are_named_for_their_option(self):
        root = Path(__file__).resolve().parents[2]
        for document in sorted(root.rglob("*.json5")):
            if document.name == "peppy_repository.json5" or "examples" in document.parts or ".github" in document.parts:
                continue
            for axis in combinations.load_json5(document, str(document)).get("components", []):
                for option, spec in axis["options"].items():
                    # An option naming several files ends with its own; the
                    # ones before it are shared with another option. An
                    # option written in place names no file.
                    files = [
                        part
                        for part in combinations.fragment_parts(spec, str(document))
                        if isinstance(part, str)
                    ]
                    if not files:
                        continue
                    self.assertEqual(Path(files[-1]).stem, option,
                                     f"{document}: {axis['name']}={option} selects {files[-1]}")

    def test_a_fragment_adjusts_only_instances_it_deploys(self):
        root = Path(__file__).resolve().parents[2]
        for path in sorted(root.rglob("*.json5")):
            if ".github" in path.parts:
                continue
            document = combinations.load_json5(path, str(path))
            if not isinstance(document, dict) or document.get("peppy_schema") != "launcher_fragment/v1":
                continue
            deployed = set()
            def walk(node):
                for entry in node.get("deployments", []) or []:
                    if "source" in entry and "instances" in entry:
                        deployed.update(instance["instance_id"] for instance in entry["instances"])
                for axis in node.get("components", []) or []:
                    for spec in axis["options"].values():
                        for part in combinations.fragment_parts(spec, path):
                            if isinstance(part, dict):
                                walk(part)
            walk(document)
            for adjustment in document.get("adjustments", []) or []:
                self.assertIn(
                    adjustment["target"], deployed,
                    f"{path.relative_to(root)} adjusts `{adjustment['target']}`, which it does not deploy; "
                    "the launcher writes what crosses out of a fragment, under the option that selects it",
                )

    def test_fragment_paths_follow_domain_layout_and_are_all_referenced(self):
        root = Path(__file__).resolve().parents[2]
        index = combinations.load_json5(root / "peppy_repository.json5", "index")
        references = set()
        for entry in index["launchers"].values():
            references.update(combinations.read_launcher(root, entry["path"]).references)
        fragments = set()
        for path in root.rglob("*.json5"):
            document = combinations.load_json5(path, str(path))
            if not isinstance(document, dict) or document.get("peppy_schema") != "launcher_fragment/v1":
                continue
            relative = path.relative_to(root)
            self.assertEqual(relative.parts[1], "fragments", str(relative))
            self.assertEqual(len(relative.parts), 3, str(relative))
            fragments.add(relative.as_posix())
        self.assertEqual(references, fragments)

    def test_both_robots_share_capabilities_and_own_their_tuning(self):
        root = Path(__file__).resolve().parents[2]
        # The headset is one fragment every robot shares; the recorder is one
        # per family, shared by the physical and the simulated robot. What a
        # robot needs of the headset, its rate and gripper travel, the
        # launcher writes under the robot; the recorder's rate and limb links
        # are the family's, written where the family deploys it.
        for robot, launcher, command_rate in [
            ("openarm", "physical.json5", 100),
            ("openarm_sim", "openarm_simulation.json5", 100),
            ("so101", "physical.json5", 60),
            ("so101_sim", "so101_simulation.json5", 60),
        ]:
            with self.subTest(robot=robot):
                references = combinations.read_launcher(root, launcher).references
                self.assertIn("robot_commanders/fragments/xr_commander.json5", references)
                robot_file = combinations.load_json5(root / ROBOT_FILES[robot], robot)
                axes = {axis["name"]: axis for axis in robot_file["components"]}
                self.assertEqual({"robot_commander", "recorder", "camera_rig"} - set(axes), set())
                self.assertEqual(axes["recorder"]["options"], {"lerobot_recorder": "lerobot_recorder.json5"})
                document = combinations.load_json5(root / launcher, launcher)
                commander = next(a for a in robot_option(document, robot)["adjustments"]
                                 if a["target"] == "commander_inst"
                                 and a.get("when") == {"robot_commander": "xr_commander"})
                self.assertEqual(commander["set_arguments"]["command_rate_hz"], command_rate)
                self.assertEqual(commander["set_arguments"]["gripper_open_fraction"], 0.5)
        for path, fps in [("openarm/fragments/lerobot_recorder.json5", 15),
                          ("so101/fragments/lerobot_recorder.json5", 30)]:
            with self.subTest(path=path):
                recorder = combinations.load_json5(root / path, path)
                self.assertEqual(instance_of(recorder, "recorder_inst")["arguments"], {"fps": fps})
        # The shared commander carries no robot's tuning of its own; the one
        # write it makes is on its own instance, the record button.
        document = combinations.load_json5(
            root / "robot_commanders/fragments/xr_commander.json5", "xr_commander")
        self.assertNotIn("arguments", instance_of(document, "commander_inst"))
        self.assertEqual([a["target"] for a in document["adjustments"]], ["commander_inst"])


    def test_a_deployed_copy_selects_its_own_axes_with_copy_scoped_launch_words(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                {"name": "robot", "cardinality": "zero_or_more", "options": {"openarm_v2": {
                    "components": [
                        {"name": "robot_commander", "options": {"web_commander": {}, "xr_commander": {}}},
                        {"name": "recorder", "cardinality": "zero_or_one", "options": {"lerobot_recorder": {}}},
                    ],
                    "deployments": [{"robot_commander": "web_commander"}],
                }}},
            ], "deployments": [
                {"robot": "openarm_v2", "instances": [{"instance_id": "alpha"}]},
            ]}))
            launcher = combinations.read_launcher(directory, "fleet.json5")
            self.assertEqual(launcher.copies, [combinations.Copy("alpha", "robot", "openarm_v2", {})])
            found = combinations.launcher_selections(launcher.axes, launcher.copies)
            # The copy runs the web commander with no recorder, which the
            # bare launch already covers; its three other states are words.
            self.assertEqual(
                [combinations.render_words(c.words) for c in found if c.join_option is None],
                [
                    "",
                    "alpha.robot_commander=web_commander,alpha.recorder=lerobot_recorder",
                    "alpha.robot_commander=xr_commander,alpha.recorder=lerobot_recorder",
                    "alpha.robot_commander=xr_commander",
                ],
            )

    def test_a_copys_with_names_the_selection_the_bare_launch_covers(self):
        commander = combinations.Axis(
            "robot_commander", "one", ["web_commander", "xr_commander"], deployed="web_commander")
        axes = [combinations.Axis(
            "robot", "zero_or_more", ["openarm_v2"], nested={"openarm_v2": [commander]})]
        copy = combinations.Copy("alpha", "robot", "openarm_v2", {"robot_commander": "xr_commander"})
        self.assertEqual(
            [combinations.render_words(words) for words in combinations.copy_selections(axes, copy)],
            ["alpha.robot_commander=web_commander"],
        )


    def test_a_one_or_more_axis_the_file_fills_enumerates_as_zero_or_more_does(self):
        commander = combinations.Axis("robot_commander", "one", ["web_commander", "xr_commander"], deployed="web_commander")
        alpha = combinations.Copy("alpha", "robot", "openarm_v2")
        found = {
            cardinality: combinations.launcher_selections(
                [combinations.Axis("robot", cardinality, ["openarm_v2"], deployed="openarm_v2",
                                   nested={"openarm_v2": [commander]})],
                [alpha],
            )
            for cardinality in ("zero_or_more", "one_or_more")
        }
        self.assertEqual(found["one_or_more"], found["zero_or_more"])
        # The copy the file deploys meets the floor, so the launch comes bare.
        self.assertEqual(found["one_or_more"][0], combinations.Combination([]))

    def test_a_one_or_more_axis_the_file_leaves_empty_is_filled_at_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                {"name": "simulation", "cardinality": "zero_or_one", "options": {"waldo": {}}},
                {"name": "robot", "cardinality": "one_or_more", "options": {"openarm_v1": {}, "openarm_v2": {}}},
                {"name": "cameras", "cardinality": "zero_or_more", "options": {"wrist": {}}},
            ], "deployments": [{"robot": "openarm_v2"}, {"cameras": "wrist"}]}))
            launcher = combinations.read_launcher(directory, "fleet.json5")
        self.assertEqual(launcher.copies, [])
        self.assertEqual(launcher.unlisted, ("openarm_v2", "wrist"))
        found = combinations.launcher_selections(launcher.axes, launcher.copies, launcher.unlisted)
        # No launch comes bare and none joins onto a running stack: every one
        # names a robot with `--join`, each option in turn, under every
        # selection of the stack, alone and with the unlisted camera beside it.
        self.assertEqual(
            [(c.words, c.launch_joins, c.join_option) for c in found],
            [
                ([("simulation", "waldo")], ("openarm_v1",), None),
                ([("simulation", "waldo")], ("openarm_v1", "wrist"), None),
                ([("simulation", "waldo")], ("openarm_v2",), None),
                ([("simulation", "waldo")], ("openarm_v2", "wrist"), None),
                ([("simulation", None)], ("openarm_v1",), None),
                ([("simulation", None)], ("openarm_v1", "wrist"), None),
                ([("simulation", None)], ("openarm_v2",), None),
                ([("simulation", None)], ("openarm_v2", "wrist"), None),
            ],
        )
        # An axis the file has no entry for is named at launch all the same.
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                {"name": "robot", "cardinality": "one_or_more", "options": {"openarm_v2": {}}},
            ]}))
            launcher = combinations.read_launcher(directory, "fleet.json5")
        found = combinations.launcher_selections(launcher.axes, launcher.copies, launcher.unlisted)
        self.assertEqual([(c.words, c.launch_joins) for c in found], [([], ("openarm_v2",))])

    def test_an_entry_listing_no_copy_is_an_option_a_launch_names_with_join(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                {"name": "robot", "cardinality": "zero_or_more", "options": {"openarm_v2": {}}},
            ], "deployments": [{"robot": "openarm_v2"}]}))
            launcher = combinations.read_launcher(directory, "fleet.json5")
        self.assertEqual(launcher.copies, [])
        self.assertEqual(launcher.unlisted, ("openarm_v2",))
        # The launch comes bare and with the robot named, ahead of the joins.
        found = combinations.launcher_selections(launcher.axes, launcher.copies, launcher.unlisted)
        self.assertEqual(
            [(c.words, c.launch_joins, c.join_option) for c in found],
            [([], (), None), ([], ("openarm_v2",), None), ([], (), "openarm_v2")])

    def test_waldo_runs_the_debug_inspector_whatever_the_launch_selects(self):
        # The 3D viewer and the scene services are the debug inspector's, so
        # the fragment runs it from the first launch of every launcher
        # deploying Waldo;
        # no adjustment of the fragment or of a launcher touches
        # `debug_inspector` or `plugins`.
        # Every robot joins Waldo bringing its own model, so the world it
        # opens, the stage, is the fragment's too, and no launcher sets one.
        pinned = {"world", "debug_inspector", "plugins"}
        root = Path(__file__).resolve().parents[2]
        for launcher in ROBOT_LAUNCHERS:
            document = combinations.load_json5(root / launcher, launcher)
            written = list(document.get("adjustments", []))
            for option in axis_of(document, "robot")["options"].values():
                written += option["adjustments"]
            for adjustment in written:
                self.assertFalse(pinned & set(adjustment.get("set_arguments", {})), launcher)
        waldo = combinations.load_json5(root / "simulation/fragments/waldo.json5", "waldo")
        arguments = instance_of(waldo, "simulation_inst")["arguments"]
        self.assertEqual((arguments["world"], arguments["debug_inspector"]), ("stage", True))
        self.assertIn("hand_teleop", arguments["plugins"].split(","))
        self.assertNotIn("adjustments", waldo)

    def test_the_scene_commander_edits_and_observes_the_simulation_that_declares_it(self):
        root = Path(__file__).resolve().parents[2]
        # Each simulation that declares the commander offers the one panel
        # file, beside simulation_inst, the instance its scene_manipulation
        # and object_state links both name.
        for simulation in ["waldo", "isaac_sim"]:
            with self.subTest(simulation=simulation):
                path = f"simulation/fragments/{simulation}.json5"
                fragment = combinations.load_json5(root / path, path)
                commander = axis_of(fragment, "scene_commander")
                self.assertEqual(commander["options"], {"web_scene_commander": "web_scene_commander.json5"})
                self.assertEqual(fragment["deployments"][0]["instances"][0]["instance_id"], "simulation_inst")
                self.assertNotIn("adjustments", fragment)
        panel = combinations.load_json5(
            root / "simulation/fragments/web_scene_commander.json5", "web_scene_commander")
        instance = instance_of(panel, "scene_commander_inst")
        self.assertEqual(instance["links"]["simulation"], "simulation_inst")
        self.assertEqual(instance["links"]["objects"], "simulation_inst")
        self.assertEqual(instance["framework"], {"clock": "simulation"})

    def test_the_scene_commander_binds_lighting_materials_and_cameras_on_waldo_alone(self):
        root = Path(__file__).resolve().parents[2]
        # The page carries a panel for every contract the simulation serves:
        # the three slots are written vacant, and the panel's own adjustment
        # binds them to the simulation that implements them, Waldo.
        panel = combinations.load_json5(
            root / "simulation/fragments/web_scene_commander.json5", "web_scene_commander")
        instance = instance_of(panel, "scene_commander_inst")
        self.assertEqual(set(instance["links"]), {"simulation", "objects", "lighting", "materials", "cameras"})
        for slot in ["lighting", "materials", "cameras"]:
            self.assertEqual(set(instance["links"][slot]), {"vacant"})
            self.assertTrue(instance["links"][slot]["vacant"].strip())
        self.assertEqual(panel["adjustments"], [{
            "target": "scene_commander_inst", "when": {"simulation": "waldo"},
            "set_links": {"lighting": "simulation_inst", "materials": "simulation_inst", "cameras": "simulation_inst"},
        }])
        for other in ["mujoco"]:
            with self.subTest(simulation=other):
                fragment = combinations.load_json5(root / f"simulation/fragments/{other}.json5", other)
                self.assertNotIn("scene_commander", [axis["name"] for axis in fragment.get("components", [])])


    def test_the_launcher_lists_each_robot_on_the_stacks_server(self):
        root = Path(__file__).resolve().parents[2]
        # Every target of the server is a set the robots fill, and the
        # server is the launcher's: the launcher lists a robot with its
        # identity and how it stands, adds the moves under the MCP
        # commander, the cameras under the rig, and the brain and the
        # recorder whenever they run. None of it is guarded on the server:
        # an entry naming an instance the launch does not run is skipped.
        # Every robot's backbone answers where its design lets it work; only
        # the OpenArm's says how close its arms stand and where its cameras
        # are mounted.
        readouts_of_every_robot = {
            "identity": ["init_inst"], "limb_state": ["backbone_inst"], "workspace": ["backbone_inst"]}
        readouts = {
            "openarm": {**readouts_of_every_robot,
                        "collision": ["backbone_inst"], "camera_mounts": ["backbone_inst"]},
            "so101": readouts_of_every_robot,
        }
        for path, robots in ROBOT_LAUNCHERS.items():
            document = combinations.load_json5(root / path, path)
            for robot in robots:
                with self.subTest(path=path, robot=robot):
                    entries = robot_option(document, robot)["adjustments"]
                    listing, = [a for a in entries if a["target"] == SERVER and "when" not in a]
                    self.assertEqual(listing, {"target": SERVER, "add_links": readouts[family_of(robot)]})
                    moves, = [a for a in entries
                              if a["target"] == SERVER and a.get("when") == {"robot_commander": "mcp_commander"}]
                    self.assertEqual(moves["add_links"], {
                        "postures": ["backbone_inst"], "limb_motion": ["backbone_inst"]})
                    episodes, = [a for a in entries
                                 if a["target"] == SERVER and a.get("when") == {"recorder": "lerobot_recorder"}]
                    self.assertEqual(episodes["add_links"], {"recorder": ["recorder_inst"]})

    def test_the_shared_mcp_commander_deploys_nothing_and_requires_the_endpoint(self):
        root = Path(__file__).resolve().parents[2]
        # What it needs of a robot the launcher writes.
        commander = combinations.load_json5(
            root / "robot_commanders/fragments/mcp_commander.json5", "mcp_commander")
        self.assertEqual(commander["deployments"], [])
        self.assertNotIn("adjustments", commander)
        rule, = commander["constraints"]
        self.assertEqual(rule["requires"], [{"robot_control": "robot_control"}])
        self.assertIn("launch with robot_control", rule["reason"])

    def test_the_brains_tools_are_listed_under_the_mcp_commander(self):
        root = Path(__file__).resolve().parents[2]
        brain = combinations.load_json5(root / "openarm/fragments/ai_brain_vla.json5", "ai_brain_vla")
        self.assertNotIn("adjustments", brain)
        for path in ROBOT_LAUNCHERS:
            document = combinations.load_json5(root / path, path)
            for robot in ROBOT_LAUNCHERS[path]:
                if family_of(robot) != "openarm":
                    continue
                with self.subTest(path=path, robot=robot):
                    thinking, = [a for a in robot_option(document, robot)["adjustments"]
                                 if a["target"] == SERVER
                                 and a.get("when") == {"brain": "ai_brain_vla",
                                                       "robot_commander": "mcp_commander"}]
                    self.assertEqual(thinking["add_links"], {
                        "item_perception": ["brain_inst"], "item_manipulation": ["brain_inst"]})

    def test_the_simulated_brain_sees_through_the_rendered_rig(self):
        root = Path(__file__).resolve().parents[2]
        # The simulation runs where a GPU is, so the launcher gives a
        # simulated brain the local perception pipeline and the chest camera
        # the rendered rig binds; a physical brain keeps the fragment's "none".
        sees = [
            {"target": "brain_inst", "when": {"brain": "ai_brain_vla"},
             "set_arguments": {"perception_backend": "sam3_siglip"}},
            {"target": "brain_inst", "when": {"brain": "ai_brain_vla", "camera_rig": "cameras_sim"},
             "set_links": {"camera": "chest", "geometry": "chest"}},
        ]
        for path, robots in ROBOT_LAUNCHERS.items():
            document = combinations.load_json5(root / path, path)
            for robot in robots:
                if family_of(robot) != "openarm":
                    continue
                with self.subTest(path=path, robot=robot):
                    writes = [a for a in robot_option(document, robot)["adjustments"]
                              if a["target"] == "brain_inst" and "set_framework" not in a]
                    self.assertEqual(writes, sees if robot == "openarm_sim" else [])

    def test_the_backbone_reads_the_geometry_of_its_rigs_chest_camera(self):
        root = Path(__file__).resolve().parents[2]
        # The workspace answers judge what the camera the robot finds items
        # with sees from that camera's geometry, a slot the robot file writes
        # vacant: a robot without a rig has no chest camera to read. Each
        # robot binds its rig's chest camera whenever the rig runs, whatever
        # its brain: the ZED Mini on hardware, the rendered relay in a
        # simulation. The robot file deploys the backbone and declares the
        # rig, so it writes the binding, and no launcher writes the slot.
        # A v1's design carries no perception camera, so hardware binds the
        # chest on a v2 alone; no simulation renders a rig on v1 links.
        for robot, when, rig_file, chest_node in [
            ("openarm", {"camera_rig": "cameras", "hardware_version": "v2"}, "cameras.json5", "zed_camera"),
            ("openarm_sim", {"camera_rig": "cameras_sim"}, "cameras_sim.json5", "sim_rgbd_camera"),
        ]:
            with self.subTest(robot=robot):
                document = combinations.load_json5(root / ROBOT_FILES[robot], robot)
                vacancy = instance_of(document, "backbone_inst")["links"]["perception_geometry"]
                self.assertEqual(set(vacancy), {"vacant"})
                self.assertTrue(vacancy["vacant"].strip())
                self.assertEqual(
                    [a for a in document["adjustments"] if writes_link(a, "perception_geometry")],
                    [{"target": "backbone_inst", "when": when,
                      "set_links": {"perception_geometry": "chest"}}])
                # The camera the binding names is the rig's RGB-D node, which
                # serves camera_geometry.
                rig = option_file(ROBOT_FILES[robot], rig_file)
                chest, = [deployment["source"] for deployment in rig["deployments"]
                          for instance in deployment["instances"] if instance["instance_id"] == "chest"]
                self.assertEqual(chest, {"name": chest_node, "tag": "v1"})
        for path, robots in ROBOT_LAUNCHERS.items():
            document = combinations.load_json5(root / path, path)
            for robot in robots:
                with self.subTest(path=path, robot=robot):
                    self.assertEqual([a for a in robot_option(document, robot)["adjustments"]
                                      if writes_link(a, "perception_geometry")], [])

    def test_the_record_button_binds_on_the_commander_that_carries_one(self):
        root = Path(__file__).resolve().parents[2]
        # The panel and the headset bind the recorder on their own instance;
        # the KER and the SO-101 leader carry no button and record over the
        # endpoint alone.
        button = {"target": "commander_inst", "when": {"recorder": "lerobot_recorder"},
                  "add_links": {"recorder": ["recorder_inst"]}}
        for path, carries in [("openarm/fragments/web_commander.json5", True),
                              ("robot_commanders/fragments/xr_commander.json5", True),
                              ("openarm/fragments/ker_commander.json5", False),
                              ("so101/fragments/so101_leader.json5", False)]:
            with self.subTest(path=path):
                leader = combinations.load_json5(root / path, path)
                buttons = [a for a in leader.get("adjustments", []) if "recorder" in a.get("add_links", {})]
                self.assertEqual(buttons, [button] if carries else [])
        for path, robots in ROBOT_LAUNCHERS.items():
            document = combinations.load_json5(root / path, path)
            for robot in robots:
                with self.subTest(path=path, robot=robot):
                    self.assertEqual([a for a in robot_option(document, robot)["adjustments"]
                                      if a["target"] == "commander_inst" and "recorder" in a.get("add_links", {})], [])

    def test_a_simulated_robot_reads_the_simulations_clock_everywhere(self):
        root = Path(__file__).resolve().parents[2]
        # A robot writes nothing onto the server, and a simulated one declares
        # the simulation's clock on the instances it deploys where it deploys
        # them; the commander, the recorder and the brain are shared with the
        # physical robot, so the launcher binds those.
        for robot, path in ROBOT_FILES.items():
            with self.subTest(path=path):
                document = combinations.load_json5(root / path, path)
                self.assertEqual([a for a in document["adjustments"] if a["target"] == SERVER], [])
                self.assertEqual([a for a in document["adjustments"] if "set_framework" in a], [])
                simulated = robot.endswith("_sim")
                # An OpenArm's initializer is its hardware version option's; the
                # initializer test holds those to the same clock.
                own = ["backbone_inst"] + (["init_inst"] if family_of(robot) == "so101" else [])
                for instance_id in own:
                    self.assertEqual(
                        instance_of(document, instance_id).get("framework"),
                        {"clock": "simulation"} if simulated else None, instance_id)
        for path in SIMULATION_LAUNCHERS:
            document = combinations.load_json5(root / path, path)
            for robot in ROBOT_LAUNCHERS[path]:
                with self.subTest(path=path, robot=robot):
                    clocked = [a for a in robot_option(document, robot)["adjustments"] if "set_framework" in a]
                    expected = ["commander_inst", "recorder_inst"] + (["brain_inst"] if family_of(robot) == "openarm" else [])
                    self.assertCountEqual(
                        clocked, [{"target": target, "set_framework": {"clock": "simulation"}} for target in expected])

    def test_the_launcher_binds_the_leaders_telemetry_and_the_simulated_panels_cap(self):
        root = Path(__file__).resolve().parents[2]
        # Real drivers report per-motor health and operator alerts, and the
        # panel and the headset show them; a simulated robot has no motors,
        # and its panel streams the cap the simulated backbone starts on.
        openarm_limbs = ["left_arm_inst", "right_arm_inst", "left_gripper_inst", "right_gripper_inst"]
        physical = combinations.load_json5(root / "physical.json5", "physical")
        for robot, commanders, sources in [
            ("openarm", ["web_commander", "xr_commander"], openarm_limbs),
            ("so101", "xr_commander", ["follower_inst"]),
        ]:
            with self.subTest(robot=robot):
                telemetry, = [a for a in robot_option(physical, robot)["adjustments"]
                              if "motor_health" in a.get("add_links", {})]
                self.assertEqual(telemetry, {
                    "target": "commander_inst", "when": {"robot_commander": commanders},
                    "add_links": {"motor_health": sources, "alerts": sources}})
        cap = instance_of(combinations.load_json5(root / ROBOT_FILES["openarm_sim"], "openarm_sim"),
                          "backbone_inst")["arguments"]["max_ee_velocity_m_s"]
        for path in SIMULATION_LAUNCHERS:
            document = combinations.load_json5(root / path, path)
            for robot in ROBOT_LAUNCHERS[path]:
                with self.subTest(path=path, robot=robot):
                    entries = robot_option(document, robot)["adjustments"]
                    self.assertEqual([a for a in entries if "motor_health" in a.get("add_links", {})], [])
                    caps = [a for a in entries if "max_ee_velocity_m_s" in a.get("set_arguments", {})]
                    self.assertEqual(caps, [{
                        "target": "commander_inst", "when": {"robot_commander": "web_commander"},
                        "set_arguments": {"max_ee_velocity_m_s": cap}}] if family_of(robot) == "openarm" else [])

    def test_the_rig_reaches_the_recorder_in_its_declared_order(self):
        root = Path(__file__).resolve().parents[2]
        # Binding order fixes the dataset's feature order, so the recorder's
        # camera links follow the rig axis's `provides`, the color cameras
        # first and the one carrying depth last.
        for path, robots in ROBOT_LAUNCHERS.items():
            document = combinations.load_json5(root / path, path)
            for robot in robots:
                with self.subTest(path=path, robot=robot):
                    rig = "cameras" if path == "physical.json5" else "cameras_sim"
                    relays = [instance["instance_id"] for deployment in option_file(ROBOT_FILES[robot], f"{rig}.json5")["deployments"]
                              for instance in deployment["instances"]]
                    filmed, = [a for a in robot_option(document, robot)["adjustments"]
                               if a["target"] == "recorder_inst" and a.get("when") == {"camera_rig": rig}]
                    color = relays[:-1] if family_of(robot) == "openarm" else relays
                    expected = {"color_cameras": color} | ({"rgbd_cameras": relays[-1:]} if family_of(robot) == "openarm" else {})
                    self.assertEqual(filmed["add_links"], expected)

    def test_the_server_is_one_per_stack_and_takes_no_links_of_its_own(self):
        root = Path(__file__).resolve().parents[2]
        fragment = combinations.load_json5(root / "mcp/fragments/robot_control.json5", "robot_control")
        deployment, = fragment["deployments"]
        self.assertEqual(deployment["source"], {"exposures": ["robot_control:v1"]})
        # Every target is a set the robots fill, so the server takes no link
        # of its own.
        self.assertEqual(deployment["instances"], [{"instance_id": SERVER, "arguments": {"port": 8900}}])
        self.assertNotIn("adjustments", fragment)
        # Every launcher with robots declares the endpoint: simulation_mcp as
        # a `one` axis it deploys, with `none` to switch it off, the others
        # as a `zero_or_one` axis `--with robot_control` turns on.
        for path, robots in ROBOT_LAUNCHERS.items():
            with self.subTest(path=path):
                document = combinations.load_json5(root / path, path)
                axes = {axis["name"]: axis for axis in document["components"]}
                prefix = "../" * (len(Path(path).parts) - 1)
                deployed = path == "mcp/simulation_mcp.json5"
                self.assertNotIn("provides", axes["robot_control"])
                self.assertEqual(axes["robot_control"]["cardinality"], "one" if deployed else "zero_or_one")
                options = {"robot_control": "fragments/robot_control.json5" if deployed else f"{prefix}mcp/fragments/robot_control.json5"}
                if deployed:
                    options["none"] = f"{prefix}common/fragments/none.json5"
                self.assertEqual(axes["robot_control"]["options"], options)
                self.assertEqual(combinations.option_entries(document, path).get("robot_control"), "robot_control" if deployed else None)
                self.assertEqual(axes["robot"]["cardinality"], "zero_or_more")
                self.assertEqual(set(axes["robot"]["options"]), set(robots))
                # A robot option names the file the robot is written in and
                # carries the launcher's writes onto the stack beside it.
                for robot in robots:
                    family = family_of(robot)
                    own = Path(path).parent.name == family
                    option = axes["robot"]["options"][robot]
                    self.assertEqual(
                        option["fragments"],
                        [f"fragments/{robot}.json5" if own else f"{prefix}{family}/fragments/{robot}.json5"])
        # The server reads one clock: a simulation launcher binds it to the
        # simulation's, each simulated robot binding the instances it
        # deploys; the physical launcher adjusts nothing at the top level
        # and its robots read wall time. The MCP launcher turns rendering on
        # itself, so a robot joined onto it gets its cameras.
        for path in SIMULATION_LAUNCHERS:
            with self.subTest(path=path):
                document = combinations.load_json5(root / path, path)
                expected = [RENDERING_ON, SERVER_CLOCK] if path == "mcp/simulation_mcp.json5" else [SERVER_CLOCK]
                self.assertEqual(document["adjustments"], expected)
        physical = combinations.load_json5(root / "physical.json5", "physical")
        self.assertNotIn("adjustments", physical)
        self.assertEqual(physical["deployments"], [])
        # Every rig is listed under the MCP commander: color and depth on
        # hardware, where neither node describes a profile and the launcher
        # lists no geometry, and all four targets in simulation.
        for path, robot, links in [
            ("physical.json5", "openarm", {
                "camera": ["wrist_left", "wrist_right"], "depth_camera": ["chest"]}),
            ("openarm_simulation.json5", "openarm_sim", {
                "camera": ["wrist_left", "wrist_right"], "depth_camera": ["chest"],
                "camera_profile": ["wrist_left", "wrist_right", "chest"],
                "camera_geometry": ["wrist_left", "wrist_right", "chest"]}),
            ("physical.json5", "so101", {"camera": ["wrist"]}),
            ("so101_simulation.json5", "so101_sim", {
                "camera": ["wrist"], "camera_profile": ["wrist"], "camera_geometry": ["wrist"]}),
        ]:
            with self.subTest(path=path, robot=robot):
                document = combinations.load_json5(root / path, path)
                rig = "cameras" if path == "physical.json5" else "cameras_sim"
                listing, = [a for a in robot_option(document, robot)["adjustments"]
                            if a["target"] == SERVER
                            and a.get("when") == {"camera_rig": rig, "robot_commander": "mcp_commander"}]
                self.assertEqual(listing["add_links"], links)


    def test_the_world_endpoint_is_the_mcp_launchers_axis(self):
        root = Path(__file__).resolve().parents[2]
        fragment = combinations.load_json5(root / "mcp/fragments/world_control.json5", "world_control")
        deployment, = fragment["deployments"]
        self.assertEqual(deployment["source"], {"exposures": ["simulation:v1"]})
        instance, = deployment["instances"]
        # An id and a port of its own: it runs beside the robots' endpoint
        # (robot_control_inst, 8900) and the browser scene panel
        # (scene_commander_inst).
        self.assertEqual(instance["instance_id"], "world_control_inst")
        self.assertEqual(instance["arguments"], {"port": 8902})
        self.assertEqual(instance["links"], {
            "scene": "simulation_inst", "controls": "simulation_inst",
            "lighting": "simulation_inst", "materials": "simulation_inst",
            "view": "simulation_inst", "clock": "simulation_inst",
            "workspace": "simulation_inst"})
        self.assertEqual(instance["framework"], {"clock": "simulation"})
        # It binds nothing of a robot copy and adjusts nothing.
        self.assertNotIn("adjustments", fragment)
        self.assertNotIn("components", fragment)
        # The axis is the MCP launcher's, deployed by the file with `none`
        # to switch it off, and Waldo alone implements the object controls,
        # lighting, materials, view, clock and workspace contracts it binds,
        # so the launcher requires Waldo beside it. No fragment declares the
        # axis.
        path = "mcp/simulation_mcp.json5"
        document = combinations.load_json5(root / path, path)
        axes = {axis["name"]: axis for axis in document["components"]}
        self.assertEqual(axes["world_control"], {
            "name": "world_control", "cardinality": "one",
            "options": {"world_control": "fragments/world_control.json5", "none": "../common/fragments/none.json5"},
        })
        self.assertEqual(combinations.option_entries(document, path)["world_control"], "world_control")
        rule, = document["constraints"]
        self.assertEqual((rule["when"], rule["requires"]), ({"world_control": "world_control"}, [{"simulation": "waldo"}]))
        self.assertIn("world_control=none", rule["reason"])
        for engine in ["waldo", "mujoco", "isaac_sim"]:
            fragment = combinations.load_json5(root / f"simulation/fragments/{engine}.json5", engine)
            self.assertNotIn("world_control", [axis["name"] for axis in fragment.get("components", [])])
        for other in ROBOT_LAUNCHERS:
            if other != path:
                document = combinations.load_json5(root / other, other)
                self.assertNotIn("world_control", [axis["name"] for axis in document["components"]])
        # `none` is the empty option that switches an axis off.
        self.assertEqual(
            combinations.load_json5(root / "common/fragments/none.json5", "none"),
            {"peppy_schema": "launcher_fragment/v1"})

    def test_every_robot_offers_the_shared_mcp_commander_and_a_rig_with_a_consumer_rule(self):
        root = Path(__file__).resolve().parents[2]
        shared = "../../robot_commanders/fragments/mcp_commander.json5"
        for path, cardinality, commanders, rig, rigs in [
            ("openarm/fragments/openarm.json5", None,
             ["web_commander", "xr_commander", "mcp_commander", "ker_commander"], "cameras", ["cameras"]),
            ("openarm/fragments/openarm_sim.json5", None,
             ["web_commander", "xr_commander", "mcp_commander", "ker_commander"], "cameras_sim", ["none", "cameras_sim"]),
            ("so101/fragments/so101.json5", None,
             ["so101_leader", "xr_commander", "mcp_commander", "none"], "cameras", ["cameras"]),
            ("so101/fragments/so101_sim.json5", "zero_or_one",
             ["so101_leader", "xr_commander", "mcp_commander"], "cameras_sim", ["cameras_sim"]),
        ]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                axes = {axis["name"]: axis for axis in robot["components"]}
                self.assertCountEqual(axes["robot_commander"]["options"], commanders)
                self.assertEqual(axes["robot_commander"]["options"]["mcp_commander"], shared)
                self.assertEqual(axes["robot_commander"].get("cardinality"), cardinality)
                self.assertCountEqual(axes["camera_rig"]["options"], rigs)
                # The rig's streams need a consumer: the recorder, the
                # headset, or the model behind the MCP commander.
                consumer, = [c for c in robot["constraints"]
                             if c.get("when") == {"camera_rig": rig} and "hardware_version" not in str(c["requires"])]
                requires = [{"recorder": "lerobot_recorder"}, {"robot_commander": ["xr_commander", "mcp_commander"]}]
                consumers = "lerobot_recorder, xr_commander or mcp_commander"
                if path == "openarm/fragments/openarm_sim.json5":
                    # The rendered OpenArm rig also feeds the brain's camera
                    # slot, so there the brain is a consumer too.
                    requires.append({"brain": "ai_brain_vla"})
                    consumers = "lerobot_recorder, xr_commander, mcp_commander or ai_brain_vla"
                self.assertEqual(consumer["requires"], requires)
                self.assertIn(f"select {consumers}", consumer["reason"])
        shared_document = combinations.load_json5(root / shared[6:], "mcp_commander")
        self.assertNotIn("adjustments", shared_document)

    def test_the_rendered_rig_is_refused_on_a_v1_first(self):
        root = Path(__file__).resolve().parents[2]
        # Every commander leads either hardware version; the rendered rig is the one
        # thing a v1 is refused, written as a requirement so a hardware version the
        # axis gains later is refused until it is proven to render.
        for path, required in [("openarm/fragments/openarm.json5", False),
                               ("openarm/fragments/openarm_sim.json5", True)]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                generation_rules = [
                    constraint for constraint in robot["constraints"]
                    if constraint.get("when") == {"camera_rig": "cameras_sim"}
                    and [{"hardware_version": "v2"}] == constraint.get("requires")
                ]
                if not required:
                    self.assertEqual(generation_rules, [])
                    continue
                rule, = generation_rules
                self.assertEqual(rule["when"], {"camera_rig": "cameras_sim"})
                # First in the file, so a v1 asking for the rig reads this
                # refusal before the consumer rule's.
                self.assertIs(robot["constraints"][0], rule)
                self.assertIn("select v2, or camera_rig=none", rule["reason"])

    def test_the_ker_is_offered_on_a_v2_alone(self):
        root = Path(__file__).resolve().parents[2]
        for path in ["openarm/fragments/openarm.json5", "openarm/fragments/openarm_sim.json5"]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                rule, = [c for c in robot["constraints"] if c.get("when") == {"robot_commander": "ker_commander"}]
                self.assertEqual(rule["requires"], [{"hardware_version": "v2"}])
                self.assertIn("select v2, or another commander", rule["reason"])

    def test_the_hardware_version_reaches_the_backbone_and_the_panel(self):
        root = Path(__file__).resolve().parents[2]
        # The backbone takes its hardware version from the robot file, per
        # hardware version. The browser panel loads the hardware version's model: it
        # carries v2 and writes v1 on its own instance under the robot's
        # hardware version axis. The KER is a v2's and carries v2 alone.
        for robot in ["openarm", "openarm_sim"]:
            with self.subTest(robot=robot):
                document = combinations.load_json5(root / ROBOT_FILES[robot], robot)
                self.assertNotIn("hardware_version", instance_of(document, "backbone_inst")["arguments"])
                written = [a for a in document["adjustments"]
                           if a["target"] == "backbone_inst" and "hardware_version" in a.get("set_arguments", {})]
                self.assertCountEqual(written, [
                    {"target": "backbone_inst", "when": {"hardware_version": version},
                     "set_arguments": {"hardware_version": version}} for version in ("v1", "v2")])
        panel = combinations.load_json5(root / "openarm/fragments/web_commander.json5", "web_commander")
        self.assertEqual(instance_of(panel, "commander_inst")["arguments"]["hardware_version"], "v2")
        self.assertIn({"target": "commander_inst", "when": {"hardware_version": "v1"},
                       "set_arguments": {"hardware_version": "v1"}}, panel["adjustments"])
        ker = combinations.load_json5(root / "openarm/fragments/ker_commander.json5", "ker_commander")
        self.assertEqual(instance_of(ker, "commander_inst")["arguments"]["hardware_version"], "v2")
        self.assertNotIn("adjustments", ker)
        for launcher, robot in [("physical.json5", "openarm"),
                                ("openarm_simulation.json5", "openarm_sim"),
                                ("so101_simulation.json5", "openarm_sim"),
                                ("mcp/simulation_mcp.json5", "openarm_sim")]:
            with self.subTest(launcher=launcher):
                document = combinations.load_json5(root / launcher, launcher)
                self.assertEqual([a for a in robot_option(document, robot)["adjustments"]
                                  if "hardware_version" in a.get("set_arguments", {})], [])

    def test_isaac_slows_the_simulated_backbones_state_rate(self):
        root = Path(__file__).resolve().parents[2]
        # Isaac publishes measured state once per rendered frame.
        for robot in ["openarm_sim"]:
            document = combinations.load_json5(root / ROBOT_FILES[robot], robot)
            self.assertIn({"target": "backbone_inst", "when": {"simulation": "isaac_sim"},
                           "set_arguments": {"follower_state_rate_hz": 60}}, document["adjustments"])

    def test_the_so101_runs_on_actions_alone_under_none_or_an_unfilled_axis(self):
        root = Path(__file__).resolve().parents[2]
        # The physical SO-101 runs actions-only under the empty option; the
        # simulated one under its unfilled axis, which its fragment leaves
        # unfilled.
        physical = combinations.load_json5(root / "so101/fragments/so101.json5", "so101")
        self.assertEqual(
            {axis["name"]: axis for axis in physical["components"]}["robot_commander"]["options"]["none"],
            "../../common/fragments/none.json5")
        simulated = combinations.load_json5(root / "so101/fragments/so101_sim.json5", "so101_sim")
        self.assertNotIn("robot_commander", combinations.option_entries(simulated, "so101_sim"))

    def test_an_episode_needs_a_trigger(self):
        root = Path(__file__).resolve().parents[2]
        # The KER and SO-101 leaders lack one: the record button of the panel
        # or the headset, or the endpoint's recorder tool, which the launcher
        # lists whenever the endpoint runs.
        for path, commanders in [
            ("openarm/fragments/openarm.json5", ["web_commander", "xr_commander"]),
            ("so101/fragments/so101.json5", "xr_commander"),
        ]:
            with self.subTest(path=path):
                document = combinations.load_json5(root / path, path)
                trigger, = [c for c in document["constraints"] if c.get("when") == {"recorder": "lerobot_recorder"}]
                self.assertEqual(trigger["requires"],
                                 [{"robot_commander": commanders}, {"robot_control": "robot_control"}])
                self.assertIn("launch with robot_control", trigger["reason"])

    def test_the_robot_file_releases_the_sockets_the_selected_leader_drives(self):
        root = Path(__file__).resolve().parents[2]
        # No leader streams by default: the backbone comes up with every
        # upstream socket vacant, and the robot releases the sockets the
        # selected leader drives. The headset also switches it to pose mode,
        # and the panel supplies the operator's governor toggle.
        openarm_sockets = ["leader_left_arm", "leader_right_arm", "leader_left_gripper", "leader_right_gripper"]
        for path, vacant, pose, releases in [
            ("openarm/fragments/openarm.json5",
             openarm_sockets + ["collision_ctrl", "leader_left_arm_pose", "leader_right_arm_pose"],
             ["leader_left_arm_pose", "leader_right_arm_pose"],
             {"web_commander": openarm_sockets, "ker_commander": openarm_sockets}),
            ("openarm/fragments/openarm_sim.json5",
             openarm_sockets + ["collision_ctrl", "leader_left_arm_pose", "leader_right_arm_pose"],
             ["leader_left_arm_pose", "leader_right_arm_pose"],
             {"web_commander": openarm_sockets, "ker_commander": openarm_sockets}),
            ("so101/fragments/so101.json5", ["leader_arm", "leader_gripper", "leader_pose"],
             ["leader_pose", "leader_gripper"], {"so101_leader": ["leader_arm", "leader_gripper"]}),
            ("so101/fragments/so101_sim.json5", ["leader_arm", "leader_gripper", "leader_pose"],
             ["leader_pose", "leader_gripper"], {"so101_leader": ["leader_arm", "leader_gripper"]}),
        ]:
            with self.subTest(path=path):
                document = combinations.load_json5(root / path, path)
                backbone = instance_of(document, "backbone_inst")
                self.assertEqual(backbone["arguments"]["upstream_mode"], "joints")
                for slot in vacant:
                    self.assertEqual(set(backbone["links"][slot]), {"vacant"}, slot)
                headset, = [a for a in document["adjustments"]
                            if a["target"] == "backbone_inst"
                            and a.get("when") == {"robot_commander": "xr_commander"}]
                self.assertEqual(headset["set_arguments"], {"upstream_mode": "pose"})
                self.assertEqual(headset["unset_links"], pose)
                for leader, sockets in releases.items():
                    released, = [a for a in document["adjustments"]
                                 if a["target"] == "backbone_inst"
                                 and a.get("when") == {"robot_commander": leader}]
                    self.assertEqual(released["unset_links"], sockets)
                    self.assertEqual(released.get("set_links"),
                                     {"collision_ctrl": "commander_inst"} if leader == "web_commander" else None)
        # A leader deploys its own commander and writes nothing outside it.
        for path in ["openarm/fragments/web_commander.json5", "openarm/fragments/ker_commander.json5",
                     "so101/fragments/so101_leader.json5"]:
            with self.subTest(path=path):
                leader = combinations.load_json5(root / path, path)
                self.assertEqual({a["target"] for a in leader.get("adjustments", [])} - {"commander_inst"}, set())

    def test_every_guard_names_an_option_its_axis_declares(self):
        # A guard naming an option its axis lacks is refused when the
        # launcher loads, so every guard here names a declared option.
        root = Path(__file__).resolve().parents[2]
        for path, robots in ROBOT_LAUNCHERS.items():
            launcher = combinations.read_launcher(root, path)
            stack = {axis.name: set(axis.options) for axis in launcher.axes}
            robot_axis = next(axis for axis in launcher.axes if axis.name == "robot")
            document = combinations.load_json5(root / path, path)
            for robot in robots:
                declared = {**stack, **{axis.name: set(axis.options) for axis in robot_axis.nested[robot]}}
                for entry in robot_option(document, robot)["adjustments"]:
                    for axis, options in entry.get("when", {}).items():
                        with self.subTest(path=path, robot=robot, target=entry["target"], axis=axis):
                            self.assertIn(axis, declared)
                            named = {options} if isinstance(options, str) else set(options)
                            self.assertLessEqual(named, declared[axis])

    def test_the_rendered_rig_binds_camera_control_per_simulation(self):
        root = Path(__file__).resolve().parents[2]
        for path, cameras in [
            ("openarm/fragments/openarm_sim.json5",
             {"wrist_left": "rgb_cameras", "wrist_right": "rgb_cameras", "chest": "rgbd_cameras"}),
            ("so101/fragments/so101_sim.json5", {"wrist": "rgb_cameras"}),
        ]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                rig_axis = axis_of(robot, "camera_rig")
                self.assertEqual(rig_axis["options"]["cameras_sim"], "cameras_sim.json5")
                rig = option_file(path, "cameras_sim.json5")
                relays = [instance for deployment in rig["deployments"] for instance in deployment["instances"]]
                self.assertEqual([relay["instance_id"] for relay in relays], list(cameras))
                # Each relay pairs into the simulation's slot for its kind
                # of camera, which holds every robot's pairs and knows the
                # camera by the relay's id, and writes its control slot
                # vacant; Waldo, the one simulation with a camera response
                # model, binds it, by the rig's own write on its relays.
                for relay in relays:
                    self.assertEqual(relay["links"]["simulation"], f"simulation_inst/{cameras[relay['instance_id']]}")
                    self.assertEqual(set(relay["links"]["control"]), {"vacant"})
                    self.assertTrue(relay["links"]["control"]["vacant"].strip())
                    self.assertEqual(relay["framework"], {"clock": "simulation"})
                self.assertEqual(rig["adjustments"], [
                    {"target": target, "when": {"simulation": "waldo"}, "set_links": {"control": "simulation_inst"}}
                    for target in cameras
                ])
                # The browser scene commander's camera panel reads the
                # cameras from the simulation, so the rig links nothing into
                # the stack and a copy with a rig joins and leaves beside a
                # running panel.
                self.assertNotIn("scene_commander_inst", [a["target"] for a in robot["adjustments"]])
        # Rendering is what a rig costs the simulation, so the launcher asks
        # for it under the robot whose rig runs.
        for launcher in ["openarm_simulation.json5", "so101_simulation.json5"]:
            with self.subTest(launcher=launcher):
                document = combinations.load_json5(root / launcher, launcher)
                for option in next(
                        axis for axis in document["components"] if axis["name"] == "robot")["options"].values():
                    self.assertIn(RENDERING_ON | {"when": {"camera_rig": "cameras_sim"}}, option["adjustments"])


    def test_every_robot_runs_the_one_initializer_under_its_model(self):
        root = Path(__file__).resolve().parents[2]
        vacancy = "vacant"
        openarm_limbs = ["left_arm_inst", "right_arm_inst", "left_gripper_inst", "right_gripper_inst"]
        # Every robot deploys the one initializer where its model is known:
        # an OpenArm's hardware version options each deploy theirs in place, and
        # the axis provides the node; every SO-101 is the one model, named
        # in the robot file. On hardware the simulation slot is vacant and
        # the robot's drivers answer for its limbs; in a simulation the
        # simulation answers for them, and the limbs slot, `zero_or_more`, is
        # left out.
        def generation_body(path, option):
            return axis_of(combinations.load_json5(root / path, path), "hardware_version")["options"][option]
        for path, model, links, clock in [
            ("openarm/fragments/openarm.json5:v1", "openarm_v1", {"simulation": vacancy, "limbs": openarm_limbs}, None),
            ("openarm/fragments/openarm.json5:v2", "openarm_v2", {"simulation": vacancy, "limbs": openarm_limbs}, None),
            ("openarm/fragments/openarm_sim.json5:v1", "openarm_v1", {"simulation": "simulation_inst"}, {"clock": "simulation"}),
            ("openarm/fragments/openarm_sim.json5:v2", "openarm_v2", {"simulation": "simulation_inst"}, {"clock": "simulation"}),
            ("so101/fragments/so101.json5", "so101", {"simulation": vacancy, "limbs": ["follower_inst"]}, None),
            ("so101/fragments/so101_sim.json5", "so101", {"simulation": "simulation_inst"}, {"clock": "simulation"}),
        ]:
            with self.subTest(path=path):
                file, _, option = path.partition(":")
                document = generation_body(file, option) if option else combinations.load_json5(root / file, file)
                initializer = next(deployment for deployment in document["deployments"]
                                   if deployment.get("source") == {"name": "robot_initializer", "tag": "v1"})
                instance, = initializer["instances"]
                self.assertEqual(instance["instance_id"], "init_inst")
                self.assertEqual(instance["arguments"], {"model": model})
                self.assertEqual(set(instance["links"]), set(links))
                for slot, target in links.items():
                    if target == vacancy:
                        self.assertEqual(set(instance["links"][slot]), {"vacant"})
                        self.assertTrue(instance["links"][slot]["vacant"].strip())
                    else:
                        self.assertEqual(instance["links"][slot], target)
                self.assertEqual(instance.get("framework"), clock)
        for path in ["openarm/fragments/openarm.json5", "openarm/fragments/openarm_sim.json5"]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                version = axis_of(robot, "hardware_version")
                self.assertEqual(list(version["options"]), ["v1", "v2"])
                self.assertEqual(version["provides"], ["init_inst"])
                # A simulated OpenArm stands a v2 unless a word says otherwise;
                # a physical one has no default, so a copy names its hardware version.
                entries = combinations.option_entries(robot, path)
                if path.endswith("openarm_sim.json5"):
                    self.assertEqual(entries["hardware_version"], "v2")
                else:
                    self.assertNotIn("hardware_version", entries)
                self.assertEqual(instance_of(robot, "backbone_inst")["links"]["robot_init"], "init_inst")

    def test_a_backbone_names_its_downstream_links_after_its_limbs(self):
        root = Path(__file__).resolve().parents[2]
        openarm = ["left_arm", "right_arm", "left_gripper", "right_gripper"]
        # On hardware each limb link pairs with its driver, which the robot
        # file deploys as a v2 and sets to a v1's interfaces and frames per
        # hardware version.
        for path, links in [
            ("openarm/fragments/openarm.json5", {limb: f"{limb}_inst" for limb in openarm}),
            ("so101/fragments/so101.json5", {"arm": "follower_inst", "gripper": "follower_inst"}),
        ]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                backbone = instance_of(robot, "backbone_inst")
                self.assertEqual({limb: backbone["links"][limb] for limb in links}, links)
        physical = combinations.load_json5(root / ROBOT_FILES["openarm"], "openarm")
        interfaces = {"left_arm_inst": "left_arm", "right_arm_inst": "right_arm",
                      "left_gripper_inst": "left_arm", "right_gripper_inst": "right_arm"}
        v1_interfaces = {"left_arm_inst": "can0", "right_arm_inst": "can1",
                         "left_gripper_inst": "can0", "right_gripper_inst": "can1"}
        for driver, interface in interfaces.items():
            with self.subTest(driver=driver):
                arguments = instance_of(physical, driver)["arguments"]
                self.assertEqual((arguments["hardware_version"], arguments["can_interface"]), ("v2", interface))
                self.assertIn({"target": driver, "when": {"hardware_version": "v1"},
                               "set_arguments": {"hardware_version": "v1", "can_interface": v1_interfaces[driver]}},
                              physical["adjustments"])
        # A simulation holds one slot per kind of limb, any number of pairs
        # on each: it tells a pair's robot by its copy and its limb by the
        # backbone link the pair comes from.
        slots = {"left_arm": "arms", "right_arm": "arms", "arm": "arms",
                 "left_gripper": "grippers", "right_gripper": "grippers", "gripper": "grippers"}
        for path, limbs in [
            ("openarm/fragments/openarm_sim.json5", openarm),
            ("so101/fragments/so101_sim.json5", ["arm", "gripper"]),
        ]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                backbone = instance_of(robot, "backbone_inst")
                self.assertEqual(
                    {limb: backbone["links"][limb] for limb in limbs},
                    {limb: f"simulation_inst/{slots[limb]}" for limb in limbs})
        # What is observed of a robot is named by the backbone's end of each
        # pair, the same on hardware and in a simulation, so one recorder
        # file per family links the limbs.
        for path, arms, grippers in [
            ("openarm/fragments/lerobot_recorder.json5",
             ["backbone_inst/left_arm", "backbone_inst/right_arm"],
             ["backbone_inst/left_gripper", "backbone_inst/right_gripper"]),
            ("so101/fragments/lerobot_recorder.json5", ["backbone_inst/arm"], ["backbone_inst/gripper"]),
        ]:
            with self.subTest(path=path):
                recorder = combinations.load_json5(root / path, path)
                self.assertEqual(instance_of(recorder, "recorder_inst")["links"], {
                    "observed_joints": arms, "observed_grippers": grippers,
                    "commanded_joints": arms, "commanded_grippers": grippers,
                })
        panel = combinations.load_json5(root / "openarm/fragments/web_commander.json5", "web_commander")
        links = panel["deployments"][0]["instances"][0]["links"]
        self.assertEqual(
            {slot: target for slot, target in links.items() if slot.startswith("observed_")},
            {f"observed_{limb}": f"backbone_inst/{limb}" for limb in openarm})

    def test_the_real_and_the_simulated_so101_run_the_same_control(self):
        root = Path(__file__).resolve().parents[2]
        real = combinations.load_json5(root / "so101/fragments/so101.json5", "so101")
        simulated = combinations.load_json5(root / "so101/fragments/so101_sim.json5", "so101_sim")
        # Each robot deploys the control it runs, so the backbone reads the
        # same way wherever its limbs are.
        for robot, name in [(real, "so101"), (simulated, "so101_sim")]:
            with self.subTest(robot=name):
                backbone = next(d for d in robot["deployments"]
                                if d.get("source") == {"name": "so101_backbone", "tag": "v1"})
                instance, = backbone["instances"]
                self.assertEqual(instance["instance_id"], "backbone_inst")
                self.assertEqual(instance["arguments"]["upstream_mode"], "joints")
                self.assertEqual(set(instance["links"]), {"arm", "gripper", "leader_arm", "leader_gripper", "leader_pose"})
        self.assertEqual(instance_of(simulated, "backbone_inst")["arguments"],
                         instance_of(real, "backbone_inst")["arguments"])
        # The follower is the real robot's alone: the engine plays the
        # follower role toward a simulated robot's backbone.
        self.assertCountEqual(
            [d["source"]["name"] for d in real["deployments"] if "source" in d],
            ["so101_follower", "robot_initializer", "so101_backbone"])
        self.assertCountEqual(
            [d["source"]["name"] for d in simulated["deployments"] if "source" in d],
            ["robot_initializer", "so101_backbone"])
        # The leader arm leads the real robot; the simulated one comes up
        # with no commander, so it needs no SO-101 hardware.
        self.assertEqual(combinations.option_entries(real, "so101")["robot_commander"], "so101_leader")
        self.assertNotIn("robot_commander", combinations.option_entries(simulated, "so101_sim"))
        # Per-motor health and alerts come from a real driver, so the
        # launcher binds the headset's only where the robot is physical.
        telemetry = {"motor_health": ["follower_inst"], "alerts": ["follower_inst"]}
        physical = combinations.load_json5(root / "physical.json5", "physical")
        option = next(axis for axis in physical["components"] if axis["name"] == "robot")["options"]["so101"]
        self.assertIn(
            {"target": "commander_inst", "when": {"robot_commander": "xr_commander"}, "add_links": telemetry},
            option["adjustments"])
        for launcher in ["openarm_simulation.json5", "so101_simulation.json5",
                         "mcp/simulation_mcp.json5"]:
            document = combinations.load_json5(root / launcher, launcher)
            simulated_option = next(
                axis for axis in document["components"] if axis["name"] == "robot")["options"]["so101_sim"]
            for adjustment in simulated_option["adjustments"]:
                for verb in ["set_links", "add_links"]:
                    self.assertFalse(set(telemetry) & set(adjustment.get(verb, {})), launcher)

    def test_the_launchers_offer_their_robots_and_list_their_copies(self):
        root = Path(__file__).resolve().parents[2]
        for path, robots in ROBOT_LAUNCHERS.items():
            with self.subTest(path=path):
                document = combinations.load_json5(root / path, path)
                robot = next(axis for axis in document["components"] if axis["name"] == "robot")
                self.assertEqual(set(robot["options"]), set(robots))
                simulation = [axis for axis in document["components"] if axis["name"] == "simulation"]
                if path == "physical.json5":
                    self.assertEqual(simulation, [])
                else:
                    self.assertEqual(set(simulation[0]["options"]), {"mujoco", "isaac_sim", "waldo"})
                    self.assertEqual(combinations.option_entries(document, path)["simulation"], "waldo")
                # Every other simulation launcher lists one copy, alpha, of
                # its own family's robot. The MCP launcher lists none: its
                # entries set every copy of their option up, and a launch
                # starts no robot. The physical launcher lists none either:
                # every robot is joined by name.
                launcher = combinations.read_launcher(root, path)
                if path == "physical.json5":
                    self.assertEqual(launcher.copies, [])
                    self.assertEqual(launcher.unlisted, ())
                elif path == "mcp/simulation_mcp.json5":
                    self.assertEqual(launcher.copies, [])
                    self.assertEqual(launcher.unlisted, ("openarm_sim", "so101_sim"))
                else:
                    self.assertEqual([copy.name for copy in launcher.copies], ["alpha"])
                    self.assertEqual(
                        launcher.copies[0].option, "so101_sim" if Path(path).name.startswith("so101") else "openarm_sim")
                    self.assertEqual(launcher.unlisted, ())
        index = combinations.load_json5(root / "peppy_repository.json5", "index")
        self.assertEqual(index["launchers"]["physical"], {"path": "physical.json5"})
        self.assertEqual(index["launchers"]["so101_simulation"], {"path": "so101_simulation.json5"})
        self.assertEqual(index["launchers"]["simulation_mcp"], {"path": "mcp/simulation_mcp.json5"})


    def test_the_mcp_launcher_deploys_waldo_and_the_endpoints_with_no_robot(self):
        root = Path(__file__).resolve().parents[2]
        path = "mcp/simulation_mcp.json5"
        document = combinations.load_json5(root / path, path)
        axes = {axis["name"]: axis for axis in document["components"]}
        # The robots' guards name MuJoCo and Isaac Sim, and a guard naming an
        # option the axis does not declare is refused when the launcher
        # loads, so the axis carries all three; the file deploys Waldo, and
        # the robots' endpoint runs under the other two as well.
        self.assertEqual(axes["simulation"]["cardinality"], "one")
        self.assertEqual(list(axes["simulation"]["options"]), ["mujoco", "isaac_sim", "waldo"])
        # The order is the planner's: it renders launch words axis by axis.
        self.assertEqual(list(axes), ["simulation", "robot_control", "world_control", "robot"])
        entries = combinations.option_entries(document, path)
        self.assertEqual(
            {axis: option for axis, option in entries.items() if axis != "robot"},
            {"simulation": "waldo", "robot_control": "robot_control", "world_control": "world_control"})
        # Each robot's entry states, for every copy of the option, the MCP
        # commander and the rendered rig: the ones `--join OPTION:NAME`
        # starts with the launch and the ones joined later without it, a v1
        # being one the simulations render no rig for. No entry lists a
        # copy, so the bare launch is the simulation and the two endpoints
        # with no robot.
        self.assertEqual([entry for entry in document["deployments"] if "robot" in entry], [
            {"robot": "openarm_sim", "with": {"robot_commander": "mcp_commander", "camera_rig": "cameras_sim"}},
            {"robot": "so101_sim", "with": {"robot_commander": "mcp_commander", "camera_rig": "cameras_sim"}},
        ])
        launcher = combinations.read_launcher(root, path)
        self.assertEqual(launcher.copies, [])
        self.assertEqual(launcher.unlisted, ("openarm_sim", "so101_sim"))
        # Every launch under Waldo: bare or with either robot named at
        # launch, with and without the scene commander, each endpoint on or
        # off.
        found = combinations.launcher_selections(launcher.axes, launcher.copies, launcher.unlisted)
        waldo = [c for c in found if c.join_option is None and c.words[0] == ("simulation", "waldo")]
        launched = [(combinations.render_words(c.words), c.launch_joins) for c in waldo]
        for stack in ["simulation=waldo,robot_control=robot_control,world_control=world_control",
                      "simulation=waldo,robot_control=none,world_control=none"]:
            with self.subTest(stack=stack):
                self.assertIn((stack, ()), launched)
                self.assertIn((stack, ("openarm_sim",)), launched)
                self.assertIn((stack, ("so101_sim",)), launched)


    def test_an_option_entry_may_carry_settings_shared_by_its_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                {"name": "robot", "cardinality": "zero_or_more", "options": {"openarm_v2": {
                    "components": [{"name": "robot_commander", "options": {"web_commander": {}, "xr_commander": {}}}],
                    "deployments": [{"robot_commander": "web_commander"}],
                }}},
            ], "deployments": [
                {"robot": "openarm_v2", "with": {"robot_commander": "xr_commander"},
                 "arguments": {"commander_inst": {"https_port": 4444}},
                 "adjustments": [{"target": "commander_inst", "set_arguments": {"command_rate_hz": 60}}],
                 "instances": [
                     {"instance_id": "alpha"},
                     {"instance_id": "bravo", "adjustments": [{"target": "commander_inst", "set_arguments": {"command_rate_hz": 100}}]},
                 ]},
            ]}))
            launcher = combinations.read_launcher(directory, "fleet.json5")
            self.assertEqual([axis.name for axis in launcher.axes], ["robot"])
            # Both copies run the entry's selection.
            self.assertEqual(
                launcher.copies,
                [combinations.Copy(name, "robot", "openarm_v2", {"robot_commander": "xr_commander"})
                 for name in ["alpha", "bravo"]],
            )

    def test_refused_keys_and_cardinalities_are_rejected(self):
        for extra in [{"optional": True}, {"cardinality": "many"},
                      {"default": "openarm_v2"}, {"components": []}]:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                    {"name": "robot", "options": {"openarm_v2": {}}, **extra},
                ]}))
                with self.assertRaises(combinations.Json5Error):
                    combinations.read_launcher(directory, "fleet.json5")
        for cardinality in combinations.COPY_CARDINALITIES:
            with self.subTest(cardinality=cardinality), tempfile.TemporaryDirectory() as directory:
                Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                    {"name": "robot", "options": {"openarm_v2": {
                        "components": [{"name": "cameras", "cardinality": cardinality, "options": {"a": {}}}],
                    }}},
                ]}))
                with self.assertRaisesRegex(combinations.Json5Error, f"{cardinality} inside a fragment"):
                    combinations.read_launcher(directory, "fleet.json5")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                {"name": "robot", "options": {"openarm_v2": {
                    "components": [{"name": "robot_commander", "options": {"web_commander": {
                        "components": [{"name": "deep", "options": {"a": {}}}],
                    }}}],
                }}},
            ]}))
            with self.assertRaises(combinations.Json5Error):
                combinations.read_launcher(directory, "fleet.json5")


REFUSAL = completed(returncode=1, stderr=(
    "Error: camera_rig=cameras_sim requires recorder=lerobot_recorder, "
    "which this selection (camera_rig=cameras_sim) does not satisfy"
))
JOIN_REFUSAL = completed(returncode=1, stderr=(
    "Error: joining bravo would change simulation_inst, which already runs"
))
#: A resolve that flattened its launcher and checked no link rule over it.
UNCHECKED = completed(
    resolved_plan("waldo:v1").stdout,
    stderr="link rules not checked, 1 manifest(s) unavailable: waldo:v1 (not in the nodes cache)",
)


class ResolveTests(unittest.TestCase):
    def test_plan_previews_the_launch_and_its_join_and_launches_the_same(self):
        answers = {
            ("fleet.json5", "", "openarm_v2", "recorder=lerobot_recorder"):
                resolved_plan("sim_mujoco:v1", "lerobot_recorder:v1"),
        }
        with planned({"fleet": fleet_launcher()}, answers) as (resolve, labels, plan, _):
            self.assertIn([
                "peppy", "stack", "resolve", "fleet.json5",
                "--with", f"{combinations.COPY_NAME}.recorder=lerobot_recorder",
                "--join", f"openarm_v2:{combinations.COPY_NAME}",
            ], [argv for argv, _ in resolve.calls])
            # The preview reads the plan as JSON5, so peppy logs errors only.
            self.assertTrue(all(kwargs["env"]["RUST_LOG"] == "error" for _, kwargs in resolve.calls))
            # The plain join is a launch of its own beside the one with words.
            self.assertEqual(labels, [
                "fleet + join openarm_v2",
                "fleet + join openarm_v2 (recorder=lerobot_recorder)",
            ])
            _, launch = plan
            self.assertEqual(launch.words, "")
            self.assertEqual(launch.join_option, "openarm_v2")
            self.assertEqual(launch.join_name, combinations.COPY_NAME)
            self.assertEqual(launch.join_words, "recorder=lerobot_recorder")
            self.assertFalse(launch.local)

    def test_plan_previews_a_launch_naming_its_copy_and_launches_the_same(self):
        started = completed(json.dumps({"deployments": [
            {"source": {"name": "sim_mujoco", "tag": "v1"}, "instances": [{"instance_id": "simulation_inst"}]},
            {"source": {"name": "openarm_backbone", "tag": "v1"},
             "instances": [{"instance_id": "bravo_backbone_inst", "core_node": "bravo"}]},
        ]}))
        answers = {("fleet.json5", "", "openarm_v2", ""): started}
        with planned({"fleet": fleet_launcher(at_launch=True)}, answers) as (resolve, labels, plan, _):
            self.assertIn(
                ["peppy", "stack", "resolve", "fleet.json5", "--join", "openarm_v2:bravo"],
                [argv for argv, _ in resolve.calls])
            self.assertIn("fleet --join openarm_v2:bravo", labels)
            launch, = [launch for launch in plan if launch.launch_joins]
            self.assertEqual(launch.launch_joins, ["openarm_v2"])
            self.assertEqual(launch.join_option, "")
            self.assertEqual(launch.join_instances, ["bravo_backbone_inst"])

    def test_plan_previews_a_deployed_copys_launch_words_without_a_join(self):
        words = "alpha.recorder=lerobot_recorder"
        answers = {
            ("fleet.json5", words, "", ""): resolved_plan("sim_mujoco:v1", "lerobot_recorder:v1"),
        }
        with planned({"fleet": fleet_launcher(file_copy="alpha")}, answers) as (resolve, labels, plan, _):
            self.assertIn(
                ["peppy", "stack", "resolve", "fleet.json5", "--with", words],
                [argv for argv, _ in resolve.calls],
            )
            self.assertEqual(labels, [f"fleet ({words})", "fleet + join openarm_v2"])
            launch, _ = plan
            self.assertEqual(launch.words, words)
            self.assertEqual(launch.join_option, "")
            self.assertEqual(launch.join_name, "")

    def test_a_launcher_declaring_core_nodes_launches_locally(self):
        launcher = {**simulation_launcher("mujoco"), "core_nodes": ["robot_onboard"]}
        with planned({"split": launcher}) as (_, labels, plan, _summary):
            self.assertEqual(labels, ["split [--local]"])
            self.assertTrue(plan[0].local)

    def test_a_selection_the_launchers_constraints_refuse_is_reported_not_launched(self):
        answers = {("sim.json5", "simulation=waldo", "", ""): REFUSAL}
        with planned({"sim": simulation_launcher("mujoco", "waldo")}, answers) as (_, labels, _plan, summary):
            self.assertEqual(labels, ["sim (simulation=mujoco)"])
            self.assertIn("Refused by the launcher's own constraints (1)", summary)
            self.assertIn("| sim (simulation=waldo) | camera_rig=cameras_sim requires", summary)

    def test_a_combination_deploying_a_node_the_runner_cannot_run_is_skipped(self):
        answers = {("sim.json5", "simulation=waldo", "", ""): resolved_plan("waldo:v1", "zed_camera:v1")}
        with planned({"sim": simulation_launcher("mujoco", "waldo")}, answers) as (_, labels, _plan, summary):
            self.assertEqual(labels, ["sim (simulation=mujoco)"])
            self.assertIn("Skipped: hardware or rollout dependencies (1)", summary)
            self.assertIn("| sim (simulation=waldo) | deploys zed_camera: the runner has no ZED camera |", summary)

    def test_a_copy_refused_for_changing_what_runs_runs_from_the_file_alone(self):
        answers = {("fleet.json5", "", "openarm_v2", ""): JOIN_REFUSAL}
        with planned({"fleet": fleet_launcher()}, answers) as (_, labels, _plan, summary):
            self.assertNotIn("fleet + join openarm_v2", labels)
            self.assertIn("Copies only the launcher's file deploys (1)", summary)
            self.assertIn("| fleet + join openarm_v2 | joining bravo would change simulation_inst", summary)

    def test_a_join_comes_up_beside_the_files_copy(self):
        def plan_of(*copies):
            return completed(json.dumps({"deployments": [
                {"source": {"name": "waldo", "tag": "v1"}, "instances": [{"instance_id": "simulation_inst"}]},
                {"source": {"name": "scene_commander", "tag": "v1"},
                 "instances": [{"instance_id": "scene_commander_inst", "links": {"cameras": ["alpha_chest"]}}]},
                {"source": {"name": "sim_rgb_camera", "tag": "v1"},
                 "instances": [{"instance_id": f"{copy}_chest", "core_node": copy} for copy in copies]},
            ]}))

        answers = {
            ("fleet.json5", "", "", ""): plan_of("alpha"),
            ("fleet.json5", "", "openarm_v2", ""): plan_of("alpha", "bravo"),
            ("fleet.json5", "", "openarm_v2", "recorder=lerobot_recorder"): plan_of("alpha", "bravo"),
        }
        with planned({"fleet": fleet_launcher(file_copy="alpha")}, answers) as (_, labels, plan, summary):
            # Every simulation stands the joined copy beside the file's.
            self.assertEqual(labels, ["fleet + join openarm_v2"])
            launch, = plan
            self.assertEqual(launch.join_instances, ["bravo_chest"])

    def test_a_run_reaching_only_refused_combinations_launches_nothing(self):
        refusal_with_a_pipe = completed(returncode=1, stderr=REFUSAL.stderr + " (a|b)")
        answers = {("sim.json5", "", "", ""): refusal_with_a_pipe}
        with planned({"sim": simulation_launcher("mujoco")}, answers) as (_, labels, _plan, summary):
            self.assertEqual(labels, [])
            self.assertIn("Launching nothing: every combination this change reaches is refused or skipped", summary)
            # The refusal lands in a table cell, its pipe escaped once.
            self.assertIn("does not satisfy (a\\|b) |", summary)

    def test_a_combination_nothing_refuses_and_nothing_resolves_fails_the_plan(self):
        answers = {("sim.json5", "simulation=waldo", "", ""): completed(returncode=1, stderr="Error: no such fragment")}
        with self.assertRaises(SystemExit) as failure, contextlib.redirect_stderr(io.StringIO()):
            with planned({"sim": simulation_launcher("mujoco", "waldo")}, answers):
                pass
        self.assertIn("sim (simulation=waldo) does not resolve", str(failure.exception))

    def test_a_resolve_that_checked_no_link_rule_fails_the_plan(self):
        """With the caches filled, a resolve that reports the link rules
        unchecked names a manifest the run should have had, so peppy's report
        line stops it."""
        answers = {("sim.json5", "simulation=waldo", "", ""): UNCHECKED}
        with self.assertRaises(SystemExit) as failure:
            with planned({"sim": simulation_launcher("mujoco", "waldo")}, answers):
                pass
        self.assertIn("sim (simulation=waldo): link rules not checked", str(failure.exception))
        self.assertIn("run `peppy repo refresh` before planning", str(failure.exception))
        self.assertIn("waldo:v1", str(failure.exception))

    def test_a_resolve_the_caches_could_not_prove_passes_and_says_what_went_unchecked(self):
        """A fork's pull request is given no deploy key for the private hub,
        so the step that fills the caches reports them short and the run
        carries on, naming the combinations it left unproven."""
        answers = {("sim.json5", "simulation=waldo", "", ""): UNCHECKED}
        reason = "the private hub is not registered"
        with planned({"sim": simulation_launcher("mujoco", "waldo")}, answers,
                     unprovable=reason) as (_, labels, _plan, summary):
            self.assertEqual(labels, ["sim (simulation=mujoco)"])
            self.assertIn("### 🔓 Link rules not checked (1)", summary)
            self.assertIn(f"checked no link rule over these plans: {reason}.", summary)
            self.assertIn("- sim (simulation=waldo)", summary)

    def test_a_refusal_naming_no_copy_is_not_mistaken_for_a_file_only_one(self):
        answers = {("sim.json5", "simulation=waldo", "", ""): JOIN_REFUSAL}
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            with planned({"sim": simulation_launcher("mujoco", "waldo")}, answers):
                pass


ROOT = Path(__file__).resolve().parents[2]
SO101_SIM = "so101/fragments/so101_sim.json5"
#: so101_simulation's bare stack under Waldo, and the same with the robots'
#: endpoint selected.
WALDO = "simulation=waldo"
WALDO_MCP = "simulation=waldo,robot_control=robot_control"


def fragment_deployments(path, copy=None):
    """The nodes one of the repository's fragments deploys, as `stack
    resolve` flattens them: ids as written for the stack's own instances,
    minted under the name of `copy` for a copy's, and given that copy as
    their core node."""
    return body_deployments(combinations.load_json5(ROOT / path, path), copy)


def body_deployments(body, copy=None):
    """The nodes one fragment body deploys, a file's or an option's written
    in place, flattened the same way."""
    def minted(instance):
        if copy is None:
            return {"instance_id": instance["instance_id"]}
        return {"instance_id": f"{copy}_{instance['instance_id']}", "core_node": copy}

    return [
        {"source": deployment["source"],
         "instances": [minted(instance) for instance in deployment["instances"]]}
        for deployment in body.get("deployments", [])
        if "source" in deployment
    ]


def so101_copy(name, **selected):
    """One so101_sim copy flattened: what its fragment deploys, and the
    options it selects on its own axes, `selected` over them, each option's
    parts read from the file or from the body written in place."""
    robot = combinations.load_json5(ROOT / SO101_SIM, SO101_SIM)
    selection = {**combinations.option_entries(robot, SO101_SIM), **selected}
    deployments = body_deployments(robot, name)
    for axis in robot["components"]:
        if axis["name"] not in selection:
            continue
        for part in combinations.fragment_parts(
            axis["options"][selection[axis["name"]]], axis["name"]
        ):
            if isinstance(part, dict):
                deployments += body_deployments(part, name)
            else:
                deployments += fragment_deployments(
                    posixpath.normpath(posixpath.join(posixpath.dirname(SO101_SIM), part)), name)
    return deployments


def so101_stack(simulation, *copies):
    """What `stack resolve` prints for so101_simulation under `simulation`
    with `copies` in it."""
    deployments = fragment_deployments(f"simulation/fragments/{simulation}.json5")
    return completed(json.dumps({"deployments": deployments + [d for copy in copies for d in copy]}))


class SimulatedSo101Tests(unittest.TestCase):
    """so101_simulation as the planner sees it. The launcher, its fragments
    and the skip file are the repository's own, and `stack resolve` is
    answered with the nodes those fragments deploy."""

    @classmethod
    def setUpClass(cls):
        cls.inventory = combinations.read_launcher_inventory(
            ROOT, "so101_simulation", "so101_simulation.json5")
        cls.skips = combinations.read_node_reasons(ROOT / ".github/unlaunchable-nodes.json5")

    def resolved(self, answer, words, join_option="", join_words="", launch_joins=()):
        """The combination as the planner enumerates it, and its verdict
        where `stack resolve` prints `answer`."""
        candidate, = [c for c in self.inventory.candidates
                      if (c.words, c.launch_joins, c.join_option, c.join_words)
                      == (words, launch_joins, join_option, join_words)]
        with patch.object(combinations.subprocess, "run", return_value=answer):
            return candidate, combinations.resolve_candidate(ROOT, candidate, self.skips)


    def test_a_launch_of_the_mcp_stack_names_the_so101_it_starts(self):
        # simulation_mcp sets every robot up with no copy, as an MCP robot
        # with its rendered rig, so a launch names the one it starts and
        # nothing stands beside it.
        inventory = combinations.read_launcher_inventory(
            ROOT, "simulation_mcp", "mcp/simulation_mcp.json5")
        self.assertEqual(inventory.candidates[0].file_copies, ())
        stack = "simulation=waldo,robot_control=robot_control,world_control=world_control"
        candidate, = [c for c in inventory.candidates
                      if (c.words, c.launch_joins, c.join_option) == (stack, ("so101_sim",), "")]
        robot_control = so101_copy("bravo", robot_commander="mcp_commander", camera_rig="cameras_sim")
        with patch.object(combinations.subprocess, "run", return_value=so101_stack("waldo", robot_control)):
            resolution = combinations.resolve_candidate(ROOT, candidate, self.skips)
        self.assertIs(resolution.verdict, combinations.Verdict.LAUNCH)
        launch = combinations.Launch.of(candidate, resolution)
        self.assertEqual(launch.launch_joins, ["so101_sim"])
        self.assertEqual(launch.join_instances, ["bravo_backbone_inst", "bravo_init_inst", "bravo_wrist"])
        self.assertEqual(
            combinations.launch_command(launch)[:8],
            ["peppy", "stack", "launch", "simulation_mcp", "--with", stack, "--join", "so101_sim:bravo"])
    def test_a_so101_joins_the_bare_simulation(self):
        # The file lists alpha, so the join is a second robot in the
        # simulation, under Waldo and MuJoCo alike.
        self.assertEqual(self.inventory.candidates[0].file_copies, ("alpha",))
        for simulation, words in [("waldo", WALDO), ("mujoco", "simulation=mujoco,robot_control=robot_control")]:
            with self.subTest(simulation=simulation):
                answer = so101_stack(simulation, so101_copy("alpha"), so101_copy("bravo"))
                candidate, resolution = self.resolved(answer, words, "so101_sim")
                self.assertIs(resolution.verdict, combinations.Verdict.LAUNCH)
                launch = combinations.Launch.of(candidate, resolution)
                self.assertEqual(launch.join_instances, ["bravo_backbone_inst", "bravo_init_inst"])


    def test_a_leader_arm_or_a_headset_is_skipped_as_hardware_the_runner_lacks(self):
        for commander in ["so101_leader", "xr_commander"]:
            with self.subTest(commander=commander):
                answer = so101_stack("waldo", so101_copy("bravo", robot_commander=commander))
                _, resolution = self.resolved(answer, WALDO, "so101_sim", f"robot_commander={commander}")
                self.assertIs(resolution.verdict, combinations.Verdict.SKIPPED)
                self.assertEqual(resolution.detail, f"deploys {commander}: {self.skips[commander]}")

    def test_the_action_only_and_the_mcp_robots_launch(self):
        answer = so101_stack("waldo", so101_copy("bravo"))
        _, resolution = self.resolved(answer, WALDO, "so101_sim")
        self.assertIs(resolution.verdict, combinations.Verdict.LAUNCH)
        # The MCP robot is the arm and the rendered relay listed on the
        # stack's server, and none of that is hardware.
        robot_control = so101_copy("bravo", robot_commander="mcp_commander", camera_rig="cameras_sim")
        self.assertEqual(
            [(d["source"], d["instances"][0]["instance_id"]) for d in robot_control],
            [({"name": "robot_initializer", "tag": "v1"}, "bravo_init_inst"),
             ({"name": "so101_backbone", "tag": "v1"}, "bravo_backbone_inst"),
             ({"name": "sim_rgb_camera", "tag": "v1"}, "bravo_wrist")])
        _, resolution = self.resolved(
            so101_stack("waldo", robot_control), WALDO_MCP, "so101_sim", "robot_commander=mcp_commander,camera_rig=cameras_sim")
        self.assertIs(resolution.verdict, combinations.Verdict.LAUNCH)
        self.assertEqual(
            combinations.copy_instances(resolution.plan, "bravo"),
            ["bravo_backbone_inst", "bravo_init_inst", "bravo_wrist"])
        # Its rig turns rendering on, which a join cannot: the daemon refuses
        # it as a change to the running simulation, and the planner reports
        # the copy as one only a launcher file deploys.
        _, resolution = self.resolved(
            JOIN_REFUSAL, WALDO_MCP, "so101_sim", "robot_commander=mcp_commander,camera_rig=cameras_sim")
        self.assertIs(resolution.verdict, combinations.Verdict.FILE_ONLY)


class CoverageTests(unittest.TestCase):
    def test_a_combination_whose_instances_all_run_in_another_launch_is_not_launched(self):
        answers = {
            ("fleet.json5", "", "", ""): resolved_plan("sim_mujoco:v1"),
            ("fleet.json5", "", "openarm_v2", ""): resolved_plan("sim_mujoco:v1", "openarm_backbone:v1"),
            ("fleet.json5", "", "openarm_v2", "recorder=lerobot_recorder"):
                resolved_plan("sim_mujoco:v1", "openarm_backbone:v1", "lerobot_recorder:v1"),
        }
        with planned({"fleet": fleet_launcher()}, answers) as (_, labels, _plan, summary):
            # The bare launch runs nothing the joins do not. The plain join
            # runs nothing the join with a recorder does not either, and is
            # launched all the same: it alone has the daemon decide the copy.
            self.assertEqual(labels, [
                "fleet + join openarm_v2",
                "fleet + join openarm_v2 (recorder=lerobot_recorder)",
            ])
            self.assertIn("Launching 2 of the 3 launchable combinations in scope", summary)
            self.assertIn("| fleet | fleet + join openarm_v2 |", summary)

    def test_two_instances_are_launched_side_by_side_wherever_a_combination_runs_them_so(self):
        # Two launches would run every instance once; the third is the only
        # one running the camera beside the recorder.
        answers = {
            ("sim.json5", "simulation=a", "", ""): resolved_plan("backbone:v1", "camera:v1"),
            ("sim.json5", "simulation=b", "", ""): resolved_plan("backbone:v1", "recorder:v1"),
            ("sim.json5", "simulation=c", "", ""): resolved_plan("camera:v1", "recorder:v1"),
        }
        with planned({"sim": simulation_launcher("a", "b", "c")}, answers) as (_, labels, _plan, _summary):
            self.assertEqual(labels, ["sim (simulation=a)", "sim (simulation=b)", "sim (simulation=c)"])

    def test_the_same_node_configured_differently_is_launched_both_ways(self):
        def simulation(world):
            return completed(json.dumps({"deployments": [{
                "source": {"name": "waldo", "tag": "v1"},
                "instances": [{"instance_id": "simulation_inst", "arguments": {"world": world}}],
            }]}))

        answers = {
            ("sim.json5", "simulation=a", "", ""): simulation("openarm_v1"),
            ("sim.json5", "simulation=b", "", ""): simulation("openarm_v2"),
            ("sim.json5", "simulation=c", "", ""): simulation("openarm_v2"),
        }
        with planned({"sim": simulation_launcher("a", "b", "c")}, answers) as (_, labels, _plan, _summary):
            self.assertEqual(labels, ["sim (simulation=a)", "sim (simulation=b)"])

    def test_every_launcher_is_launched_even_where_another_deploys_the_same_instances(self):
        launchers = {"fleet": simulation_launcher("mujoco"), "openarm_simulation": simulation_launcher("mujoco")}
        with planned(launchers) as (_, labels, _plan, _summary):
            self.assertEqual(labels, ["fleet", "openarm_simulation"])

    def test_a_join_runs_beside_the_files_copy(self):
        def plan_of(*instance_ids):
            return {"deployments": [{
                "source": {"name": "node", "tag": "v1"},
                "instances": [{"instance_id": instance_id} for instance_id in instance_ids],
            }]}

        launcher = combinations.read_launcher_inventory
        with tempfile.TemporaryDirectory() as directory:
            write_repository(Path(directory) / "head", {"fleet": fleet_launcher(file_copy="alpha")})
            inventory = launcher(Path(directory) / "head", "fleet", "fleet.json5")
        bare, joined = (
            next(c for c in inventory.candidates if (c.words, c.join_option, c.join_words) == key)
            for key in (("", "", ""), ("", "openarm_v2", ""))
        )
        resolutions = {
            bare.key: combinations.Resolution(
                combinations.Verdict.LAUNCH, "-", plan_of("simulation_inst", "alpha_backbone_inst")),
            joined.key: combinations.Resolution(
                combinations.Verdict.LAUNCH, "-",
                plan_of("simulation_inst", "alpha_backbone_inst", "bravo_backbone_inst")),
        }

        def ids(state):
            return sorted(json.loads(configuration)[1]["instance_id"] for configuration in state)

        def pairs(states):
            units = combinations.coverage_units(joined, states, frozenset())
            return [unit for unit in units if unit[0] == "pair"]

        # The joined copy runs beside the file's, and the launch proves the
        # two robots side by side.
        launch_state, join_state = combinations.running_states(joined, resolutions)
        self.assertEqual(ids(launch_state), ["alpha_backbone_inst", "simulation_inst"])
        self.assertEqual(ids(join_state), ["alpha_backbone_inst", "bravo_backbone_inst", "simulation_inst"])
        self.assertEqual(len(pairs([launch_state, join_state])), 3)
        self.assertEqual(combinations.running_states(bare, resolutions), [launch_state])

    def test_a_joined_copy_is_wired_into_the_simulation_it_stands_in(self):
        """What a plain join comes up beside is read off the plan: a
        simulated robot's initializer and backbone link into the
        simulation's instance."""
        plan = {"deployments": [
            {"source": {"name": "sim_mujoco", "tag": "v1"},
             "instances": [{"instance_id": "simulation_inst"}]},
            {"source": {"name": "robot_initializer", "tag": "v1"},
             "instances": [{"instance_id": "bravo_init_inst", "core_node": "bravo",
                            "links": {"simulation": "simulation_inst"}}]},
            {"source": {"name": "openarm_backbone", "tag": "v1"},
             "instances": [{"instance_id": "bravo_backbone_inst", "core_node": "bravo",
                            "links": {"left_arm": "simulation_inst/arms",
                                      "robot_init": "bravo_init_inst"}}]},
        ]}

        self.assertEqual(combinations.joined_into(plan, "bravo"), frozenset({"sim_mujoco"}))

    def test_a_joined_copy_that_drives_hardware_is_wired_into_nothing_outside_itself(self):
        """A robot on real motors comes up beside the simulation without
        standing in it, so which simulation the stack runs says nothing about
        what its join proves."""
        plan = {"deployments": [
            {"source": {"name": "sim_mujoco", "tag": "v1"},
             "instances": [{"instance_id": "simulation_inst"}]},
            {"source": {"name": "openarm_arm", "tag": "v1"},
             "instances": [{"instance_id": "bravo_left_arm_inst", "core_node": "bravo"}]},
            {"source": {"name": "openarm_backbone", "tag": "v1"},
             "instances": [{"instance_id": "bravo_backbone_inst", "core_node": "bravo",
                            "links": {"left_arm": "bravo_left_arm_inst",
                                      "peers": ["bravo_left_arm_inst"],
                                      "simulation": {
                                          "vacant": "this robot drives its own limbs"}}}]},
        ]}

        self.assertEqual(combinations.joined_into(plan, "bravo"), frozenset())

    def test_a_link_shape_the_planner_cannot_read_stops_the_plan(self):
        """A link read as naming no instance shrinks the plan in silence, so
        a shape the reader does not know is a plan that does not run."""
        plan = {"deployments": [
            {"source": {"name": "openarm_backbone", "tag": "v1"},
             "instances": [{"instance_id": "bravo_backbone_inst", "core_node": "bravo",
                            "links": {"left_arm": {"instance": "simulation_inst"}}}]},
        ]}

        with self.assertRaisesRegex(SystemExit, r"the link `left_arm` of bravo_backbone_inst"):
            combinations.joined_into(plan, "bravo")

    def test_a_copy_wired_into_an_exposure_server_is_keyed_by_what_deploys_it(self):
        """A deployment publishing MCP exposures names no node, and a copy
        wired into one comes up beside it: each such server is a host of its
        own, so two launches standing a copy beside different servers prove
        different things."""
        def plan_of(*exposures):
            return {"deployments": [
                {"source": {"exposures": list(exposures)},
                 "instances": [{"instance_id": "world_control_inst"}]},
                {"source": {"name": "openarm_backbone", "tag": "v1"},
                 "instances": [{"instance_id": "bravo_backbone_inst", "core_node": "bravo",
                                "links": {"scene": "world_control_inst"}}]},
            ]}

        scene = combinations.joined_into(plan_of("simulation:v1"), "bravo")

        self.assertEqual(len(scene), 1)
        self.assertNotEqual(scene, combinations.joined_into(plan_of("other:v1"), "bravo"))

    def test_a_copy_placed_on_a_core_node_of_another_name_is_not_its_own(self):
        """`core_node` places an instance: a copy for a copy's instances, and
        the core node a launcher declares for the rest."""
        plan = {"deployments": [
            {"source": {"name": "sim_mujoco", "tag": "v1"},
             "instances": [
                 {"instance_id": "simulation_inst", "core_node": "robot_onboard"},
                 # An id opening with the copy's name, placed nowhere.
                 {"instance_id": "bravo_shared_log_inst"},
             ]},
            {"source": {"name": "robot_initializer", "tag": "v1"},
             "instances": [{"instance_id": "bravo_init_inst", "core_node": "bravo",
                            "links": {"simulation": "simulation_inst"}}]},
        ]}

        self.assertEqual(combinations.copy_instances(plan, "bravo"), ["bravo_init_inst"])
        self.assertEqual(combinations.joined_into(plan, "bravo"), frozenset({"sim_mujoco"}))

    def test_a_stack_instances_configuration_leaves_out_the_links_copies_add(self):
        # The server every robot is listed on is one configuration however
        # many robots fill its sets; each robot's own instances are whole.
        def plan(members):
            return {"deployments": [
                {"source": {"exposures": ["robot_control:v1"]}, "instances": [
                    {"instance_id": "robot_control_inst", "core_node": "self", "arguments": {"port": 8900},
                     "links": {"identity": members, "clock": "simulation_inst"}}]},
                {"source": {"name": "robot_initializer", "tag": "v1"}, "instances": [
                    {"instance_id": f"{copy}_init_inst", "core_node": copy, "links": {"simulation": "simulation_inst"}}
                    for copy in ("alpha", "bravo")]},
            ]}
        one = combinations.instance_configurations(plan(["alpha_init_inst"]), ("alpha", "bravo"))
        two = combinations.instance_configurations(plan(["alpha_init_inst", "bravo_init_inst"]), ("alpha", "bravo"))
        self.assertEqual(one, two)
        self.assertEqual(len(two), 3)
        server, = [c for c in two if "robot_control_inst" in c]
        self.assertIn('"links":{"clock":"simulation_inst"}', server)
        # A stack instance's own links stay part of its configuration.
        self.assertNotEqual(
            combinations.instance_configurations(plan([]), ("alpha", "bravo")),
            combinations.instance_configurations(
                {"deployments": [{"source": {"exposures": ["robot_control:v1"]}, "instances": [
                    {"instance_id": "robot_control_inst", "core_node": "self", "arguments": {"port": 8901}, "links": {}}]}]},
                ("alpha", "bravo")))

    def test_a_plain_join_is_launched_even_where_a_join_with_words_runs_the_same_instances(self):
        def plan_of(*names):
            return {"deployments": [{"source": {"name": "node", "tag": "v1"},
                                     "instances": [{"instance_id": name} for name in names]}]}

        def candidate(join_words):
            return combinations.Candidate("fleet", "fleet.json5", "", "openarm_v2", join_words, False)

        state = [combinations.instance_configurations(plan_of("simulation_inst", "bravo_backbone_inst"), ("bravo",))]
        plain = combinations.coverage_units(candidate(""), state, frozenset({"sim_mujoco"}))
        spelled = combinations.coverage_units(
            candidate("robot_commander=web_commander"), state, frozenset({"sim_mujoco"})
        )
        self.assertEqual(plain - spelled, {("plain join", "fleet", "openarm_v2", ("sim_mujoco",))})
        # A plain join proves the simulation it came up in and no other: each
        # of them stands a joined robot beside the ones standing its own way.
        elsewhere = combinations.coverage_units(candidate(""), state, frozenset({"waldo"}))
        self.assertEqual(
            elsewhere - plain, {("plain join", "fleet", "openarm_v2", ("waldo",))}
        )
        # The spelled join proves more instances, and the plain one still launches.
        units = {"spelled": spelled | {("configuration", "recorder")}, "plain": plain}
        self.assertEqual(combinations.select_launches(units), ["spelled", "plain"])

    def test_a_join_whose_own_launch_does_not_resolve_fails_the_plan(self):
        answers = {("fleet.json5", "", "", ""): REFUSAL}
        with self.assertRaises(SystemExit) as failure:
            with planned({"fleet": fleet_launcher()}, answers):
                pass
        self.assertIn("the launch it joins, fleet, does not resolve", str(failure.exception))

    def test_selection_is_greedy_and_takes_the_earliest_on_a_tie(self):
        units = {"small": {1}, "first": {1, 2}, "second": {1, 2}, "other": {3}}
        self.assertEqual(combinations.select_launches(units), ["first", "other"])
        self.assertEqual(combinations.select_launches({}), [])

    def test_a_combination_left_out_names_the_launches_that_prove_it(self):
        units = {"a": {1, 2}, "b": {3}, "c": {4}}
        self.assertEqual(combinations.covering_launches({2, 3}, ["a", "b", "c"], units), ["a", "b"])


class ScopeTests(unittest.TestCase):
    LAUNCHER_FILES = {"fleet.json5", "openarm/fragments/mujoco.json5"}

    def kind(self, changed_files):
        return combinations.classify_scope(changed_files, self.LAUNCHER_FILES).kind

    def test_a_run_without_a_diff_covers_everything(self):
        self.assertIs(self.kind(None), combinations.ScopeKind.EVERYTHING)
        self.assertIs(self.kind([]), combinations.ScopeKind.EVERYTHING)

    def test_a_change_to_launcher_files_alone_covers_what_it_changes(self):
        self.assertIs(self.kind(["openarm/fragments/mujoco.json5"]), combinations.ScopeKind.CHANGED)
        self.assertIs(self.kind(["fleet.json5", "Readme.md"]), combinations.ScopeKind.CHANGED)

    def test_documentation_alone_covers_nothing(self):
        self.assertIs(self.kind(["Readme.md", "openarm/README.MD"]), combinations.ScopeKind.NOTHING)

    def test_a_file_outside_every_launcher_covers_everything_and_is_named(self):
        scope = combinations.classify_scope(
            ["fleet.json5", ".github/workflows/tests.yml"], self.LAUNCHER_FILES)
        self.assertIs(scope.kind, combinations.ScopeKind.EVERYTHING)
        self.assertIn("`.github/workflows/tests.yml` is outside every launcher", scope.reason)

    def scoped(self, changed, launchers, base_launchers=None):
        """The scope decided for a pull request changing `changed`, and what
        the step reports to the workflow."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_repository(root / "head", launchers)
            if base_launchers is not None:
                write_repository(root / "base", base_launchers)
            (root / "changed.txt").write_text("".join(f"{file}\n" for file in changed))
            output = root / "output.txt"
            with patch.dict(combinations.os.environ, {"GITHUB_OUTPUT": str(output)}), \
                    contextlib.redirect_stdout(io.StringIO()):
                combinations.command_scope(
                    root / "head", root / "changed.txt",
                    root / "base" if base_launchers is not None else None, root / "scope.json")
            return combinations.read_scope(root / "scope.json").kind, output.read_text()

    def test_the_step_reports_whether_anything_is_planned(self):
        launchers = {"fleet": simulation_launcher("mujoco")}
        self.assertEqual(self.scoped(["fleet.json5"], launchers), (combinations.ScopeKind.CHANGED, "any=true\n"))
        self.assertEqual(self.scoped(["Readme.md"], launchers), (combinations.ScopeKind.NOTHING, "any=false\n"))
        self.assertEqual(self.scoped(["fleet.json5", "LICENSE"], launchers),
                         (combinations.ScopeKind.EVERYTHING, "any=true\n"))

    def test_a_launcher_the_pull_request_deletes_is_still_a_launcher_file(self):
        kind, _ = self.scoped(
            ["retired.json5"],
            {"fleet": simulation_launcher("mujoco")},
            base_launchers={"fleet": simulation_launcher("mujoco"), "retired": simulation_launcher("mujoco")},
        )
        self.assertIs(kind, combinations.ScopeKind.CHANGED)

    def test_only_the_combinations_the_change_moves_are_launched(self):
        launchers = {"sim": simulation_launcher("mujoco", "waldo")}
        answers = {("sim.json5", "simulation=waldo", "", ""): resolved_plan("waldo:v2")}
        base_answers = {("sim.json5", "simulation=waldo", "", ""): resolved_plan("waldo:v1")}
        with planned(launchers, answers, CHANGED, launchers, base_answers) as (_, labels, _plan, summary):
            self.assertEqual(labels, ["sim (simulation=waldo)"])
            self.assertIn("1 launchable combinations resolve to the plan the base tree gives them", summary)
            self.assertIn("- sim (simulation=mujoco)", summary)

    def test_a_combination_the_base_tree_lacks_is_launched(self):
        with planned({"sim": simulation_launcher("mujoco", "isaac_sim", "waldo")}, scope=CHANGED,
                     base_launchers={"sim": simulation_launcher("mujoco", "isaac_sim")}) as (_, labels, _plan, _summary):
            self.assertEqual(labels, ["sim (simulation=waldo)"])

    def test_a_combination_the_change_makes_launchable_is_launched(self):
        launchers = {"sim": simulation_launcher("mujoco", "waldo")}
        base_answers = {("sim.json5", "simulation=waldo", "", ""): REFUSAL}
        with planned(launchers, scope=CHANGED, base_launchers=launchers,
                     base_answers=base_answers) as (_, labels, _plan, _summary):
            self.assertEqual(labels, ["sim (simulation=waldo)"])

    def test_a_base_launcher_this_reader_refuses_counts_every_combination_as_changed(self):
        with planned({"sim": simulation_launcher("mujoco")}, scope=CHANGED,
                     base_launchers={"sim": "{ components: 'not a list' }"}) as (_, labels, _plan, _summary):
            self.assertEqual(labels, ["sim"])

    def test_a_change_that_moves_no_plan_launches_nothing(self):
        launchers = {"sim": simulation_launcher("mujoco", "waldo")}
        with planned(launchers, scope=CHANGED, base_launchers=launchers) as (_, labels, _plan, summary):
            self.assertEqual(labels, [])
            self.assertIn("Launching nothing: this change moves no combination's resolved plan", summary)

    def test_a_changed_scope_without_a_base_tree_fails_the_plan(self):
        with self.assertRaises(SystemExit) as failure:
            with planned({"sim": simulation_launcher("mujoco")}, scope=CHANGED):
                pass
        self.assertIn("--base-root", str(failure.exception))


JOINED_INSTANCES = ["bravo_backbone_inst", "bravo_commander_inst"]


class FakePeppy:
    """Stands in for the `peppy` commands of a launch: every command exits 0
    unless `failing` holds its first words, `stack list --json` answers
    `copies`, copy name to the instances it minted, with every instance
    running and healthy but for `states`, instance id to the state peppy
    reports, and `unhealthy`, the running instances whose health probe
    failed; the commands are kept in order."""

    def __init__(self, failing=(), copies=None, states=None, unhealthy=()):
        self.failing = [list(words) for words in failing]
        self.copies = {combinations.COPY_NAME: JOINED_INSTANCES} if copies is None else copies
        self.states = states or {}
        self.unhealthy = set(unhealthy)
        self.commands = []

    def __call__(self, argv, capture=False):
        self.commands.append(argv)
        failed = any(argv[: len(words)] == words for words in self.failing)
        instances = [
            {
                "instance_id": instance_id,
                "state": self.states.get(instance_id, "running"),
                "healthy": instance_id not in self.unhealthy,
            }
            for instance_ids in self.copies.values()
            for instance_id in instance_ids
            if self.states.get(instance_id) != "missing"
        ]
        listing = json.dumps({"core_nodes": [{
            "copies": [
                {"name": name, "instance_ids": instance_ids} for name, instance_ids in self.copies.items()
            ],
            "stack": {"nodes": [{"instances": instances}]},
        }]})
        return completed(listing if capture else "", returncode=1 if failed else 0)


def a_launch(launcher="fleet", words="", join_option="", join_words="", local=False, launch_joins=()):
    label = combinations.combination_label(launcher, words, join_option, join_words, local, launch_joins)
    join_name = combinations.COPY_NAME if join_option else ""
    return combinations.Launch(
        label, launcher, words, join_option, join_name, join_words, local,
        JOINED_INSTANCES if join_option or launch_joins else [], list(launch_joins),
    )


def launched(launches, peppy):
    with contextlib.redirect_stdout(io.StringIO()) as log:
        outcomes = combinations.launch_all(launches, peppy)
    return outcomes, log.getvalue()


IDLE = ["--node-build-idle-timeout-secs", "900"]


class LaunchTests(unittest.TestCase):
    def test_a_launch_comes_up_is_listed_and_is_reset(self):
        peppy = FakePeppy()
        outcomes, log = launched([a_launch(words="simulation=mujoco", local=True)], peppy)
        self.assertEqual(peppy.commands, [
            ["peppy", "stack", "launch", "fleet", "--with", "simulation=mujoco", "--local", *IDLE],
            ["peppy", "stack", "list"],
            ["peppy", "stack", "reset"],
        ])
        self.assertEqual([outcome.status for outcome in outcomes], [combinations.Status.PASSED])
        self.assertIn("::group::fleet (simulation=mujoco) [--local]\n", log)
        self.assertIn("::endgroup::\n", log)

    def test_a_copy_named_at_launch_is_held_to_its_preview(self):
        peppy = FakePeppy()
        outcomes, _ = launched([a_launch(launch_joins=("openarm_v2",))], peppy)
        self.assertEqual(peppy.commands, [
            ["peppy", "stack", "launch", "fleet", "--join", "openarm_v2:bravo", *IDLE],
            ["peppy", "stack", "list", "--json"],
            ["peppy", "stack", "list"],
            ["peppy", "stack", "reset"],
        ])
        self.assertEqual([outcome.status for outcome in outcomes], [combinations.Status.PASSED])
        outcomes, _ = launched([a_launch(launch_joins=("openarm_v2",))], FakePeppy(copies={"bravo": ["bravo_x"]}))
        self.assertEqual([outcome.status for outcome in outcomes], [combinations.Status.FAILED])
        self.assertIn("bravo", outcomes[0].detail)

    def test_a_flat_launcher_launches_by_name_alone(self):
        peppy = FakePeppy()
        launched([a_launch("rust_robot")], peppy)
        self.assertEqual(peppy.commands[0], ["peppy", "stack", "launch", "rust_robot", *IDLE])

    def test_a_join_comes_up_beside_the_files_copies_and_is_held_to_its_preview(self):
        peppy = FakePeppy()
        outcomes, _ = launched([a_launch(join_option="openarm_v2", join_words="recorder=lerobot_recorder")], peppy)
        self.assertEqual(peppy.commands, [
            ["peppy", "stack", "launch", "fleet", *IDLE],
            ["peppy", "stack", "join", "openarm_v2:bravo", "--with", "recorder=lerobot_recorder", *IDLE],
            ["peppy", "stack", "list", "--json"],
            ["peppy", "stack", "list"],
            ["peppy", "stack", "remove", "bravo"],
            ["peppy", "stack", "list"],
            ["peppy", "stack", "reset"],
        ])
        self.assertEqual([outcome.status for outcome in outcomes], [combinations.Status.PASSED])

    def test_a_joined_copy_running_other_instances_than_its_preview_fails_the_launch(self):
        # A plain join that comes up under the fragment's default commander,
        # where the preview gave it the launcher entry's.
        peppy = FakePeppy(copies={"alpha": ["alpha_backbone_inst"], "bravo": ["bravo_backbone_inst"]})
        outcomes, log = launched([a_launch(join_option="openarm_v2")], peppy)
        self.assertEqual([outcome.status for outcome in outcomes], [combinations.Status.FAILED])
        self.assertEqual(
            outcomes[0].detail,
            "copy `bravo` runs bravo_backbone_inst; `peppy stack resolve` previewed "
            "bravo_backbone_inst, bravo_commander_inst",
        )
        # The launch stops at the mismatch and the stack is still reset.
        self.assertEqual(peppy.commands[-2:], [
            ["peppy", "stack", "list", "--json"],
            ["peppy", "stack", "reset"],
        ])
        self.assertIn("::error title=fleet + join openarm_v2::copy `bravo` runs", log)

    def test_a_joined_copy_the_stack_does_not_list_fails_the_launch(self):
        outcomes, _ = launched([a_launch(join_option="openarm_v2")], FakePeppy(copies={}))
        self.assertIn("copy `bravo` runs no instance", outcomes[0].detail)

    def test_a_joined_copy_with_an_instance_not_running_fails_the_launch(self):
        """The engine took the robot out during the join: its initializer
        finished while the join reported success."""
        for states, unhealthy, detail in [
            ({"bravo_backbone_inst": "finished"}, (), "bravo_backbone_inst is finished"),
            ({"bravo_commander_inst": "failed"}, (), "bravo_commander_inst is failed"),
            ({}, ("bravo_backbone_inst",), "bravo_backbone_inst is unhealthy"),
            ({"bravo_commander_inst": "missing"}, (), "bravo_commander_inst is missing"),
            (
                {"bravo_backbone_inst": "finished", "bravo_commander_inst": "starting"},
                (),
                "bravo_backbone_inst is finished, bravo_commander_inst is starting",
            ),
        ]:
            with self.subTest(detail=detail):
                peppy = FakePeppy(states=states, unhealthy=unhealthy)
                outcomes, log = launched([a_launch(join_option="openarm_v2")], peppy)
                self.assertEqual([outcome.status for outcome in outcomes], [combinations.Status.FAILED])
                self.assertEqual(
                    outcomes[0].detail, f"copy `bravo` is not running whole after its join: {detail}"
                )
                self.assertEqual(peppy.commands[-2:], [
                    ["peppy", "stack", "list", "--json"],
                    ["peppy", "stack", "reset"],
                ])
                self.assertIn("::error title=fleet + join openarm_v2::copy `bravo` is not running whole", log)

    def test_a_joined_copy_running_whole_passes(self):
        peppy = FakePeppy(states={"alpha_backbone_inst": "finished"}, copies={
            "alpha": ["alpha_backbone_inst"], combinations.COPY_NAME: JOINED_INSTANCES,
        })
        outcomes, _ = launched([a_launch(join_option="openarm_v2")], peppy)
        self.assertEqual([outcome.status for outcome in outcomes], [combinations.Status.PASSED])

    def test_a_join_without_words_passes_none(self):
        peppy = FakePeppy()
        launched([a_launch(join_option="openarm_v2")], peppy)
        self.assertIn(["peppy", "stack", "join", "openarm_v2:bravo", *IDLE], peppy.commands)

    def test_a_failed_launch_is_named_reset_and_followed_by_the_next(self):
        peppy = FakePeppy(failing=[["peppy", "stack", "launch", "fleet"]])
        outcomes, log = launched([a_launch("fleet"), a_launch("rust_robot")], peppy)
        self.assertEqual(peppy.commands, [
            ["peppy", "stack", "launch", "fleet", *IDLE],
            ["peppy", "stack", "reset"],
            ["peppy", "stack", "launch", "rust_robot", *IDLE],
            ["peppy", "stack", "list"],
            ["peppy", "stack", "reset"],
        ])
        self.assertEqual([outcome.status for outcome in outcomes],
                         [combinations.Status.FAILED, combinations.Status.PASSED])
        self.assertIn("`peppy stack launch fleet", outcomes[0].detail)
        self.assertIn("::error title=fleet::`peppy stack launch fleet", log)

    def test_a_failed_join_stops_that_launch_at_the_join(self):
        peppy = FakePeppy(failing=[["peppy", "stack", "join"]])
        outcomes, _ = launched([a_launch(join_option="openarm_v2")], peppy)
        self.assertEqual(peppy.commands[-2:], [
            ["peppy", "stack", "join", "openarm_v2:bravo", *IDLE],
            ["peppy", "stack", "reset"],
        ])
        self.assertIn("`peppy stack join openarm_v2", outcomes[0].detail)

    def test_a_stack_that_does_not_reset_ends_the_run_and_names_what_never_launched(self):
        peppy = FakePeppy(failing=[["peppy", "stack", "reset"]])
        outcomes, _ = launched([a_launch("fleet"), a_launch("rust_robot"), a_launch("python_robot")], peppy)
        self.assertEqual(peppy.commands[-1], ["peppy", "stack", "reset"])
        self.assertNotIn(["peppy", "stack", "launch", "rust_robot", *IDLE], peppy.commands)
        self.assertEqual([outcome.status for outcome in outcomes], [
            combinations.Status.FAILED, combinations.Status.NOT_LAUNCHED, combinations.Status.NOT_LAUNCHED,
        ])
        self.assertIn("`peppy stack reset` exited 1", outcomes[0].detail)
        self.assertEqual(outcomes[1].detail, "the stack did not reset after fleet")

    def test_a_failed_launch_and_a_failed_reset_are_both_named(self):
        peppy = FakePeppy(failing=[["peppy", "stack", "launch"], ["peppy", "stack", "reset"]])
        outcomes, _ = launched([a_launch("fleet")], peppy)
        self.assertIn("`peppy stack launch fleet", outcomes[0].detail)
        self.assertIn("then `peppy stack reset` exited 1", outcomes[0].detail)

    def run_command(self, peppy, launches):
        with tempfile.TemporaryDirectory() as directory:
            plan, summary = Path(directory) / "plan.json", Path(directory) / "summary.md"
            combinations.write_plan(launches, plan)
            with patch.object(combinations, "run_peppy", peppy), \
                    patch.dict(combinations.os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    combinations.command_launch(plan)
                    code = 0
                except SystemExit as exit:
                    code = exit.code
            return code, summary.read_text()

    def test_the_job_passes_when_every_launch_comes_up(self):
        code, summary = self.run_command(FakePeppy(), [a_launch("fleet"), a_launch("rust_robot")])
        self.assertEqual(code, 0)
        self.assertIn("2 of 2 launches came up start to end.", summary)
        self.assertIn("| rust_robot | ✅ launched |", summary)

    def test_the_job_fails_when_any_launch_does_not(self):
        peppy = FakePeppy(failing=[["peppy", "stack", "launch", "fleet"]])
        code, summary = self.run_command(peppy, [a_launch("fleet"), a_launch("rust_robot")])
        self.assertEqual(code, 1)
        self.assertIn("1 of 2 launches came up start to end.", summary)
        self.assertIn("| fleet | ❌ failed `peppy stack launch fleet", summary)

    def test_a_captured_command_holds_peppys_logging_to_errors(self):
        with patch.object(combinations.subprocess, "run", return_value=completed("{}")) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            combinations.run_peppy(["peppy", "stack", "list", "--json"], capture=True)
            self.assertEqual(run.call_args.kwargs["env"]["RUST_LOG"], "error")
            self.assertEqual(run.call_args.kwargs["stdout"], combinations.subprocess.PIPE)
            combinations.run_peppy(["peppy", "stack", "list"])
            self.assertIsNone(run.call_args.kwargs["env"])
            self.assertIsNone(run.call_args.kwargs["stdout"])

    def test_a_label_can_only_ever_be_text_in_a_workflow_command(self):
        with contextlib.redirect_stdout(io.StringIO()) as log:
            combinations.workflow_command("error", "100%\n::group::x", title="a,b:c\n")
        self.assertEqual(log.getvalue(), "::error title=a%2Cb%3Ac%0A::100%25%0A::group::x\n")

    def test_the_plan_reaches_the_launch_job_whole(self):
        launches = [a_launch(words="simulation=mujoco", join_option="openarm_v2", local=True)]
        with tempfile.TemporaryDirectory() as directory:
            combinations.write_plan(launches, Path(directory) / "plan.json")
            self.assertEqual(combinations.read_plan(Path(directory) / "plan.json"), launches)


if __name__ == "__main__":
    unittest.main()
