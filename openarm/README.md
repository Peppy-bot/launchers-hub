# openarm launchers

Launchers for the OpenArm bimanual teleop stack, one per hardware generation, backend, and operator surface. All of them bring up the same core node graph (one robot_initializer, two arms, two grippers, one backbone, one leader); they differ in which implementation fills each slot (a slot is a named attachment point a node declares; the launcher's `links` wire slots together, see [docs.peppy.bot](https://docs.peppy.bot)), which `hardware_version` the nodes receive, who leads, and whether any cameras are bound:

| Launcher | Generation | Runs against | Led by |
|---|---|---|---|
| `openarm_v1_teleop.json5` | v1.0 | the real robot | browser panel |
| `openarm_v1_teleop_isaac.json5` | v1.0 | Isaac Sim | browser panel |
| `openarm_v1_teleop_mujoco.json5` | v1.0 | MuJoCo | browser panel |
| `openarm_v2_teleop.json5` | v2.0 | the real robot | browser panel |
| `openarm_v2_teleop_isaac.json5` | v2.0 | Isaac Sim | browser panel |
| `openarm_v2_teleop_mujoco.json5` | v2.0 | MuJoCo | browser panel |

## Who leads, and in which space

The backbone follows exactly one kind of upstream arm command, named by its required `upstream_mode` argument, and subscribes only that kind of arm slot (gripper and posture slots are read under either mode):

- `"joints"` - `openarm_commander` (the browser panel) streams joint setpoints on `joint_link`. Every panel-led launcher above.
- `"pose"` - `xr_commander` streams an end-effector pose per hand on `pose_link`, and the backbone solves it. The `_vr_` launcher.

One or the other, never both: a backbone reading two command authorities for one arm is not a state the mode can express. An arm slot of the kind the mode does *not* name would never be read, so linking one refuses the launch, naming every offending slot.

The headset launchers run without `openarm_commander` entirely. `governor_control` is an optional backbone feature, since not every leader can produce it (`xr_commander` is robot-agnostic, so it never will): with no producer bound, the governor runs on the backbone's launch-time band, enable, and EE-speed cap for the whole session. To retune, edit the backbone arguments and relaunch it, or use a panel-led launcher.

The v2 record launchers are the same teleop graphs plus `lerobot_recorder`; see the recorder's README in nodes-hub for the dataset workflow. The real-robot variants also add the three cameras, whose device paths come from `rules/99-openarm-cameras.rules` (install it per the file's header).

| Launcher | Generation | Runs against | Led by | Episodes driven from |
|---|---|---|---|---|
| `openarm_v2_teleop_record.json5` | v2.0 | the real robot, with cameras | browser panel | the panel's Record button |
| `openarm_v2_teleop_record_isaac.json5` | v2.0 | Isaac Sim | browser panel | the panel's Record button |
| `openarm_v2_teleop_record_mujoco.json5` | v2.0 | MuJoCo | browser panel | the panel's Record button |
| `openarm_v2_teleop_vr_record.json5` | v2.0 | the real robot, with cameras | WebXR headset | the left controller's X and Y |

The two real-robot record launchers are the same stack under different leaders, so a dataset recorded through the headset and one recorded from the panel carry the same cameras under the same names.

## Before launching

The node repos must be registered with the daemon and the nodes built. The top-level README in `openarm-nodes` walks through the whole sequence; in short, every repo gets a `peppy repo add` (followed by `peppy repo refresh`), and every node gets a `peppy node add <path> -sb`.

