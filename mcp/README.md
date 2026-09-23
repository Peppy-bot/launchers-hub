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

## The endpoints

`simulation_mcp` serves both endpoints, and the other launchers with
robots serve the robots' with `--with robot_control`. The boundary between the two is
whether what a model does transfers to the physical robots:

| Family | Endpoint | Exposure | Bound to | On the physical robots |
|---|---|---|---|---|
| Robots | `http://127.0.0.1:8900/robot_control/v1/mcp` | `robot_control:v1`: every robot of the stack by name: who it is, its posture, arm and gripper moves, its limb state, its cameras and their depth, its brain and its recorder | each robot's initializer, backbone, rig, brain and recorder | yes |
| Simulated world | `http://127.0.0.1:8902/simulation/v1/mcp` | `simulation:v1`: the scene, the controls of its spawned objects, its light sources, its materials | the simulation | no |

The two URLs are the stack's: the same for one robot or ten, and the same
before and after any join or removal, so a client registers them once. A
model reads two `instructions` blocks. The robots' says it is the robots'
own surface and is to be preferred; the simulated world's says it sets the
world up and is never a way to complete a task.

Each endpoint is an axis of the file that owns what it publishes:

- **The robots' endpoint** is the `robot_control` axis of every launcher with robots
  ([fragments/robot_control.json5](fragments/robot_control.json5)), deployed by
  `simulation_mcp` and selected with `--with robot_control` on the others, one server
  for the whole stack, reading the simulation's clock in a simulation
  launcher and wall time in `physical.json5`. Every target of `robot_control:v1` is a set the
  robots fill, so the server takes no link of its own, and each fragment a
  robot's copy runs adds what it deploys with `add_links` on `robot_control_inst`.
  The family's control
  ([openarm](../openarm/fragments/control_common.json5),
  [so101](../so101/fragments/control_common.json5)) lists the robot with
  its initializer for its identity and its backbone for its limb state and
  collision readout, so every robot of the stack is listed with what it is
  and how it stands. The
  [recorder](../recording/fragments/lerobot_recorder.json5) lists itself
  whenever it runs, so an episode starts over the endpoint under any
  commander. The
  [mcp_commander](../robot_commanders/fragments/mcp_commander.json5)
  option adds the backbone's moves, and under that option the
  [brain](../openarm/fragments/ai_brain.json5) adds its actions and the rig
  ([cameras](../openarm/fragments/cameras.json5),
  [cameras_sim](../openarm/fragments/cameras_sim.json5)) adds the cameras:
  everything that moves the robot's arms is listed under one option, so a
  robot under the browser panel or the headset is listed with its state and
  its recorder and no way to move it, and a robot without a rig with no
  camera. A join adds its robot's instances to the
  running server and a removal takes them out: the robot is listed when the
  join returns and gone when the remove returns. `--with robot_control=none` launches
  `simulation_mcp` without it, and a robot's `mcp_commander` is refused
  wherever the endpoint is off, with its reason.
- **The simulated world's endpoint** is the `world_control` axis of
  `simulation_mcp` ([fragments/world_control.json5](fragments/world_control.json5)),
  deployed by the file, with a `none` option that switches it off. It binds
  the simulation alone, under an instance id of its own, so the browser
  scene panel runs beside it. It binds `object_controls`, `scene_lighting`
  and `scene_materials`, which Waldo alone implements, so the launcher refuses
  it under MuJoCo or Isaac Sim with that reason.

Both endpoints bind the simulation or nothing, so both keep running when
every robot is removed. Only Waldo models the cameras' response, so under
the other two engines the frame resources, `camera.info` and the geometry
tools work and the exposure, gain and white balance setters refuse with a
message, as the document tells a model to expect.

## simulation_mcp

[simulation_mcp.json5](simulation_mcp.json5) is `openarm_simulation` with
both endpoints deployed and every robot driven over MCP by default. It
lists one robot, `alpha`, an OpenArm v2, and each robot entry states, for
every copy of its option, the MCP commander, with the rendered rig on the
v2 and the SO-101, so `alpha`, the robots a launch names with `--join OPTION:NAME` and the robots
joined later alike are driven over MCP. The launcher turns the simulation's
rendering on itself, which a join cannot, so the first robot joined onto it
gets its cameras.

`peppy stack list` reports, for the bare launch:

| Node | Instance | Endpoint |
|---|---|---|
| `mcp_robot_control_v1:builtin` | `robot_control_inst` | `http://127.0.0.1:8900/robot_control/v1/mcp` |
| `mcp_simulation_v1:builtin` | `world_control_inst` | `http://127.0.0.1:8902/simulation/v1/mcp` |

### Registering the endpoints in a client

