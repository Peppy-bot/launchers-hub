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
| `zero_or_more` | `{ robot: "openarm_v2_sim", instances: [{ instance_id: "alpha" }] }` | the listed copies start | `stack join openarm_v2_sim -i bravo`, `stack remove bravo` |

An option's fragment declares axes of its own. A robot's fragment declares
its robot commander, its recorder and its camera rig, selected per copy with
`with:` in the file or `--with` on `stack join`; a simulation's fragment
declares its scene commander, selected with `--with` at launch. A launcher
that runs one robot, as `so101` does, reaches the robot's axes with `--with`
at launch too.

A launcher cannot pre-select an axis a fragment declares: an entry on a
`one` axis takes no `with`, and a launch word is what selects a nested axis.
What a file must serve by default and still let a launch switch off is
therefore an axis of the launcher itself, `one`, deployed by the file, with
a `none` option whose fragment deploys nothing:
`openarm_simulation_mcp.json5` declares `simulation_mcp` that way, and
`--with simulation_mcp=none` turns it off.

## Launchers

Three launchers run robots as copies, each named, its ids minted under its
name: `alpha_left_arm_inst`, `alpha_backbone_inst`, `alpha_commander_inst`.
[fleet.json5](fleet.json5) deploys nothing: a simulation is selected at
launch, or none, and every robot, physical or simulated, is joined by name,
a physical one placed on its machine by that name.
[openarm/openarm_simulation.json5](openarm/openarm_simulation.json5) runs
one simulation, Waldo unless a launch word selects another, and deploys the
copy `alpha`, a simulated OpenArm v2 with the browser commander.
[mcp/openarm_simulation_mcp.json5](mcp/openarm_simulation_mcp.json5) runs
Waldo and serves two MCP endpoints, one per family: the robot's own
surface, its moves and its three cameras, from the copy `alpha` with the
MCP commander and the rendered camera rig, and the simulated world, its
scene, lighting and materials, from the launcher's own `simulation_mcp`
axis, documented in the [MCP guide](mcp/README.md). It selects the MCP
commander and the rig on the robot's entry, so every robot joined under it
is an MCP robot with an endpoint of its own, whose URL the join prints. The
robot
options are the four OpenArm fragments, `openarm_v1`, `openarm_v2`
(physical), `openarm_v1_sim`, `openarm_v2_sim` (simulated), sharing the
OpenArm control in `control_common.json5`, and `so101`; the
[OpenArm guide](openarm/README.md) and the [SO-101 guide](so101/README.md)
cover the hardware and commander setup:

```sh
peppy stack launch openarm_simulation --with mujoco   # MuJoCo and alpha
peppy stack remove alpha                               # the simulation keeps running
peppy stack join openarm_v2_sim -i bravo --with xr_commander --set-arguments commander_inst.https_port=4444
peppy stack list
peppy stack launch fleet                               # a new stack with no simulation
peppy stack join openarm_v2 -i alpha --place jetson-1
peppy stack join so101 -i bravo
peppy stack launch openarm_simulation_mcp              # Waldo and alpha, driven over MCP
peppy stack join openarm_v2_sim -i bravo               # a second MCP robot, on its own endpoint
```

A simulation stands a robot when it holds the robot's model in its scene and
drives that model's limbs for the robot's backbone. Waldo and Isaac Sim stand
any number of robots at once, each joined copy bringing its own model; MuJoCo
stands one.

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
| `simulation` | `openarm_simulation.json5` and `openarm_simulation_mcp.json5` (`one`), `fleet.json5` (`zero_or_one`) | `waldo` (deployed by both `one` launchers), `mujoco`, `isaac_sim` |
| `simulation_mcp` | `openarm_simulation_mcp.json5` (`one`) | `mcp_scene_commander` (deployed): the simulated world's MCP endpoint on port 8902, bound to the simulation alone; requires `waldo`. `none` switches it off |
| `scene_commander` | Isaac Sim and Waldo | `web_scene_commander`: edits the simulation's scene and lists its spawned objects; on Waldo it also edits the scene's lighting and the robot's materials, and it shows a camera panel when a rendered rig runs |
| `robot` | all three launchers | `openarm_v1_sim`, `openarm_v2_sim` in `openarm_simulation.json5`; `openarm_v2_sim` in `openarm_simulation_mcp.json5`; those, `openarm_v1`, `openarm_v2` and `so101` in `fleet.json5` |
| `control` | the robot | `control_common` (deployed): the shared initializer and backbone |
| `robot_commander` | the robot | `web_commander` (deployed), `xr_commander`, and `mcp_commander`, the robot's MCP endpoint on port 8900, which requires the robot's camera rig; a simulated v1 has no rig and so no `mcp_commander` |
| `recorder` | the robot | `lerobot_recorder`; requires web or XR |
| `camera_rig` | the robot | `cameras` on a physical robot, `cameras_sim` on a simulated v2; requires a consumer: a recorder, XR or `mcp_commander` |
| `brain` | the v2 robots | `ai_brain`, with its MCP server on port 8901 |

