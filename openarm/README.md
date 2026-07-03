# openarm launchers

Launchers for the OpenArm bimanual teleop stack, one per hardware generation and backend. All six bring up the same node graph (one robot_initializer, two arms, two grippers, one backbone, one joint_commander) and differ only in which implementation fills each slot and which `hardware_version` the nodes receive:

| Launcher | Generation | Runs against |
|---|---|---|
| `openarm_v1_teleop.json5` | v1.0 | the real robot |
| `openarm_v1_teleop_isaac.json5` | v1.0 | Isaac Sim |
| `openarm_v1_teleop_mujoco.json5` | v1.0 | MuJoCo |
| `openarm_v2_teleop.json5` | v2.0 | the real robot |
| `openarm_v2_teleop_isaac.json5` | v2.0 | Isaac Sim |
| `openarm_v2_teleop_mujoco.json5` | v2.0 | MuJoCo |

## Before launching

The node repos must be registered with the daemon and the nodes built. The top-level README in `openarm_nodes` walks through the whole sequence; in short, every repo gets a `peppy repo add` (followed by `peppy repo refresh`), and every node gets a `peppy node add <path> -sb`. Then verify:

```sh
peppy stack list
```

Every node the launcher references should show `Stage: Ready`.

## Launch

```sh
peppy stack launch ./openarm_v1_teleop_mujoco.json5
```

The launcher starts all eight instances in dependency order (sim first, then arms and grippers, then backbone, then the UI) and wires the bindings between them. Once it prints `Launch complete`:

- open **http://localhost:8765** for the control panel, one slider per joint
- open **http://localhost:8080** for the MuJoCo viewer (for Isaac, connect with the [livestream client](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/manual_livestream_clients.html) instead)

Move a slider, press **Send**, and watch the arm follow. To stop everything, Ctrl-C the launch terminal, or stop instances individually with `peppy node stop <instance_id>`.

## Real robot

`openarm_v1_teleop.json5` / `openarm_v2_teleop.json5` drive the physical arms over CAN instead of a sim, so there is no viewer port: the only UI is the control panel at **http://localhost:8765**. Before launching, bring the buses up (the launcher wires left arm + gripper to `can0` and right arm + gripper to `can1`):

```sh
sudo ip link set can0 up type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can1 up type can bitrate 1000000 dbitrate 5000000 fd on
```

The arms load the V1.0 description for their gravity/Coriolis feedforward; it is baked into the `openarm_arm` container, so unlike the rate and CAN arguments there is no host path to set. Adjust `can_interface`, `control_rate_hz`, or `state_rate_hz` in the launcher if your wiring or loop budget differs.

## Troubleshooting

**`failed to resolve source for deployment repo:<node>:v1 ... not found in nodes.json5`**
The repo providing that node isn't registered with the daemon. Run `peppy repo add /path/to/<repo>` and `peppy repo refresh`, then launch again.

**The launch stalls on the robot_initializer build**
The first build pulls the sim base image and can outlive the daemon's idle timeout. Build it once beforehand with a longer timeout, then launch:

```sh
peppy node add /path/to/ws/openarm_nodes/openarm_robot_initializer_isaac -sb --idle-timeout 18000
```

**Everything launches but the arms don't respond**
The sim keeps loading after `Launch complete`, and Isaac can take a minute. Check instance health with `peppy stack list` and watch the sim's log with `peppy node info openarm_robot_initializer_<engine>:v1`.

**The Isaac stream is a black screen**
Stop the stack, clear the shader cache with `rm -rf ~/.cache/isaac-sim`, and launch again.
