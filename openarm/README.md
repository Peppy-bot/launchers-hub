# OpenArm launchers and fragments

Each OpenArm is one fragment: `openarm_v1` and `openarm_v2` drive the
physical robot over CAN, `openarm_v1_sim` and `openarm_v2_sim` join a
simulation that runs their limbs. Each robot fragment selects the shared
OpenArm control
([fragments/control_common.json5](fragments/control_common.json5)), the
robot's initializer and backbone, on its `control` axis, and declares the
robot's own axes: the robot commander, the recorder, the camera rig and,
on a v2, the brain. The initializer is `robot_initializer`, the identity node every
robot runs, and the fragment names its model, `openarm_v1` or `openarm_v2`.
The backbone names its downstream links after the limbs, `left_arm`,
`right_arm`, `left_gripper` and `right_gripper`: on hardware each pairs
with its driver, in a simulation with the simulation's `arms` or `grippers`
slot. The repository's launchers compose the fragments:
[physical.json5](../physical.json5) is the physical robots, each joined by
name and placed on its machine,
[openarm_simulation.json5](openarm_simulation.json5) runs one simulation
and simulated robots as copies, `alpha` listed, another named at launch
with `--join OPTION:NAME` or joined later,
[../so101/so101_simulation.json5](../so101/so101_simulation.json5) is the
same launcher with the simulated SO-101 first and the simulated OpenArms as
options, and [../mcp/simulation_mcp.json5](../mcp/simulation_mcp.json5) is
`openarm_simulation` with every robot driven over MCP, `alpha` listed.
`simulation_mcp` deploys the robots' MCP endpoint, the other launchers
serve it with `--with robot_control`, and every robot is listed on it. A copy's ids
carry its name: `alpha_backbone_inst`. See
the [repository README](../Readme.md) for the option table, limits, and
configuration ownership.

## Real hardware

Bring up the CAN interfaces before launching a robot. The v1 fragment uses
`can0` and `can1`. The v2 fragment uses the `left_arm` and `right_arm`
names assigned by the host's `80-openarm-can.rules`:

```sh
sudo ip link set left_arm up type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set right_arm up type can bitrate 1000000 dbitrate 5000000 fd on
```

For v1, use `can0` and `can1` in those commands. Check the actual adapters
and hardware generation before enabling the buses.

```sh
peppy repo refresh
peppy stack launch physical
peppy stack join openarm_v2 -i alpha                     # a v2 with the browser panel, on this machine
peppy stack join openarm_v2 -i alpha --place jetson-1
peppy stack join openarm_v1 -i bravo --place jetson-2
peppy stack list
peppy stack remove alpha
peppy stack reset --federated
```

The browser panel is served on port 8765 by default; the launch prints its
URLs under `Web pages:`, one per address of the host running it, and
`peppy stack list` shows them again under `Instance endpoints`. Launch returns
after startup. `stack remove alpha` stops every instance owned by alpha;
`stack reset --federated` tears down the stack.

The hardware generation applies to the arms, grippers, backbone and browser
panel, as `hardware_version`; the initializer takes the same choice as its
`model`, `openarm_v1` or `openarm_v2`. A mismatched gripper setting has
these physical consequences:

| Rig | Incorrect setting | Consequence |
|---|---|---|
| v1 left | `v2` | drives backwards into the closed stop |
| v1 right | `v2` | drives 50% past the open stop |
| v2 left | `v1` | drives backwards into the closed stop |
| v2 right | `v1` | opens two thirds of the way and reports fully open |

The same setting selects MIT hold for v1 and POS_FORCE control for v2.
Select the option matching the physical robot.

## Commanders, recording, and cameras

These are the robot's own axes, declared by its fragments, and selected per
copy: with `with:` in the file, or `--with` on `stack join`:

```sh
peppy stack join openarm_v2 -i bravo --with xr_commander,lerobot_recorder,cameras
peppy stack join openarm_v1 -i charlie --with xr_commander,cameras --place jetson-2
peppy stack join openarm_v2 -i delta --with web_commander,ai_brain
peppy stack join openarm_v2 -i echo --with ker_commander
```

