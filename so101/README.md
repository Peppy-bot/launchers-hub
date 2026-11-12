# SO-101 launcher and fragments

Single-arm SO-101 teleop over the `nodes-hub` so101 family, on hardware and
in a simulation:

```text
commander_inst or headset_inst ──(joint or pose)+gripper──▶ backbone_inst ──arm+gripper──▶ follower_inst      (so101)
commander_inst or headset_inst ──(joint or pose)+gripper──▶ backbone_inst ──arm+gripper──▶ simulation_inst    (so101_sim)
```

Each SO-101 is one fragment. [so101](fragments/so101.json5) drives the
physical robot and is an option of the repository's
[physical launcher](../physical.json5); one copy runs the follower, the
initializer, the backbone and the commander, its ids under the copy's name
(`alpha_follower_inst`, `alpha_init_inst`, `alpha_backbone_inst`,
`alpha_commander_inst`). [so101_sim](fragments/so101_sim.json5) joins a
simulation that stands its limbs, and is an option of every simulation
launcher: [so101_simulation.json5](../so101_simulation.json5), which lists
one as `alpha`,
[openarm_simulation.json5](../openarm_simulation.json5) and
[simulation_mcp.json5](../mcp/simulation_mcp.json5).

Both deploy the SO-101 control: the initializer, `robot_initializer` with
`model: "so101"`, which answers `robot_identity` and `robot_ready`, and the
backbone. A fragment writes only the instances it deploys and the
backbone's pairs into the stack's headset, so what the headset reaches of
the robot, its postures, recorder, camera and motor telemetry, the
recorder's rig and dataset, and the robot's listing on the MCP endpoint are
the launcher's, under the option that selects the robot; the recorder's
limb links are the family recorder file's. The backbone names its
downstream links after the limbs, `arm` and `gripper`. On hardware they
pair with `follower_inst`, the initializer's `limbs` slot links the
follower and its `simulation` slot is vacant. In a simulation they pair
into `simulation_inst/arms` and `simulation_inst/grippers`, the initializer
joins the simulation as `so101`, every instance reads the `simulation`
clock, and there is no follower node: the engine plays the follower role
toward the backbone. `so101_backbone` does not wait on `robot_ready`.

The stack's headset is shared with OpenArm through the repository-level
`teleop/` directory, and leads the SO-101 with its right hand; the
recorder is the family's,
[fragments/lerobot_recorder.json5](fragments/lerobot_recorder.json5). "Leader"/"follower"
name pairing roles here; the SO-101 leader arm is one leader, the headset
another (see nodes-hub/so101/README.md, Terminology). The fragments'
axes:

