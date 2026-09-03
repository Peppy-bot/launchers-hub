# openarm launchers

Launchers for the OpenArm bimanual teleop stack: one base per hardware generation, with the robot backend, the command surface, the recorder, and the cameras selected at launch time with `--with`. Every launch brings up the same core node graph (one robot_initializer, two arms, two grippers, one backbone, and one command surface: a leader that streams setpoints, or the MCP server built into peppy); the options differ in which implementation fills each slot (a slot is a named attachment point a node declares; the launcher's `links` wire slots together, see [docs.peppy.bot](https://docs.peppy.bot)), and the base specializes the shared pieces for its generation.

```sh
peppy stack launch openarm_v2                                   # real robot, browser panel
peppy stack launch openarm_v2 --with=mujoco                     # MuJoCo, browser panel
peppy stack launch openarm_v2 --with=isaac_sim,lerobot_recorder # Isaac Sim, recording
peppy stack launch openarm_v2 --with=isaac_sim,scene_commander  # Isaac Sim, runtime scene panel
peppy stack launch openarm_v2 --with=waldo                   # Waldo, browser panel
peppy stack launch openarm_v2 --with=waldo,scene_commander   # Waldo, runtime scene panel
peppy stack launch openarm_v2 --with=mujoco,lerobot_recorder,sim_cameras     # ... with rendered cameras
peppy stack launch openarm_v2 --with=xr_commander,lerobot_recorder,cameras  # the headset session
peppy stack launch openarm_v2 --with=mujoco,mcp_commander       # MuJoCo, driven through MCP
peppy stack launch openarm_v2 --with=isaac_sim,mcp_commander    # Isaac Sim, driven through MCP
peppy stack launch openarm_v1 --with=...                        # same axes on the v1 base
```

The five axes, one option per axis per launch:

| Axis | Options | Default | Fills |
|---|---|---|---|
| `robot` | `real`, `mujoco`, `isaac_sim`, `waldo` (v2) | `real` | the arms and grippers (real CAN drivers, or sim relays plus an engine) |
| `commander` | `web_commander`, `xr_commander`, `mcp_commander` (v2) | `web_commander` | the command surface (`commander_inst`): the browser panel, the XR headset, or the MCP server built into peppy serving the `openarm_v2:v1` exposure |
| `recorder` | `lerobot_recorder` | off (`optional`) | the dataset recorder and the record button |
| `cameras` | `cameras`, `sim_cameras` (v2) | off (`optional`) | both wrist cameras and the chest camera, filmed by the USB rig or rendered by the engine |
| `scene` | `scene_commander` | off (`optional`) | the runtime scene panel spawning and moving objects in a running Isaac Sim or Waldo engine, with a robot menu switching the Waldo engine between its worlds |

Every axis left unselected takes its default, so the bare launch is the plain real-robot teleop. `peppy stack resolve openarm_v2 --with=...` prints the flattened launcher each selection produces, plus a report of every adjustment it applied and skipped.

The old per-variant launchers (`openarm_v2_teleop_mujoco`, `openarm_v2_teleop_vr_record`, and siblings) map one-to-one onto the invocations above.

## Selections the family refuses

Not every combination of the axes is a member of the family. Each base declares its `constraints`, and a selection violating one is refused before anything is pinned or started: the refusal names the requirement and quotes the reason.

- **`cameras` requires `robot=real`.** The cameras film the physical rig. Beside a simulated robot they would record datasets whose video is a static desk while the joint and action streams move: silent training-data poison. A simulated session takes `sim_cameras` instead.
- **`sim_cameras` requires `robot=mujoco`, `robot=isaac_sim`, or `robot=waldo`.** Rendered cameras need a scene to render, so the option is refused beside the real robot (which takes `cameras`). Any engine renders the three viewpoints onto the same slots (the MuJoCo and Isaac engines from the same `config/cameras.json5`, the Waldo engine from the same placement in its world manifest), so a dataset gets the same feature keys and shapes either way; the pixels differ, because the engines light and rasterize the scene differently.
- **`cameras` and `sim_cameras` each require `lerobot_recorder` or `xr_commander`.** Only the recorder and the XR leader have camera slots; the web panel has none. Without a consumer the three cameras would publish to zero subscribers.
- **`scene_commander` requires `robot=isaac_sim` or `robot=waldo`.** It drives the `scene_control` contract, which only the Isaac Sim and Waldo engines implement.
- **`lerobot_recorder` requires `web_commander` or `xr_commander` (v2).** The recorder's record button attaches to the commander's recorder slot, which only the panel and the headset declare; the MCP server has no such slot and no way to start an episode, so a session it leads cannot record.

Unselected axes count as what they resolve to: `--with=cameras` alone is refused because the commander *defaults* to the web panel and the recorder stays off, and the refusal's echo marks both with `(default)` / `(off)` so the fix is visible. A constraint never picks an option to satisfy itself: it refuses, and you say what you meant.

That leaves 19 members on the v1 base: all 12 camera-less combinations, `real` with cameras and any of `lerobot_recorder`, `xr_commander`, or both, and `scene_commander` over the 4 camera-less `isaac_sim` selections. The v2 base adds `waldo` as a third engine (its 4 camera-less selections over the panel and the headset, and `scene_commander` over each of them, 8 in all), `sim_cameras` on any of the three engines over the same three consumers (9), `scene_commander` over the rendered-camera `isaac_sim` and `waldo` selections too (6), and `mcp_commander` over `real`, `mujoco`, `isaac_sim`, `waldo`, and the last two with `scene_commander` (6; every other pairing of it is refused by the recorder rule or the camera rules), for 48. `peppy repo index --check` enumerates exactly these and also fails if a constraint ever strangles an option (or the bare launch) out of the family.

## Who leads, and in which space

The backbone follows exactly one kind of upstream arm command, named by its required `upstream_mode` argument, and subscribes only that kind of arm slot (gripper and posture slots are read under either mode):

- `"joints"` - `openarm_web_commander` (the browser panel) streams joint setpoints on `joint_link`. The base's default.
- `"pose"` - `xr_commander` streams an end-effector pose per hand on `pose_link`, and the backbone solves it. The `xr_commander` fragment flips the mode and re-vacates the slots as part of being selected.
- Nobody streams - `mcp_commander` (v2) drives the backbone through discrete actions only: the whole-robot posture moves and the per-limb arm and gripper moves it exposes as tools. `upstream_mode` stays `"joints"`, all six leader sockets are vacant with their reasons, and the governor keeps its launch-time band, enable, and EE-speed caps for the whole session, as under the headset.

One or the other, never both: a backbone reading two command authorities for one arm is not a state the mode can express. An arm slot of the kind the mode does *not* name would never be read, so linking one refuses the launch, naming every offending slot.

The `xr_commander` selection runs without `openarm_web_commander` entirely. `governor_control` is an optional backbone feature, since not every leader can produce it (`xr_commander` is robot-agnostic, so it never will): with no producer bound, the governor runs on the backbone's launch-time band, enable, and EE-speed cap for the whole session. To retune, edit the backbone arguments and relaunch, or use the panel.

Recording adds `lerobot_recorder` (see the recorder's README in nodes-hub for the dataset workflow). The `cameras` option adds the three cameras, whose device paths come from `rules/99-openarm-cameras.rules` (install it per the file's header); the headset retunes them for in-headset panels as part of its own selection.

## The fragments

Each option's body lives in `fragments/`, one concern per file, and the bases reference them:

| Fragment | Carries |
|---|---|
| `sim_relays.json5` | the four engine-agnostic relays all three sim options share, plus the sim's raised EE-speed cap |
| `mujoco_engine.json5` / `waldo_engine.json5` / `isaac_engine.json5` | the engine instance and the recorder's storage root for it; the isaac fragment also carries the browser frontend for the engine's WebRTC stream (the Waldo engine serves its own Bevy viewer over https) |
| `web_commander.json5` / `xr_commander.json5` | the leader, plus (headset) the backbone's pose-mode flip and the camera retunes |
| `mcp_commander.json5` | the built-in MCP server serving `openarm_v2:v1` with both targets bound to the backbone, plus the backbone's leader-socket and governor-control vacancies |
| `lerobot_recorder.json5` | the recorder and the record-button attach to whichever leader was selected |
| `cameras.json5` | the three cameras and their binds into whichever recorder was selected |
| `sim_cameras.json5` | the same three viewpoints rendered by whichever engine was selected, and the same binds |
| `scene_commander.json5` | the runtime scene panel driving the `scene_control` contract of whichever engine was selected; its `world_control` slot (the robot menu) is vacant here and bound by the Waldo engine's fragment, the one engine that implements it |

The bases (`openarm_v1.json5`, `openarm_v2.json5`) hold the invariant graph (initializer, backbone), each generation's real-hardware option inline, and the generation's `hardware_version` (the Waldo engine's `world`) and dataset labels as base adjustments. A fragment is referenced by the option that wants it and is never a launchable stack of its own; `peppy repo index --check` validates every fragment and every legal selection.

## Before launching

The node repos must be registered with the daemon and the nodes built. The `openarm/README.md` in [nodes-hub](https://github.com/Peppy-bot/nodes-hub) walks through the whole sequence; in short, every repo gets a `peppy repo add` (followed by `peppy repo refresh`), and every node gets a `peppy node add <path> -sb`. The `mcp_commander` option deploys no node of its own: the server is built into peppy and its exposure comes from the [mcp-hub](https://github.com/Peppy-bot/mcp-hub), which peppy registers by default.

The `xr_commander` option additionally needs `xr_commander`, which lives in the separate [nodes-hub](https://github.com/Peppy-bot/nodes-hub) repo: register it the same way, then `peppy node add /path/to/ws/nodes-hub/xr_commander -sb`. The `cameras` option deploys the camera nodes from that repo:

```sh
peppy node add /path/to/ws/nodes-hub/uvc_camera/linux -sb
peppy node add /path/to/ws/nodes-hub/zed_camera -sb
```

The `sim_cameras` option deploys the relay nodes from that same repo instead:

```sh
peppy node add /path/to/ws/nodes-hub/sim_rgb_camera -sb
peppy node add /path/to/ws/nodes-hub/sim_rgbd_camera -sb
```

The `waldo` option deploys `waldo:v1`, which comes from the separate `private-nodes-hub` repository: register it with `peppy repo add <path-or-url>` (followed by `peppy repo refresh`) and build it before the first launch, with the larger idle timeout its first build needs:

```sh
peppy node add /path/to/ws/private-nodes-hub/waldo -sb --idle-timeout 18000
```

Its Bevy viewer is at https://localhost:8080 (accept the self-signed certificate once). The option runs the engine's `hand_teleop` plugin (`plugins: "hand_teleop"` on `sim_inst`): the viewer's "Start camera" panel tracks your hands on the webcam and each drives the arm of the same name, pinching to close its gripper, ahead of the backbone's setpoints while the hand is tracked.

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
- open **http://localhost:8080** for the MuJoCo viewer, or **https://localhost:8080** for the Waldo viewer (for Isaac, connect with the [livestream client](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/manual_livestream_clients.html) instead)

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

## MCP commander

The `mcp_commander` selection (v2 base) runs no leader node: the server built into peppy serves the `openarm_v2:v1` exposure from the mcp-hub, both of its targets (`postures`, `limb_motion`) bound to the backbone, and publishes four tools backed by MCP tasks: `openarm.move_to_ready`, `openarm.move_to_home`, `openarm.move_arm`, and `openarm.move_gripper`. The same fragment serves the real robot and every engine, since the backbone is the same instance under all four. After `Launch complete`:

1. `peppy stack list` shows the endpoint in its `Instance endpoints` table: `http://127.0.0.1:8900/openarm_v2/v1/mcp` (the port is the fragment's `port` argument).
2. Point an MCP client at it, or run the standard-library script that ships beside the exposure in mcp-hub: `python3 openarm/openarm_v2_demo.py tools` prints what the endpoint advertises, `... demo` brings the arms to ready, closes and opens both grippers, and returns home, and each tool has a subcommand (`move-to-ready`, `move-to-home`, `move-arm`, `move-gripper`). Ctrl-C cancels the move in flight and waits for the robot to settle.

Every move runs through the backbone's own admission and planner: a goal naming an unknown limb, an opening outside 0..1, or a limb still executing a motion is refused with the reason, and the client sees it as a failed task quoting it. Recording is refused beside this commander (the constraint above), and the governor keeps its launch-time settings for the session.

## Real robot

The `real` option drives the physical arms over CAN instead of a sim (`openarm_v1` or `openarm_v2`, panel-led or headset-led). Before launching, bring the buses up. The v1 base wires `can0`/`can1`; the v2 base uses the `left_arm`/`right_arm` channel names a host udev rule (`80-openarm-can.rules`) gives the PEAK adapter:

```sh
sudo ip link set left_arm up type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set right_arm up type can bitrate 1000000 dbitrate 5000000 fd on
```

Both limbs take a `hardware_version`. The arms load the description matching theirs for the gravity/Coriolis feedforward; it is baked into the `openarm_arm` container, so unlike the rate and CAN arguments there is no host path to set. The grippers take the same argument, where it selects the motor's CAN control mode and both the direction and the distance the fingers travel. Leaving one on the other generation's value never just degrades it:

| rig | set to | what the gripper does |
|---|---|---|
| v1 left | `v2` | drives backwards into the closed stop |
| v1 right | `v2` | drives 50% past the open stop |
| v2 left | `v1` | drives backwards into the closed stop |
| v2 right | `v1` | opens two thirds of the way and reports that as fully open |

The control mode follows the same argument, so a gripper wrongly set to `v1` also runs an ungoverned MIT hold with no POS_FORCE grip-force ceiling, and one wrongly set to `v2` drives v1.0's prismatic jaws in POS_FORCE. Adjust `can_interface`, `control_rate_hz`, or `state_rate_hz` in the base (or flatten with `peppy stack resolve` and hand-edit) if your wiring or loop budget differs.

## Troubleshooting

**`deployment <id>: Dependencies missing from nodes cache (...): <name>:<tag>`**
The repo providing that node isn't registered with the daemon. Run `peppy repo add /path/to/<repo>` and `peppy repo refresh`, then launch again.

**The launch stalls on the sim engine build**
The first build pulls the sim base image and can outlive the daemon's idle timeout. Build it once beforehand with a longer timeout, then launch:

```sh
peppy node add /path/to/ws/nodes-hub/openarm/sim_isaac -sb --idle-timeout 18000
```

**Everything launches but the arms don't respond**
The sim keeps loading after `Launch complete`, and Isaac can take a minute. Check instance health with `peppy stack list` and watch the sim's log with `peppy node info openarm_sim_<engine>:v1` (or `waldo:v1`).

**The Isaac stream is a black screen**
Stop the stack, clear the shader cache with `rm -rf ~/.cache/isaac-sim`, and launch again.

**The headset shows the page but "Enter VR" is missing**
WebXR needs a secure context, so the node self-generates a per-machine TLS certificate and always serves HTTPS; click through the browser's self-signed warning once. Over the network, open the https URL from the startup log. Over USB, `adb reverse tcp:4443 tcp:4443` and open `https://localhost:4443`.

**The headset is connected but neither arm moves**
Hold a grip button: it is the deadman, per hand, and with it released the node publishes nothing at all so the arms hold. If holding it does nothing, check the backbone's startup log line for which upstream mode it is following: a `"joints"` backbone reads only the panel's joint slots and a `"pose"` backbone only the headset's pose slots. A leader wired to the off-mode slots never reaches launch, so what remains is a leader that is publishing nothing: check the headset link and the grip in the node's status panel.