The default web commander streams joint setpoints. XR streams end-effector
poses and selects the backbone's pose mode. MCP drives the robot by name
on the stack's `robot_control:v1`
endpoint, the robots' own surface, where every call names its robot: the
robot's identity, the backbone's discrete motion actions, its limb state
and collision readout, and the cameras of the copy's rig, their latest
frames and the chest's depth as resources and their stream info, exposure,
gain and white balance as tools.

The endpoint is the launcher's `robot_control` axis, deployed by `simulation_mcp`
and selected with `--with robot_control` on the other launchers. Every robot beside
it is listed with its
identity, limb state and collision readout under any commander, by the
shared control's `add_links` on `robot_control_inst`, and with its brain and recorder
whenever they run. `mcp_commander`
([../robot_commanders/fragments/mcp_commander.json5](../robot_commanders/fragments/mcp_commander.json5))
is one option, shared by every robot and the same on hardware and in
simulation: no node of its own and one `add_links` adding the backbone's
moves; it requires the endpoint. The backbone's leader sockets stand
vacant unless a leader releases them, so the option touches the backbone
not at all. The rig, `cameras` on a physical robot and `cameras_sim` on a
simulated v2, lists its cameras under it; a robot without a rig is listed
with no camera. No simulation renders a rig on v1 links, so a simulated v1
does not offer it; a physical v1 does, with `cameras`.

The KER leader, on the v2 robots (physical or simulated), streams joint
setpoints from enactic's motorless leader arm.

The robots' endpoint is `http://127.0.0.1:8900/robot_control/v1/mcp`, the
stack's, the same for every robot; the launch prints every exposure's URL
under `MCP endpoints:`, labeled `<exposure>_<tag>`, and `stack list`
reports them under `Instance endpoints`. Recording starts from the web or
XR commander's record button, or over MCP through `recorder.record_episode`.

What exists only because the world is simulated, the scene's objects, its
lighting and its materials, is a second endpoint a model uses to set the
world up, `http://127.0.0.1:8902/simulation/v1/mcp` (`simulation:v1`). It is
not the robot's: the `simulation_mcp` launcher deploys it on an axis of
its own, `world_control`, bound to the simulation alone, under Waldo, the one
simulation implementing the object controls, lighting and materials
contracts. The
endpoints, the
client setup and the move from simulation to the real robot are in the
[MCP guide](../mcp/README.md):

```sh
peppy stack launch simulation_mcp                                                     # Waldo, both endpoints, alpha over MCP
peppy stack launch openarm_simulation --with robot_control,alpha.mcp_commander,alpha.cameras_sim
peppy stack launch physical --with robot_control
peppy stack join openarm_v1 -i charlie --with mcp_commander,cameras --place jetson-2
```

A v2 copy's `brain` axis adds `ai_brain`, the environment aware action layer
serving `item_perception` and `item_manipulation` over the backbone's
`limb_motion`. It composes with the selected commander: the operator and
the brain send the same kind of goal to the same producer. Under
`mcp_commander` the brain's tools are listed under the robot's name on the
`robot_control` endpoint, beside the direct moves. The recorder's episodes
are listed there under any commander.

Install [the camera udev rules](rules/99-openarm-cameras.rules), following their
header, before selecting `cameras`. That option brings up both wrist cameras
and the chest camera, attaches them to the recorder or headset, and lists
them on the endpoint under `mcp_commander`.

For XR, open one of the HTTPS URLs the launch prints under `Web pages:` and
accept its self-signed certificate. Over USB, enable developer mode and run
`adb reverse tcp:4443 tcp:4443`, then open `https://localhost:4443`.

Hold a grip button to move the matching arm; release it to hold position.
The trigger controls that hand's gripper while grip is held. A moves home,
B moves ready, and either grip cancels a posture move. With recording selected,
X starts or saves an episode; holding Y for one second finishes the session.
Set the demonstration task at `https://<host>:4443/task` before recording.