| Axis | `so101` | `so101_sim` | Provides |
|---|---|---|---|
| `robot_commander` (`role: "robot_commander"`) | `so101_leader` (deployed), `none` (actions, and the stack's headset when the stack runs one) | `none` (deployed), `so101_leader` | none declared; the leader arm deploys `commander_inst` |
| `recorder` (`zero_or_one`) | `lerobot_recorder` | `lerobot_recorder` | `recorder_inst` |
| `camera_rig` (`zero_or_one`) | `cameras` | `cameras_sim` | `wrist` |
| `motion_planner` (`zero_or_one`) | `motion_planner` | `motion_planner` | `planner_inst` |

```sh
peppy stack launch physical
peppy stack join so101:alpha                                             # leader-arm teleop
peppy stack join so101:alpha --with robot_commander=none                 # actions only
peppy stack launch physical --with xr_commander                          # the stack's headset
peppy stack join so101:alpha --with robot_commander=none,lerobot_recorder   # headset + recording
peppy stack join so101:alpha --with robot_commander=none,lerobot_recorder,cameras
peppy stack launch physical --with robot_control                                   # the robots' and the framework's MCP endpoints
peppy stack join so101:alpha --with robot_commander=none,cameras         # driven over MCP, its camera served
peppy stack join so101:alpha --with robot_commander=none,motion_planner  # paths planned around the planning scene's objects
peppy stack resolve physical --join so101:alpha --with xr_commander,alpha.robot_commander=none   # inspect the flattened stack
```

The `motion_planner` axis adds the motion planner, the node `motion_planner`,
which takes the robot's model from its initializer's identity, serving
`motion_planning`: arm paths planned around the objects of its planning scene,
which a caller runs with the backbone's `execute_joint_path`. SO-101 has no depth
camera, so its map stays empty and it sees only the objects a caller enters. A plan keeps
the arm's own links apart too, so plan from the ready posture: home folds the arm onto
itself, and a plan from there is refused. A `move_arm` goal the arm refuses for its
orientation plans here, with `tolerance_rad` or `free_axis` naming the slack.
`simulation_mcp` gives every `so101_sim` copy the planner from its entry. The fragment
states where each of its values comes from. Its tools are listed under the robot's name on
the `robot_control` endpoint with the prefix `planner.`.

Recording needs a trigger: episodes start via the recorder's
`record_episode` action, from the button of the stack's headset leading
the robot or from the `recorder.record_episode` tool of the stack's
`robot_control` endpoint, where the recorder is listed whenever the
endpoint runs. A camera rig requires a consumer of its stream: the
recorder, the stack's headset leading the robot, or the `robot_control`
endpoint, which lists the camera. The headset and the `robot_control`
endpoint show the follower's motor health and alerts on the physical robot;
a simulated robot has no motors to report on.

Where the endpoint runs, the launcher lists the robot on it under any
commander: its identity, its limb state, its moves, where its design lets
it work and, on the physical robot, the follower's motor health and alerts.
The backbone's `robot.describe_workspace` and `robot.check_positions` check
reach alone: the `wrist` camera is on the arm, so the design carries no
perception camera.

## Simulation

`so101_simulation.json5` is `openarm_simulation.json5` with another robot
first: the same `simulation` axis, Waldo unless a launch word selects
another, and `so101_sim` as the first robot option, listed as `alpha`,
which comes up with no commander of its own, so the stack launches on a
machine with no SO-101 hardware, a CI runner or a development host, and
actions drive the arm, or the stack's headset when the stack runs one.
`openarm_sim` is an option of its `robot` axis too:

```sh
peppy stack launch so101_simulation                                         # Waldo and one SO-101
peppy stack launch so101_simulation --with mujoco                           # the same robot, alone in MuJoCo
peppy stack launch so101_simulation --with isaac_sim
peppy stack join so101_sim:charlo                                           # a second SO-101 beside alpha
peppy stack join openarm_sim:bravo                                          # an OpenArm v2 beside them
peppy stack remove alpha                                                    # the simulation keeps running
```

The same join brings an SO-101 into an OpenArm's simulation, whether the
launch names the copy or `stack join` adds it later:

```sh
peppy stack launch openarm_simulation
peppy stack join so101_sim:charlo                    # an SO-101 beside the OpenArm alpha
peppy stack launch openarm_simulation --join so101_sim:charlo
```

The simulated robot's options, as launch words on the file's copy or as
join words:

```sh
peppy stack launch so101_simulation --with alpha.so101_leader
peppy stack launch so101_simulation --with xr_commander,alpha.lerobot_recorder,alpha.cameras_sim
peppy stack launch so101_simulation --with robot_control,alpha.cameras_sim
peppy stack resolve so101_simulation --with mujoco --join so101_sim:charlo   # inspect the flattened stack
```

- `so101_leader` is a real leader arm driving the simulated follower:
  LeRobot data without the follower. It opens `/dev/so101_leader`, so the
  leader's udev rule and calibration below apply; nothing else of the
  simulated robot touches a device.
- `xr_commander`, a word of the stack, runs the stack's headset, which
  leads `alpha` with its right hand while `alpha` runs no commander of its
  own, with the recorder and the rendered camera as on hardware.
- `cameras_sim` ([fragments/cameras_sim.json5](fragments/cameras_sim.json5))
  is the rendered `wrist` camera in place of the real one: one
  `sim_rgb_camera` relay paired into `simulation_inst/rgb_cameras`, under
  the same instance id, so a simulated session records the image key a real
  one does. Waldo models the camera's response after the Innomaker
  U20CAM-1080P's, so its controls work there and refuse under MuJoCo and
  Isaac Sim.
- `robot_control`, a word of the stack, serves the stack's `robot_control` endpoint, `http://127.0.0.1:8900/robot_control/v1/mcp`, the one every robot of the stack is driven through by name, where the launcher lists the robot with its moves under any commander, as above, and, with `cameras_sim`, its `wrist` relay, and the framework's endpoint, `http://127.0.0.1:8903/framework_controls/v1/mcp`, where a client halts the stack. `simulation_mcp` deploys the endpoints. Over MCP a model drives the SO-101's `arm` and `gripper`, sees through its camera and, on `simulation_mcp`, moves the robot's base through the `simulation` endpoint.
- The launcher names the dataset per engine, a robot type and a storage root:
  `so101_mujoco`, `so101_isaac` and `so101_waldo`, in
  `/tmp/lerobot_so101_mujoco`, `/tmp/lerobot_so101_isaac` and
  `/tmp/lerobot_so101_waldo`.

The limits:

- Every simulation stands any number of robots at once, each clear of the
  others. Waldo and Isaac Sim place and move them through the scene
  contract.
- A copy named with `--join` is composed as a join, as `stack join` is,
  and a join cannot turn rendering on, so an SO-101 joined to a plain
  `openarm_simulation` or `so101_simulation` has no `wrist` camera, and
  `--with cameras_sim` on that join is refused. Under `simulation_mcp` a
  joined SO-101 has its `wrist` camera, because the launcher turns
  rendering on itself, and the rig and `robot_commander` `none` are the
  entry's, for every copy of the option:

  ```sh
  peppy stack launch simulation_mcp --join so101_sim:charlie   # an SO-101 over MCP, with its wrist camera
  peppy stack join so101_sim:delta                             # another, listed on the same URL when the join returns
  ```

CI launches the simulated SO-101 where it opens no device: the plain join
onto the bare simulation under Waldo and MuJoCo, and the copy named at
launch on `simulation_mcp`, with the entry's `cameras_sim` and the stack's
headset. The `so101_leader` selection and Isaac Sim are validated and
skipped, as the [skip file](../.github/unlaunchable-nodes.json5) says.

## Host prerequisites

These are the physical robot's, and the simulated one's `so101_leader`
option needs the leader's share of them.

1. **udev rules**: install `rules/60-so101.rules` (fill in the board serials
   or hub-port paths first) so `/dev/so101_follower` and `/dev/so101_leader`
   stay stable across replugs; two identical adapters are otherwise
   indistinguishable. The `cameras` option additionally needs the
   `/dev/so101_wrist_cam` rule, which matches the Innomaker U20CAM-1080P on
   the wrist; a rig with two of them, or another camera model, fills it in.
2. **Calibration**: the nodes refuse to start uncalibrated. The follower is
   in every selection of `so101`, so it always needs this:

   ```sh
   lerobot-calibrate --robot.type=so101_follower \
     --robot.port=/dev/so101_follower --robot.id=follower
   ```

   The leader is deployed only by the `so101_leader` option, so calibrate it
   when that is the commander:

   ```sh
   lerobot-calibrate --teleop.type=so101_leader \
     --teleop.port=/dev/so101_leader --teleop.id=leader
   ```

   Each writes `<id>.json` under lerobot's own calibration directory. Place
   or symlink both under `/var/lib/so101/calibration/`, which is the host
   path this fragment bind-mounts and where the nodes look for `<id>.json`.
3. **Postures**: `move_to_home` targets the arm's `home` state, where it
   rests, and `move_to_ready` its `ready` state, the working posture where
   work starts, the arm out level with the wrist camera above the jaw; both
   are states of the arm in SO-101's SRDF in `robot_description`, which holds
   each within the joint limits of the same model's URDF (no host URDF to
   fetch).

