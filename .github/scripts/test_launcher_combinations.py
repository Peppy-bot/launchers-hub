import contextlib
import io
import json
from pathlib import Path
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


def fleet_launcher(file_copy=None):
    """A MuJoCo simulation a robot joins as a copy, with or without a
    recorder, the file deploying the copy `file_copy` where one is named."""
    robot = {"components": [
        {"name": "recorder", "cardinality": "zero_or_one", "options": {"lerobot_recorder": {}}},
    ]}
    deployments = [{"simulation": "mujoco"}]
    if file_copy:
        deployments.append({"robot": "openarm_v2", "instances": [{"instance_id": file_copy}]})
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
    from `(launcher path, launch words, joined option, join words)`, an empty
    plan where a tree names none, and keeps the calls it received."""

    def __init__(self, **answers_by_tree):
        self.answers_by_tree = answers_by_tree
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        options = dict(zip(argv[4::2], argv[5::2]))
        key = (argv[3], options.get("--with", ""), options.get("--join", ""), options.get("--join-with", ""))
        answers = self.answers_by_tree.get(Path(kwargs["cwd"]).name, {})
        return answers.get(key, resolved_plan())


EVERYTHING = combinations.Scope(combinations.ScopeKind.EVERYTHING, "a test of every combination")
CHANGED = combinations.Scope(combinations.ScopeKind.CHANGED, "a test of a launcher change")


@contextlib.contextmanager
def planned(launchers, answers=None, scope=EVERYTHING, base_launchers=None, base_answers=None):
    """The launchers planned in a repository of their own, `stack resolve`
    answering `answers`, and `base_answers` for the base tree a changed scope
    compares against: the fake resolve, the labels of the planned launches,
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
                patch.dict(combinations.os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            combinations.command_plan(head, scope_path, base, skips, plan_path)
        plan = combinations.read_plan(plan_path)
        yield resolve, [launch.label for launch in plan], plan, summary.read_text()


class CombinationsTests(unittest.TestCase):
    def test_every_state_of_every_axis_in_reach_is_enumerated(self):
        combinations_found = combinations.launcher_selections(fleet_axes())
        # Stack: simulation (2) times scene commander (1 + unfilled) = 4, each
        # bare and with every copy: robot commander (2) times recorder (1 +
        # unfilled) = 4.
        self.assertEqual(len(combinations_found), 4 * (1 + 4))
        bare = [c for c in combinations_found if c.join_option is None]
        self.assertIn([("simulation", "mujoco"), ("scene_commander", None)], [c.words for c in bare])
        joined = [c for c in combinations_found if c.join_option == "openarm_v2"]
        self.assertIn(
            [("robot_commander", "xr_commander"), ("recorder", None)], [c.join_words for c in joined]
        )
        self.assertEqual(
            combinations.render_words([("simulation", "mujoco"), ("scene_commander", None)]), "simulation=mujoco"
        )

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

        # The SO-101 joins the fleet as a copy, under every selection of its
        # commander (3), recorder (1 + unfilled) and camera_rig (1 + unfilled).
        self.assertIn(
            ["combo", "fleet", "", "so101", "robot_commander=xr_commander,recorder=lerobot_recorder,camera_rig=cameras", "-"],
            combos("fleet"),
        )
        # The fleet: the simulation off or any of the five stack selections
        # (each simulation, Isaac Sim and Waldo also with their scene
        # commander), each bare and joined by any robot under every
        # selection of its own axes: the v1 has no brain (3 times 2 times 2),
        # the v2 has one (3 times 2 times 2 times 2), the simulated v1 has
        # neither camera rig nor brain, and without a rig no MCP commander
        # (2 times 2), the simulated v2 has both and all three commanders (3
        # times 2 times 2 times 2), and the SO-101 has three commanders, a
        # recorder and a camera rig. It deploys nothing, so it has no copy of
        # its own to select for.
        fleet = combos("fleet")
        self.assertEqual(
            len(fleet), 6 * (1 + 3 * 2 * 2 + 3 * 2 * 2 * 2 + 2 * 2 + 3 * 2 * 2 * 2 + 3 * 2 * 2)
        )
        self.assertIn(["combo", "fleet", "", "", "", "-"], fleet)
        self.assertIn(
            ["combo", "fleet", "", "openarm_v1", "robot_commander=xr_commander,recorder=lerobot_recorder,camera_rig=cameras", "-"],
            fleet,
        )
        self.assertIn(["combo", "fleet", "simulation=mujoco", "openarm_v2_sim", "robot_commander=web_commander", "-"], fleet)
        # The openarm_simulation launcher: one stack selection per simulation, Isaac Sim
        # and Waldo each with and without their scene commander (5), each
        # bare, with every other selection of its deployed v2 copy's axes,
        # and joined by either simulated robot: the v1 has no camera rig, no
        # brain and two commanders (2 times 2), the v2 has both and three
        # commanders (3 times 2 times 2 times 2).
        sim = combos("openarm_simulation")
        self.assertEqual(len(sim), 5 * (1 + (3 * 2 * 2 * 2 - 1) + 2 * 2 + 3 * 2 * 2 * 2))
        self.assertIn(["combo", "openarm_simulation", "simulation=waldo,scene_commander=web_scene_commander", "", "", "-"], sim)
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=mujoco,alpha.robot_commander=xr_commander,alpha.recorder=lerobot_recorder,alpha.camera_rig=cameras_sim", "", "", "-"],
            sim,
        )
        self.assertIn(
            ["combo", "openarm_simulation", "simulation=mujoco", "openarm_v2_sim", "robot_commander=xr_commander,recorder=lerobot_recorder,camera_rig=cameras_sim", "-"],
            sim,
        )
        references = next(line for line in lines if line.startswith("launcher\topenarm_simulation\t"))
        for reference in [
            "openarm/fragments/openarm_v1_sim.json5",
            "openarm/fragments/openarm_v2_sim.json5",
            "openarm/fragments/cameras_sim.json5",
            "openarm/fragments/mcp_commander.json5",
            "robot_commanders/fragments/xr_commander.json5",
            "recording/fragments/lerobot_recorder.json5",
            "simulation/fragments/waldo.json5",
            "simulation/fragments/web_scene_commander.json5",
        ]:
            self.assertIn(reference, references)
        # The openarm_simulation_mcp launcher: the same five simulation
        # selections (its simulation axis carries the three simulations the
        # robot fragment's guards name, Waldo deployed), each with the
        # simulated world's MCP endpoint and with `none` in its place (a
        # `one` axis of two options is a choice every launch writes out),
        # each bare, with every other selection of its deployed copy's axes
        # (the copy runs the MCP commander and the rendered rig, so that one
        # selection is the bare launch), and joined by the simulated v2. The
        # planner picks the launcher and the option up from the index and
        # the fragments alone.
        mcp = combos("openarm_simulation_mcp")
        self.assertEqual(len(mcp), 5 * 2 * (1 + (3 * 2 * 2 * 2 - 1) + 3 * 2 * 2 * 2))
        self.assertIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,simulation_mcp=mcp_scene_commander", "", "", "-"], mcp
        )
        self.assertIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,scene_commander=web_scene_commander,simulation_mcp=mcp_scene_commander", "", "", "-"],
            mcp,
        )
        self.assertIn(["combo", "openarm_simulation_mcp", "simulation=mujoco,simulation_mcp=none", "", "", "-"], mcp)
        self.assertIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,simulation_mcp=mcp_scene_commander,alpha.robot_commander=web_commander,alpha.recorder=lerobot_recorder,alpha.camera_rig=cameras_sim", "", "", "-"],
            mcp,
        )
        self.assertIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,simulation_mcp=mcp_scene_commander,alpha.robot_commander=mcp_commander,alpha.camera_rig=cameras_sim,alpha.brain=ai_brain", "", "", "-"],
            mcp,
        )
        self.assertNotIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,simulation_mcp=mcp_scene_commander,alpha.robot_commander=mcp_commander,alpha.camera_rig=cameras_sim", "", "", "-"],
            mcp,
        )
        self.assertIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,simulation_mcp=none", "openarm_v2_sim", "robot_commander=mcp_commander,camera_rig=cameras_sim", "-"],
            mcp,
        )
        references = next(line for line in lines if line.startswith("launcher\topenarm_simulation_mcp\t"))
        for reference in [
            "openarm/fragments/openarm_v2_sim.json5",
            "openarm/fragments/mcp_commander.json5",
            "openarm/fragments/cameras_sim.json5",
            "simulation/fragments/waldo.json5",
            "simulation/fragments/web_scene_commander.json5",
            "simulation/fragments/mcp_scene_commander.json5",
            "simulation/fragments/none.json5",
        ]:
            self.assertIn(reference, references)

    def test_fragment_files_are_named_for_their_option(self):
        root = Path(__file__).resolve().parents[2]
        for document in sorted(root.rglob("*.json5")):
            if document.name == "peppy_repository.json5" or "examples" in document.parts or ".github" in document.parts:
                continue
            for axis in combinations.load_json5(document, str(document)).get("components", []):
                for option, path in axis["options"].items():
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
            ("so101", "fleet.json5", "so101/fragments/so101.json5", "so101/fragments/so101.json5", 60, 30),
        ]:
            with self.subTest(robot=robot):
                references = combinations.read_launcher(root, launcher).references
                self.assertIn("robot_commanders/fragments/xr_commander.json5", references)
                self.assertIn("recording/fragments/lerobot_recorder.json5", references)
                fragment = combinations.load_json5(root / robot_path, robot)
                axes = {axis["name"] for axis in fragment["components"]}
                self.assertEqual({"robot_commander", "recorder"} - axes, set())
                self.assertIn("robot_commander", combinations.option_entries(fragment, robot))
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

    def test_a_copy_of_a_repeatable_axis_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                {"name": "robot", "cardinality": "zero_or_more", "options": {"openarm_v2": {}}},
            ], "deployments": [{"robot": "openarm_v2"}]}))
            with self.assertRaises(combinations.Json5Error):
                combinations.read_launcher(directory, "fleet.json5")

    def test_waldo_runs_the_inspector_whatever_the_launch_selects(self):
        # The 3D viewer and the scene services are the inspector's, so the
        # fragment runs it from the first launch of every launcher deploying
        # Waldo rather than behind the scene commander selection; no
        # adjustment of the fragment or of a launcher touches `plugins`.
        # Every robot joins Waldo bringing its own model, so the world it
        # opens, the stage, is the fragment's too, and no launcher sets one.
        root = Path(__file__).resolve().parents[2]
        for launcher in ["openarm/openarm_simulation.json5", "mcp/openarm_simulation_mcp.json5", "fleet.json5"]:
            document = combinations.load_json5(root / launcher, launcher)
            for adjustment in document.get("adjustments", []):
                self.assertFalse({"world", "plugins"} & set(adjustment.get("set_arguments", {})), launcher)
        waldo = combinations.load_json5(root / "simulation/fragments/waldo.json5", "waldo")
        arguments = waldo["deployments"][0]["instances"][0]["arguments"]
        self.assertEqual((arguments["world"], arguments["plugins"]), ("stage", "hand_teleop,sim_inspector"))
        for adjustment in waldo.get("adjustments", []):
            self.assertFalse({"world", "plugins"} & set(adjustment.get("set_arguments", {})))

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

    def test_the_mcp_commander_serves_the_robot_document_and_binds_every_target(self):
        root = Path(__file__).resolve().parents[2]
        fragment = combinations.load_json5(root / "openarm/fragments/mcp_commander.json5", "mcp_commander")
        deployment, = fragment["deployments"]
        self.assertEqual(deployment["source"], {"exposures": ["openarm_v2:v1"]})
        instance, = deployment["instances"]
        self.assertEqual(instance["instance_id"], "commander_inst")
        self.assertEqual(instance["arguments"], {"port": 8900})
        # One link per target of the robot document: the two move roles on
        # the backbone, the three cameras on the copy's rig, physical or
        # rendered, under the same ids.
        self.assertEqual(instance["links"], {
            "postures": "backbone_inst", "limb_motion": "backbone_inst",
            "wrist_left": "wrist_left", "wrist_right": "wrist_right", "chest": "chest",
        })
        # The robot's family has one commander: the option that bound the
        # simulation from inside the robot copy is gone.
        self.assertFalse((root / "openarm/fragments/mcp_sim_commander.json5").exists())

    def test_the_mcp_scene_commander_serves_the_world_document_from_the_simulation_alone(self):
        root = Path(__file__).resolve().parents[2]
        fragment = combinations.load_json5(
            root / "simulation/fragments/mcp_scene_commander.json5", "mcp_scene_commander")
        deployment, = fragment["deployments"]
        self.assertEqual(deployment["source"], {"exposures": ["simulation:v1"]})
        instance, = deployment["instances"]
        # An id and a port of its own: it runs beside a copy's MCP commander
        # (commander_inst, 8900), the brain's server (8901) and the browser
        # scene panel (scene_commander_inst).
        self.assertEqual(instance["instance_id"], "scene_mcp_inst")
        self.assertEqual(instance["arguments"], {"port": 8902})
        self.assertEqual(instance["links"], {
            "scene": "simulation_inst", "lighting": "simulation_inst", "materials": "simulation_inst"})
        self.assertEqual(instance["framework"], {"clock": "simulation"})
        # It binds nothing of a robot copy and adjusts nothing.
        self.assertNotIn("adjustments", fragment)
        self.assertNotIn("components", fragment)
        # `none` is the empty option that switches the axis off.
        self.assertEqual(
            combinations.load_json5(root / "simulation/fragments/none.json5", "none"),
            {"peppy_schema": "launcher_fragment/v1"})

    def test_every_robot_offering_the_mcp_commander_requires_its_rig(self):
        root = Path(__file__).resolve().parents[2]
        for path, rig in [
            ("openarm/fragments/openarm_v2_sim.json5", "cameras_sim"),
            ("openarm/fragments/openarm_v2.json5", "cameras"),
            ("openarm/fragments/openarm_v1.json5", "cameras"),
        ]:
            with self.subTest(path=path):
                robot = combinations.load_json5(root / path, path)
                axes = {axis["name"]: axis for axis in robot["components"]}
                self.assertEqual(axes["robot_commander"]["options"]["mcp_commander"], "mcp_commander.json5")
                self.assertNotIn("mcp_sim_commander", axes["robot_commander"]["options"])
                # The exposure list is fixed and every target takes a link,
                # so the commander requires the rig, and nothing else: the
                # Waldo requirement is the world document's, on the launcher.
                mcp = [c for c in robot["constraints"] if c.get("when") == {"robot_commander": "mcp_commander"}]
                self.assertEqual([c["requires"] for c in mcp], [[{"camera_rig": rig}]])
                self.assertEqual([c["reason"] for c in mcp], [
                    "mcp_commander serves the robot's cameras and its exposure list is fixed, "
                    f"so the camera targets cannot be optional; select {rig}"])
                # The rig's consumer rule admits the MCP commander: the model
                # is the consumer. The scene commander's axis is out of a
                # robot fragment's reach, so the rule cannot name it.
                rule, = [c for c in robot["constraints"] if c.get("when") == {"camera_rig": rig}]
                self.assertEqual(rule["requires"], [
                    {"recorder": "lerobot_recorder"},
                    {"robot_commander": ["xr_commander", "mcp_commander"]},
                ])
                self.assertEqual(
                    rule["reason"],
                    "camera streams need a consumer; select lerobot_recorder, xr_commander or mcp_commander")
        # No simulation renders a rig on v1 links, so the simulated v1 has no
        # camera_rig axis and cannot offer the commander at all.
        v1_sim = combinations.load_json5(root / "openarm/fragments/openarm_v1_sim.json5", "openarm_v1_sim")
        axes = {axis["name"]: axis for axis in v1_sim["components"]}
        self.assertNotIn("camera_rig", axes)
        self.assertEqual(list(axes["robot_commander"]["options"]), ["web_commander", "xr_commander"])

    def test_the_rendered_rig_binds_camera_control_per_simulation(self):
        root = Path(__file__).resolve().parents[2]
        rig = combinations.load_json5(root / "openarm/fragments/cameras_sim.json5", "cameras_sim")
        vacancy = {"vacant": "this simulation has no camera response model; every camera control refuses"}
        relays = [instance for deployment in rig["deployments"] for instance in deployment["instances"]]
        self.assertEqual([relay["instance_id"] for relay in relays], ["wrist_left", "wrist_right", "chest"])
        # Each relay views its slot on the simulation and writes its control
        # slot vacant; Waldo, the one simulation with a camera response
        # model, binds it instead.
        for relay in relays:
            with self.subTest(relay=relay["instance_id"]):
                self.assertEqual(relay["links"], {
                    "simulation": f"simulation_inst/{relay['instance_id']}", "control": vacancy})
        waldo = [a for a in rig["adjustments"] if a.get("when") == {"simulation": "waldo"}]
        self.assertEqual(waldo, [
            {"target": target, "when": {"simulation": "waldo"}, "set_links": {"control": "simulation_inst"}}
            for target in ["wrist_left", "wrist_right", "chest"]
        ])
        # The browser scene commander's camera panel reads the cameras from
        # the simulation, so the rig links nothing into the stack and a copy
        # with a rig joins and leaves beside a running panel.
        self.assertNotIn("scene_commander_inst", [a["target"] for a in rig["adjustments"]])

    def test_the_mcp_launcher_deploys_waldo_the_world_endpoint_and_the_mcp_copy(self):
        root = Path(__file__).resolve().parents[2]
        path = "mcp/openarm_simulation_mcp.json5"
        index = combinations.load_json5(root / "peppy_repository.json5", "index")
        self.assertEqual(index["launchers"]["openarm_simulation_mcp"], {"path": path})
        document = combinations.load_json5(root / path, path)
        simulation = next(axis for axis in document["components"] if axis["name"] == "simulation")
        # The robot fragment's guards name MuJoCo and Isaac Sim, and a guard
        # naming an option the axis does not declare is refused when the
        # launcher loads, so the axis carries all three; the file deploys
        # Waldo, and the robot's endpoint runs under the other two as well.
        self.assertEqual(simulation["cardinality"], "one")
        self.assertEqual(list(simulation["options"]), ["mujoco", "isaac_sim", "waldo"])
        self.assertEqual(simulation["options"]["waldo"], "../simulation/fragments/waldo.json5")
        robot = next(axis for axis in document["components"] if axis["name"] == "robot")
        self.assertEqual(robot["cardinality"], "zero_or_more")
        self.assertEqual(robot["options"], {"openarm_v2_sim": "../openarm/fragments/openarm_v2_sim.json5"})
        # The simulated world's endpoint is an axis of the launcher, not an
        # option of Waldo's scene_commander axis: a launcher cannot
        # pre-select an axis a fragment declares, and an axis takes one
        # option, so the browser scene panel keeps its own. A `one` axis
        # with a `none` option is on by default and switchable.
        world = next(axis for axis in document["components"] if axis["name"] == "simulation_mcp")
        self.assertEqual(world["cardinality"], "one")
        self.assertEqual(world["options"], {
            "mcp_scene_commander": "../simulation/fragments/mcp_scene_commander.json5",
            "none": "../simulation/fragments/none.json5",
        })
        self.assertEqual(combinations.option_entries(document, path), {
            "simulation": "waldo", "simulation_mcp": "mcp_scene_commander", "robot": "openarm_v2_sim"})
        # The Waldo requirement is the world document's, so it sits here and
        # not on the robot.
        self.assertEqual(document["constraints"], [{
            "when": {"simulation_mcp": "mcp_scene_commander"},
            "requires": [{"simulation": "waldo"}],
            "reason": "the simulated world's endpoint binds scene_lighting and scene_materials, which "
                      "only waldo implements; launch with waldo or with simulation_mcp=none",
        }])
        launcher = combinations.read_launcher(root, path)
        self.assertEqual(launcher.copies, [combinations.Copy(
            "alpha", "robot", "openarm_v2_sim",
            {"robot_commander": "mcp_commander", "camera_rig": "cameras_sim"})])
        # Alpha joins the stage Waldo opens, so the file sets no world.
        self.assertNotIn("adjustments", document)
        # The copy's axes are in the planner's reach: every state of the
        # commander, recorder, rig and brain, minus the one the file runs.
        found = combinations.launcher_selections(launcher.axes, launcher.copies)
        deployed = [combinations.render_words(c.words) for c in found
                    if c.join_option is None and c.words[0] == ("simulation", "waldo")]
        # Waldo with and without its scene commander, the world's endpoint
        # on or off.
        self.assertEqual(len(deployed), 2 * 2 * (1 + 3 * 2 * 2 * 2 - 1))
        self.assertIn(
            "simulation=waldo,simulation_mcp=mcp_scene_commander,alpha.robot_commander=xr_commander,alpha.camera_rig=cameras_sim",
            deployed)
        self.assertNotIn(
            "simulation=waldo,simulation_mcp=mcp_scene_commander,alpha.robot_commander=mcp_commander,alpha.camera_rig=cameras_sim",
            deployed)

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
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fleet.json5").write_text(json.dumps({"components": [
                {"name": "robot", "options": {"openarm_v2": {
                    "components": [{"name": "cameras", "cardinality": "zero_or_more", "options": {"a": {}}}],
                }}},
            ]}))
            with self.assertRaises(combinations.Json5Error):
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