For the KER, install [the KER udev rule](rules/60-openarm-ker.rules) on the
machine the copy is placed on, following its header, and zero the KER on its
calibration jig first (the
`openarm_ker` README has that procedure). Plug the KER into the machine the
copy is placed on, since the commander runs there. Squeeze an arm's trigger
down to a fifth open, near shut, to engage that arm; from then on the arm and
its gripper track the KER. Engagement latches: releasing the trigger opens
that gripper and the arm keeps tracking. The same lever drives both, so a
released trigger holds its gripper half open, a full squeeze closes it, and an
arm engages with its gripper near shut. Unplugging the KER pauses it, holding
both arms where they are and still energized; `peppy stack remove echo` ends
the session and the arms disable, going limp where they are, so bring them low
first. After a pause, plug the KER back in, then release a trigger and squeeze
it again to re-engage. The node reads the hardware 2.x channel layout, so any
2.x unit runs on the arguments in the fragment.

The KER replaces the browser panel, so a KER session has no alerts or
motor-health readout and no record button; with the endpoint on,
`recorder.record_episode` records a KER session. The governor keeps the
backbone's launch-time band and caps.

For two robots on one host, assign the second copy's CAN interfaces,
commander port, and dataset directory:

```sh
peppy stack join openarm_v2 -i bravo --with lerobot_recorder \
  --set-arguments 'left_arm_inst.can_interface="bravo_left"' \
  --set-arguments 'left_gripper_inst.can_interface="bravo_left"' \
  --set-arguments 'right_arm_inst.can_interface="bravo_right"' \
  --set-arguments 'right_gripper_inst.can_interface="bravo_right"' \
  --set-arguments 'commander_inst.http_port=8767' \
  --set-arguments 'recorder_inst.storage_root="/tmp/lerobot_bravo"'
```

Arguments name the instance as the fragment writes it, such as
`left_arm_inst.can_interface`; the copy's name prefixes the running instance.

## Who leads, and in which space

The backbone follows exactly one kind of upstream arm command, named by its required `upstream_mode` argument, and subscribes only that kind of arm slot (gripper and posture slots are read under either mode):

- `"joints"` - `openarm_web_commander` (the browser panel) streams joint setpoints on `joint_link`. The commander every robot fragment deploys. `openarm_ker` (the KER leader) streams them the same way.
- `"pose"` - `xr_commander` streams an end-effector pose per hand on `pose_link`, and the backbone solves it. The shared control selects the mode and releases the pose sockets under that selection.
- Nobody streams - `mcp_commander` drives the backbone through discrete actions only: the whole-robot posture moves and the per-limb arm and gripper moves it exposes as tools, beside the cameras it publishes from the rig. `upstream_mode` stays `"joints"`, all six leader sockets keep the control's vacancies, and the governor keeps its launch-time band, enable, and EE-speed caps for the whole session, as under the headset.

The control leaves every leader socket vacant and `governor_control` unproduced; a leader that streams releases the sockets it drives from its own fragment (the panel and the KER the four joint sockets, the panel binding `governor_control` too), and the headset selection switches the backbone to pose mode and releases the pose sockets.

One or the other, never both: a backbone reading two command authorities for one arm is not a state the mode can express. An arm slot of the kind the mode does *not* name would never be read, so linking one refuses the launch, naming every offending slot.

The `xr_commander` and `ker_commander` selections run without `openarm_web_commander` entirely. `governor_control` is an optional backbone feature, since not every leader can produce it (`xr_commander` is robot-agnostic and the KER produces only motion, so neither ever will): with no producer bound, the governor runs on the backbone's launch-time band, enable, and EE-speed cap for the whole session. To retune, edit the backbone arguments and relaunch, or use the panel.