Every endpoint speaks streamable HTTP. Register one server per family; the
key spelling below (`mcpServers`, a `type` and a `url` per server) is the
common shape, and a client that spells the transport differently takes the
same two URLs:

```json
{
  "mcpServers": {
    "robots": { "type": "http", "url": "http://127.0.0.1:8900/robot_control/v1/mcp" },
    "simulation": { "type": "http", "url": "http://127.0.0.1:8902/simulation/v1/mcp" }
  }
}
```

The servers bind `127.0.0.1`, so the client runs on the machine that
launched the stack, or reaches it through a tunnel. Each endpoint keeps its
own catalog, subscriptions and task handles: a tool name or a resource on
one is unknown to the other. Each server prefers its port and takes another
from the operating system when it is held, printing the one it took under
`MCP endpoints:`; the port is the `arguments.port` of
[fragments/robot_control.json5](fragments/robot_control.json5).

### One robot, two, many

```sh
peppy stack launch simulation_mcp                                                  # Waldo, the two endpoints, alpha listed
peppy stack launch simulation_mcp --join so101_sim:charlie                         # alpha and an SO-101
peppy stack launch simulation_mcp --join openarm_v2_sim:bravo --join so101_sim:charlie   # two OpenArms and an SO-101
peppy stack join openarm_v2_sim:bravo                                              # a second OpenArm, listed when the join returns
peppy stack join so101_sim:foxtrot                                                 # another SO-101, with its front camera
peppy stack join openarm_v2_sim:delta --with ai_brain                              # an OpenArm with a brain, its brain tools on the same URL
peppy stack join openarm_v2_sim:echo --with xr_commander                           # one under the headset, listed with no moves
peppy stack remove bravo                                                           # gone from the listing when the remove returns
peppy stack remove alpha                                                           # the endpoints keep running, listing the rest
```

`robot.list` on the robots' endpoint names every robot present, and every
other tool takes `robot`, one of those names. A robot's name is its copy's
name, so `alpha` here is `alpha` in the simulated world's
`scene.get_robots_list` too. The listing carries, per robot, its identity
(model and host), its arms and grippers, its cameras under `members`
(`camera` for color cameras, `depth_camera` for the ones carrying depth),
the capabilities its tools answer for it, and notes on anything that could
not be read. A call naming a robot that is not there, a capability it does
not fill or a camera it does not have is refused, and the refusal names what
is there. Resources are published per robot: `alpha/robot.limb_state`,
`alpha/wrist_left/camera.latest_frame`, `alpha/chest/depth_camera.latest_depth`,
and the server sends `resources/list_changed` when a join or a removal
changes the list.

A `--with` word on a join wins on its own axis and the entry's other axes
stay selected, so the rendered rig runs for every v2 and SO-101: `--with
xr_commander` is a headset robot with the rig feeding its panels, and
`--with web_commander,lerobot_recorder` is the browser panel with the
recorder filming. `--with
robot_control=none,alpha.web_commander,alpha.lerobot_recorder` on the
launch is the world's endpoint alone, alpha under the browser panel with
the recorder filming its rig; a robot joined onto it needs a commander word
too, since `mcp_commander` requires the endpoint. Each MCP OpenArm adds three
rendered cameras to the simulation and an SO-101 one, `front`.

The simulated world's endpoint binds the simulation, not a robot, so it
lists and places every robot whatever its commander and its model:
`scene.get_robots_list` names `alpha`, `charlie`, `delta`, `echo` and
`foxtrot`, and `scene.move_robot` moves the base of any of them.