class ResolveTests(unittest.TestCase):
    def test_plan_previews_the_launch_and_its_join_and_launches_the_same(self):
        answers = {
            ("fleet.json5", "", "openarm_v2", "recorder=lerobot_recorder"):
                resolved_plan("openarm_sim_mujoco:v1", "lerobot_recorder:v1"),
        }
        with planned({"fleet": fleet_launcher()}, answers) as (resolve, labels, plan, _):
            self.assertIn([
                "peppy", "stack", "resolve", "fleet.json5",
                "--join", "openarm_v2", "--join-name", combinations.COPY_NAME,
                "--join-with", "recorder=lerobot_recorder",
            ], [argv for argv, _ in resolve.calls])
            # The preview reads the plan as JSON5, so peppy logs errors only.
            self.assertTrue(all(kwargs["env"]["RUST_LOG"] == "error" for _, kwargs in resolve.calls))
            self.assertEqual(labels, ["fleet + join openarm_v2 (recorder=lerobot_recorder)"])
            launch, = plan
            self.assertEqual(launch.words, "")
            self.assertEqual(launch.join_option, "openarm_v2")
            self.assertEqual(launch.join_name, combinations.COPY_NAME)
            self.assertEqual(launch.join_words, "recorder=lerobot_recorder")
            self.assertFalse(launch.local)

    def test_plan_previews_a_deployed_copys_launch_words_without_a_join(self):
        words = "alpha.recorder=lerobot_recorder"
        answers = {
            ("fleet.json5", words, "", ""): resolved_plan("openarm_sim_mujoco:v1", "lerobot_recorder:v1"),
        }
        with planned({"fleet": fleet_launcher(file_copy="alpha")}, answers) as (resolve, labels, plan, _):
            self.assertIn(
                ["peppy", "stack", "resolve", "fleet.json5", "--with", words],
                [argv for argv, _ in resolve.calls],
            )
            self.assertEqual(labels, [f"fleet ({words})"])
            launch, = plan
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

    def test_a_join_refused_for_changing_what_runs_is_launch_only(self):
        answers = {("fleet.json5", "", "openarm_v2", ""): JOIN_REFUSAL}
        with planned({"fleet": fleet_launcher()}, answers) as (_, labels, _plan, summary):
            self.assertNotIn("fleet + join openarm_v2", labels)
            self.assertIn("refused as a join (1)", summary)
            self.assertIn("| fleet + join openarm_v2 | joining bravo would change simulation_inst", summary)

    def test_a_join_the_files_copy_cannot_make_way_for_is_reported_not_launched(self):
        def plan_with(scene_commander_links):
            return completed(json.dumps({"deployments": [
                {"source": {"name": "scene_commander", "tag": "v1"},
                 "instances": [{"instance_id": "scene_commander_inst", "links": scene_commander_links}]},
                {"source": {"name": "sim_rgb_camera", "tag": "v1"},
                 "instances": [{"instance_id": "alpha_chest", "links": {"simulation": "simulation_inst/chest"}},
                               {"instance_id": "alpha_wrist", "links": {}}]},
            ]}))

        held = plan_with({"rgbd_cameras": "alpha_chest/frames", "color_cameras": ["alpha_wrist", "studio_camera"],
                          "lighting": {"vacant": "no light rig"}})
        answers = {
            ("fleet.json5", "", "", ""): held,
            ("fleet.json5", "", "openarm_v2", ""): held,
            ("fleet.json5", "", "openarm_v2", "recorder=lerobot_recorder"): held,
        }
        with planned({"fleet": fleet_launcher(file_copy="alpha")}, answers) as (_, labels, _plan, summary):
            # The launch itself keeps running; only its joins are held back.
            self.assertEqual(labels, ["fleet"])
            self.assertIn("Joins the file's copy cannot make way for (2)", summary)
            self.assertIn(
                "| fleet + join openarm_v2 | scene_commander_inst.rgbd_cameras -> alpha_chest/frames, "
                "scene_commander_inst.color_cameras -> alpha_wrist |", summary)

    def test_a_stack_linking_to_no_copy_lets_the_copy_make_way(self):
        plan = {"deployments": [{"source": {"name": "node", "tag": "v1"}, "instances": [
            {"instance_id": "scene_commander_inst", "links": {"simulation": "simulation_inst", "alphabet": "alphabet_inst"}},
            {"instance_id": "alpha_recorder_inst", "links": {"cameras": ["alpha_chest"]}},
        ]}]}
        self.assertEqual(combinations.links_holding_copies(plan, ("alpha",)), [])
        self.assertEqual(combinations.links_holding_copies(plan, ()), [])

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

    def test_a_refusal_that_is_no_join_is_not_mistaken_for_a_launch_only_copy(self):
        answers = {("sim.json5", "simulation=waldo", "", ""): JOIN_REFUSAL}
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            with planned({"sim": simulation_launcher("mujoco", "waldo")}, answers):
                pass


