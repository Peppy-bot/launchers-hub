# SO-101 launcher and fragments

Single-arm SO-101 teleop over the `nodes-hub` so101 family, on hardware and
in a simulation:

```text
commander_inst ──(joint or pose)+gripper──▶ backbone_inst ──arm+gripper──▶ follower_inst      (so101)
commander_inst ──(joint or pose)+gripper──▶ backbone_inst ──arm+gripper──▶ simulation_inst    (so101_sim)
```

Each SO-101 is one fragment. [so101](fragments/so101.json5) drives the
physical robot and is an option of the repository's
[fleet launcher](../fleet.json5); one copy runs the follower, the
initializer, the backbone and the commander, its ids under the copy's name
(`alpha_follower_inst`, `alpha_init_inst`, `alpha_backbone_inst`,
`alpha_commander_inst`). [so101_sim](fragments/so101_sim.json5) joins a
simulation that stands its limbs, and is an option of every launcher with a
simulation: the fleet, [so101_simulation.json5](so101_simulation.json5),
which deploys one as `alpha`,
[openarm_simulation.json5](../openarm/openarm_simulation.json5) and
[openarm_simulation_mcp.json5](../mcp/openarm_simulation_mcp.json5).

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

XR and recorder deployments are shared with OpenArm through the
repository-level robot commander and recording directories. "Leader"/"follower"
name pairing roles here; the SO-101 leader arm is one commander option
among several (see nodes-hub/so101/README.md, Terminology). The fragments'
axes:

| Axis | `so101` | `so101_sim` | Provides |
|---|---|---|---|
| `control` | `control_common` (deployed) | `control_common` (deployed) | `init_inst`, `backbone_inst` |
| `robot_commander` | `so101_leader` (deployed), `xr_commander`, `no_commander` (actions only) | `no_commander` (deployed), `so101_leader`, `xr_commander`, `mcp_commander` | `commander_inst` with a commander |
| `recorder` (`zero_or_one`) | `lerobot_recorder` | `lerobot_recorder` | `recorder_inst` |
| `camera_rig` (`zero_or_one`) | `cameras` | `cameras_sim` | `front` |

```sh
peppy stack launch fleet
peppy stack join so101 -i alpha                                          # leader-arm teleop
peppy stack join so101 -i alpha --with xr_commander,lerobot_recorder     # headset + recording
peppy stack join so101 -i alpha --with xr_commander,lerobot_recorder,cameras
peppy stack join so101 -i alpha --with no_commander                      # actions only
peppy stack resolve fleet --join so101 --join-name alpha --join-with xr_commander   # inspect the flattened stack
```

Recording requires the XR commander: episodes start via the recorder's
`record_episode` action, and only the headset carries that button. A camera
rig requires a consumer of its stream: the recorder or the headset, and on
the simulated robot the MCP commander too. The headset shows the follower's
motor health and alerts on the physical robot; a simulated robot has no
motors to report on.

## Simulation

`so101_simulation.json5` is `openarm_simulation.json5` with another robot in
it: the same `simulation` axis, Waldo unless a launch word selects another,
and one `so101_sim` copy named `alpha`. The copy comes up with no commander,
so the stack launches on a machine with no SO-101 hardware, a CI runner or a
development host, and actions drive the arm. `openarm_v1_sim` and
`openarm_v2_sim` are options of its `robot` axis too:

```sh
peppy stack launch so101_simulation                  # Waldo and one SO-101
peppy stack launch so101_simulation --with mujoco    # the same robot, alone in MuJoCo
peppy stack launch so101_simulation --with isaac_sim
peppy stack join so101_sim -i charlo                 # a second SO-101 beside alpha
peppy stack join openarm_v2_sim -i bravo             # an OpenArm v2 beside them
peppy stack remove alpha                             # the simulation keeps running
```

The same join brings an SO-101 into an OpenArm's simulation:

```sh
peppy stack launch openarm_simulation
peppy stack join so101_sim -i charlo                 # an SO-101 beside the OpenArm alpha
```

The simulated robot's options, as launch words on the file's copy or as join
words:

```sh
peppy stack launch so101_simulation --with alpha.so101_leader
peppy stack launch so101_simulation --with alpha.xr_commander,alpha.lerobot_recorder,alpha.cameras_sim
peppy stack launch so101_simulation --with alpha.mcp_commander,alpha.cameras_sim
peppy stack resolve so101_simulation --with mujoco --join so101_sim --join-name charlo   # inspect the flattened stack
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
- `mcp_commander` ([fragments/mcp_commander.json5](fragments/mcp_commander.json5))
  serves the MCP hub's `front_camera:v1` exposure bound to that relay, at
  `http://127.0.0.1:8903/front_camera/v1/mcp`; a copy that finds the port
  held takes another, which the join prints. It requires `cameras_sim`.
  Nothing streams into the backbone under it, so its option lists
  `no_commander.json5` as its first part and takes the upstream vacancies
  from it. Over MCP a model sees through the SO-101's camera and, where a
  launcher serves the `simulation` endpoint, moves the robot's base through
  it; the arm is not drivable over MCP.
- The recorder writes under a robot type and a storage root per engine:
  `so101_mujoco`, `so101_isaac` and `so101_waldo`, in
  `/tmp/lerobot_so101_mujoco`, `/tmp/lerobot_so101_isaac` and
  `/tmp/lerobot_so101_waldo`.

The limits:

- Waldo and Isaac Sim stand any number of robots at once and place and move
  them through the scene contract. MuJoCo stands one robot at a time, the
  robot's file being the whole world: under `--with mujoco` the engine
  refuses a join beside `alpha`, with its own reason, until
  `peppy stack remove alpha`.
- A join cannot turn rendering on, so an SO-101 joined to a plain
  `openarm_simulation` or `so101_simulation` has no `front` camera, and
  `--with cameras_sim` on that join is refused. Under
  `openarm_simulation_mcp` it can, because `alpha`'s rig turns rendering on
  at launch:

  ```sh
  peppy stack launch openarm_simulation_mcp
  peppy stack join so101_sim -i delta --with mcp_commander,cameras_sim   # its front camera served over MCP
  ```

  That launcher has no entry for `so101_sim`, so a plain join there is the
  robot its fragment deploys, with no commander and no camera.
- `fleet.json5` states which simulations stand a `so101_sim`, in one list:
  the ones whose catalogue carries the `so101` model.

CI launches the simulated SO-101 where it opens no device: `no_commander`,
and `mcp_commander` with `cameras_sim`, under Waldo and MuJoCo, the joined
copy beside `alpha` under Waldo and in its place under MuJoCo. The
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
