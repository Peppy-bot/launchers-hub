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
| Robot | `http://127.0.0.1:8900/openarm_v2/v1/mcp` | `openarm_v2:v1`: the posture, arm and gripper moves, the three cameras and their controls | the copy's backbone and camera rig | yes |
| Simulated world | `http://127.0.0.1:8902/simulation/v1/mcp` | `simulation:v1`: the scene, its light sources, its materials | the simulation | no |

A model reads two `instructions` blocks. The robot's says it is the robot's
own surface and is to be preferred; the simulated world's says it sets the
world up and is never a way to complete a task.

Each family is deployed by the part that owns what it publishes:

- **The robot's endpoint** is the `mcp_commander` option of the copy
  `alpha`, a simulated OpenArm v2
  ([openarm_v2_sim.json5](../openarm/fragments/openarm_v2_sim.json5)) with
  the rendered camera rig `cameras_sim`. The launcher writes that selection
  on its robot entry, so every robot joined later is the same robot with an
  endpoint of its own ([More than one robot](#more-than-one-robot)). It is
  the one MCP commander, the
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

### More than one robot

One endpoint per robot. The launcher states its robots once, on the
`robot: "openarm_v2_sim"` entry, and a join starts from that entry as
`alpha` does, so a plain join brings up another MCP robot with its rendered
rig and a server of its own:

```sh
peppy stack launch openarm_simulation_mcp
peppy stack join openarm_v2_sim -i bravo
```

Every robot's server prefers port 8900. `alpha` holds it; a server that
finds it held takes a port from the operating system and announces that
one, so the join ends with the new robot's URL under `MCP endpoints:` and
`peppy stack list` reports all of them, the instance name saying which
robot an endpoint drives (the port `bravo` took is whatever the system
gave it):

| Node | Instance | Endpoint |
|---|---|---|
| `mcp_openarm_v2_v1:builtin` | `alpha_commander_inst` | `http://127.0.0.1:8900/openarm_v2/v1/mcp` |
| `mcp_openarm_v2_v1:builtin` | `bravo_commander_inst` | `http://127.0.0.1:41873/openarm_v2/v1/mcp` |
| `mcp_simulation_v1:builtin` | `scene_mcp_inst` | `http://127.0.0.1:8902/simulation/v1/mcp` |

Register one server per robot in the client, under a key that names the
robot, beside the simulated world's:

```json
{
  "mcpServers": {
    "openarm_alpha": { "type": "http", "url": "http://127.0.0.1:8900/openarm_v2/v1/mcp" },
    "openarm_bravo": { "type": "http", "url": "http://127.0.0.1:41873/openarm_v2/v1/mcp" },
    "simulation": { "type": "http", "url": "http://127.0.0.1:8902/simulation/v1/mcp" }
  }
}
```

A port the system chose changes from one launch to the next. A client
configuration that has to survive a relaunch fixes each joined robot's
port on the join:

```sh
peppy stack join openarm_v2_sim -i bravo --set-arguments commander_inst.port=8910
```

`peppy stack remove alpha` followed by `peppy stack join openarm_v2_sim -i
alpha` brings `alpha` back as the file deployed it, on 8900 again once the
port is free.

The simulated world's endpoint lists and places every robot whatever its
commander: `scene.get_robots_list` names `alpha` and `bravo`, and
`scene.move_robot` moves either base. A join word wins on the axis it
names and the entry's rig stays selected: `--with xr_commander` joins a
robot under the headset, its cameras streamed to it, while a commander
that consumes no camera (`--with web_commander`) is refused by the robot
fragment's constraint, which names the consumers the rig accepts. Each MCP
robot adds three rendered cameras to the simulation's frame budget.

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

1. Discover the lights: call `lighting.get_lighting` on the simulation
   endpoint. It lists every light with its id, kind, the properties it
   supports and each property's unit, bounds, authored default and current
   value; every `light_id` the setters take comes from this list.
2. Dim the key light: call `lighting.set_light_intensity` with the
   directional light's `light_id` and a lower illuminance in lux. The
   setter validates the whole request first, refuses out-of-bounds values
   without changing anything, and answers with the effective value after
   the call, so read the response rather than assuming the request took.
   The change shows in every view the engine renders, the robot's cameras
   included.
3. Look through a wrist camera: read the resource `wrist_left.latest_frame`
   on the robot endpoint, the latest frame as a JPEG, published at no more
   than 2 Hz. `wrist_left.info` reports the stream's resolution, frame rate
   and encoding; `wrist_left.set_exposure` and `wrist_left.set_gain` carry
   their bounds in their schemas and their modes and units in their
   descriptions.
4. Move the robot: call `openarm.move_to_ready` on the same endpoint, with
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
The last two are the real robot, the robot endpoint alone with the three
physical cameras behind it.

`peppy stack remove alpha` leaves the simulated world's endpoint running
with the simulation. `peppy stack resolve` previews any of these without
starting a node, its stdout the flat plan and its stderr the adjustment
report; `peppy stack launch` replaces the current stack.