A copy selects one option per axis of its robot. `stack resolve` previews any
launch, and a join onto it, without starting nodes:

```sh
peppy stack resolve openarm_simulation --with mujoco
peppy stack resolve fleet --with mujoco --join openarm_v2_sim --join-name bravo --join-with xr_commander,lerobot_recorder
peppy stack resolve openarm_simulation_mcp --with web_scene_commander
```

### What a copy can change

A copy's fragment may write to a stack instance: the rendered camera rig
turns the simulation's rendering on. Copies deployed together must agree on
every field they both write, and a join may write to a stack instance only
what already runs there; its instances pair into the simulation's limb and
camera slots, which take one pair per robot. A join that would change a
running instance is refused, naming the instance and each field that
differs, so rendered cameras are selected in the file at launch. The
[planner](.github/scripts/launcher_combinations.py) reports such copies as
launch-only.

### How a robot reaches a simulation

A simulated robot is the same three nodes as a physical one, the initializer,
the backbone and its commander, with the simulation standing its limbs. The
initializer joins the simulation through the `simulation_robot` contract,
naming the copy the robot runs as and the model to stand, and the backbone
leads the simulation's limb slots on the same pairings it leads to a real
robot's drivers. Isaac Sim and Waldo hold one pair per robot on each limb
slot and tell the robots apart by the copy each pair belongs to, so a stack
holds as many as its machines can run; MuJoCo admits one robot at a time,
refusing a second attach while one stands. The simulation stays up when a
robot is removed; `stack reset` stops everything. Physical robots can join
beside the simulated ones: they read wall time while the simulated robots
read the simulation's clock. A simulated robot has no motors to report on,
so the web and XR panels show no motor health for it.

| Robot | MuJoCo | Isaac Sim | Waldo | Rendered cameras |
|---|---|---|---|---|
| OpenArm v1 | One robot | Any number | Not in its catalogue | No |
| OpenArm v2 | One robot | Any number | Any number | Yes |

Waldo opens a world that stands no robot of its own (`stage`) and Isaac Sim
an empty stage; every robot brings the model its fragment names. A rendered
camera rig is one robot's: Isaac Sim and Waldo render one per robot that
selects it, MuJoCo renders the one robot's.

Isaac Sim requires a supported NVIDIA GPU. XR requires a reachable HTTPS
endpoint and a headset for operator control.

Each simulation fragment declares the clock domain `simulation`, supplied by
`simulation_inst`, and every instance of the simulated robot binds to it with
`framework: { clock: "simulation" }`. The binding travels in the launch, so a
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
`--with alpha.xr_commander` selects a file copy's own axis. Values are
JSON5; the order of application is fragment values and adjustments, launcher
adjustments, the entry's then the copy's adjustments, then the entry's
arguments and the copy's over them. The [OpenArm guide](openarm/README.md#commanders-recording-and-cameras)
shows a second physical v2 on one host taking its own CAN bindings,
commander port and dataset directory. The launch
snapshots its launcher and fragment files; edits take effect on the next
launch.

## SO-101

The SO-101 is the `so101` option of `fleet.json5`: one copy runs the
follower, the backbone and the leader arm as its commander, its ids under
the copy's name (`alpha_follower_inst`, `alpha_backbone_inst`,
`alpha_commander_inst`); the [setup guide](so101/README.md) covers the host
prerequisites.

```sh
peppy stack launch fleet
peppy stack join so101 -i alpha
peppy stack join so101 -i alpha --with xr_commander,lerobot_recorder,cameras
```

## Inspecting and testing