class CoverageTests(unittest.TestCase):
    def test_a_combination_whose_instances_all_run_in_another_launch_is_not_launched(self):
        answers = {
            ("fleet.json5", "", "", ""): resolved_plan("openarm_sim_mujoco:v1"),
            ("fleet.json5", "", "openarm_v2", ""): resolved_plan("openarm_sim_mujoco:v1", "openarm_backbone:v1"),
            ("fleet.json5", "", "openarm_v2", "recorder=lerobot_recorder"):
                resolved_plan("openarm_sim_mujoco:v1", "openarm_backbone:v1", "lerobot_recorder:v1"),
        }
        with planned({"fleet": fleet_launcher()}, answers) as (_, labels, _plan, summary):
            self.assertEqual(labels, ["fleet + join openarm_v2 (recorder=lerobot_recorder)"])
            self.assertIn("Launching 1 of the 3 launchable combinations in scope", summary)
            self.assertIn("| fleet + join openarm_v2 | fleet + join openarm_v2 (recorder=lerobot_recorder) |", summary)
            self.assertIn("| fleet | fleet + join openarm_v2 (recorder=lerobot_recorder) |", summary)

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

    def test_a_join_runs_beside_the_stack_once_the_files_copy_has_made_way(self):
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

        launch_state, join_state = combinations.running_states(joined, resolutions)
        self.assertEqual(ids(launch_state), ["alpha_backbone_inst", "simulation_inst"])
        self.assertEqual(ids(join_state), ["bravo_backbone_inst", "simulation_inst"])
        # The copies never run together, so no launch is asked to prove the pair.
        pairs = [unit for unit in combinations.coverage_units(joined, [launch_state, join_state]) if unit[0] == "pair"]
        self.assertEqual(len(pairs), 2)
        self.assertEqual(combinations.running_states(bare, resolutions), [launch_state])

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