Recording adds `lerobot_recorder` (see the recorder's README in nodes-hub for the dataset workflow). The `cameras` option adds the three cameras, whose device paths come from `rules/99-openarm-cameras.rules` (install it per the file's header); the headset retunes them for in-headset panels as part of its own selection.

## Simulation

`openarm_simulation.json5` runs the simulation as the stack and the robot
as a copy. The file deploys Waldo and `alpha`, a v2 with the browser
commander; `--join OPTION:NAME` names another robot at launch and
`--with robot_control` serves the robots' MCP endpoint:

```sh
peppy stack launch openarm_simulation                                   # Waldo and alpha
peppy stack launch openarm_simulation --with mujoco                     # the same copy in MuJoCo
peppy stack launch openarm_simulation --with isaac_sim
peppy stack launch openarm_simulation --join so101_sim:charlo           # alpha and an SO-101
peppy stack remove alpha                                                # the simulation keeps running
```

Every simulation stands as many robots as the machines can run, of any
model, each joined copy bringing its own, the simulation running throughout.
The `robot` axis offers every simulated robot, so an SO-101
([so101_sim](../so101/README.md#simulation)) joins beside the OpenArm:

```sh
peppy stack launch openarm_simulation --with isaac_sim
peppy stack join openarm_v2_sim -i bravo --with xr_commander
peppy stack join openarm_v1_sim -i charlie
peppy stack join so101_sim -i charlo                           # an SO-101 beside the OpenArm alpha
peppy stack join openarm_v2_sim -i delta --with ker_commander  # the KER against a simulated follower
peppy stack remove bravo
```

Every simulation stands any number of robots, so a join comes up beside
`alpha` whichever one runs. A join cannot turn rendering on, so a robot
joined to a launch whose copies select no rig has no rendered camera.

Every simulation stands a v1 or a v2, the model the robot's fragment
names: MuJoCo and Isaac Sim from their own images, Waldo from its catalogue
(from private-nodes-hub), which carries both, so a v1 and a v2 share one
Waldo world. For rendered wrist/chest streams and recording, the launch
words select them on the copy it names:

```sh
peppy stack launch openarm_simulation --with alpha.xr_commander,alpha.lerobot_recorder,alpha.cameras_sim
```

Rendered cameras require v2, so only `openarm_v2_sim` declares the rig,
[fragments/cameras_sim.json5](fragments/cameras_sim.json5); the camera
geometry lives in the simulation's OpenArm configuration and matches the v2
link layout. Real camera devices remain configured in
[fragments/cameras.json5](fragments/cameras.json5).

What a copy may change on the running simulation, and how a robot reaches
a simulation, are in the
[repository README](../Readme.md#what-a-copy-can-change).

Isaac Sim and Waldo declare a scene commander, selected at launch with
`--with web_scene_commander`. It drives the simulation's `scene_manipulation`
contract and reads its `object_state` for the list of spawned objects. On
Waldo its page also carries a lighting panel and a materials panel, bound
to the engine's `scene_lighting` and `scene_materials`, and with rendered
rigs running (`alpha.camera_rig=cameras_sim`, which needs a consumer such as
`alpha.recorder=lerobot_recorder`) a camera panel listing every robot's
rendered cameras with their device profiles, read from Waldo's
`sim_camera_control`; the relays forward their own camera controls to
Waldo's device models too. MuJoCo and Isaac Sim model no camera response,
so their page carries no camera panel and every camera control refuses.
Waldo serves its HTTPS page on `viewer_port` (8080, at the URLs the launch
prints under `Web pages:`; accept the self-signed certificate once). The page
carries the engine's 3D viewer of the running world, its `sim_inspector`
plugin, and the "Start camera" panel, its `hand_teleop` plugin: webcam hand
tracking drives an arm of the robot the panel chooses (the first standing
when none is chosen), ahead of that robot's pairing while a hand is tracked.
Its `robot_names` plugin has the viewer write `<robot>@<core node>` over every
robot (`beta@cn-funky-animal` for `peppy stack join openarm_v2_sim -i beta` on
the core node `cn-funky-animal`), which tells the robots of one world apart,
and puts the button hiding and showing those names on the page.
The fragment
runs the three plugins whatever the launch selects (`plugins:
"robot_names,hand_teleop,sim_inspector"`), so the viewer is on from the first launch
of every launcher deploying Waldo, and the inspector answers the scene
commander's `scene_manipulation` and `object_state` calls. The viewer needs a
browser with WebGPU and WebTransport (Chrome or Edge 119+) and streams
over WebTransport on UDP `viewer_port` beside the HTTPS port, so both must
reach the simulation's machine.

## Inspection and builds

```sh
peppy stack resolve openarm_simulation --with mujoco
peppy stack resolve physical --then-join openarm_v1 --then-join-name bravo --then-join-with xr_commander
peppy stack resolve openarm_simulation --with isaac_sim,web_scene_commander --then-join openarm_v2_sim --then-join-name bravo --then-join-with xr_commander
peppy stack resolve simulation_mcp --with web_scene_commander
peppy node add /path/to/ws/nodes-hub/robot_initializer -sb
```

Use `--node-build-idle-timeout-secs 18000` on launch or join for long first
builds. `stack list` reports health; join failures include the node's log
paths and machine names. See the [repository README](../Readme.md) for release
requirements and the full option table.

## Troubleshooting

**`deployment <id>: Dependencies missing from nodes cache (...): <name>:<tag>`**
The repo providing that node isn't registered with the daemon. Run `peppy repo add /path/to/<repo>` and `peppy repo refresh`, then launch again.

**The launch stalls on the simulation's build**
The first build pulls the sim base image and can outlive the daemon's idle timeout. Build it once beforehand with a longer timeout, then launch:

```sh
peppy node add /path/to/ws/nodes-hub/sim_isaac -sb --idle-timeout 18000
```

**Everything launches but the arms don't respond**
The simulation keeps loading after `Launch complete`, and Isaac can take a minute. Check instance health with `peppy stack list` and watch its log with `peppy node info sim_mujoco:v1`, `sim_isaac:v1` or `waldo:v1`.

**The Isaac stream is a black screen**
Stop the stack, clear the shader cache with `rm -rf ~/.cache/isaac-sim`, and launch again.

**The headset shows the page but "Enter VR" is missing**
WebXR needs a secure context, so the node self-generates a per-machine TLS certificate and always serves HTTPS; click through the browser's self-signed warning once. Over the network, open one of the https URLs the launch printed under `Web pages:` (`peppy stack list` shows them again). Over USB, `adb reverse tcp:4443 tcp:4443` and open `https://localhost:4443`.

**The KER is plugged in but neither arm moves**
Squeeze a trigger down to a fifth open, near shut: engagement is per arm, and the node publishes nothing for an arm that has never been squeezed, so the arms hold. Watch the node's log. "KER connected: fw .. hw .." means the link is up; if no "KER left arm engaged, tracking the leader" line follows a squeeze, the squeeze never reached `engage_trigger_opening`. A "KER connection lost (open: ...)" line names the cause, and repeats when the cause changes or after the link streams again: an attached-but-unopenable device needs the udev rule, and on a host reached over SSH its group matchers as well, since uaccess grants only a local seat; and no device at all means the KER is unplugged, switched off, or running a firmware that streams over its serial device; `lsusb -d 303a:` shows `303a:1001` for the last and nothing for the first two. A trigger held from before the node started, or across a stall or reconnect, stays disengaged by design: release it and squeeze again.

**The headset is connected but neither arm moves**
Hold a grip button: it is the deadman, per hand, and with it released the node publishes nothing at all so the arms hold. If holding it does nothing, check the backbone's startup log line for which upstream mode it is following: a `"joints"` backbone reads only the panel's joint slots and a `"pose"` backbone only the headset's pose slots. A leader wired to the off-mode slots never reaches launch, so what remains is a leader that is publishing nothing: check the headset link and the grip in the node's status panel.
