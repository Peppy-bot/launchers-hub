# Launchers Hub

A collection of [Peppy](https://github.com/Peppy-bot/peppy) launcher files for robotic systems. Each launcher describes a complete node stack, which nodes to deploy, how many instances of each, and how they are wired together.

## Repository Structure

Launchers are grouped by robot or system:

```text
<system_name>/
├── <launcher_name>.json5
└── fragments/            # optional: bodies a launcher's component options share
```

Each `.json5` file is a self-contained launcher manifest declaring its `deployments`: the nodes it pulls from the [Nodes Hub](https://github.com/Peppy-bot/nodes_hub) (by `name`, `tag`, and optional `variant`) and the named `instances` to spawn from each. A launcher may also declare `components`, making it a family of stacks whose members are selected at launch time with `--with`; each option's body is written inline or lives in a `launcher_fragment/v1` file under `fragments/`. A fragment is referenced by the option that wants it, is never listed in `peppy_repository.json5`, and is never a launchable stack of its own.

## Launcher Variants

One composed launcher can describe every configuration of one system, one `--with` selection each (see the `openarm/` launchers). Common patterns include:

- **Real hardware vs simulator** as options of a `robot` axis (`real`, `mujoco`, `isaac_sim`)
- **Operator surface** as options of a `commander` axis (browser panel, XR headset)
- **Add-on features** as optional axes (a recorder, a camera set)

See the [Peppy documentation](https://docs.peppy.bot/guides/launch-files/) for the full launcher schema.

## Adding an item to this repository

This repository publishes what `peppy_repository.json5` says it publishes, and nothing else. An item
that is not listed there is invisible to peppy, so after adding, moving, or renaming a launcher, run:

```sh
peppy repo index .
```

Commit the updated `peppy_repository.json5` alongside your change, and run
`peppy repo index --check` before pushing: it fails if the index has drifted
from the repository, naming the file and the identity involved.

Generation refuses, naming both files, if your change claims a launcher name another one already
publishes. Rename yours: within one repository, a launcher name (its file stem) is claimed by exactly one file.