class FakePeppy:
    """Stands in for the `peppy` commands of a launch: every command exits 0
    unless `failing` holds its first words, `stack list --json` answers
    `copies`, and the commands are kept in order."""

    def __init__(self, failing=(), copies=()):
        self.failing = [list(words) for words in failing]
        self.copies = list(copies)
        self.commands = []

    def __call__(self, argv, capture=False):
        self.commands.append(argv)
        failed = any(argv[: len(words)] == words for words in self.failing)
        listing = json.dumps({"core_nodes": [{"copies": [{"name": name} for name in self.copies]}]})
        return completed(listing if capture else "", returncode=1 if failed else 0)


def a_launch(launcher="fleet", words="", join_option="", join_words="", local=False):
    label = combinations.combination_label(launcher, words, join_option, join_words, local)
    join_name = combinations.COPY_NAME if join_option else ""
    return combinations.Launch(label, launcher, words, join_option, join_name, join_words, local)


def launched(launches, peppy, rebuild=False):
    with contextlib.redirect_stdout(io.StringIO()) as log:
        outcomes = combinations.launch_all(launches, rebuild, peppy)
    return outcomes, log.getvalue()


IDLE = ["--node-build-idle-timeout-secs", "900"]


class LaunchTests(unittest.TestCase):
    def test_a_launch_comes_up_is_listed_and_is_reset(self):
        peppy = FakePeppy()
        outcomes, log = launched([a_launch(words="simulation=mujoco", local=True)], peppy, rebuild=True)
        self.assertEqual(peppy.commands, [
            ["peppy", "stack", "launch", "fleet", "--with", "simulation=mujoco", "--local", "--rebuild", *IDLE],
            ["peppy", "stack", "list"],
            ["peppy", "stack", "reset"],
        ])
        self.assertEqual([outcome.status for outcome in outcomes], [combinations.Status.PASSED])
        self.assertIn("::group::fleet (simulation=mujoco) [--local]\n", log)
        self.assertIn("::endgroup::\n", log)

    def test_a_flat_launcher_launches_by_name_alone(self):
        peppy = FakePeppy()
        launched([a_launch("rust_robot")], peppy)
        self.assertEqual(peppy.commands[0], ["peppy", "stack", "launch", "rust_robot", *IDLE])

    def test_a_join_follows_the_launch_once_the_files_copies_have_made_way(self):
        peppy = FakePeppy(copies=["alpha", "charlie"])
        launched([a_launch(join_option="openarm_v2", join_words="recorder=lerobot_recorder")], peppy)
        self.assertEqual(peppy.commands, [
            ["peppy", "stack", "launch", "fleet", *IDLE],
            ["peppy", "stack", "list", "--json"],
            ["peppy", "stack", "remove", "alpha"],
            ["peppy", "stack", "remove", "charlie"],
            ["peppy", "stack", "join", "openarm_v2", "-i", "bravo", "--with", "recorder=lerobot_recorder", *IDLE],
            ["peppy", "stack", "list"],
            ["peppy", "stack", "remove", "bravo"],
            ["peppy", "stack", "list"],
            ["peppy", "stack", "reset"],
        ])

    def test_a_join_without_words_passes_none(self):
        peppy = FakePeppy()
        launched([a_launch(join_option="openarm_v2")], peppy)
        self.assertIn(["peppy", "stack", "join", "openarm_v2", "-i", "bravo", *IDLE], peppy.commands)

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
            ["peppy", "stack", "join", "openarm_v2", "-i", "bravo", *IDLE],
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
                    combinations.command_launch(plan, rebuild=False)
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
