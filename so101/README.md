# SO-101 launcher and fragments

Single-arm SO-101 teleop over the `nodes-hub` so101 family, on hardware and
in a simulation:

```text
commander_inst ──(joint or pose)+gripper──▶ backbone_inst ──arm+gripper──▶ follower_inst      (so101)
commander_inst ──(joint or pose)+gripper──▶ backbone_inst ──arm+gripper──▶ simulation_inst    (so101_sim)
```

Each SO-101 is one fragment. [so101](fragments/so101.json5) drives the
physical robot and is an option of the repository's
[physical launcher](../physical.json5); one copy runs the follower, the
initializer, the backbone and the commander, its ids under the copy's name
(`alpha_follower_inst`, `alpha_init_inst`, `alpha_backbone_inst`,
`alpha_commander_inst`). [so101_sim](fragments/so101_sim.json5) joins a
simulation that stands its limbs, and is an option of every simulation
launcher: [so101_simulation.json5](so101_simulation.json5), which lists
one as `alpha`,
[openarm_simulation.json5](../openarm/openarm_simulation.json5) and
[simulation_mcp.json5](../mcp/simulation_mcp.json5).

Both select the shared SO-101 control
([fragments/control_common.json5](fragments/control_common.json5)): the
initializer, `robot_initializer` with `model: "so101"`, which answers
`robot_identity` and `robot_ready`, and the backbone, with the XR and
recorder wiring into it. The backbone names its downstream links after the
limbs, `arm` and `gripper`. On hardware they pair with `follower_inst`, the
initializer's `limbs` slot links the follower and its `simulation` slot is
vacant. In a simulation they pair into `simulation_inst/arms` and
`simulation_inst/grippers`, the initializer joins the simulation as `so101`,
every instance reads the `simulation` clock, and there is no follower node:
the engine plays the follower role toward the backbone. `so101_backbone`
does not wait on `robot_ready`.

The XR and MCP commanders and the recorder are shared with OpenArm through
the repository-level robot commander and recording directories. "Leader"/"follower"
name pairing roles here; the SO-101 leader arm is one commander option
among several (see nodes-hub/so101/README.md, Terminology). The fragments'
axes:

| Axis | `so101` | `so101_sim` | Provides |
|---|---|---|---|
| `control` | `control_common` (deployed) | `control_common` (deployed) | `init_inst`, `backbone_inst` |
| `robot_commander` | `so101_leader` (deployed), `xr_commander`, `mcp_commander`, `none` (actions only) | `zero_or_one`, unfilled: `so101_leader`, `xr_commander`, `mcp_commander` | `commander_inst` with a leader or the headset |
| `recorder` (`zero_or_one`) | `lerobot_recorder` | `lerobot_recorder` | `recorder_inst` |
| `camera_rig` (`zero_or_one`) | `cameras` | `cameras_sim` | `front` |

```sh
peppy stack launch physical
peppy stack join so101:alpha                                             # leader-arm teleop
peppy stack join so101:alpha --with xr_commander,lerobot_recorder        # headset + recording
peppy stack join so101:alpha --with xr_commander,lerobot_recorder,cameras
peppy stack join so101:alpha --with robot_commander=none                 # actions only
peppy stack launch physical --with robot_control                                   # the robots' MCP endpoint
peppy stack join so101:alpha --with mcp_commander,cameras                # driven over MCP, its camera served
peppy stack resolve physical --join so101:alpha --with alpha.xr_commander   # inspect the flattened stack
```

Recording needs a trigger: episodes start via the recorder's
`record_episode` action, from the headset's button or from the
`recorder.record_episode` tool of the stack's `robot_control` endpoint,
where the recorder is listed whenever the endpoint runs. A camera rig
requires a consumer of its stream: the recorder, the headset, or the MCP
commander, which lists the camera on the endpoint. The headset shows the
follower's motor health and alerts on the physical robot; a simulated robot
has no motors to report on.

## Simulation

`so101_simulation.json5` is `openarm_simulation.json5` with another robot
first: the same `simulation` axis, Waldo unless a launch word selects
another, and `so101_sim` as the first robot option, listed as `alpha`,
which comes up with no commander, so the stack launches on a machine with
no SO-101 hardware, a CI runner or a development host, and actions drive
the arm. `openarm_v1_sim` and `openarm_v2_sim` are options of its `robot`
axis too:

```sh
peppy stack launch so101_simulation                                         # Waldo and one SO-101
peppy stack launch so101_simulation --with mujoco                           # the same robot, alone in MuJoCo
peppy stack launch so101_simulation --with isaac_sim
peppy stack join so101_sim:charlo                                           # a second SO-101 beside alpha
peppy stack join openarm_v2_sim:bravo                                       # an OpenArm v2 beside them
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
peppy stack launch so101_simulation --with alpha.xr_commander,alpha.lerobot_recorder,alpha.cameras_sim
peppy stack launch so101_simulation --with robot_control,alpha.mcp_commander,alpha.cameras_sim
peppy stack resolve so101_simulation --with mujoco --join so101_sim:charlo   # inspect the flattened stack
```