`--with mujoco,world_control=none` is the robots' endpoint alone; `--with
mujoco` on its own is refused, since the world's endpoint needs Waldo.
Every simulation stands any number of robots at once.

### The same client on the real robots

The robots' endpoint is the same document, `robot_control:v1`, whether a
backbone drives the simulation's relays or the CAN drivers and whether the
frames are rendered or captured, so a client written against it moves to the
real robots unchanged. On hardware the `uvc_camera_linux` and `zed_camera` nodes
fill the color and depth targets, and the `cameras` rigs list no profile or
geometry, so `camera_profile.get` and the `camera_geometry` tools refuse for
those cameras and the listing's capabilities say so. The simulated world's
endpoint has no counterpart there (its title and instructions say so): drop
the `simulation` entry, keep `robots`, and launch `physical` with the
endpoint, joining each robot with its MCP option and rig:

```sh
peppy stack launch physical --with robot_control
peppy stack join openarm_v2:alpha --with mcp_commander,cameras --place jetson-1
peppy stack join so101:bravo --with mcp_commander,cameras
```

The server reads one clock: the simulation's beside a simulation and wall
time on the physical robots, so a stack under MCP is real or simulated, and
the physical robots have a launcher of their own.

### A session

The tool names are the exposures' public names, `<target>.<verb>`, as the
documents under `robot/` and `simulation/` of the MCP hub write them. With
the stack up and `alpha` in it:

1. Find the robots: call `robot.list` on the robots' endpoint. Every entry
   carries `robot`, the name every other tool takes, its `identity`
   (`model` and the `core_node` hosting it), its `limbs`, its cameras and
   its capabilities. Then call `scene.get_robots_list` on the simulation
   endpoint: the entry whose `robot` matches is the same robot, with
   `position`, where its base stands in the simulated world, and `yaw`,
   which way it faces in radians about +Z. A point `p` in the robot's own
   frame, the frame `robot.move_arm` poses are written in, stands in the
   simulated world at `position + Rz(yaw) * p`, which is how something is
   placed where the robot can reach it: `scene.spawn_object` takes a `yaw`
   of its own to turn what it places, and `scene.move_robot` takes the
   robot's.
2. Discover the lights: call `lighting.get_lighting` on the simulation
   endpoint. It lists every light with its id, kind, the properties it
   supports and each property's unit, bounds, authored default and current
   value; every `light_id` the setters take comes from this list.
3. Dim the key light: call `lighting.set_light_intensity` with the
   directional light's `light_id` and a lower illuminance in lux. The
   setter validates the whole request first, refuses out-of-bounds values
   without changing anything, and answers with the effective value after
   the call, so read the response rather than assuming the request took.
   The change shows in every view the engine renders, the robots' cameras
   included.
4. Look through a wrist camera: read the resource
   `alpha/wrist_left/camera.latest_frame` on the robots' endpoint, the
   latest frame as a JPEG, published at no more than 2 Hz, and
   `alpha/chest/depth_camera.latest_depth`, the chest's depth as a 16-bit
   PNG in the unit `depth_camera.depth_info` reports. `camera.info` with
   `robot: alpha, camera: wrist_left` reports the stream's resolution,
   frame rate and encoding; `camera.set_exposure` and `camera.set_gain`
   carry their bounds in their schemas and their modes and units in their
   descriptions.
5. Move the robot: call `robot.move_to_ready` with `robot: alpha` and
   `duration_s` 0 for as fast as the joint limits allow. It is an
   action-backed tool: for a client that declares the MCP tasks extension
   the call returns a task handle and `tasks/get` reports its progress; for
   any other client the call itself answers once the move settles. Either
   way it completes when both arms reach the working posture.
   `robot.move_arm` plans from there, naming one of the arms `robot.list`
   gave the robot; the rest posture is not a place to plan Cartesian moves
   from.

The simulation endpoint sets the rest of the world up the same way:
`scene.get_assets_list` before `scene.spawn_object`, `scene.move_object`,
`scene.apply_force` or `scene.move_robot`, and `scene.load_scene` or
`scene.clear_scene` restore a scene's authored lighting and materials as
`lighting.reset_lighting` and `materials.reset_materials` do on their own.
It is for setting up and checking, never for completing a task: to move an
object, grasp it with a robot through the robots' endpoint.

### Reference stacks

```sh
peppy stack launch openarm_simulation --with web_scene_commander
peppy stack launch simulation_mcp
peppy stack launch simulation_mcp --with web_scene_commander
peppy stack launch simulation_mcp --with mujoco,world_control=none
peppy stack launch openarm_simulation --with robot_control,alpha.mcp_commander,alpha.cameras_sim
peppy stack launch so101_simulation --with robot_control,alpha.mcp_commander,alpha.cameras_sim
peppy stack launch physical --with robot_control
peppy stack join openarm_v2:alpha --with mcp_commander,cameras
```

The first is the browser scene commander on Waldo with `alpha` under the
browser panel: the scene panel, the lighting and materials panels Waldo
binds, and no MCP endpoint. The second is this launcher's
bare launch: the two endpoints and `alpha`, with no browser panel. The
third adds the browser scene commander beside them, and with the rendered
rig running its page carries the camera panel too, listing the same three
relays the robots' endpoint serves. The fourth is the
robots' endpoint alone, under MuJoCo. The fifth is the same robot on the
`openarm_simulation` launcher, selected by launch words: the robots'
endpoint alone, since the simulated world's axis is this launcher's. The
sixth is a
simulated SO-101 on its own launcher, its arm and front camera on the
robots' endpoint. The last two are the real robot, the robots' endpoint
alone with the three physical cameras behind it.

`peppy stack remove alpha` leaves both endpoints running with the
simulation. `peppy stack resolve` previews any of these without starting a
node, its stdout the flat plan and its stderr the adjustment report;
`peppy stack launch` replaces the current stack.