## Safety model

No collision governor exists on this single arm; the backbone is the one
motion authority, and what it limits depends on how the arm is being driven.

**Action goals** (`move_to_home`, `move_to_ready`, `move_arm_joints`,
`move_arm`, `execute_joint_path`) are timed within the per-joint velocity and
acceleration caps, integrating the speed along the path over a grid of at
least 1000 cells. A goal outside the URDF joint limits is refused
outright.

**A joints-led stream** (the SO-101 leader arm) is clamped into the joint
limits and rate-stepped per joint at the joint velocity limits, then governed
by the end-effector speed cap.

**A pose-led stream** (the headset) is clipped into the arm's fitted
reachable ball before the solver sees it, then treated the same.

After any stream gap the backbone re-anchors on the measured position, so a
leader parked far away walks the follower over rather than snapping it. The
follower applies no per-cycle clamp of its own: on silence it simply stops
writing goals and the servo PID holds the last one. Setpoint consumers
age-gate on the producer's wire timestamp, so a backlog replayed after a
stall is dropped rather than executed. The leader has no engage button;
staleness is the deadman, so unplugging it stops the leader's stream within
`stale_timeout_s` (0.25 s by default) and the arm settles at its last
governed target shortly after. Effort feedforward is rejected at both the
backbone and the follower: the STS3215 is position-only hardware.
