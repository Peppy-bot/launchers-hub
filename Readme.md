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

## Launchers

Two launchers run robots as copies, each named, its ids minted under its
name: `alpha_left_arm_inst`, `alpha_backbone_inst`, `alpha_commander_inst`.
[fleet.json5](fleet.json5) deploys nothing: a simulation is selected at
launch, or none, and every robot, physical or simulated, is joined by name,
a physical one placed on its machine by that name.
[openarm/openarm_simulation.json5](openarm/openarm_simulation.json5) runs
one simulation, Waldo unless a launch word selects another, and deploys the
copy `alpha`, a simulated OpenArm v2 with the browser commander. The robot
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
```

Waldo and Isaac Sim seat any number of robots at once, each joined copy
taking a seat of its own with `--with sim_seat`; MuJoCo carries the one
robot its own limb slots stand.

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
| `simulation` | `openarm_simulation.json5` (`one`) and `fleet.json5` (`zero_or_one`) | `waldo` (deployed by `openarm_simulation.json5`), `mujoco`, `isaac_sim` |
| `scene_commander` | Isaac Sim and Waldo | `web_scene_commander`; on Waldo it also serves the engine's 3D viewer |
| `robot` | both launchers | `openarm_v1_sim`, `openarm_v2_sim` in `openarm_simulation.json5`; those, `openarm_v1`, `openarm_v2` and `so101` in `fleet.json5` |
| `control` | the robot | `control_common` (deployed): the shared initializer and backbone |
| `robot_commander` | the robot | `web_commander` (deployed), `xr_commander`, `mcp_commander` |
| `recorder` | the robot | `lerobot_recorder`; requires web or XR |
| `camera_rig` | the robot | `cameras` on a physical robot, `cameras_sim` on a simulated v2; requires a recorder or XR |
| `brain` | the v2 robots | `ai_brain`, with its MCP server on port 8901 |

A copy selects one option per axis of its robot. `stack resolve` previews any
launch, and a join onto it, without starting nodes:

```sh
peppy stack resolve openarm_simulation --with mujoco
peppy stack resolve fleet --with mujoco --join openarm_v2_sim --join-name bravo --join-with xr_commander,lerobot_recorder
```

### What a copy can change

A copy's fragment may write to a stack instance: a v1 copy sets the
simulation's hardware generation, and the rendered camera rig turns its
rendering on. Copies deployed together must agree on every field they both
write, and a join may write to a stack instance only what already runs
there, except that its relays may pair into the slots the instance declares
vacant: the relay's own `links` name the slot, and the copy's fragment
releases the vacancy with `unset_links` on the instance. A join that would
change a running instance is refused, naming the
instance and each field that differs, so a v1 simulation and rendered
cameras are selected in the file at launch. The
[planner](.github/scripts/launcher_combinations.py) reports such copies as
launch-only.

### How a robot reaches a simulation

A robot is wired to a simulation one of two ways, chosen per copy on the
`wiring` axis. On `sim_limbs` the robot's relays pair into the simulation's
own limb slots, which the one robot that simulation stands fills. On
`sim_seat` the robot's initializer takes a seat through the
`simulation_robot` contract, standing the robot with its own model, and the
relays pair into the seat's limbs; the simulation tells its robots apart by
the seat that commands them, so a stack holds as many as its machines can
run. The simulation stays up when a robot is removed; `stack reset` stops
everything. Physical robots can join beside the simulated ones in the
default wall-time daemon mode.

| Robot | MuJoCo | Isaac Sim | Waldo | Rendered cameras |
|---|---|---|---|---|
| OpenArm v1 | Limbs | Limbs, seat | Seat | No |
| OpenArm v2 | Limbs | Limbs, seat | Limbs, seat | Yes |

A seated robot in Waldo stands in a world that carries none (`stage`); a
robot on Waldo's own limb slots is the robot its world stands. Isaac Sim
opens an empty stage and stands the model a limb-wired robot names. Its
rendered cameras hang off the robot the simulation stands, so a camera rig
there runs on `sim_limbs`.

Isaac Sim requires a supported NVIDIA GPU. XR requires a reachable HTTPS
endpoint and a headset for operator control. To run on the simulation's
clock, start every participating daemon with
`peppy service serve --clock-source sim`; clock consumers require a live
simulation-time source.

### Per-copy arguments

Colocated copies need distinct server ports, hardware interfaces, and
dataset directories. A copy's `arguments` in the file, or `--set-arguments`
on join, override its instances' arguments by the id written in the
fragment; `with` and `arguments` written on the entry itself apply to every
copy it lists, each copy's own winning per axis and per argument, and an
entry or a copy may carry `adjustments` with the fragment verbs, run after
the launcher's adjustments and before the copy's arguments. At launch,
`--with alpha.xr_commander` selects a file copy's own axis. Values are
JSON5; the order of application is fragment values and adjustments, launcher
adjustments, the entry's then the copy's adjustments, then the copy's
arguments. The [OpenArm guide](openarm/README.md#commanders-recording-and-cameras)
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
and every copy a fleet can add as a join, its axes as join words. It
validates the admitted combinations and launches the ones its runner can
run, joining and removing the planned copy. The
[skip file](.github/unlaunchable-nodes.json5) lists the hardware the runner
lacks. Structural checks and runtime startup checks are separate results.

## Configuration ownership

| Location | Owns |
|---|---|
| `fleet.json5`, `openarm/openarm_simulation.json5` | The simulation axis, the robot options, and what the file deploys |
| `openarm/fragments/control_common.json5` | The initializer and backbone every OpenArm shares, with the commander and recorder wiring into both |
| `openarm/fragments/openarm_v1.json5`, `openarm_v2.json5`, `openarm_v1_sim.json5`, `openarm_v2_sim.json5` | One robot each: its limbs or relays, the control it selects, the robot commander, recorder, camera rig and wiring axes, its generation, speed cap, commander tuning, dataset labels, and the model or world the simulation stands for it |
| `openarm/fragments/sim_relays.json5` | The four relays that stand in for a simulated OpenArm's CAN drivers, and both termini the `wiring` axis selects between |
| `so101/fragments/so101.json5` | The SO-101 robot, on the same pattern |
| `openarm/fragments/cameras.json5`, `cameras_sim.json5` | The physical and rendered camera rigs |
| `openarm/fragments/ai_brain.json5` | The environment aware action layer beside a robot commander, with its MCP server |
| `robot_commanders/fragments/` | Reusable robot commanders, with robot tuning supplied by the robot fragments |
| `recording/fragments/` | Reusable recorder deployment and record-button attachment |
| `simulation/fragments/` | The simulations, the Isaac viewer, and the scene commander |

OpenArm's web commander and MCP exposure both reference OpenArm interfaces,
so their fragments stay under `openarm/fragments/`. XR is shared by both
robots.

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
in `deployments`) and the matching nodes-hub release with `openarm_initializer`,
`openarm_sim_arm`, `openarm_sim_gripper`, and refreshed repository entries
for the scene commander and Isaac viewer. Refresh repository caches after
upgrading. Rebuild application nodes with the matching SDK. Set the CI
`PEPPY_VERSION` variable to the compatible release.