```sh
peppy stack resolve openarm_simulation --with mujoco --join openarm_v2_sim --join-name bravo --join-with xr_commander
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
deployed copy's as launch words, `alpha.camera_rig=cameras_sim` among them,
and every copy a fleet can add as a join: plain, the way an operator types
it, and with its axes as join words. It validates every admitted combination
and launches a subset of the ones its runner can run, joining and removing
the planned copy: the launches that, between them, run every configured node
instance those combinations deploy and every pair of instances any of them
runs side by side, with every launcher launched at least once and every
robot joined plain at least once. The launches run one after the other in
one job, on one daemon, the stack reset between them. A pull request that
changes launcher files alone launches from the combinations whose resolved
plan differs from the base tree's; the run summary names every combination
left out and the launches that stand for it.

A joined copy comes up beside the copies the file deploys, so a launch of
`openarm_simulation_mcp` under Waldo ends with two MCP robots side by side,
both preferring port 8900. The launch job holds the joined copy to the
instances `peppy stack resolve` previewed for it, so a plain join that comes
up under the fragment's default commander, where the launcher's entry gives
it another, fails the launch. A stack deploying a node the
[single-robot file](.github/single-robot-nodes.json5) names is the exception,
MuJoCo today: there the file's copies make way for the joined one first, and
the join is planned only where they can. `stack remove` keeps a copy the
stack links to, and the summary lists those joins with the links that hold
the copy. The [skip file](.github/unlaunchable-nodes.json5) lists the
hardware the runner lacks. Structural checks and runtime startup checks are
separate results.

## Configuration ownership

| Location | Owns |
|---|---|
| `fleet.json5`, `openarm/openarm_simulation.json5` | The simulation axis, the robot options, and what the file deploys |
| `openarm/fragments/control_common.json5` | The initializer and backbone every OpenArm shares, with the commander and recorder wiring into both |
| `openarm/fragments/openarm_v1.json5`, `openarm_v2.json5`, `openarm_v1_sim.json5`, `openarm_v2_sim.json5` | One robot each: its limbs, or the simulation slots its control leads, the control it selects, the robot commander, recorder and camera rig axes, its generation, speed cap, commander tuning, dataset labels, and the model the simulation stands for it |
| `so101/fragments/so101.json5` | The SO-101 robot, on the same pattern |
| `openarm/fragments/cameras.json5`, `cameras_sim.json5` | The physical and rendered camera rigs |
| `openarm/fragments/ai_brain.json5` | The environment aware action layer beside a robot commander, with its MCP server |
| `openarm/fragments/mcp_commander.json5` | The robot's MCP surface, the same on hardware and in simulation: its moves on the backbone and its three cameras on the copy's rig |
| `robot_commanders/fragments/` | Reusable robot commanders, with robot tuning supplied by the robot fragments |
| `recording/fragments/` | Reusable recorder deployment and record-button attachment |
| `simulation/fragments/` | The simulations, the Isaac viewer, the scene commander, with the lighting and materials slots it binds on Waldo, the simulated world's MCP endpoint (`mcp_scene_commander.json5`), and `none.json5`, the empty option of an axis a launch can switch off |
| `mcp/` | The launchers whose command surface is MCP, the `simulation_mcp` axis that serves the simulated world, and the guide to their endpoints and clients |

OpenArm's web commander and MCP commander both reference OpenArm
interfaces, so their fragments stay under `openarm/fragments/`. XR is shared
by both robots. The simulated world's MCP endpoint binds the simulation and
nothing of a robot, so its fragment sits under `simulation/fragments/`
beside the browser scene commander. A launcher whose command surface is MCP
lives under `mcp/` and composes the same robot and simulation fragments as
the others.

Fragment paths are relative to the file that names them: the launcher's
directory for its options, the fragment's for the options of its own axes.
A repository launcher can reference siblings, such as
`../simulation/fragments/mujoco.json5`. The nearest valid
`peppy_repository.json5` defines the boundary; parent traversal and symlinks
must stay within it. Standalone launchers are confined to their own
directory. A fragment file is named for the option that selects it, and a
simulated variant of an option takes the `_sim` suffix: `openarm_v2` and
`openarm_v2_sim`, `cameras` and `cameras_sim`.

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
in `deployments`) and the matching nodes-hub release with `openarm_initializer`
and refreshed repository entries for the scene commander and Isaac viewer.
Refresh repository caches after upgrading. Rebuild application nodes with the matching SDK. Set the CI
`PEPPY_VERSION` variable to the compatible release.
