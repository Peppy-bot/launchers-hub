import contextlib
import io
import json
from pathlib import Path
import posixpath
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
    "physical.json5": ("openarm_v1", "openarm_v2", "so101"),
    "openarm/openarm_simulation.json5": ("openarm_v1_sim", "openarm_v2_sim", "so101_sim"),
    "so101/so101_simulation.json5": ("so101_sim", "openarm_v1_sim", "openarm_v2_sim"),
    "mcp/simulation_mcp.json5": ("openarm_v1_sim", "openarm_v2_sim", "so101_sim"),
}
SIMULATION_LAUNCHERS = [path for path in ROBOT_LAUNCHERS if path != "physical.json5"]

#: What every simulation launcher adjusts: the server reads the simulation's
#: clock. The MCP launcher turns rendering on as well.
SERVER_CLOCK = {"target": SERVER, "set_framework": {"clock": "simulation"}}
RENDERING_ON = {"target": "simulation_inst", "set_arguments": {"cameras_enabled": True}}


def family_of(option):
    return "so101" if option.startswith("so101") else "openarm"


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

    def test_a_one_axis_with_a_single_option_is_no_choice(self):
        control = combinations.Axis("control", "one", ["control_common"], deployed="control_common")
        commander = combinations.Axis("robot_commander", "one", ["web_commander", "xr_commander"], deployed="web_commander")
        found = combinations.selections_of([control, commander])
        self.assertEqual(
            found,
            [[("robot_commander", "web_commander")], [("robot_commander", "xr_commander")]],
        )
        # A single option nothing deploys is a choice the launch has to
        # write out.
        undeployed = combinations.Axis("control", "one", ["control_common"])
        self.assertEqual(
            combinations.selections_of([undeployed]), [[("control", "control_common")]]
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

        # A join is plain (1) or under any selection of the robot's axes: a
        # physical v1 has three commanders, a recorder and a rig, a physical
        # v2 four commanders, a recorder, a rig and a brain, a physical
        # SO-101 four commanders, a recorder and a rig; the simulated v1 has
        # three commanders and a recorder, the simulated v2 the physical
        # v2's axes, the simulated SO-101 the physical one's.
        v1, v2, so101 = 1 + 3 * 2 * 2, 1 + 4 * 2 * 2 * 2, 1 + 4 * 2 * 2
        # The simulated SO-101's commander axis is zero_or_one: its unfilled
        # state is the plain join, so no selection is counted twice.
        v1_sim, v2_sim, so101_sim = 1 + 3 * 2, v2, 4 * 2 * 2
        # The physical launcher: the endpoint unfilled or on, each bare and
        # joined by any physical robot. It lists no robot, so no launch names
        # one.
        physical = combos("physical")
        self.assertEqual(len(physical), 2 * (1 + v1 + v2 + so101))
        self.assertIn(["combo", "physical", "robot_control=robot_control", "", "", "", "-"], physical)
        self.assertIn(["combo", "physical", "", "", "", "", "-"], physical)
        self.assertIn(
            ["combo", "physical", "", "", "so101", "robot_commander=xr_commander,recorder=lerobot_recorder,camera_rig=cameras", "-"],
            physical,
        )
        self.assertIn(
            ["combo", "physical", "robot_control=robot_control", "", "openarm_v2", "robot_commander=mcp_commander,camera_rig=cameras,brain=ai_brain_vla", "-"],
            physical,
        )
        self.assertEqual([c for c in physical if c[3]], [])
        # A simulation launcher's stack: MuJoCo, Isaac Sim and Waldo with and
        # without their scene commander (5), the robots' endpoint unfilled or
        # on; each launched with alpha under every selection of its axes (the
        # v2's, or the SO-101's) and joined by any simulated robot.
        stacks = (1 + 2 + 2) * 2
        alpha_v2, alpha_so101 = 4 * 2 * 2 * 2, 4 * 2 * 2
        sim = combos("openarm_simulation")
        self.assertEqual(len(sim), stacks * (alpha_v2 + v1_sim + v2_sim + so101_sim))
        self.assertIn(["combo", "openarm_simulation", "simulation=mujoco,robot_control=robot_control", "", "", "", "-"], sim)
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=mujoco,robot_control=robot_control,alpha.robot_commander=mcp_commander,alpha.camera_rig=cameras_sim", "", "", "", "-"],
            sim,
        )
        self.assertIn(["combo", "openarm_simulation", "simulation=waldo,robot_control=robot_control", "", "so101_sim", "", "-"], sim)
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=waldo,scene_commander=web_scene_commander", "", "", "", "-"],
            sim,
        )
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=mujoco,robot_control=robot_control", "", "openarm_v2_sim", "robot_commander=mcp_commander,camera_rig=cameras_sim", "-"],
            sim,
        )
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=isaac_sim", "", "openarm_v2_sim", "robot_commander=xr_commander,recorder=lerobot_recorder,camera_rig=cameras_sim", "-"],
            sim,
        )
        references = next(line for line in lines if line.startswith("launcher\topenarm_simulation\t"))
        for reference in [
            "mcp/fragments/robot_control.json5",
            "openarm/fragments/control_common.json5",
            "openarm/fragments/openarm_v1_sim.json5",
            "openarm/fragments/openarm_v2_sim.json5",
            "openarm/fragments/cameras_sim.json5",
            "robot_commanders/fragments/mcp_commander.json5",
            "robot_commanders/fragments/xr_commander.json5",
            "so101/fragments/control_common.json5",
            "so101/fragments/so101_sim.json5",
            "recording/fragments/lerobot_recorder.json5",
            "simulation/fragments/waldo.json5",
            "simulation/fragments/web_scene_commander.json5",
        ]:
            self.assertIn(reference, references)
        self.assertNotIn("world_control", references)
        # The so101_simulation launcher: the same selections, joined by any
        # simulated robot, plain and under every selection of its axes.
        so101_launcher = combos("so101_simulation")
        self.assertEqual(len(so101_launcher), stacks * (alpha_so101 + v1_sim + v2_sim + so101_sim))
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
            ["combo", "so101_simulation", "simulation=isaac_sim,robot_control=robot_control", "", "openarm_v2_sim", "robot_commander=xr_commander", "-"],
            so101_launcher,
        )
        # The simulation_mcp launcher: the same stacks with the world's
        # endpoint on or off as well (the constraint refuses it off Waldo at
        # resolve), each launched with alpha under every selection of its
        # axes, naming either unlisted robot at launch, and joined by any
        # robot, plain and under every selection of its axes. The entries
        # state the MCP commander and the rendered rig for every copy of
        # their option, so alpha, the copy a launch names and the plain join
        # are MCP robots, and the planner picks all of it up from the index
        # and the fragments alone.
        robot_control = combos("simulation_mcp")
        self.assertEqual(len(robot_control), stacks * 2 * (alpha_v2 + 2 + v1_sim + v2_sim + so101_sim))
        stack = "simulation=waldo,robot_control=robot_control,world_control=world_control"
        self.assertIn(["combo", "simulation_mcp", stack, "", "", "", "-"], robot_control)
        self.assertIn(["combo", "simulation_mcp", stack, "so101_sim", "", "", "-"], robot_control)
        self.assertIn(["combo", "simulation_mcp", stack, "openarm_v1_sim", "", "", "-"], robot_control)
        self.assertIn(["combo", "simulation_mcp", stack, "", "openarm_v2_sim", "", "-"], robot_control)
        self.assertIn(["combo", "simulation_mcp", f"{stack},alpha.robot_commander=xr_commander", "", "", "", "-"], robot_control)
        self.assertIn(
            ["combo", "simulation_mcp", stack, "", "so101_sim", "robot_commander=mcp_commander,camera_rig=cameras_sim", "-"],
            robot_control,
        )
        self.assertIn(
            ["combo", "simulation_mcp", stack, "", "openarm_v2_sim", "robot_commander=mcp_commander,camera_rig=cameras_sim,brain=ai_brain_vla", "-"],
            robot_control,
        )
        self.assertIn(
            ["combo", "simulation_mcp", "simulation=waldo,scene_commander=web_scene_commander,robot_control=robot_control,world_control=world_control", "", "", "", "-"],
            robot_control,
        )
        self.assertIn(["combo", "simulation_mcp", "simulation=mujoco,robot_control=none,world_control=none", "", "", "", "-"], robot_control)
        self.assertIn(["combo", "simulation_mcp", "simulation=mujoco,robot_control=robot_control,world_control=none", "openarm_v1_sim", "", "", "-"], robot_control)
        references = next(line for line in lines if line.startswith("launcher\tsimulation_mcp\t"))
        for reference in [
            "mcp/fragments/robot_control.json5",
            "openarm/fragments/openarm_v2_sim.json5",
            "robot_commanders/fragments/mcp_commander.json5",
            "openarm/fragments/cameras_sim.json5",
            "so101/fragments/so101_sim.json5",
            "so101/fragments/cameras_sim.json5",
            "simulation/fragments/waldo.json5",
            "simulation/fragments/web_scene_commander.json5",
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
                    # An option listing several parts ends with its own; the
                    # parts before it are other options' files, shared.
                    path = combinations.fragment_parts(spec)[-1]
                    self.assertEqual(Path(path).stem, option, f"{document}: {axis['name']}={option} selects {path}")

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
        for robot, launcher, robot_path, tuning_path, command_rate, fps in [
            ("openarm", "openarm/openarm_simulation.json5", "openarm/fragments/openarm_v2_sim.json5",
             "openarm/fragments/control_common.json5", 100, 15),
            ("so101", "physical.json5", "so101/fragments/so101.json5",
             "so101/fragments/control_common.json5", 60, 30),
            ("so101_sim", "so101/so101_simulation.json5", "so101/fragments/so101_sim.json5",
             "so101/fragments/control_common.json5", 60, 30),
        ]:
            with self.subTest(robot=robot):
                references = combinations.read_launcher(root, launcher).references
                self.assertIn("robot_commanders/fragments/xr_commander.json5", references)
                self.assertIn("recording/fragments/lerobot_recorder.json5", references)
                fragment = combinations.load_json5(root / robot_path, robot)
                axes = {axis["name"] for axis in fragment["components"]}
                self.assertEqual({"robot_commander", "recorder"} - axes, set())
                control = combinations.load_json5(root / tuning_path, robot)
                commander = next(adjustment for adjustment in control["adjustments"]
                                 if adjustment["target"] == "commander_inst"
                                 and adjustment.get("when") == {"robot_commander": "xr_commander"})
                self.assertEqual(commander["set_arguments"]["command_rate_hz"], command_rate)
                self.assertEqual(commander["set_arguments"]["gripper_open_fraction"], 0.5)
                recorder = next(adjustment for adjustment in control["adjustments"]
                                if adjustment["target"] == "recorder_inst")
                self.assertEqual(recorder["set_arguments"]["fps"], fps)
        for path, robot_arguments in [
            ("robot_commanders/fragments/xr_commander.json5", {"command_rate_hz", "gripper_open_fraction"}),
            ("recording/fragments/lerobot_recorder.json5", {"fps", "robot_type", "storage_root"}),
        ]:
            document = combinations.load_json5(root / path, path)
            for deployment in document["deployments"]:
                for instance in deployment["instances"]:
                    self.assertTrue(robot_arguments.isdisjoint(instance.get("arguments", {})), path)

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
        # deploying Waldo rather than behind the scene commander selection;
        # no adjustment of the fragment or of a launcher touches
        # `debug_inspector` or `plugins`.
        # Every robot joins Waldo bringing its own model, so the world it
        # opens, the stage, is the fragment's too, and no launcher sets one.
        pinned = {"world", "debug_inspector", "plugins"}
        root = Path(__file__).resolve().parents[2]
        for launcher in ROBOT_LAUNCHERS:
            document = combinations.load_json5(root / launcher, launcher)
            for adjustment in document.get("adjustments", []):
                self.assertFalse(pinned & set(adjustment.get("set_arguments", {})), launcher)
        waldo = combinations.load_json5(root / "simulation/fragments/waldo.json5", "waldo")
        arguments = waldo["deployments"][0]["instances"][0]["arguments"]
        self.assertEqual(
            (arguments["world"], arguments["debug_inspector"], arguments["plugins"]),
            ("stage", True, "connection_status,frame_rate,robot_names,hand_teleop,contact_markers,dark_mode,viewer_hint"),
        )
        for adjustment in waldo.get("adjustments", []):
            self.assertFalse(pinned & set(adjustment.get("set_arguments", {})))

    def test_the_scene_commander_edits_and_observes_the_simulation_that_declares_it(self):
        root = Path(__file__).resolve().parents[2]
        # Both simulations that declare the commander deploy it as
        # simulation_inst, the instance its scene_manipulation and object_state
        # links both name.
        for simulation in ["waldo", "isaac_sim"]:
            with self.subTest(simulation=simulation):
                path = f"simulation/fragments/{simulation}.json5"
                fragment = combinations.load_json5(root / path, path)
                commander = next(axis for axis in fragment["components"] if axis["name"] == "scene_commander")
                self.assertEqual(commander["options"], {"web_scene_commander": "web_scene_commander.json5"})
                self.assertEqual(fragment["deployments"][0]["instances"][0]["instance_id"], "simulation_inst")
        scene = combinations.load_json5(
            root / "simulation/fragments/web_scene_commander.json5", "web_scene_commander")
        instance = scene["deployments"][0]["instances"][0]
        self.assertEqual(instance["links"]["simulation"], "simulation_inst")
        self.assertEqual(instance["links"]["objects"], "simulation_inst")

    def test_the_scene_commander_binds_lighting_materials_and_cameras_on_waldo_alone(self):
        root = Path(__file__).resolve().parents[2]
        scene = combinations.load_json5(
            root / "simulation/fragments/web_scene_commander.json5", "web_scene_commander")
        instance, = scene["deployments"][0]["instances"]
        self.assertEqual(instance["instance_id"], "scene_commander_inst")
        # The three optional slots are written vacant, with a reason; the
        # panel's fragment binds nothing itself.
        self.assertEqual(
            set(instance["links"]), {"simulation", "objects", "lighting", "materials", "cameras"})
        for slot in ["lighting", "materials", "cameras"]:
            with self.subTest(slot=slot):
                self.assertEqual(set(instance["links"][slot]), {"vacant"})
                self.assertTrue(instance["links"][slot]["vacant"].strip())
        self.assertNotIn("adjustments", scene)
        # Waldo, the one simulation serving the three contracts, binds them
        # from its own fragment without a guard: the adjustment runs exactly
        # when Waldo is selected and is skipped when no scene commander is.
        waldo = combinations.load_json5(root / "simulation/fragments/waldo.json5", "waldo")
        self.assertIn({
            "target": "scene_commander_inst",
            "set_links": {
                "lighting": "simulation_inst", "materials": "simulation_inst", "cameras": "simulation_inst",
            },
        }, waldo["adjustments"])
        for other in ["isaac_sim", "mujoco"]:
            with self.subTest(simulation=other):
                fragment = combinations.load_json5(root / f"simulation/fragments/{other}.json5", other)
                self.assertNotIn("scene_commander_inst", [
                    adjustment["target"] for adjustment in fragment.get("adjustments", [])])



    def test_each_fragment_lists_what_it_deploys_on_the_stacks_server(self):
        root = Path(__file__).resolve().parents[2]
        # Every target of the server is a set, and the fragment that deploys
        # an instance is the one that adds it: the family's control lists
        # the robot with its identity and how it stands, the shared MCP
        # commander adds the moves, the rig its cameras under that
        # commander, and the brain and the recorder list themselves. None
        # of it is guarded on the server: an adjustment naming an instance
        # the launch does not run is skipped.
        for control, readout in [
            ("openarm/fragments/control_common.json5",
             {"identity": ["init_inst"], "limb_state": ["backbone_inst"], "collision": ["backbone_inst"]}),
            ("so101/fragments/control_common.json5",
             {"identity": ["init_inst"], "limb_state": ["backbone_inst"]}),
        ]:
            with self.subTest(control=control):
                document = combinations.load_json5(root / control, control)
                listing, = [a for a in document["adjustments"] if a["target"] == SERVER]
                self.assertEqual(listing, {"target": SERVER, "add_links": readout})
        commander = combinations.load_json5(
            root / "robot_commanders/fragments/mcp_commander.json5", "mcp_commander")
        self.assertEqual(commander["deployments"], [])
        self.assertEqual(commander["adjustments"], [
            {"target": SERVER, "add_links": {"postures": ["backbone_inst"], "limb_motion": ["backbone_inst"]}},
        ])
        self.assertEqual(commander["constraints"], [{
            "requires": [{"robot_control": "robot_control"}],
            "reason": "mcp_commander adds the robot's moves to the stack's MCP endpoint; launch with robot_control",
        }])
        brain = combinations.load_json5(root / "openarm/fragments/ai_brain_vla.json5", "ai_brain_vla")
        self.assertEqual(brain["adjustments"], [
            {"target": SERVER, "when": {"robot_commander": "mcp_commander"},
             "add_links": {"item_perception": ["brain_inst"], "item_manipulation": ["brain_inst"]}},
        ])
        recorder = combinations.load_json5(
            root / "recording/fragments/lerobot_recorder.json5", "lerobot_recorder")
        self.assertEqual(recorder["adjustments"], [
            {"target": SERVER, "add_links": {"recorder": ["recorder_inst"]}},
        ])
        # The record button is the family's wiring, on the commanders that
        # carry a recorder slot: the KER and the SO-101 leader carry none,
        # and record over the endpoint alone.
        for control, commanders in [
            ("openarm/fragments/control_common.json5", ["web_commander", "xr_commander"]),
            ("so101/fragments/control_common.json5", "xr_commander"),
        ]:
            with self.subTest(control=control):
                document = combinations.load_json5(root / control, control)
                button, = [a for a in document["adjustments"]
                           if a["target"] == "commander_inst" and "recorder" in a.get("add_links", {})]
                self.assertEqual(button, {
                    "target": "commander_inst",
                    "when": {"robot_commander": commanders, "recorder": "lerobot_recorder"},
                    "add_links": {"recorder": ["recorder_inst"]},
                })
        # The robot fragments list nothing themselves: what they own is the
        # robot, and a simulated one binds its copy's instances to the
        # simulation's clock.
        clocked = ["init_inst", "backbone_inst", "commander_inst", "recorder_inst"]
        for path, clocks in [
            ("openarm/fragments/openarm_v1.json5", []), ("openarm/fragments/openarm_v2.json5", []),
            ("openarm/fragments/openarm_v1_sim.json5", clocked),
            ("openarm/fragments/openarm_v2_sim.json5", clocked + ["brain_inst"]),
            ("so101/fragments/so101.json5", []), ("so101/fragments/so101_sim.json5", clocked),
        ]:
            with self.subTest(path=path):
                document = combinations.load_json5(root / path, path)
                self.assertEqual([a for a in document["adjustments"] if a["target"] == SERVER], [])
                self.assertEqual(
                    [a["target"] for a in document["adjustments"] if a.get("set_framework") == {"clock": "simulation"}],
                    clocks)

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
                self.assertEqual(tuple(axes["robot"]["options"]), robots)
                for robot in robots:
                    family = family_of(robot)
                    own = path != "physical.json5" and path.startswith(family)
                    self.assertEqual(
                        axes["robot"]["options"][robot],
                        f"fragments/{robot}.json5" if own else f"{prefix}{family}/fragments/{robot}.json5")
        # The server reads one clock: a simulation launcher binds it to the
        # simulation's, each simulated robot's fragment binding the copy's
        # own instances; the physical launcher adjusts nothing and its robots
        # read wall time. The MCP launcher turns rendering on itself, so a
        # robot joined onto it gets its cameras.
        for path in SIMULATION_LAUNCHERS:
            with self.subTest(path=path):
                document = combinations.load_json5(root / path, path)
                expected = [RENDERING_ON, SERVER_CLOCK] if path == "mcp/simulation_mcp.json5" else [SERVER_CLOCK]
                self.assertEqual(document["adjustments"], expected)
        physical = combinations.load_json5(root / "physical.json5", "physical")
        self.assertNotIn("adjustments", physical)
        self.assertEqual(physical["deployments"], [])
        # Every rig lists its cameras under the MCP commander: color and
        # depth on hardware, where the uvc and zed nodes describe no profile
        # or geometry, and all four targets in simulation.
        for path, links in [
            ("openarm/fragments/cameras.json5", {
                "camera": ["wrist_left", "wrist_right"], "depth_camera": ["chest"]}),
            ("openarm/fragments/cameras_sim.json5", {
                "camera": ["wrist_left", "wrist_right"], "depth_camera": ["chest"],
                "camera_profile": ["wrist_left", "wrist_right", "chest"],
                "camera_geometry": ["wrist_left", "wrist_right", "chest"]}),
            ("so101/fragments/cameras.json5", {"camera": ["front"]}),
            ("so101/fragments/cameras_sim.json5", {
                "camera": ["front"], "camera_profile": ["front"], "camera_geometry": ["front"]}),
        ]:
            with self.subTest(path=path):
                rig = combinations.load_json5(root / path, path)
                listing, = [a for a in rig["adjustments"] if a["target"] == SERVER]
                self.assertEqual(listing, {
                    "target": SERVER, "when": {"robot_commander": "mcp_commander"}, "add_links": links})

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
            "lighting": "simulation_inst", "materials": "simulation_inst"})
        self.assertEqual(instance["framework"], {"clock": "simulation"})
        # It binds nothing of a robot copy and adjusts nothing.
        self.assertNotIn("adjustments", fragment)
        self.assertNotIn("components", fragment)
        # The axis is the MCP launcher's, deployed by the file with `none`
        # to switch it off, and Waldo alone implements the object controls,
        # lighting and materials contracts it binds, so the launcher requires Waldo
        # beside it. No fragment declares the axis.
        path = "mcp/simulation_mcp.json5"
        document = combinations.load_json5(root / path, path)
        axes = {axis["name"]: axis for axis in document["components"]}
        self.assertEqual(axes["world_control"], {
            "name": "world_control", "cardinality": "one",
            "options": {"world_control": "fragments/world_control.json5", "none": "../common/fragments/none.json5"},
        })
        self.assertEqual(combinations.option_entries(document, path)["world_control"], "world_control")
        self.assertEqual(document["constraints"], [{
            "when": {"world_control": "world_control"},
            "requires": [{"simulation": "waldo"}],
            "reason": "the simulated world's endpoint binds object_controls, scene_lighting and "
                      "scene_materials, which only waldo implements; launch with waldo or with "
                      "world_control=none",
        }])
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

    def test_every_robot_offers_the_shared_mcp_commander_and_the_leaders_release_their_sockets(self):
        root = Path(__file__).resolve().parents[2]
        shared = "../../robot_commanders/fragments/mcp_commander.json5"
        for path, cardinality, commanders, rig, brain in [
            ("openarm/fragments/openarm_v1.json5", None, ["web_commander", "xr_commander", "mcp_commander"], "cameras", None),
            ("openarm/fragments/openarm_v2.json5", None, ["web_commander", "xr_commander", "mcp_commander", "ker_commander"], "cameras", None),
            ("openarm/fragments/openarm_v1_sim.json5", None, ["web_commander", "xr_commander", "mcp_commander"], None, None),
            ("openarm/fragments/openarm_v2_sim.json5", None, ["web_commander", "xr_commander", "mcp_commander", "ker_commander"], "cameras_sim", "ai_brain_vla"),
            ("so101/fragments/so101.json5", None, ["so101_leader", "xr_commander", "mcp_commander", "none"], "cameras", None),
            ("so101/fragments/so101_sim.json5", "zero_or_one", ["so101_leader", "xr_commander", "mcp_commander"], "cameras_sim", None),
        ]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                axes = {axis["name"]: axis for axis in robot["components"]}
                self.assertEqual(list(axes["robot_commander"]["options"]), commanders)
                self.assertEqual(axes["robot_commander"]["options"]["mcp_commander"], shared)
                self.assertEqual(axes["robot_commander"].get("cardinality"), cardinality)
                if rig is None:
                    self.assertNotIn("camera_rig", axes)
                    self.assertNotIn("constraints", robot)
                else:
                    self.assertEqual(list(axes["camera_rig"]["options"]), [rig])
                    # The rig's streams need a consumer: the recorder, the
                    # headset, or the model behind the MCP commander. The
                    # rendered OpenArm rig also binds the brain's camera slot,
                    # so there the brain is a consumer too; the physical rig
                    # does not bind it.
                    requires = [{"recorder": "lerobot_recorder"}, {"robot_commander": ["xr_commander", "mcp_commander"]}]
                    consumers = "lerobot_recorder, xr_commander or mcp_commander"
                    if brain is not None:
                        requires.append({"brain": brain})
                        consumers = f"lerobot_recorder, xr_commander, mcp_commander or {brain}"
                    self.assertEqual(robot["constraints"], [{
                        "when": {"camera_rig": rig},
                        "requires": requires,
                        "reason": f"camera streams need a consumer; select {consumers}",
                    }])
        # The physical SO-101 runs actions-only under the empty option; the
        # simulated one under its unfilled axis, which its fragment leaves
        # unfilled.
        physical = combinations.load_json5(root / "so101/fragments/so101.json5", "so101")
        self.assertEqual(
            {axis["name"]: axis for axis in physical["components"]}["robot_commander"]["options"]["none"],
            "../../common/fragments/none.json5")
        simulated = combinations.load_json5(root / "so101/fragments/so101_sim.json5", "so101_sim")
        self.assertNotIn("robot_commander", combinations.option_entries(simulated, "so101_sim"))
        # An episode needs a trigger, which the KER and SO-101 leaders lack:
        # the record button of the panel or the headset, or the endpoint's
        # recorder tool, which the recorder lists itself on whenever the
        # endpoint runs. The rule is the family's.
        for control, commanders, reason in [
            ("openarm/fragments/control_common.json5", ["web_commander", "xr_commander"],
             "an episode starts from the record button of web_commander or xr_commander, or from "
             "recorder.record_episode on the MCP endpoint; select one of those commanders or launch with robot_control"),
            ("so101/fragments/control_common.json5", "xr_commander",
             "an episode starts from the XR commander's button or from recorder.record_episode on the "
             "MCP endpoint; select xr_commander or launch with robot_control"),
        ]:
            with self.subTest(control=control):
                document = combinations.load_json5(root / control, control)
                self.assertEqual(document["constraints"], [{
                    "when": {"recorder": "lerobot_recorder"},
                    "requires": [{"robot_commander": commanders}, {"robot_control": "robot_control"}],
                    "reason": reason,
                }])
        # No leader streams by default: the control leaves every upstream
        # socket vacant, and a leader releases the sockets it drives from its
        # own fragment. The headset block in the control switches the
        # backbone to pose mode and releases the pose sockets.
        openarm_sockets = ["leader_left_arm", "leader_right_arm", "leader_left_gripper", "leader_right_gripper"]
        for control, sockets, vacant, pose in [
            ("openarm/fragments/control_common.json5", openarm_sockets,
             openarm_sockets + ["collision_ctrl", "leader_left_arm_pose", "leader_right_arm_pose"],
             ["leader_left_arm_pose", "leader_right_arm_pose"]),
            ("so101/fragments/control_common.json5", ["leader_arm", "leader_gripper"],
             ["leader_arm", "leader_gripper", "leader_pose"], ["leader_pose", "leader_gripper"]),
        ]:
            with self.subTest(control=control):
                document = combinations.load_json5(root / control, control)
                backbone = next(instance for deployment in document["deployments"]
                                for instance in deployment["instances"] if instance["instance_id"] == "backbone_inst")
                self.assertEqual(backbone["arguments"]["upstream_mode"], "joints")
                for slot in vacant:
                    self.assertEqual(set(backbone["links"][slot]), {"vacant"}, slot)
                headset, = [a for a in document["adjustments"]
                            if a["target"] == "backbone_inst" and a.get("when") == {"robot_commander": "xr_commander"}]
                self.assertEqual(headset, {
                    "target": "backbone_inst", "when": {"robot_commander": "xr_commander"},
                    "set_arguments": {"upstream_mode": "pose"}, "unset_links": pose,
                })
        for path, release, sets in [
            ("openarm/fragments/web_commander.json5", openarm_sockets, {"collision_ctrl": "commander_inst"}),
            ("openarm/fragments/ker_commander.json5", openarm_sockets, None),
            ("so101/fragments/so101_leader.json5", ["leader_arm", "leader_gripper"], None),
        ]:
            with self.subTest(path=path):
                leader = combinations.load_json5(root / path, path)
                expected = {"target": "backbone_inst", "unset_links": release}
                if sets:
                    expected["set_links"] = sets
                self.assertEqual(leader["adjustments"], [expected])
        shared_document = combinations.load_json5(root / shared[6:], "mcp_commander")
        self.assertEqual([a["target"] for a in shared_document["adjustments"]], [SERVER])

    def test_the_rendered_rig_binds_camera_control_per_simulation(self):
        root = Path(__file__).resolve().parents[2]
        vacancy = {"vacant": "this simulation has no camera response model; every camera control refuses"}
        for path, cameras in [
            ("openarm/fragments/cameras_sim.json5",
             {"wrist_left": "rgb_cameras", "wrist_right": "rgb_cameras", "chest": "rgbd_cameras"}),
            ("so101/fragments/cameras_sim.json5", {"front": "rgb_cameras"}),
        ]:
            with self.subTest(path=path):
                rig = combinations.load_json5(root / path, path)
                relays = [instance for deployment in rig["deployments"] for instance in deployment["instances"]]
                self.assertEqual([relay["instance_id"] for relay in relays], list(cameras))
                # Each relay pairs into the simulation's slot for its kind
                # of camera, which holds every robot's pairs and knows the
                # camera by the relay's id, and writes its control slot
                # vacant; Waldo, the one simulation with a camera response
                # model, binds it instead.
                for relay in relays:
                    self.assertEqual(relay["links"], {
                        "simulation": f"simulation_inst/{cameras[relay['instance_id']]}", "control": vacancy})
                    self.assertEqual(relay["framework"], {"clock": "simulation"})
                waldo = [a for a in rig["adjustments"] if a.get("when") == {"simulation": "waldo"}]
                self.assertEqual(waldo, [
                    {"target": target, "when": {"simulation": "waldo"}, "set_links": {"control": "simulation_inst"}}
                    for target in cameras
                ])
                # The rig is what turns the simulation's rendering on.
                self.assertIn(RENDERING_ON, rig["adjustments"])
                # The browser scene commander's camera panel reads the
                # cameras from the simulation, so the rig links nothing into
                # the stack and a copy with a rig joins and leaves beside a
                # running panel.
                self.assertNotIn("scene_commander_inst", [a["target"] for a in rig["adjustments"]])

    def test_every_robot_runs_the_one_initializer_under_its_model(self):
        root = Path(__file__).resolve().parents[2]
        vacancy = {"vacant": "this robot drives its own limbs"}
        openarm_limbs = ["left_arm_inst", "right_arm_inst", "left_gripper_inst", "right_gripper_inst"]
        # The OpenArm control leaves the model to the robot fragment, one per
        # generation; every SO-101 is the one model, so its control names it.
        for control, arguments in [
            ("openarm/fragments/control_common.json5", None),
            ("so101/fragments/control_common.json5", {"model": "so101"}),
        ]:
            with self.subTest(control=control):
                document = combinations.load_json5(root / control, control)
                initializer = next(deployment for deployment in document["deployments"]
                                   if deployment["source"] == {"name": "robot_initializer", "tag": "v1"})
                instance, = initializer["instances"]
                self.assertEqual(instance["instance_id"], "init_inst")
                self.assertEqual(instance.get("arguments"), arguments)
        # On hardware the simulation slot is vacant and the robot's drivers
        # answer for its limbs; in a simulation the simulation answers for
        # them, and the limbs slot, `zero_or_more`, is left out.
        for path, model, links in [
            ("openarm/fragments/openarm_v1.json5", "openarm_v1", {"simulation": vacancy, "limbs": openarm_limbs}),
            ("openarm/fragments/openarm_v2.json5", "openarm_v2", {"simulation": vacancy, "limbs": openarm_limbs}),
            ("openarm/fragments/openarm_v1_sim.json5", "openarm_v1", {"simulation": "simulation_inst"}),
            ("openarm/fragments/openarm_v2_sim.json5", "openarm_v2", {"simulation": "simulation_inst"}),
            ("so101/fragments/so101.json5", None, {"simulation": vacancy, "limbs": ["follower_inst"]}),
            ("so101/fragments/so101_sim.json5", None, {"simulation": "simulation_inst"}),
        ]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                identity, = [a for a in robot["adjustments"] if a["target"] == "init_inst" and "set_links" in a]
                self.assertEqual(identity["set_links"], links)
                self.assertEqual(identity.get("set_arguments"), {"model": model} if model else None)

    def test_a_backbone_names_its_downstream_links_after_its_limbs(self):
        root = Path(__file__).resolve().parents[2]
        openarm = ["left_arm", "right_arm", "left_gripper", "right_gripper"]
        for path, links in [
            ("openarm/fragments/openarm_v1.json5", {limb: f"{limb}_inst" for limb in openarm}),
            ("openarm/fragments/openarm_v2.json5", {limb: f"{limb}_inst" for limb in openarm}),
            ("so101/fragments/so101.json5", {"arm": "follower_inst", "gripper": "follower_inst"}),
        ]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                backbone, = [a for a in robot["adjustments"] if a["target"] == "backbone_inst" and "set_links" in a]
                self.assertEqual(backbone["set_links"], links)
        # A simulation holds one slot per kind of limb, any number of pairs
        # on each: it tells a pair's robot by its copy and its limb by the
        # backbone link the pair comes from.
        slots = {"left_arm": "arms", "right_arm": "arms", "arm": "arms",
                 "left_gripper": "grippers", "right_gripper": "grippers", "gripper": "grippers"}
        for path, limbs in [
            ("openarm/fragments/openarm_v1_sim.json5", openarm),
            ("openarm/fragments/openarm_v2_sim.json5", openarm),
            ("so101/fragments/so101_sim.json5", ["arm", "gripper"]),
        ]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                backbone, = [a for a in robot["adjustments"]
                             if a["target"] == "backbone_inst" and "set_links" in a and "when" not in a]
                self.assertEqual(
                    backbone["set_links"], {limb: f"simulation_inst/{slots[limb]}" for limb in limbs})
        # What is observed of a robot is named by the backbone's end of each
        # pair, the same on hardware and in a simulation, so the recorder's
        # links are the control fragment's.
        for control, arms, grippers in [
            ("openarm/fragments/control_common.json5",
             ["backbone_inst/left_arm", "backbone_inst/right_arm"],
             ["backbone_inst/left_gripper", "backbone_inst/right_gripper"]),
            ("so101/fragments/control_common.json5", ["backbone_inst/arm"], ["backbone_inst/gripper"]),
        ]:
            with self.subTest(control=control):
                document = combinations.load_json5(root / control, control)
                recorder, = [a for a in document["adjustments"] if a["target"] == "recorder_inst"]
                self.assertEqual(recorder["set_links"], {
                    "observed_joints": arms, "observed_grippers": grippers,
                    "commanded_joints": arms, "commanded_grippers": grippers,
                })
        panel = combinations.load_json5(root / "openarm/fragments/web_commander.json5", "web_commander")
        links = panel["deployments"][0]["instances"][0]["links"]
        self.assertEqual(
            {slot: target for slot, target in links.items() if slot.startswith("observed_")},
            {f"observed_{limb}": f"backbone_inst/{limb}" for limb in openarm})

    def test_the_real_and_the_simulated_so101_share_their_control(self):
        root = Path(__file__).resolve().parents[2]
        real = combinations.load_json5(root / "so101/fragments/so101.json5", "so101")
        simulated = combinations.load_json5(root / "so101/fragments/so101_sim.json5", "so101_sim")
        for robot, name in [(real, "so101"), (simulated, "so101_sim")]:
            control = next(axis for axis in robot["components"] if axis["name"] == "control")
            self.assertEqual(control["options"], {"control_common": "control_common.json5"})
            self.assertEqual(control["provides"], ["init_inst", "backbone_inst"])
            self.assertEqual(combinations.option_entries(robot, name)["control"], "control_common")
        # The follower is the real robot's alone: the engine plays the
        # follower role toward a simulated robot's backbone.
        self.assertEqual([d["source"]["name"] for d in real["deployments"] if "source" in d], ["so101_follower"])
        self.assertEqual([d for d in simulated["deployments"] if "source" in d], [])
        # The leader arm leads the real robot; the simulated one comes up
        # with no commander, so it needs no SO-101 hardware.
        self.assertEqual(combinations.option_entries(real, "so101")["robot_commander"], "so101_leader")
        self.assertNotIn("robot_commander", combinations.option_entries(simulated, "so101_sim"))
        # Per-motor health and alerts come from a real driver: the headset
        # of the real robot binds the follower's, the simulated one's none.
        control = combinations.load_json5(root / "so101/fragments/control_common.json5", "control_common")
        telemetry = {"motor_health": ["follower_inst"], "alerts": ["follower_inst"]}
        self.assertIn(
            {"target": "commander_inst", "when": {"robot_commander": "xr_commander"}, "add_links": telemetry},
            real["adjustments"])
        for document in [simulated, control]:
            for adjustment in document["adjustments"]:
                for verb in ["set_links", "add_links"]:
                    self.assertFalse(set(telemetry) & set(adjustment.get(verb, {})))
        # The dataset is labeled and stored per engine.
        datasets = {a["when"]["simulation"]: a["set_arguments"] for a in simulated["adjustments"]
                    if a["target"] == "recorder_inst" and "when" in a}
        self.assertEqual(datasets, {
            engine: {"robot_type": f"so101_{label}", "storage_root": f"/tmp/lerobot_so101_{label}"}
            for engine, label in [("mujoco", "mujoco"), ("isaac_sim", "isaac"), ("waldo", "waldo")]
        })

    def test_the_launchers_offer_their_robots_and_list_alpha(self):
        root = Path(__file__).resolve().parents[2]
        for path, robots in ROBOT_LAUNCHERS.items():
            with self.subTest(path=path):
                document = combinations.load_json5(root / path, path)
                robot = next(axis for axis in document["components"] if axis["name"] == "robot")
                self.assertEqual(tuple(robot["options"]), robots)
                simulation = [axis for axis in document["components"] if axis["name"] == "simulation"]
                if path == "physical.json5":
                    self.assertEqual(simulation, [])
                else:
                    self.assertEqual(set(simulation[0]["options"]), {"mujoco", "isaac_sim", "waldo"})
                    self.assertEqual(combinations.option_entries(document, path)["simulation"], "waldo")
                # Every simulation launcher lists one copy, alpha, the first
                # robot option's; the MCP launcher's other entries set every
                # copy of their option up without listing one. The physical
                # launcher lists none: every robot is joined by name.
                launcher = combinations.read_launcher(root, path)
                self.assertEqual([copy.name for copy in launcher.copies], [] if path == "physical.json5" else ["alpha"])
                if path == "physical.json5":
                    self.assertEqual(launcher.unlisted, ())
                else:
                    self.assertEqual(
                        launcher.copies[0].option, "so101_sim" if path.startswith("so101/") else "openarm_v2_sim")
                    self.assertEqual(
                        launcher.unlisted, ("openarm_v1_sim", "so101_sim") if path == "mcp/simulation_mcp.json5" else ())
        index = combinations.load_json5(root / "peppy_repository.json5", "index")
        self.assertEqual(index["launchers"]["physical"], {"path": "physical.json5"})
        self.assertEqual(index["launchers"]["so101_simulation"], {"path": "so101/so101_simulation.json5"})
        self.assertEqual(index["launchers"]["simulation_mcp"], {"path": "mcp/simulation_mcp.json5"})

    def test_the_mcp_launcher_deploys_waldo_the_endpoint_and_alpha(self):
        root = Path(__file__).resolve().parents[2]
        path = "mcp/simulation_mcp.json5"
        document = combinations.load_json5(root / path, path)
        axes = {axis["name"]: axis for axis in document["components"]}
        # The robot fragments' guards name MuJoCo and Isaac Sim, and a guard
        # naming an option the axis does not declare is refused when the
        # launcher loads, so the axis carries all three; the file deploys
        # Waldo, and the robots' endpoint runs under the other two as well.
        self.assertEqual(axes["simulation"]["cardinality"], "one")
        self.assertEqual(list(axes["simulation"]["options"]), ["mujoco", "isaac_sim", "waldo"])
        self.assertEqual(list(axes), ["simulation", "robot_control", "world_control", "robot"])
        entries = combinations.option_entries(document, path)
        self.assertEqual(
            {axis: option for axis, option in entries.items() if axis != "robot"},
            {"simulation": "waldo", "robot_control": "robot_control", "world_control": "world_control"})
        # Each robot's entry states, for every copy of the option, the MCP
        # commander with its rendered rig, the v1 having none: alpha, the
        # ones `--join OPTION:NAME` starts with the launch and the ones
        # joined later.
        self.assertEqual([entry for entry in document["deployments"] if "robot" in entry], [
            {"robot": "openarm_v1_sim", "with": {"robot_commander": "mcp_commander"}},
            {"robot": "openarm_v2_sim", "with": {"robot_commander": "mcp_commander", "camera_rig": "cameras_sim"},
             "instances": [{"instance_id": "alpha"}]},
            {"robot": "so101_sim", "with": {"robot_commander": "mcp_commander", "camera_rig": "cameras_sim"}},
        ])
        launcher = combinations.read_launcher(root, path)
        self.assertEqual([copy.name for copy in launcher.copies], ["alpha"])
        self.assertEqual(launcher.unlisted, ("openarm_v1_sim", "so101_sim"))
        # Every launch under Waldo: alpha under every selection of its axes
        # or one unlisted robot named beside it, with and without the scene
        # commander, each endpoint on or off.
        found = combinations.launcher_selections(launcher.axes, launcher.copies, launcher.unlisted)
        waldo = [c for c in found if c.join_option is None and c.words[0] == ("simulation", "waldo")]
        self.assertEqual(len(waldo), 2 * 2 * 2 * (4 * 2 * 2 * 2 + 2))
        started = [(combinations.render_words(c.words), c.launch_joins) for c in waldo if c.launch_joins]
        self.assertIn(("simulation=waldo,robot_control=robot_control,world_control=world_control", ("so101_sim",)), started)
        self.assertIn(("simulation=waldo,robot_control=none,world_control=none", ("openarm_v1_sim",)), started)

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
    def minted(instance):
        if copy is None:
            return {"instance_id": instance["instance_id"]}
        return {"instance_id": f"{copy}_{instance['instance_id']}", "core_node": copy}

    return [
        {"source": deployment["source"],
         "instances": [minted(instance) for instance in deployment["instances"]]}
        for deployment in combinations.load_json5(ROOT / path, path).get("deployments", [])
        if "source" in deployment
    ]


