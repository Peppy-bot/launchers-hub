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
    `name:tag`."""
    deployments = [
        {"source": {"name": name, "tag": tag}} for name, tag in (node.split(":") for node in nodes)
    ]
    return combinations.subprocess.CompletedProcess([], 0, json.dumps({"deployments": deployments}), "")


@contextlib.contextmanager
def planned(combo_lines, plans=None, launchers=("fleet",)):
    """Combo lines planned against a repository of the named launchers, with
    `stack resolve` answering `plans` in order (an empty plan for every
    combination when none are given): the recorded subprocess call and the
    matrix the plan wrote."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "peppy_repository.json5").write_text(json.dumps({
            "launchers": {name: {"path": f"{name}.json5"} for name in launchers},
        }))
        combos = root / "combos.tsv"
        combos.write_text(combo_lines)
        skips = root / "skips.json5"
        skips.write_text("[]")
        matrix = root / "matrix.json"
        if plans is None:
            plans = [resolved_plan()] * combo_lines.count("\n")
        with patch.object(combinations.subprocess, "run", side_effect=plans) as run:
            combinations.command_plan(root, combos, skips, matrix)
        yield run, json.loads(matrix.read_text())


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
        # neither camera rig nor brain (3 times 2), the simulated v2 has both
        # and a fourth commander, the MCP simulation commander (4 times 2
        # times 2 times 2), and the SO-101 has three commanders, a recorder
        # and a camera rig. It deploys nothing, so it has no copy of its own
        # to select for.
        fleet = combos("fleet")
        self.assertEqual(
            len(fleet), 6 * (1 + 3 * 2 * 2 + 3 * 2 * 2 * 2 + 3 * 2 + 4 * 2 * 2 * 2 + 3 * 2 * 2)
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
        # and joined by either simulated robot: the v1 has no camera rig and
        # no brain (3 times 2), the v2 has both and four commanders (4 times
        # 2 times 2 times 2).
        sim = combos("openarm_simulation")
        self.assertEqual(len(sim), 5 * (1 + (4 * 2 * 2 * 2 - 1) + 3 * 2 + 4 * 2 * 2 * 2))
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
            "openarm/fragments/mcp_sim_commander.json5",
            "robot_commanders/fragments/xr_commander.json5",
            "recording/fragments/lerobot_recorder.json5",
            "simulation/fragments/waldo.json5",
            "simulation/fragments/web_scene_commander.json5",
        ]:
            self.assertIn(reference, references)
        # The openarm_simulation_mcp launcher: the same five stack selections
        # (its simulation axis carries the three simulations the robot
        # fragment's guards name, Waldo deployed), each bare, with every
        # other selection of its deployed copy's axes (the copy runs the MCP
        # simulation commander and the rendered rig, so that one selection
        # is the bare launch), and joined by the simulated v2. The planner
        # picks the launcher and the option up from the index and the
        # fragments alone.
        mcp = combos("openarm_simulation_mcp")
        self.assertEqual(len(mcp), 5 * (1 + (4 * 2 * 2 * 2 - 1) + 4 * 2 * 2 * 2))
        self.assertIn(["combo", "openarm_simulation_mcp", "simulation=waldo", "", "", "-"], mcp)
        self.assertIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,scene_commander=web_scene_commander", "", "", "-"], mcp
        )
        self.assertIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,alpha.robot_commander=web_commander,alpha.recorder=lerobot_recorder,alpha.camera_rig=cameras_sim", "", "", "-"],
            mcp,
        )
        self.assertIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,alpha.robot_commander=mcp_sim_commander,alpha.camera_rig=cameras_sim,alpha.brain=ai_brain", "", "", "-"],
            mcp,
        )
        self.assertNotIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo,alpha.robot_commander=mcp_sim_commander,alpha.camera_rig=cameras_sim", "", "", "-"],
            mcp,
        )
        self.assertIn(
            ["combo", "openarm_simulation_mcp", "simulation=waldo", "openarm_v2_sim", "robot_commander=mcp_sim_commander,camera_rig=cameras_sim", "-"],
            mcp,
        )
        references = next(line for line in lines if line.startswith("launcher\topenarm_simulation_mcp\t"))
        for reference in [
            "openarm/fragments/openarm_v2_sim.json5",
            "openarm/fragments/mcp_sim_commander.json5",
            "openarm/fragments/cameras_sim.json5",
            "simulation/fragments/waldo.json5",
            "simulation/fragments/web_scene_commander.json5",
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

    def test_plan_previews_the_launch_and_its_join_and_launches_the_same(self):
        line = "combo\tfleet\tsimulation=mujoco\topenarm_v2\trobot_commander=xr_commander\t-\n"
        with planned(line) as (run, matrix):
            self.assertEqual(run.call_args.args[0], [
                "peppy", "stack", "resolve", "fleet.json5", "--with", "simulation=mujoco",
                "--join", "openarm_v2", "--join-name", combinations.COPY_NAME, "--join-with", "robot_commander=xr_commander",
            ])
            # The preview reads the plan as JSON5, so peppy logs errors only.
            self.assertEqual(run.call_args.kwargs["env"]["RUST_LOG"], "error")
            entry, = matrix
            self.assertEqual(entry["words"], "simulation=mujoco")
            self.assertEqual(entry["join_option"], "openarm_v2")
            self.assertEqual(entry["join_name"], combinations.COPY_NAME)
            self.assertEqual(entry["join_words"], "robot_commander=xr_commander")
            self.assertEqual(entry["label"], "fleet (simulation=mujoco) + join openarm_v2 (robot_commander=xr_commander)")

    def test_the_launcher_deploying_the_most_nodes_commits_its_cache_and_builds_the_rest(self):
        lines = (
            "combo\tfleet\tsimulation=mujoco\t\t\t-\n"
            "combo\tfleet\tsimulation=waldo\topenarm_v2\trecorder=lerobot_recorder\t-\n"
            "combo\tfleet\tsimulation=waldo\topenarm_v2\tbrain=ai_brain\t-\n"
        )
        plans = [
            resolved_plan("openarm_sim_mujoco:v1", "openarm_backbone:v1"),
            resolved_plan("waldo:v1", "openarm_backbone:v1", "lerobot_recorder:v1"),
            resolved_plan("waldo:v1", "openarm_backbone:v1", "openarm_ai_brain:v1"),
        ]
        with planned(lines, plans) as (_, matrix):
            self.assertEqual([entry["cache_writer"] for entry in matrix], [False, True, False])
            # The writer deploys three nodes, as does the third combination;
            # the first of the two wins, and builds what its siblings deploy
            # and it does not, sorted by name.
            self.assertEqual(matrix[1]["extra_nodes"], "openarm_ai_brain:v1 openarm_sim_mujoco:v1")
            self.assertEqual([entry["extra_nodes"] for entry in matrix if not entry["cache_writer"]], ["", ""])

    def test_every_launcher_names_a_cache_writer_of_its_own(self):
        lines = (
            "combo\tfleet\tsimulation=mujoco\t\t\t-\n"
            "combo\trust_robot\t\t\t\t-\n"
        )
        plans = [
            resolved_plan("openarm_sim_mujoco:v1"),
            resolved_plan("my_rust_robot_arm:v1", "my_rust_robot_brain:v1"),
        ]
        with planned(lines, plans, launchers=("fleet", "rust_robot")) as (_, matrix):
            self.assertEqual([(entry["launcher"], entry["cache_writer"]) for entry in matrix],
                             [("fleet", True), ("rust_robot", True)])
            self.assertEqual([entry["extra_nodes"] for entry in matrix], ["", ""])

    def test_an_untagged_node_is_named_bare(self):
        self.assertEqual(combinations.node_item("waldo", "v1"), "waldo:v1")
        self.assertEqual(combinations.node_item("waldo", ""), "waldo")

    def test_plan_previews_a_deployed_copys_launch_words_without_a_join(self):
        words = "simulation=mujoco,alpha.camera_rig=cameras_sim,alpha.recorder=lerobot_recorder"
        with planned(f"combo\tfleet\t{words}\t\t\t-\n") as (run, matrix):
            self.assertEqual(run.call_args.args[0], [
                "peppy", "stack", "resolve", "fleet.json5", "--with", words,
            ])
            entry, = matrix
            self.assertEqual(entry["words"], words)
            self.assertEqual(entry["join_option"], "")
            self.assertEqual(entry["join_name"], "")
            self.assertEqual(entry["label"], f"fleet ({words})")

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
        root = Path(__file__).resolve().parents[2]
        for launcher in ["openarm/openarm_simulation.json5", "mcp/openarm_simulation_mcp.json5", "fleet.json5"]:
            document = combinations.load_json5(root / launcher, launcher)
            waldo = [adjustment["set_arguments"] for adjustment in document["adjustments"]
                     if adjustment.get("when") == {"simulation": "waldo"}]
            self.assertEqual(waldo, [{"world": "openarm_v2"}], launcher)
            for adjustment in document["adjustments"]:
                self.assertNotIn("plugins", adjustment.get("set_arguments", {}), launcher)
        waldo = combinations.load_json5(root / "simulation/fragments/waldo.json5", "waldo")
        self.assertEqual(
            waldo["deployments"][0]["instances"][0]["arguments"]["plugins"], "hand_teleop,sim_inspector")
        for adjustment in waldo.get("adjustments", []):
            self.assertNotIn("plugins", adjustment.get("set_arguments", {}))

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

    def test_the_scene_commander_binds_lighting_and_materials_on_waldo_alone(self):
        root = Path(__file__).resolve().parents[2]
        scene = combinations.load_json5(
            root / "simulation/fragments/web_scene_commander.json5", "web_scene_commander")
        instance, = scene["deployments"][0]["instances"]
        self.assertEqual(instance["instance_id"], "scene_commander_inst")
        # The two optional slots are written vacant, with a reason, and the
        # camera slots (zero_or_more) are left to the rendered rig; the
        # panel's fragment binds nothing itself.
        self.assertEqual(
            set(instance["links"]), {"simulation", "objects", "lighting", "materials"})
        for slot in ["lighting", "materials"]:
            with self.subTest(slot=slot):
                self.assertEqual(set(instance["links"][slot]), {"vacant"})
                self.assertTrue(instance["links"][slot]["vacant"].strip())
        self.assertNotIn("adjustments", scene)
        # Waldo, the one simulation serving the two contracts, binds them from
        # its own fragment without a guard: the adjustment runs exactly when
        # Waldo is selected and is skipped when no scene commander is.
        waldo = combinations.load_json5(root / "simulation/fragments/waldo.json5", "waldo")
        self.assertIn({
            "target": "scene_commander_inst",
            "set_links": {"lighting": "simulation_inst", "materials": "simulation_inst"},
        }, waldo["adjustments"])
        for other in ["isaac_sim", "mujoco"]:
            with self.subTest(simulation=other):
                fragment = combinations.load_json5(root / f"simulation/fragments/{other}.json5", other)
                self.assertNotIn("scene_commander_inst", [
                    adjustment["target"] for adjustment in fragment.get("adjustments", [])])

    def test_the_mcp_sim_commander_lists_five_exposures_and_binds_every_target(self):
        root = Path(__file__).resolve().parents[2]
        fragment = combinations.load_json5(
            root / "openarm/fragments/mcp_sim_commander.json5", "mcp_sim_commander")
        deployment, = fragment["deployments"]
        self.assertEqual(deployment["source"], {"exposures": [
            "openarm_v2:v1", "scene_manipulation:v1", "scene_lighting:v1",
            "scene_materials:v1", "openarm_v2_sim_cameras:v1",
        ]})
        instance, = deployment["instances"]
        self.assertEqual(instance["instance_id"], "commander_inst")
        self.assertEqual(instance["arguments"], {"port": 8900})
        # One link per target of the five exposures: the robot's two roles
        # on the backbone, the three scene roles on the simulation, each
        # camera and its profile on the relay that serves both.
        self.assertEqual(instance["links"], {
            "postures": "backbone_inst", "limb_motion": "backbone_inst",
            "scene": "simulation_inst", "lighting": "simulation_inst", "materials": "simulation_inst",
            "wrist_left": "wrist_left", "wrist_right": "wrist_right", "chest": "chest",
            "wrist_left_profile": "wrist_left", "wrist_right_profile": "wrist_right",
            "chest_profile": "chest",
        })
        # The backbone is vacated exactly as under the real robot's MCP
        # commander, which stays as it is.
        real = combinations.load_json5(root / "openarm/fragments/mcp_commander.json5", "mcp_commander")
        self.assertEqual(fragment["adjustments"], real["adjustments"])
        self.assertEqual(real["deployments"][0]["source"], {"exposures": ["openarm_v2:v1"]})
        self.assertEqual(
            real["deployments"][0]["instances"][0]["links"],
            {"postures": "backbone_inst", "limb_motion": "backbone_inst"})

    def test_the_simulated_v2_offers_the_mcp_sim_commander_under_its_constraints(self):
        root = Path(__file__).resolve().parents[2]
        robot = combinations.load_json5(root / "openarm/fragments/openarm_v2_sim.json5", "openarm_v2_sim")
        commander = next(axis for axis in robot["components"] if axis["name"] == "robot_commander")
        self.assertEqual(commander["options"]["mcp_sim_commander"], "mcp_sim_commander.json5")
        self.assertEqual(commander["options"]["mcp_commander"], "mcp_commander.json5")
        mcp = [c for c in robot["constraints"] if c.get("when") == {"robot_commander": "mcp_sim_commander"}]
        self.assertEqual([c["requires"] for c in mcp], [[{"camera_rig": "cameras_sim"}], [{"simulation": "waldo"}]])
        self.assertEqual([c["reason"] for c in mcp], [
            "mcp_sim_commander serves the rendered cameras and its exposure list is fixed, "
            "so the camera targets cannot be optional; select cameras_sim",
            "mcp_sim_commander binds scene_lighting and scene_materials, which only waldo "
            "implements; launch with waldo",
        ])
        # The rig's consumer rule admits the MCP simulation commander; the
        # scene commander's axis is out of a robot fragment's reach, so the
        # rule cannot name it.
        rig, = [c for c in robot["constraints"] if c.get("when") == {"camera_rig": "cameras_sim"}]
        self.assertEqual(rig["requires"], [
            {"recorder": "lerobot_recorder"},
            {"robot_commander": ["xr_commander", "mcp_sim_commander"]},
        ])
        self.assertEqual(
            rig["reason"],
            "camera streams need a consumer; select lerobot_recorder, xr_commander or mcp_sim_commander")
        # No MCP option serves the physical rig, so the real robots' rule
        # names the recorder and the headset alone.
        for path in ["openarm/fragments/openarm_v2.json5", "openarm/fragments/openarm_v1.json5"]:
            with self.subTest(path=path):
                real = combinations.load_json5(root / path, path)
                axes = {axis["name"]: axis for axis in real["components"]}
                self.assertNotIn("mcp_sim_commander", axes["robot_commander"]["options"])
                rule, = [c for c in real["constraints"] if c.get("when") == {"camera_rig": "cameras"}]
                self.assertEqual(
                    rule["requires"], [{"recorder": "lerobot_recorder"}, {"robot_commander": "xr_commander"}])

    def test_the_rendered_rig_binds_camera_control_per_simulation_and_the_scene_panel(self):
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
        # The browser scene commander's camera panel: the relays on the
        # stack instance, and their profiles matched by instance.
        panel, = [a for a in rig["adjustments"] if a["target"] == "scene_commander_inst"]
        self.assertEqual(panel, {"target": "scene_commander_inst", "add_links": {
            "color_cameras": ["wrist_left", "wrist_right"],
            "rgbd_cameras": ["chest"],
            "camera_profiles": ["wrist_left", "wrist_right", "chest"],
        }})

    def test_the_mcp_launcher_deploys_waldo_and_the_mcp_copy(self):
        root = Path(__file__).resolve().parents[2]
        path = "mcp/openarm_simulation_mcp.json5"
        index = combinations.load_json5(root / "peppy_repository.json5", "index")
        self.assertEqual(index["launchers"]["openarm_simulation_mcp"], {"path": path})
        document = combinations.load_json5(root / path, path)
        simulation = next(axis for axis in document["components"] if axis["name"] == "simulation")
        # The robot fragment's guards name MuJoCo and Isaac Sim, and a guard
        # naming an option the axis does not declare is refused when the
        # launcher loads, so the axis carries all three; the file deploys
        # Waldo and the fragment's own constraint refuses the MCP simulation
        # commander under the other two.
        self.assertEqual(simulation["cardinality"], "one")
        self.assertEqual(list(simulation["options"]), ["mujoco", "isaac_sim", "waldo"])
        self.assertEqual(simulation["options"]["waldo"], "../simulation/fragments/waldo.json5")
        robot = next(axis for axis in document["components"] if axis["name"] == "robot")
        self.assertEqual(robot["cardinality"], "zero_or_more")
        self.assertEqual(robot["options"], {"openarm_v2_sim": "../openarm/fragments/openarm_v2_sim.json5"})
        self.assertEqual(combinations.option_entries(document, path), {"simulation": "waldo", "robot": "openarm_v2_sim"})
        launcher = combinations.read_launcher(root, path)
        self.assertEqual(launcher.copies, [combinations.Copy(
            "alpha", "robot", "openarm_v2_sim",
            {"robot_commander": "mcp_sim_commander", "camera_rig": "cameras_sim"})])
        self.assertEqual(document["adjustments"], [{
            "target": "simulation_inst", "when": {"simulation": "waldo"},
            "set_arguments": {"world": "openarm_v2"}}])
        # The copy's axes are in the planner's reach: every state of the
        # commander, recorder, rig and brain, minus the one the file runs.
        found = combinations.launcher_selections(launcher.axes, launcher.copies)
        deployed = [combinations.render_words(c.words) for c in found
                    if c.join_option is None and c.words[0] == ("simulation", "waldo")]
        self.assertEqual(len(deployed), 2 * (1 + 4 * 2 * 2 * 2 - 1))
        self.assertIn("simulation=waldo,alpha.robot_commander=xr_commander,alpha.camera_rig=cameras_sim", deployed)
        self.assertNotIn("simulation=waldo,alpha.robot_commander=mcp_sim_commander,alpha.camera_rig=cameras_sim", deployed)

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


if __name__ == "__main__":
    unittest.main()
