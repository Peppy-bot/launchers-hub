# Launchers Hub

A collection of [Peppy](https://github.com/Peppy-bot/peppy) launcher files for robotic systems. Each launcher describes a complete node stack, which nodes to deploy, how many instances of each, and how they are wired together.

## Repository Structure

Launchers are grouped by robot or system:

```text
<system_name>/
└── <launcher_name>.json5
```

Each `.json5` file is a self-contained launcher manifest declaring its `deployments`: the nodes it pulls from the [Nodes Hub](https://github.com/Peppy-bot/nodes_hub) (by `name`, `tag`, and optional `variant`) and the named `instances` to spawn from each.

## Launcher Variants

Different launcher files describe alternative configurations of the same system. Common patterns include:

- **Real hardware** (e.g. `openarm_teleop`): runs against the physical robot
- **Simulator-specific** (e.g. `openarm_teleop_isaac`, `openarm_teleop_mujoco`): same stack wired to a simulator

See the [Peppy documentation](https://github.com/Peppy-bot/peppy) for the full launcher schema.