- `so101_leader` is a real leader arm driving the simulated follower:
  LeRobot data without the follower. It opens `/dev/so101_leader`, so the
  leader's udev rule and calibration below apply; nothing else of the
  simulated robot touches a device.
- `xr_commander` is the headset, with the recorder and the rendered camera
  as on hardware.
- `cameras_sim` ([fragments/cameras_sim.json5](fragments/cameras_sim.json5))
  is the rendered `front` camera in place of the webcam: one
  `sim_rgb_camera` relay paired into `simulation_inst/rgb_cameras`, under
  the same instance id, so a simulated session records the image key a real
  one does. Waldo models the camera's response, so its controls work there
  and refuse under MuJoCo and Isaac Sim.
- `mcp_commander`
  ([../robot_commanders/fragments/mcp_commander.json5](../robot_commanders/fragments/mcp_commander.json5)),
  shared with the OpenArm, adds the arm's moves to the stack's
  `robot_control` endpoint, `http://127.0.0.1:8900/robot_control/v1/mcp`,
  the one every robot of the stack is driven through by name, where the
  robot's identity and limb state are listed under any commander: an
  `add_links` adjustment naming the copy's backbone, and, with
  `cameras_sim`, the rig adds the `front` relay. It requires the endpoint,
  which `simulation_mcp` deploys and the other launchers serve with
  `--with robot_control`. Nothing streams into the backbone under it, and the
  control leaves the backbone's sockets vacant unless a leader releases
  them, so the option touches the backbone not at all. Over MCP a model
  drives the
  SO-101's `arm` and `gripper`, sees through its camera and, on
  `simulation_mcp`, moves the robot's base through the `simulation`
  endpoint.
- The recorder writes under a robot type and a storage root per engine:
  `so101_mujoco`, `so101_isaac` and `so101_waldo`, in
  `/tmp/lerobot_so101_mujoco`, `/tmp/lerobot_so101_isaac` and
  `/tmp/lerobot_so101_waldo`.

The limits:

- Every simulation stands any number of robots at once, each clear of the
  others. Waldo and Isaac Sim place and move them through the scene
  contract.
- A copy named with `--join` is composed as a join, as `stack join` is,
  and a join cannot turn rendering on, so an SO-101 joined to a plain
  `openarm_simulation` or `so101_simulation` has no `front` camera, and
  `--with cameras_sim` on that join is refused. Under `simulation_mcp` a
  joined SO-101 has its `front` camera, because the launcher turns
  rendering on itself, and the rig and the MCP commander are the entry's,
  for every copy of the option:

  ```sh
  peppy stack launch simulation_mcp --join so101_sim:charlie   # an SO-101 over MCP beside alpha, with its front camera
  peppy stack join so101_sim:delta                             # another, listed on the same URL when the join returns
  ```

CI launches the simulated SO-101 where it opens no device: the plain join
onto the bare simulation under Waldo and MuJoCo, and `mcp_commander`
with `cameras_sim` named at launch on `simulation_mcp`. The
`so101_leader` and `xr_commander` selections and Isaac Sim are validated and
skipped, as the [skip file](../.github/unlaunchable-nodes.json5) says.

## Host prerequisites

These are the physical robot's, and the simulated one's `so101_leader`
option needs the leader's share of them.

1. **udev rules**: install `rules/60-so101.rules` (fill in the board serials
   or hub-port paths first) so `/dev/so101_follower` and `/dev/so101_leader`
   stay stable across replugs; two identical adapters are otherwise
   indistinguishable. The `cameras` option additionally needs the
   `/dev/so101_front_cam` rule filled in.
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
3. **Postures**: `move_to_home` targets the collapsed park pose the arm
   rests in, and `move_to_ready` the calibration midpoint where work starts;
   both are `so101_description` constants, validated at startup against the
   kinematics URDF that same description embeds (no host URDF to fetch).

## Safety model

No collision governor exists on this single arm; the backbone is the one
motion authority, and what it limits depends on how the arm is being driven.

**Action goals** (`move_to_home`, `move_to_ready`, `move_arm_joints`,
`move_arm`) run minimum-jerk trajectories sized by the per-joint velocity
caps, and Cartesian moves additionally by the end-effector speed caps. A
goal outside the URDF joint limits is refused outright.

**A joints-led stream** (the SO-101 leader arm) passes through under the
end-effector speed caps alone, matching the reference teleop: streamed
targets are deliberately not joint-limit clamped, and the servo EPROM
position limits are the physical travel guard.

**A pose-led stream** (the headset) is clipped into the arm's fitted
reachable ball before the solver sees it, then limit-clamped and
rate-stepped per joint, then governed by the same end-effector speed caps.

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