def so101_copy(name, **selected):
    """One so101_sim copy flattened: the options its fragment deploys on its
    own axes, `selected` over them, each option's parts read from the
    fragment."""
    robot = combinations.load_json5(ROOT / SO101_SIM, SO101_SIM)
    selection = {**combinations.option_entries(robot, SO101_SIM), **selected}
    return [
        deployment
        for axis in robot["components"] if axis["name"] in selection
        for part in combinations.fragment_parts(axis["options"][selection[axis["name"]]])
        for deployment in fragment_deployments(
            posixpath.normpath(posixpath.join(posixpath.dirname(SO101_SIM), part)), name)
    ]


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
            ROOT, "so101_simulation", "so101/so101_simulation.json5")
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
        # simulation_mcp lists the SO-101 with no copy, as an MCP
        # robot with its rendered rig, so a launch names the one it starts.
        inventory = combinations.read_launcher_inventory(
            ROOT, "simulation_mcp", "mcp/simulation_mcp.json5")
        self.assertEqual(inventory.candidates[0].file_copies, ("alpha",))
        stack = "simulation=waldo,robot_control=robot_control,world_control=world_control"
        candidate, = [c for c in inventory.candidates
                      if (c.words, c.launch_joins, c.join_option) == (stack, ("so101_sim",), "")]
        robot_control = so101_copy("bravo", robot_commander="mcp_commander", camera_rig="cameras_sim")
        with patch.object(combinations.subprocess, "run", return_value=so101_stack("waldo", robot_control)):
            resolution = combinations.resolve_candidate(ROOT, candidate, self.skips)
        self.assertIs(resolution.verdict, combinations.Verdict.LAUNCH)
        launch = combinations.Launch.of(candidate, resolution)
        self.assertEqual(launch.launch_joins, ["so101_sim"])
        self.assertEqual(launch.join_instances, ["bravo_backbone_inst", "bravo_front", "bravo_init_inst"])
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
             ({"name": "sim_rgb_camera", "tag": "v1"}, "bravo_front")])
        _, resolution = self.resolved(
            so101_stack("waldo", robot_control), WALDO_MCP, "so101_sim", "robot_commander=mcp_commander,camera_rig=cameras_sim")
        self.assertIs(resolution.verdict, combinations.Verdict.LAUNCH)
        self.assertEqual(
            combinations.copy_instances(resolution.plan, "bravo"),
            ["bravo_backbone_inst", "bravo_front", "bravo_init_inst"])
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
