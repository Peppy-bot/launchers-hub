# MCP launchers

The launchers whose command surface is [MCP](https://modelcontextprotocol.io)
live here. Each composes the same robot and simulation fragments as the
other launchers, and selects for its robot copy a commander that is no node
at all: the server built into `peppy` serving exposures from the
[MCP hub](https://github.com/Peppy-bot/mcp-hub), one process on one port,
each exposure at `http://127.0.0.1:<port>/<name>/<tag>/mcp`. `peppy stack
list` reports every endpoint a running stack serves. The exposure
documents are the whole surface: what a client sees, its tool names, its
prose and its policies are written there, and this guide names them as the
documents do. See the [MCP exposure guide](https://docs.peppy.bot/advanced_guides/mcp/)
for the document format and the [repository README](../Readme.md) for the
option table and the copy semantics.

## openarm_simulation_mcp

[openarm_simulation_mcp.json5](openarm_simulation_mcp.json5) runs Waldo
and deploys the copy `alpha`, a simulated OpenArm v2 with the
`mcp_sim_commander` option of
[openarm_v2_sim.json5](../openarm/fragments/openarm_v2_sim.json5) and the
rendered camera rig `cameras_sim`. The commander's server serves five
endpoints on port 8900:

| Endpoint | Exposure | Bound to |
|---|---|---|
| `http://127.0.0.1:8900/openarm_v2/v1/mcp` | `openarm_v2:v1`: the posture, arm and gripper moves | the copy's backbone |
| `http://127.0.0.1:8900/scene_manipulation/v1/mcp` | `scene_manipulation:v1`: the asset catalogue, spawned objects and the robot's placement | the simulation |
| `http://127.0.0.1:8900/scene_lighting/v1/mcp` | `scene_lighting:v1`: the scene's light sources | the simulation |
| `http://127.0.0.1:8900/scene_materials/v1/mcp` | `scene_materials:v1`: the robot's materials | the simulation |
| `http://127.0.0.1:8900/openarm_v2_sim_cameras/v1/mcp` | `openarm_v2_sim_cameras:v1`: the three rendered cameras and their device profiles | the copy's camera relays |

Waldo is the simulation the file deploys: it alone implements the lighting
and materials contracts, and the fragment's constraint refuses
`mcp_sim_commander` under MuJoCo or Isaac Sim with that reason. The
exposure list is fixed, so the rendered rig is required beside the
commander: its three relays fill the camera targets, one camera target and
one profile target per relay. The real robot's `mcp_commander` serves the
robot endpoint alone and is untouched; a copy runs one MCP option or the
other.

### Registering the endpoints in a client

Every endpoint speaks streamable HTTP. Register each one as its own server
in the client's configuration; the key spelling below (`mcpServers`, a
`type` and a `url` per server) is the common shape, and a client that
spells the transport differently takes the same five URLs:

```json
{
  "mcpServers": {
    "openarm": { "type": "http", "url": "http://127.0.0.1:8900/openarm_v2/v1/mcp" },
    "scene": { "type": "http", "url": "http://127.0.0.1:8900/scene_manipulation/v1/mcp" },
    "lighting": { "type": "http", "url": "http://127.0.0.1:8900/scene_lighting/v1/mcp" },
    "materials": { "type": "http", "url": "http://127.0.0.1:8900/scene_materials/v1/mcp" },
    "cameras": { "type": "http", "url": "http://127.0.0.1:8900/openarm_v2_sim_cameras/v1/mcp" }
  }
}
```

The server binds `127.0.0.1`, so the client runs on the machine that
launched the stack, or reaches it through a tunnel. Each endpoint keeps
its own catalog, subscriptions and task handles: a tool name or a resource
on one is unknown to the others.

### The same client on the real robot

The robot endpoint is the same document, `openarm_v2:v1`, whether the
backbone drives the simulation's relays or the CAN drivers, so a client
written against it moves to the real robot unchanged. The four simulation
endpoints have no counterpart there (their titles and instructions say
so): drop those four entries, keep `openarm`, and launch the fleet with the
robot's own MCP option:

```sh
peppy stack launch fleet
peppy stack join openarm_v2 -i alpha --with mcp_commander
```

### A session

The tool names are the exposures' public names, `<target>.<verb>`, as the
documents under `simulation/` and `openarm/` of the MCP hub write them.
With the stack up:

1. Discover the lights: call `lighting.get_lighting` on the lighting
   endpoint. It lists every light with its id, kind, the properties it
   supports and each property's unit, bounds, authored default and current
   value; every `light_id` the setters take comes from this list.
2. Dim the key light: call `lighting.set_light_intensity` with the
   directional light's `light_id` and a lower illuminance in lux. The
   setter validates the whole request first, refuses out-of-bounds values
   without changing anything, and answers with the effective value after
   the call, so read the response rather than assuming the request took.
   The change shows in every view the engine renders, the rendered cameras
   included.
3. Look through a wrist camera: read the resource `wrist_left.latest_frame`
   on the cameras endpoint, the latest rendered frame as a JPEG, published
   at no more than 2 Hz. `wrist_left.profile` says which controls the
   simulated Arducam models, with their modes and bounds, before a
   `wrist_left.set_exposure` or `wrist_left.set_gain`; `wrist_left.reset`
   restores the device defaults.
4. Move the robot: call `openarm.move_to_ready` on the robot endpoint,
   with `duration_s` 0 for as fast as the joint limits allow. It is a
   task-backed tool: the call returns a task handle, `tasks/get` reports
   its progress, and it completes when both arms reach the working
   posture. `openarm.move_arm` plans from there; the rest posture is not a
   place to plan Cartesian moves from.

The scene endpoint sets the world up the same way: `scene.get_assets_list`
before `scene.spawn_object`, `scene.move_object`, `scene.apply_force` or
`scene.move_robot`, and `scene.load_scene` or `scene.clear_scene` restore
a scene's authored lighting and materials as `lighting.reset_lighting` and
`materials.reset_materials` do on their own.

### Reference stacks

```sh
peppy stack launch openarm_simulation --with web_scene_commander
peppy stack launch openarm_simulation_mcp
peppy stack launch openarm_simulation_mcp --with web_scene_commander
peppy stack launch openarm_simulation --with alpha.robot_commander=mcp_sim_commander,alpha.camera_rig=cameras_sim
peppy stack launch fleet
peppy stack join openarm_v2 -i alpha --with mcp_commander
```

The first is the browser scene commander on Waldo with the default browser
robot commander: the scene panel, the lighting and materials panels Waldo
binds, and no MCP. The second is this launcher's bare launch: the five
endpoints and no browser panel. The third adds the browser scene commander
beside the endpoints, and with the rendered rig running its page carries
the camera panel too, listing the same three relays the cameras endpoint
serves. The fourth is the same copy on the `openarm_simulation` launcher,
selected by launch words. The last two are the real robot, the robot
endpoint alone.

`peppy stack resolve` previews any of them without starting a node, its
stdout the flat plan and its stderr the adjustment report; `peppy stack
launch` replaces the current stack.
