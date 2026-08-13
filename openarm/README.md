# openarm launchers

Launchers for the OpenArm bimanual teleop stack: one base per hardware generation, with the robot backend, the operator surface, the recorder, and the cameras selected at launch time with `--with`. Every launch brings up the same core node graph (one robot_initializer, two arms, two grippers, one backbone, one leader); the options differ in which implementation fills each slot (a slot is a named attachment point a node declares; the launcher's `links` wire slots together, see [docs.peppy.bot](https://docs.peppy.bot)), and the base specializes the shared pieces for its generation.

```sh
peppy stack launch openarm_v2                                   # real robot, browser panel
peppy stack launch openarm_v2 --with=mujoco                     # MuJoCo, browser panel
peppy stack launch openarm_v2 --with=isaac_sim,lerobot_recorder # Isaac Sim, recording
peppy stack launch openarm_v2 --with=xr_commander,lerobot_recorder,cameras  # the headset session
peppy stack launch openarm_v1 --with=...                        # same axes on the v1 base
```

The four axes, one option per axis per launch:

| Axis | Options | Default | Fills |
|---|---|---|---|
| `robot` | `real`, `mujoco`, `isaac_sim` | `real` | the arms and grippers (real CAN drivers, or sim relays plus an engine) |
| `commander` | `web_commander`, `xr_commander` | `web_commander` | the leader (`commander_inst`) |
| `recorder` | `lerobot_recorder` | off (`optional`) | the dataset recorder and the record button |
| `cameras` | `cameras` | off (`optional`) | both wrist cameras and the chest camera |

Every axis left unselected takes its default, so the bare launch is the plain real-robot teleop. `peppy stack resolve openarm_v2 --with=...` prints the flattened launcher each selection produces, plus a report of every adjustment it applied and skipped.

The old per-variant launchers (`openarm_v2_teleop_mujoco`, `openarm_v2_teleop_vr_record`, and siblings) map one-to-one onto the invocations above.

## Who leads, and in which space

The backbone follows exactly one kind of upstream arm command, named by its required `upstream_mode` argument, and subscribes only that kind of arm slot (gripper and posture slots are read under either mode):

- `"joints"` - `openarm_commander` (the browser panel) streams joint setpoints on `joint_link`. The base's default.
- `"pose"` - `xr_commander` streams an end-effector pose per hand on `pose_link`, and the backbone solves it. The `xr_commander` fragment flips the mode and re-vacates the slots as part of being selected.

One or the other, never both: a backbone reading two command authorities for one arm is not a state the mode can express. An arm slot of the kind the mode does *not* name would never be read, so linking one refuses the launch, naming every offending slot.

The `xr_commander` selection runs without `openarm_commander` entirely. `governor_control` is an optional backbone feature, since not every leader can produce it (`xr_commander` is robot-agnostic, so it never will): with no producer bound, the governor runs on the backbone's launch-time band, enable, and EE-speed cap for the whole session. To retune, edit the backbone arguments and relaunch, or use the panel.

Recording adds `lerobot_recorder` (see the recorder's README in nodes-hub for the dataset workflow). The `cameras` option adds the three cameras, whose device paths come from `rules/99-openarm-cameras.rules` (install it per the file's header); the headset retunes them for in-headset panels as part of its own selection.

## The fragments

Each option's body lives in `fragments/`, one concern per file, and the bases reference them:

| Fragment | Carries |
|---|---|
| `sim_relays.json5` | the four engine-agnostic relays both sim options share, plus the sim's raised EE-speed cap |
| `mujoco_engine.json5` / `isaac_engine.json5` | the engine instance and the recorder's storage root for it |
| `web_commander.json5` / `xr_commander.json5` | the leader, plus (headset) the backbone's pose-mode flip and the camera retunes |
| `lerobot_recorder.json5` | the recorder and the record-button attach to whichever leader was selected |
| `cameras.json5` | the three cameras and their binds into whichever recorder was selected |

The bases (`openarm_v1.json5`, `openarm_v2.json5`) hold the invariant graph (initializer, backbone), each generation's real-hardware option inline, and the generation's `hardware_version` and dataset labels as base adjustments. A fragment is referenced by the option that wants it and is never a launchable stack of its own; `peppy repo index --check` validates every fragment and every legal selection.

## Before launching

The node repos must be registered with the daemon and the nodes built. The top-level README in `openarm-nodes` walks through the whole sequence; in short, every repo gets a `peppy repo add` (followed by `peppy repo refresh`), and every node gets a `peppy node add <path> -sb`.

The `xr_commander` option additionally needs `xr_commander`, which lives in the separate [nodes-hub](https://github.com/Peppy-bot/nodes-hub) repo: register it the same way, then `peppy node add /path/to/ws/nodes-hub/xr_commander -sb`. The `cameras` option deploys the camera nodes from that repo:

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
peppy stack launch openarm_v2 --with=mujoco
```

The launcher starts the instances in dependency order (sim first, then arms and grippers, then backbone, then the UI) and wires the links between them. Once it prints `Launch complete`:

- open **http://localhost:8765** for the control panel, one slider per joint (panel-led selections)
- open **http://localhost:8080** for the MuJoCo viewer (for Isaac, connect with the [livestream client](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/manual_livestream_clients.html) instead)

In the panel-led selections, move a slider, press **Send**, and watch the arm follow; the headset selections are driven from the headset instead (next section). To stop everything, Ctrl-C the launch terminal, or stop instances individually with `peppy node stop <instance_id>`.

## VR headset

The `xr_commander` selection is led from a WebXR headset and runs no browser panel; the governor keeps its launch-time settings. After `Launch complete`:

1. Reach the WebXR page from the headset (the node's log prints the exact URLs). Wireless: open the https URL and click through the self-signed warning once. Over USB: put the headset in developer mode, `adb reverse tcp:4443 tcp:4443`, and open `https://localhost:4443`. Both paths are detailed in `xr_commander`'s README.
2. Press **Enter VR**.
3. Hold a **grip button** and move that hand: the matching arm follows. Release and it holds. The **trigger** drives that hand's gripper while the grip is held.
4. The face buttons run the whole-robot posture moves: **A** (lower) goes home, **B** goes ready; squeezing either grip cancels the move.

5. With the recorder selected, the left controller's face buttons drive it: **X** starts an episode and the next press stops and saves it; holding **Y** for a second finishes the session and opens a fresh dataset.

Before recording anything, name the task: open `https://<host>:4443/task` from the headset's browser (or a laptop) and set what you are demonstrating. Every frame carries that string and a policy reads it back as its instruction. Nothing sets it for you, so episodes recorded before you do go down as `unnamed teleop task`, and the status panel's task line reads `NOT SET (unnamed)` until then. Retitle the same way mid-session, from a laptop if the operator is in the headset; the change applies to the next episode.

With the cameras selected too, both wrist cameras and the ZED chest camera run. Each camera streams into the headset under its instance id, so `wrist_left` and `wrist_right` anchor their panels to the controllers while `chest` floats.

## Real robot

The `real` option drives the physical arms over CAN instead of a sim (`openarm_v1` or `openarm_v2`, panel-led or headset-led). Before launching, bring the buses up. The v1 base wires `can0`/`can1`; the v2 base uses the `left_arm`/`right_arm` channel names a host udev rule (`80-openarm-can.rules`) gives the PEAK adapter:

```sh
sudo ip link set left_arm up type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set right_arm up type can bitrate 1000000 dbitrate 5000000 fd on
```

The arms load the description matching their `hardware_version` for the gravity/Coriolis feedforward; it is baked into the `openarm_arm` container, so unlike the rate and CAN arguments there is no host path to set. Adjust `can_interface`, `control_rate_hz`, or `state_rate_hz` in the base (or flatten with `peppy stack resolve` and hand-edit) if your wiring or loop budget differs.

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
