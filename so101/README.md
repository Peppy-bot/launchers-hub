# so101 launcher

Single-arm SO-101 teleop over the `robot-nodes` so101 family:

```text
commander_inst ──joint or pose──▶ backbone_inst ──joint+gripper──▶ follower_inst
```

The base deploys the follower and the backbone. "Leader"/"follower"
name pairing roles here; the SO-101 leader arm is one commander option
among several (see robot-nodes/so101 README, i.e. robot-nodes/README.md, Terminology). Components:

| Axis | Options | Provides |
|---|---|---|
| `commander` (required, no default) | `so101_leader`, `xr_commander`, `so101_commander` | `commander_inst` |
| `recorder` (optional) | `lerobot_recorder` | `recorder_inst` |
| `cameras` (optional) | `cameras` | `front` |

```sh
peppy stack launch so101 --with=so101_leader                    # leader-arm teleop
peppy stack launch so101 --with=so101_commander                 # browser panel, no extra hardware
peppy stack launch so101 --with=xr_commander,lerobot_recorder   # headset + recording
peppy stack launch so101 --with=xr_commander,lerobot_recorder,cameras
peppy stack resolve so101 --with=...                            # inspect the flattened stack
```

Recording requires the XR commander: episodes start via the recorder's
`record_episode` action, and only the headset carries that button.

## Host prerequisites

1. **udev rules**: install `rules/60-so101.rules` (fill in the board serials
   or hub-port paths first) so `/dev/so101_follower` and `/dev/so101_leader`
   stay stable across replugs; two identical adapters are otherwise
   indistinguishable. The `cameras` option additionally needs the
   `/dev/so101_front_cam` rule filled in.
2. **Calibration**: run `lerobot-calibrate` for both arms with ids
   `follower` and `leader`, then place (or symlink) the JSONs under
   `/var/lib/so101/calibration/`. The nodes refuse to start uncalibrated.
3. **Postures**: `move_to_home` targets the calibration midpoint and
   `move_to_ready` a tucked, raised pose; both are `so101_description`
   constants, validated at startup against the kinematics URDF that same
   description embeds (no host URDF to fetch).

## Safety model

No governor exists on this single arm: the backbone velocity-caps every
motion, clamps every target into the URDF joint limits (goals beyond a limit
are refused outright), re-anchors on the measured position after any stream
gap (a leader parked far away walks the follower over, never snaps it), and
the follower clamps per-cycle jumps (`max_relative_target_deg`) and holds
via its servo PID on any silence. Setpoint consumers also age-gate on the
wire timestamp, so a backlog replayed after a stall is dropped rather than
executed. The leader has no engage button; staleness is the deadman, so
unplugging it stops the leader's stream within 0.25 s and the arm settles at
its last velocity-capped target within roughly twice that. Effort
feedforward is rejected at both the backbone and the follower: the STS3215
is position-only hardware.
