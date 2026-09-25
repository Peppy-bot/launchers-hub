# Launchers Hub

[Peppy](https://github.com/Peppy-bot/peppy) launchers for robotic systems.

## The one rule

A launcher is a `launcher/v1` file; the files an option pulls in are
fragments. A launcher's `components` declare what may run: each component
is an axis with named options and a cardinality, how many of its options
may run at once. Its `deployments` say what runs: nodes, and the option
each axis starts with. `--with` swaps an option at launch; a `zero_or_more`
axis runs as named copies, listed in the file or added with `peppy stack
join`.

| Cardinality | The file deploys it as | At launch | Later |
|---|---|---|---|
| `one` | `{ simulation: "waldo" }` | `--with mujoco` swaps it | fixed for the stack's life |
| `zero_or_one` | not deployed | `--with web_scene_commander` turns it on | fixed |
| `zero_or_more` | `{ robot: "openarm_v2_sim", instances: [{ instance_id: "alpha" }] }`, or the entry alone, `{ robot: "openarm_v2_sim" }`, saying how every copy of the option is set up | the listed copies start, and `--join openarm_v2_sim:bravo` names another | `stack join openarm_v2_sim:charlie`, `stack remove charlie` |

An option's fragment declares axes of its own. A robot's fragment declares
its robot commander, its recorder and its camera rig, selected per copy with
`with:` in the file or `--with` on `stack join`; a simulation's fragment
declares its scene commander, selected with `--with` at launch. A launcher
whose `robot` axis is `one` reaches the robot's axes with `--with` at launch
too.

A launcher cannot pre-select an axis a fragment on a `one` axis declares:
an entry on a `one` axis takes no `with`, and a launch word is what selects a nested axis.
What must serve by default and still let a launch switch off is therefore
a `one` axis of its own, deployed by the file that declares it, with a
`none` option whose fragment deploys nothing: `simulation_mcp.json5`
declares `robot_control`, the robots' MCP endpoint, and `world_control`, the simulated
world's, that way; `--with robot_control=none` and
`--with world_control=none` turn them off. The other launchers declare `robot_control`
as a `zero_or_one` axis, and `--with robot_control` turns it on.
Every launcher that runs robots declares a `robot_control` axis: the robots'
fragments add their links on `robot_control_inst`, and the recorder's trigger
rule and the MCP commander's requirement name the axis.

## Launchers

[simulation/simulation.json5](simulation/simulation.json5) stands a
simulation and nothing else: the engine and its viewer, no robot, on the
machine the launch is sent to. It declares no robot axis, so nothing joins
it. Four more run robots as copies, each named, its ids minted under its
name: `alpha_left_arm_inst`, `alpha_backbone_inst`, `alpha_commander_inst`.
[physical.json5](physical.json5) is the physical robots: it deploys
nothing, every robot is named at launch with `--join OPTION:NAME` or joined
by name and placed on the machine its hardware is wired to, and
`--with robot_control` serves the robots' MCP endpoint.
[openarm/openarm_simulation.json5](openarm/openarm_simulation.json5) runs
one simulation, Waldo unless a launch word selects another, and one robot,
`alpha`, a simulated OpenArm v2 with the browser commander; `--join
OPTION:NAME` names another at launch and `--with robot_control` serves the endpoint.
[so101/so101_simulation.json5](so101/so101_simulation.json5) is the same
launcher with another robot first: `alpha` is a simulated SO-101 with no
commander, which launches on a machine with no SO-101 hardware.
[mcp/simulation_mcp.json5](mcp/simulation_mcp.json5) is
`openarm_simulation` with the endpoint deployed and every robot driven
over MCP by default: its
entries for the three simulated robots state the MCP commander and the
rendered rig for every copy of the option, so `alpha`, a robot named at
launch and a robot joined later alike are listed on the one endpoint, on
the same URL when the join returns, and the simulated world's endpoint runs
beside it from the file's `world_control` axis. The endpoints, the client setup
and the move to the real robots are in the [MCP guide](mcp/README.md).

The robot options are the four OpenArm fragments, `openarm_v1`,
`openarm_v2` (physical), `openarm_v1_sim`, `openarm_v2_sim` (simulated),
sharing the OpenArm control in `openarm/fragments/control_common.json5`,
and the two SO-101 fragments, `so101` (physical) and `so101_sim`
(simulated), sharing the SO-101 control in
`so101/fragments/control_common.json5`. The stack's MCP server reads one
clock, so a stack under MCP is real or simulated: the physical robots are
options of `physical.json5` and the simulated robots of the three
simulation launchers, where an SO-101 joins beside an OpenArm in one
simulation. The [OpenArm guide](openarm/README.md) and the
[SO-101 guide](so101/README.md) cover the hardware and commander setup:

```sh
peppy stack launch openarm_simulation --with mujoco                         # MuJoCo and alpha
peppy stack remove alpha                                                    # the simulation keeps running
peppy stack join openarm_v2_sim:bravo --with xr_commander --set-arguments commander_inst.https_port=4444
peppy stack list
peppy stack launch so101_simulation                                         # Waldo and one SO-101
peppy stack launch so101_simulation --with mujoco                           # the same robot, alone in MuJoCo
peppy stack launch so101_simulation --with isaac_sim
peppy stack launch openarm_simulation --join so101_sim:charlo               # alpha and an SO-101 named at launch
peppy stack join so101_sim:delta                                            # another SO-101 beside them
peppy stack launch simulation                                               # a simulation with no robot
peppy stack launch physical                                                 # a new stack with no robot
peppy stack join openarm_v2:alpha --place jetson-1
peppy stack join so101:bravo
peppy stack launch simulation_mcp                                           # Waldo, the two endpoints and alpha over MCP
peppy stack launch simulation_mcp --join so101_sim:charlie                  # alpha and an SO-101 over MCP
peppy stack join openarm_v2_sim:bravo                                       # a second OpenArm, listed on the same URL when the join returns
peppy stack join so101_sim:delta                                            # an SO-101, its arm and front camera on the same URL
```

A simulation stands a robot when it holds the robot's model in its scene and
drives that model's limbs for the robot's backbone. Every simulation stands
any number of robots at once, of any model in its catalogue, each joined
copy bringing its own, and each standing clear of the others. Waldo and
Isaac Sim place and move them through the scene contract; MuJoCo composes
its scene from the models standing in it.

These are separate sessions. **Launch replaces the current stack.** Complete
the [hardware and commander setup](openarm/README.md) before launching. Waldo
requires access to its private node repository; MuJoCo and Isaac Sim are in
nodes-hub. Run join and remove on the coordinator, locally or with
`--core-node NAME`. A copy nobody placed runs on the coordinator. All
participating daemons must use the same Peppy version and workspace. See
[federation](https://docs.peppy.bot/advanced_guides/federation/) for
discovery and host setup.

| Axis | Declared by | Options |
|---|---|---|
| `simulation` | `simulation.json5`, `openarm_simulation.json5`, `so101_simulation.json5` and `simulation_mcp.json5` (`one`) | `waldo` (deployed), `mujoco`, `isaac_sim`; `simulation.json5` offers the two that answer scene calls |
| `robot_control` | `simulation_mcp.json5` (`one`), `physical.json5`, `openarm_simulation.json5` and `so101_simulation.json5` (`zero_or_one`) | `robot_control`: the robots' MCP endpoint on port 8900, one server every robot is listed on, its `mcp_commander` adding the moves; deployed by `simulation_mcp.json5`, where `none` switches it off, and turned on elsewhere with `--with robot_control`. The server reads the simulation's clock in the simulation launchers and wall time in `physical.json5` |
| `world_control` | `simulation_mcp.json5` (`one`) | `world_control` (deployed): the simulated world's MCP endpoint on port 8902, bound to the simulation alone; requires `waldo`. `none` switches it off |
| `scene_commander` | Isaac Sim and Waldo | `web_scene_commander`: edits the simulation's scene and lists its spawned objects; on Waldo it also edits the scene's lighting and the robot's materials, and it shows a camera panel when a rendered rig runs |
| `robot` | the four launchers with robots (`zero_or_more`) | `openarm_v1_sim`, `openarm_v2_sim` and `so101_sim` in the three simulation launchers; `openarm_v1`, `openarm_v2` and `so101` in `physical.json5` |
| `control` | the robot | `control_common` (deployed): the initializer and backbone the robot shares with its simulated or physical twin, one fragment per robot family |
| `robot_commander` | an OpenArm | `web_commander` (deployed), `xr_commander`, and `mcp_commander`, which adds the robot's moves to the stack's `robot_control` endpoint and requires it, the rig adding the cameras; `ker_commander` on the v2 robots |
| `robot_commander` | an SO-101 | `so101_leader` (deployed by `so101`), `xr_commander`, and `mcp_commander`, which adds the arm's moves, and with a rig the front camera, to the stack's `robot_control` endpoint; `none` on `so101` and the unfilled `zero_or_one` axis on `so101_sim` run the robot on actions alone |
| `recorder` | the robot | `lerobot_recorder`; needs a trigger: the record button of the browser panel or the headset, or `recorder.record_episode` on the `robot_control` endpoint, where the recorder is listed whenever the stack serves it; an SO-101 has the headset's button and the endpoint |
| `camera_rig` | the robot | `cameras` on a physical robot, `cameras_sim` on a simulated v2 and a simulated SO-101; requires a consumer: a recorder, XR or `mcp_commander`, which lists the cameras on the `robot_control` endpoint |
| `brain` | the v2 robots | `ai_brain`, whose tools are listed under the robot's name on the `robot_control` endpoint under `mcp_commander` |

A copy selects one option per axis of its robot. `stack resolve` previews a
launch and each copy it names, in order, without starting nodes:

```sh
peppy stack resolve openarm_simulation --with mujoco
peppy stack resolve physical --join openarm_v2:bravo --with bravo.xr_commander,bravo.lerobot_recorder
peppy stack resolve simulation_mcp --join so101_sim:charlie --with web_scene_commander
peppy stack resolve so101_simulation --join so101_sim:charlo
peppy stack resolve simulation_mcp --join so101_sim:delta
```

### What a copy can change

A copy's fragments write to the stack's instances in three ways: the
rendered camera rig turns the simulation's rendering on, the copy's
instances pair into the simulation's limb and camera slots, which hold any
number of pairs, and they join the sets the MCP endpoint's targets declare,
with `add_links` on `robot_control_inst`. Copies deployed together must agree on
every field they both write. A copy named on the command line is composed as
a join, `--join OPTION:NAME` at launch and `stack join OPTION:NAME` later
alike, so a robot you can name at launch is a robot you can join later. Such
a copy may write to a stack instance only what already runs there, its pairs
and its set members aside; one that would change a running instance any
other way is refused, naming the instance and each field that differs. A
join therefore cannot turn rendering on: rendered cameras are selected by a
word on a copy the file lists (`--with alpha.cameras_sim`) or by the
launcher itself, as `simulation_mcp` does. A robot joined to a plain
`openarm_simulation` or `so101_simulation` has no rendered camera, an
SO-101's `front` included, while one joined to `simulation_mcp` has its
rig. The [planner](.github/scripts/launcher_combinations.py) reports such
copies as file-only.

### How a robot reaches a simulation

A simulated robot is the same nodes as a physical one, the initializer, the
backbone and its commander, with the simulation standing its limbs: it has
no driver node, and a simulated SO-101 no follower, the engine playing the
follower role toward the backbone. The initializer, `robot_initializer`, is
the one identity node every robot runs. Its `model` argument is the model
id verbatim, `openarm_v1`, `openarm_v2` or `so101`; it answers
`robot_identity` and `robot_ready`, and joins the simulation through the
`simulation_robot` contract, naming the copy the robot runs as and the model
to stand. On hardware its `simulation` slot is vacant and its `limbs` slot
links the robot's drivers.

A backbone names its downstream links after its limbs: `left_arm`,
`right_arm`, `left_gripper` and `right_gripper` on an OpenArm, `arm` and
`gripper` on an SO-101, on hardware and in simulation alike. On hardware
each pairs with a driver; in a simulation each pairs into one of the
simulation's four pairing slots, one per kind of pair and each holding any
number of pairs: `arms`, `grippers`, `rgb_cameras` and `rgbd_cameras`. A
pair's robot is its copy, a limb pair's limb is the backbone link it comes
from, and a camera pair's camera is its relay's name in the copy
(`wrist_left`, `front`), so a stack holds as many robots as its machines can
run, of any model. The simulation stays up when a robot is removed;
`stack reset` stops everything. A physical robot reads wall time and a
simulated one the simulation's clock, and the stack's MCP server reads one
clock, so the physical robots have a launcher of their own,
`physical.json5`, and the simulation launchers offer the simulated robots.
A simulated robot has no motors to report on, so the web and XR panels
show no motor health for it.

| Robot | Rendered cameras its `camera_rig` axis offers |
|---|---|
| OpenArm v1 | None: the axis is not on the fragment |
| OpenArm v2 | `wrist_left`, `wrist_right`, `chest` |
| SO-101 | `front` |

Waldo opens a world that stands no robot of its own (`stage`) and Isaac Sim
an empty stage; every robot brings the model its fragment names. A rendered
camera rig is one robot's, and every engine renders one per robot that
selects it.

Isaac Sim requires a supported NVIDIA GPU. XR requires a reachable HTTPS
endpoint and a headset for operator control.

Each simulation fragment declares the clock domain `simulation`, supplied by
`simulation_inst`; every instance of a simulated robot binds to it with
`set_framework: { clock: "simulation" }` in the robot's fragment, and the
simulation launchers bind the MCP server the same way. The binding travels
in the launch, so a
daemon runs a simulated robot and a physical one at the same time and needs no
flag and no restart to switch between them. `peppy clock list` shows the
domains a federation is running and what reads each one.

### Per-copy arguments

Colocated copies need distinct hardware interfaces and dataset directories,
and a copy whose URL must be known in advance needs a port of its own: the
browser panel and the MCP servers prefer their port and take another when it
is held, reporting the one they took. A copy's `arguments` in the file, or
`--set-arguments` on join, override its instances' arguments by the id
written in the fragment; `with` and `arguments` written on the entry itself
apply to every copy of the option, the ones it lists and the ones a join
adds, each copy's own winning per axis and per argument, and an entry or a
copy may carry `adjustments` with the fragment verbs, run after the
launcher's adjustments and before the copy's arguments. At launch,
`--with alpha.xr_commander` selects the axis of a copy the file lists or
the launch names. Values are
JSON5; the order of application is fragment values and adjustments, launcher
adjustments, the entry's then the copy's adjustments, then the entry's
arguments and the copy's over them. The [OpenArm guide](openarm/README.md#commanders-recording-and-cameras)
shows a second physical v2 on one host taking its own CAN bindings,
commander port and dataset directory. The launch
snapshots its launcher and fragment files; edits take effect on the next
launch.

## SO-101

The physical SO-101 is the `so101` option of `physical.json5`: one copy runs
the follower, the initializer, the backbone and the leader arm as its
commander, its ids under the copy's name (`alpha_follower_inst`,
`alpha_init_inst`, `alpha_backbone_inst`, `alpha_commander_inst`); the
[setup guide](so101/README.md) covers the host prerequisites.

```sh
peppy stack launch physical
peppy stack join so101:alpha
peppy stack join so101:alpha --with xr_commander,lerobot_recorder,cameras
peppy stack launch physical --with robot_control                                    # the robots' MCP endpoint
peppy stack join so101:alpha --with mcp_commander,cameras                 # driven over MCP, its camera served
```

The simulated SO-101 is the `so101_sim` option of every simulation launcher,
and the robot `so101_simulation.json5` lists as `alpha`. It runs the same
initializer and backbone with the simulation in the follower's place, and
comes up with no commander, so it needs no SO-101 hardware. Its other
options are `so101_leader`, a real leader arm driving the simulated
follower, `xr_commander`, and `mcp_commander`, which adds the arm's moves,
and with `cameras_sim` the rendered `front` camera, to the stack's
`robot_control` endpoint:

```sh
peppy stack launch so101_simulation                                         # Waldo and one SO-101
peppy stack launch so101_simulation --with mujoco                           # the same robot, alone in MuJoCo
peppy stack launch so101_simulation --with isaac_sim
peppy stack launch so101_simulation --with robot_control,alpha.mcp_commander,alpha.cameras_sim
peppy stack join so101_sim:charlo                                           # an SO-101 beside the copies already standing
```

Over MCP a model drives the SO-101's arm and gripper and sees through its
camera by the robot's name on `http://127.0.0.1:8900/robot_control/v1/mcp`,
and, on `simulation_mcp`, moves its base through the `simulation` endpoint.

## Inspecting and testing

```sh
peppy stack resolve openarm_simulation --with mujoco --join openarm_v2_sim:bravo --with bravo.xr_commander
peppy repo index --check .
python3 -m unittest discover -s .github/scripts -p 'test_*.py'
```

Resolve prints the flat plan and adjustment report, one `copy NAME:` line per
copy. Its report says explicitly when missing cached node manifests leave
link rules unchecked; run `peppy repo refresh` with the matching node
repositories registered for those checks. The CI combination planner
requires `peppy` on PATH.

The [workflow](.github/workflows/tests.yml) enumerates every state of every
axis, the fragments' own axes included: the launcher's own axes and each
listed copy's as launch words, each option an entry lists without a copy
named at launch with `--join`, and every copy a launcher can add as a join:
plain, the way an operator types it, and with its axes as join words. It validates every admitted combination
and launches a subset of the ones its runner can run, joining and removing
the planned copy: the launches that, between them, run every configured node
instance those combinations deploy and every pair of instances any of them
runs side by side, with every launcher launched at least once and every
robot joined plain at least once. The launches run one after the other in
one job, on one daemon, the stack reset between them. A pull request that
changes launcher files alone launches from the combinations whose resolved
plan differs from the base tree's; the run summary names every combination
left out and the launches that stand for it.

The peppy the workflow launches with, and the hub repositories it launches
against, come from the shared `hub-ci-peppy` action of the peppy repository:
every sibling hub at its branch named like the pull request's head branch
where it has one, and at its `main` otherwise. The peppy release also
dispatches the workflow, naming the peppy release run whose archive it
installs and the hub commits the release recorded; that run plans every
combination. The run summary names the hub commits that ran.

A joined copy comes up beside the copies the file deploys and the one the
launch names, so a launch of `simulation_mcp` under Waldo ends with
an MCP robot on the stack's endpoint, and one of `openarm_simulation` with
an SO-101 in the simulation. The runner has no SO-101 hardware, no headset
and no GPU, so of the simulated SO-101 it launches the selections that open
no device, the plain join and `mcp_commander` with `cameras_sim`, under
Waldo and MuJoCo; `so101_leader`, `xr_commander` and Isaac Sim are validated
and skipped. The launch job holds the copy a launch names and the joined
copy to the instances `peppy stack resolve` previewed for them, every one of
them running, so a plain join that comes up under the fragment's default
commander, where the launcher's entry gives it another, fails the launch.
The [skip file](.github/unlaunchable-nodes.json5) lists the hardware the
runner lacks. Structural checks and runtime startup checks are separate
results.

## Configuration ownership

| Location | Owns |
|---|---|
| `physical.json5`, `openarm/openarm_simulation.json5`, `so101/so101_simulation.json5`, `mcp/simulation_mcp.json5`, `simulation/simulation.json5` | The simulation axis, the `robot_control` axis, the robot options, what the file deploys and the server's clock; `mcp/simulation_mcp.json5` also turns rendering on and serves the simulated world's endpoint, and `simulation/simulation.json5` declares no robot options at all |
| `openarm/fragments/control_common.json5` | The initializer and backbone every OpenArm shares, with the backbone's leader sockets vacant until a leader releases the ones it drives, the headset and recorder wiring, the robot's listing on the `robot_control` endpoint and the recorder's trigger rule |
| `openarm/fragments/openarm_v1.json5`, `openarm_v2.json5`, `openarm_v1_sim.json5`, `openarm_v2_sim.json5` | One robot each: its limbs, or the simulation slots its control leads, the control it selects, the robot commander, recorder and camera rig axes, the rig's consumer rule, its generation, speed cap, commander tuning, dataset labels, the clock of a simulated one, and the model the simulation stands for it |
| `so101/fragments/control_common.json5` | The initializer, with the `so101` model, and the backbone every SO-101 shares, with its leader sockets vacant until a leader releases them, the headset and recorder wiring, the robot's listing on the `robot_control` endpoint and the recorder's trigger rule |
| `so101/fragments/so101.json5`, `so101_sim.json5` | One robot each, on the same pattern: the follower, or the simulation slots the backbone leads, the axes, the default commander, the rig's consumer rule, the clock of the simulated one and its dataset labels |
| `robot_commanders/fragments/` | The commanders every robot shares: the headset, and the MCP commander, which adds the robot's moves to the `robot_control` endpoint and streams nothing; robot tuning is supplied by the robot fragments. A leader that streams (the panel, the KER, the SO-101 leader arm) releases the backbone sockets it drives from its own fragment |
| `openarm/fragments/cameras.json5`, `cameras_sim.json5`, `so101/fragments/cameras.json5`, `cameras_sim.json5` | The physical and rendered camera rigs, and their cameras on the `robot_control` endpoint under `mcp_commander` |
| `openarm/fragments/ai_brain.json5` | The environment aware action layer beside a robot commander, and its tools on the `robot_control` endpoint |
| `recording/fragments/` | Reusable recorder deployment and the recorder on the `robot_control` endpoint; the family's control wires its record button |
| `simulation/fragments/` | The simulations, the Isaac viewer, the scene commander, with the lighting and materials slots it binds on Waldo |
| `common/fragments/none.json5` | The empty option, which deploys nothing, so an axis carrying it can be switched off |
| `mcp/` | The two MCP endpoints, the robots' (`fragments/robot_control.json5`) and the simulated world's (`fragments/world_control.json5`), the launcher whose robots are driven over MCP by default, and the guide to the endpoints and clients |

OpenArm's web commander references OpenArm interfaces, so its fragment
stays under `openarm/fragments/`. The headset and the MCP commander are
shared by both robots. Both MCP endpoints are served by the server built into peppy, so their
fragments sit together under `mcp/fragments/`. A launcher
whose command surface is MCP lives under `mcp/` and composes the same robot
and simulation fragments as the others. Any axis can carry the empty option,
so it sits under `common/fragments/`.

Fragment paths are relative to the file that names them: the launcher's
directory for its options, the fragment's for the options of its own axes.
A repository launcher can reference siblings, such as
`../simulation/fragments/mujoco.json5`. The nearest valid
`peppy_repository.json5` defines the boundary; parent traversal and symlinks
must stay within it. Standalone launchers are confined to their own
directory. A fragment file is named for the option that selects it, and a
simulated variant of an option takes the `_sim` suffix: `openarm_v2` and
`openarm_v2_sim`, `so101` and `so101_sim`, `cameras` and `cameras_sim`.

After adding or moving a launcher, run `peppy repo index .` and commit the
index. Fragments use `launcher_fragment/v1` and are excluded from the
launcher index.

Conditions use OR within an option list and AND across axes. A fragment's
guards and constraints name its own axes and the launcher's; a launcher's
name the launcher's. For example, the physical robot fragments bind
physical-driver alerts for either alert-capable commander:

```json5
when: { robot_commander: ["web_commander", "xr_commander"] }
```

The same condition syntax applies to adjustments and constraints. Shared
behavior uses one grouped condition; different argument values, such as v1/v2
hardware models or per-simulation dataset labels, have separate adjustments.

## Release requirements

Use a Peppy release whose launchers deploy options (`{ simulation: "waldo" }`
in `deployments`) and the matching nodes-hub release with `robot_initializer`,
`sim_mujoco`, `sim_isaac` and the backbones whose downstream links carry
their limbs' names, the Waldo release with the same four pairing slots, and
the MCP hub release with `robot_control:v1`, with refreshed repository
entries for the scene commander and Isaac viewer.
Refresh repository caches after upgrading. Rebuild application nodes with the matching SDK.