The `_vr_` launcher additionally needs `xr_commander`, which lives in the separate [nodes-hub](https://github.com/Peppy-bot/nodes-hub) repo: register it the same way, then `peppy node add /path/to/ws/nodes-hub/xr_commander -sb`. It also deploys the camera nodes from that repo:

```sh
peppy node add /path/to/ws/nodes-hub/uvc_camera/linux -sb
peppy node add /path/to/ws/nodes-hub/zed_camera -sb
```

Then verify:

```sh
peppy stack list
```

Every node the launcher references should show `Ready` in the STAGE column.

## Launch

```sh
peppy stack launch ./openarm_v1_teleop_mujoco.json5
```

The launcher starts the instances in dependency order (sim first, then arms and grippers, then backbone, then the UI) and wires the links between them. Once it prints `Launch complete`:

- open **http://localhost:8765** for the control panel, one slider per joint (panel-led launchers)
- open **http://localhost:8080** for the MuJoCo viewer (for Isaac, connect with the [livestream client](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/manual_livestream_clients.html) instead)

In the panel-led launchers, move a slider, press **Send**, and watch the arm follow; the VR launchers are driven from the headset instead (next section). To stop everything, Ctrl-C the launch terminal, or stop instances individually with `peppy node stop <instance_id>`.

## VR headset

The `_vr_` launcher is led from a WebXR headset and runs no browser panel; the governor keeps its launch-time settings. After `Launch complete`:

1. Reach the WebXR page from the headset (the node's log prints the exact URLs). Wireless: open the https URL and click through the self-signed warning once. Over USB: put the headset in developer mode, `adb reverse tcp:4443 tcp:4443`, and open `https://localhost:4443`. Both paths are detailed in `xr_commander`'s README.
2. Press **Enter VR**.
3. Hold a **grip button** and move that hand: the matching arm follows. Release and it holds. The **trigger** drives that hand's gripper while the grip is held.
4. The face buttons run the whole-robot posture moves: **A** (lower) goes home, **B** goes ready; squeezing either grip cancels the move.

5. The left controller's face buttons drive the recorder: **X** starts an episode and the next press stops and saves it; holding **Y** for a second finishes the session and opens a fresh dataset.

`openarm_v2_teleop_vr_record.json5` binds both wrist cameras and the ZED chest camera. Each camera streams into the headset under its instance id, so `wrist_left` and `wrist_right` anchor their panels to the controllers while `chest` floats.

## Real robot

`openarm_v1_teleop.json5`, `openarm_v2_teleop.json5` and `openarm_v2_teleop_record.json5` (panel-led, UI at **http://localhost:8765**) and `openarm_v2_teleop_vr_record.json5` (headset-led, no panel) drive the physical arms over CAN instead of a sim. Before launching, bring the buses up. The v1 launcher wires `can0`/`can1`; the v2 launchers use the `left_arm`/`right_arm` channel names a host udev rule (`80-openarm-can.rules`) gives the PEAK adapter:

```sh
sudo ip link set left_arm up type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set right_arm up type can bitrate 1000000 dbitrate 5000000 fd on
```

The arms load the description matching their `hardware_version` for the gravity/Coriolis feedforward; it is baked into the `openarm_arm` container, so unlike the rate and CAN arguments there is no host path to set. Adjust `can_interface`, `control_rate_hz`, or `state_rate_hz` in the launcher if your wiring or loop budget differs.

## Troubleshooting

**`deployment <id>: Dependencies missing from nodes cache (...): <name>:<tag>`**
The repo providing that node isn't registered with the daemon. Run `peppy repo add /path/to/<repo>` and `peppy repo refresh`, then launch again.

**The launch stalls on the sim engine build**
The first build pulls the sim base image and can outlive the daemon's idle timeout. Build it once beforehand with a longer timeout, then launch:

```sh
peppy node add /path/to/ws/openarm-nodes/openarm_sim_isaac -sb --idle-timeout 18000
```

**Everything launches but the arms don't respond**
The sim keeps loading after `Launch complete`, and Isaac can take a minute. Check instance health with `peppy stack list` and watch the sim's log with `peppy node info openarm_sim_<engine>:v1`.

**The Isaac stream is a black screen**
Stop the stack, clear the shader cache with `rm -rf ~/.cache/isaac-sim`, and launch again.

**The headset shows the page but "Enter VR" is missing**
WebXR needs a secure context, so the node self-generates a per-machine TLS certificate and always serves HTTPS; click through the browser's self-signed warning once. Over the network, open the https URL from the startup log. Over USB, `adb reverse tcp:4443 tcp:4443` and open `https://localhost:4443`.

**The headset is connected but neither arm moves**
Hold a grip button: it is the deadman, per hand, and with it released the node publishes nothing at all so the arms hold. If holding it does nothing, check the backbone's startup log line for which upstream mode it is following: a `"joints"` backbone reads only the panel's joint slots and a `"pose"` backbone only the headset's pose slots. A leader wired to the off-mode slots never reaches launch, so what remains is a leader that is publishing nothing: check the headset link and the grip in the node's status panel.
