# MCP launchers

The launchers whose command surface is [MCP](https://modelcontextprotocol.io)
live here. Each composes the same robot and simulation fragments as the
other launchers, and selects commanders that are no node at all: the server
built into `peppy` serving exposures from the
[MCP hub](https://github.com/Peppy-bot/mcp-hub), one process and one port
per deployment, each exposure at
`http://127.0.0.1:<port>/<name>/<tag>/mcp`. `peppy stack list` reports every
endpoint a running stack serves. The exposure documents are the whole
surface: what a client sees, its tool names, its prose and its policies are
written there, and this guide names them as the documents do. See the
[MCP exposure guide](https://docs.peppy.bot/advanced_guides/mcp/) for the
document format and the [repository README](../Readme.md) for the option
table and the copy semantics.

## openarm_simulation_mcp

[openarm_simulation_mcp.json5](openarm_simulation_mcp.json5) runs Waldo and
serves two endpoints, one per family. The boundary between them is whether
what a model does transfers to the physical robot:

| Family | Endpoint | Exposure | Bound to | On the physical robot |
|---|---|---|---|---|
| Robot | `http://127.0.0.1:8900/openarm_v2/v1/mcp` | `openarm_v2:v1`: who the robot is, the posture, arm and gripper moves, the three cameras and their controls | the copy's initializer, backbone and camera rig | yes |
| Simulated world | `http://127.0.0.1:8902/simulation/v1/mcp` | `simulation:v1`: the scene, its light sources, its materials | the simulation | no |

A simulated SO-101 joined with its MCP commander adds a third, the robot
family's again, [described below](#an-so-101-beside-the-openarm):

| Family | Endpoint | Exposure | Bound to |
|---|---|---|---|
| Robot | `http://127.0.0.1:8903/front_camera/v1/mcp` | `front_camera:v1`: one rgb camera, its latest frame, its stream's properties and its brightness | the copy's rendered `front` relay |

The ports are one per kind of server, each server preferring its own and
taking another from the operating system when it is held: 8900 an OpenArm's
commander, 8901 the AI brain, 8902 the simulated world, 8903 an SO-101's
commander.

A model reads two `instructions` blocks. The robot's says it is the robot's
own surface and is to be preferred; the simulated world's says it sets the
world up and is never a way to complete a task.

Each family is deployed by the part that owns what it publishes:

- **The robot's endpoint** is the `mcp_commander` option of the copy
  `alpha`, a simulated OpenArm v2
  ([openarm_v2_sim.json5](../openarm/fragments/openarm_v2_sim.json5)) with
  the rendered camera rig `cameras_sim`. It is the one MCP commander, the
  same fragment the physical robot runs: the backbone fills its two move
  targets and the copy's rig its three camera targets, the rendered relays
  here and the `uvc_camera` and `zed_camera` nodes on hardware, under the
  same ids. The exposure list is fixed and every target takes a link, so
  the commander requires the rig.
- **The simulated world's endpoint** is the `mcp_scene_commander` option
  ([mcp_scene_commander.json5](../simulation/fragments/mcp_scene_commander.json5))
  of the launcher's own `simulation_mcp` axis, deployed by the file, with a
  `none` option that switches it off. It binds the simulation alone, under
  an instance id of its own, so the browser scene panel runs beside it and
  it keeps running when `alpha` is removed. It binds `scene_lighting` and
  `scene_materials`, which Waldo alone implements, so the launcher refuses
  it under MuJoCo or Isaac Sim with that reason.

The rendered rig runs under every simulation, so the robot's endpoint does
too: `--with mujoco,simulation_mcp=none` serves it alone. Only Waldo models
the cameras' response, so under the other two engines the frame resources
and the `<camera>.info` tools work and the exposure, gain and white balance
setters refuse with a message, as the document tells a model to expect.

`peppy stack list` reports, for the bare launch:

| Node | Instance | Endpoint |
|---|---|---|
| `mcp_openarm_v2_v1:builtin` | `alpha_commander_inst` | `http://127.0.0.1:8900/openarm_v2/v1/mcp` |
| `mcp_simulation_v1:builtin` | `scene_mcp_inst` | `http://127.0.0.1:8902/simulation/v1/mcp` |

The AI brain's endpoint, `ai_brain:v1` on port 8901, appears when
`--with alpha.ai_brain` selects it. It follows the same rule: robot-side and
optional, so it stays its own document and its own deployment.

### Registering the endpoints in a client

Every endpoint speaks streamable HTTP. Register one server per family; the
key spelling below (`mcpServers`, a `type` and a `url` per server) is the
common shape, and a client that spells the transport differently takes the
same two URLs:

```json
{
  "mcpServers": {
    "openarm": { "type": "http", "url": "http://127.0.0.1:8900/openarm_v2/v1/mcp" },
    "simulation": { "type": "http", "url": "http://127.0.0.1:8902/simulation/v1/mcp" }
  }
}
```

The servers bind `127.0.0.1`, so the client runs on the machine that
launched the stack, or reaches it through a tunnel. Each endpoint keeps its
own catalog, subscriptions and task handles: a tool name or a resource on
one is unknown to the other.

### More than one robot

The launcher states its robots once: the `with` of the `robot` entry
selects the MCP commander and the rendered rig for every copy of
`openarm_v2_sim`, `alpha` and the robots a join adds alike. A plain join is
another MCP robot, serving the same `openarm_v2:v1` document on an endpoint
of its own:

```sh
peppy stack launch openarm_simulation_mcp
peppy stack join openarm_v2_sim -i bravo
peppy stack join openarm_v2_sim -i charlie
```

Every robot's server prefers port 8900. The first to start holds it, and a
server that finds it held takes a port from the operating system and
announces that one, so each join ends with an `MCP endpoints:` block naming
the new robot's URL, and `peppy stack list` reports all of them. The
instance name says which robot an endpoint drives, `bravo_commander_inst`
being `bravo`, and `openarm.get_identity` on an endpoint answers with that
robot's name:

| Node | Instance | Endpoint |
|---|---|---|
| `mcp_openarm_v2_v1:builtin` | `alpha_commander_inst` | `http://127.0.0.1:8900/openarm_v2/v1/mcp` |
| `mcp_openarm_v2_v1:builtin` | `bravo_commander_inst` | `http://127.0.0.1:41873/openarm_v2/v1/mcp` |
| `mcp_openarm_v2_v1:builtin` | `charlie_commander_inst` | `http://127.0.0.1:35291/openarm_v2/v1/mcp` |
| `mcp_simulation_v1:builtin` | `scene_mcp_inst` | `http://127.0.0.1:8902/simulation/v1/mcp` |

Register one server per robot, under a name that says which one it is, with
the URL the join printed:

```json
{
  "mcpServers": {
    "openarm_alpha": { "type": "http", "url": "http://127.0.0.1:8900/openarm_v2/v1/mcp" },
    "openarm_bravo": { "type": "http", "url": "http://127.0.0.1:41873/openarm_v2/v1/mcp" },
    "simulation": { "type": "http", "url": "http://127.0.0.1:8902/simulation/v1/mcp" }
  }
}
```

A port the operating system picked lasts as long as the robot runs. A
client configuration that outlives a relaunch fixes each robot's port on
the join, which then holds that port or, when something else has it,
another one it reports the same way:

```sh
peppy stack join openarm_v2_sim -i bravo --set-arguments commander_inst.port=8910
```

`peppy stack remove alpha` followed by `peppy stack join openarm_v2_sim -i
alpha` brings `alpha` back as the file deployed it, on 8900 again once the
port is free. The AI brain's server follows the same rule from its own
port, 8901, when a join selects it with `--with ai_brain`.

A `--with` word on a join wins on its own axis and the entry's other axes
stay selected, so the rendered rig runs for every robot and the robot's
fragment asks for a consumer of its streams. `--with xr_commander` is a
headset robot with the rig feeding its panels, `--with
web_commander,lerobot_recorder` is the browser panel with the recorder
filming, and `--with web_commander` alone is refused with the fragment's
reason. Each MCP robot adds three rendered cameras to the simulation.

The simulated world's endpoint binds the simulation, not a robot, so it
lists and places every robot whatever its commander and its model:
`scene.get_robots_list` names `alpha`, `bravo` and `charlie`, and
`scene.move_robot` moves the base of any of them.

### An SO-101 beside the OpenArm

`so101_sim`, the simulated SO-101
([so101_sim.json5](../so101/fragments/so101_sim.json5)), is an option of
this launcher's `robot` axis too. The launcher holds one deployment entry,
for `openarm_v2_sim`, and none for `so101_sim`: an entry lists a copy, which
would start an SO-101 at every launch. A plain joined SO-101 therefore comes
up as its fragment deploys it, with no commander and no camera, and the MCP
robot takes its words on the join:

```sh
peppy stack launch openarm_simulation_mcp
peppy stack join so101_sim -i charlo                                   # an SO-101 beside the OpenArm alpha
peppy stack join so101_sim -i delta --with mcp_commander,cameras_sim   # its front camera served over MCP
```

Its `mcp_commander`
([mcp_commander.json5](../so101/fragments/mcp_commander.json5)) serves the
MCP hub's `front_camera:v1` exposure, one target, `front_camera`, on the
`rgb_camera` contract, bound to the copy's `front` relay, a `sim_rgb_camera`,
which implements it. It requires the rendered rig `cameras_sim` and counts
as a consumer of its stream. The server prefers port 8903, and the join
prints the URL it took:

| Node | Instance | Endpoint |
|---|---|---|
| `mcp_front_camera_v1:builtin` | `delta_commander_inst` | `http://127.0.0.1:8903/front_camera/v1/mcp` |

A join cannot turn the simulation's rendering on. Here `alpha`'s rig turns
it on at launch, so the SO-101's camera comes up; joined to a plain
`openarm_simulation` or `so101_simulation`, whose copies select no rig, the
same join is refused and a plain SO-101 has no `front` camera. There the MCP
robot is a launch word on the file's copy:
`peppy stack launch so101_simulation --with alpha.mcp_commander,alpha.cameras_sim`.

Over MCP a model sees through the SO-101's camera
(`front_camera.latest_frame`, `front_camera.info`,
`front_camera.set_brightness`) and moves its base through the `simulation`
endpoint (`scene.move_robot`). Its arm is not drivable over MCP: the
exposure carries no move.

Under `--with mujoco,simulation_mcp=none` one robot stands at a time, so the
engine refuses a join beside `alpha` until `peppy stack remove alpha`.

### The same client on the real robot

The robot endpoint is the same document, `openarm_v2:v1`, whether the
backbone drives the simulation's relays or the CAN drivers and whether the
frames are rendered or captured, so a client written against it moves to
the real robot unchanged. The simulated world's endpoint has no counterpart
there (its title and instructions say so): drop the `simulation` entry, keep
`openarm`, and launch the fleet with the robot's MCP option and its rig:

```sh
peppy stack launch fleet
peppy stack join openarm_v2 -i alpha --with mcp_commander,cameras
```

### A session

The tool names are the exposures' public names, `<target>.<verb>`, as the
documents under `openarm/` and `simulation/` of the MCP hub write them.
With the stack up:

1. Find the robot: call `openarm.get_identity` on the robot endpoint. It
   reports `robot`, the name this endpoint's robot stands under, its
   `model` and the `core_node` hosting it. Then call
   `scene.get_robots_list` on the simulation endpoint: the entry whose
   `robot` matches is the robot this endpoint drives, with `position`, where
   its base stands in the simulated world, and `yaw`, which way it faces in
   radians about +Z. A point `p` in the robot's own frame, the frame
   `openarm.move_arm` poses are written in, stands in the simulated world at
   `position + Rz(yaw) * p`, which is how something is placed where the
   robot can reach it: `scene.spawn_object` takes a `yaw` of its own to
   turn what it places, and `scene.move_robot` takes the robot's.
2. Discover the lights: call `lighting.get_lighting` on the simulation
   endpoint. It lists every light with its id, kind, the properties it
   supports and each property's unit, bounds, authored default and current
   value; every `light_id` the setters take comes from this list.
3. Dim the key light: call `lighting.set_light_intensity` with the
   directional light's `light_id` and a lower illuminance in lux. The
   setter validates the whole request first, refuses out-of-bounds values
   without changing anything, and answers with the effective value after
   the call, so read the response rather than assuming the request took.
   The change shows in every view the engine renders, the robot's cameras
   included.
4. Look through a wrist camera: read the resource `wrist_left.latest_frame`
   on the robot endpoint, the latest frame as a JPEG, published at no more
   than 2 Hz. `wrist_left.info` reports the stream's resolution, frame rate
   and encoding; `wrist_left.set_exposure` and `wrist_left.set_gain` carry
   their bounds in their schemas and their modes and units in their
   descriptions.
5. Move the robot: call `openarm.move_to_ready` on the same endpoint, with
   `duration_s` 0 for as fast as the joint limits allow. It is a
   task-backed tool: the call returns a task handle, `tasks/get` reports
   its progress, and it completes when both arms reach the working
   posture. `openarm.move_arm` plans from there; the rest posture is not a
   place to plan Cartesian moves from.

The simulation endpoint sets the rest of the world up the same way:
`scene.get_assets_list` before `scene.spawn_object`, `scene.move_object`,
`scene.apply_force` or `scene.move_robot`, and `scene.load_scene` or
`scene.clear_scene` restore a scene's authored lighting and materials as
`lighting.reset_lighting` and `materials.reset_materials` do on their own.
It is for setting up and checking, never for completing a task: to move an
object, grasp it with the robot through the robot's endpoint.

### Reference stacks

```sh
peppy stack launch openarm_simulation --with web_scene_commander
peppy stack launch openarm_simulation_mcp
peppy stack launch openarm_simulation_mcp --with web_scene_commander
peppy stack launch openarm_simulation_mcp --with mujoco,simulation_mcp=none
peppy stack launch openarm_simulation --with alpha.robot_commander=mcp_commander,alpha.camera_rig=cameras_sim
peppy stack launch so101_simulation --with alpha.robot_commander=mcp_commander,alpha.camera_rig=cameras_sim
peppy stack launch fleet
peppy stack join openarm_v2 -i alpha --with mcp_commander,cameras
```

The first is the browser scene commander on Waldo with the default browser
robot commander: the scene panel, the lighting and materials panels Waldo
binds, and no MCP. The second is this launcher's bare launch: the two
endpoints and no browser panel. The third adds the browser scene commander
beside them, and with the rendered rig running its page carries the camera
panel too, listing the same three relays the robot endpoint serves. The
fourth is the robot endpoint alone, under MuJoCo. The fifth is the same
copy on the `openarm_simulation` launcher, selected by launch words: the
robot endpoint alone, since the simulated world's axis is this launcher's.
The sixth is a simulated SO-101 on its own launcher, its front camera's
endpoint alone. The last two are the real robot, the robot endpoint alone
with the three physical cameras behind it.

`peppy stack remove alpha` leaves the simulated world's endpoint running
with the simulation. `peppy stack resolve` previews any of these without
starting a node, its stdout the flat plan and its stderr the adjustment
report; `peppy stack launch` replaces the current stack.
