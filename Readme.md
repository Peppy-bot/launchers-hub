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

- **Real hardware** (e.g. `openarm_v1_teleop`, `openarm_v2_teleop`): runs against the physical robot
- **Simulator-specific** (e.g. `openarm_v1_teleop_isaac`, `openarm_v2_teleop_mujoco`): same stack wired to a simulator

See the [Peppy documentation](https://github.com/Peppy-bot/peppy) for the full launcher schema.

## Adding an item to this repository

This repository publishes what `peppy_repository.json5` says it publishes, and nothing else. An item
that is not listed there is invisible to peppy, so after adding, moving, or renaming a launcher, run:

```sh
peppy repo index .
```

Commit the updated `peppy_repository.json5` alongside your change. CI runs `peppy repo index --check`
on every pull request and fails if the index has drifted from the repository, naming the file and the
identity involved.

Generation refuses, naming both files, if your change claims a launcher name another one already
publishes. Rename yours: within one repository, a launcher name (its file stem) is claimed by exactly one file.
