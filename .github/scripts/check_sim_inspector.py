#!/usr/bin/env python3
"""Resolve real launcher compositions and hold simulator inspection to explicit opt-in."""

import argparse
import os
import subprocess
from pathlib import Path

from launcher_combinations import _Parser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peppy", default="peppy")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    # Camera/recorder and MCP selections must not accidentally grant observation
    # privileges. Scene Commander enables those privileges only for Waldo.
    cases = [
        ("waldo", ["hand_teleop"]),
        ("waldo,mcp_commander", ["hand_teleop"]),
        ("waldo,xr_commander,sim_cameras", ["hand_teleop"]),
        ("waldo,scene_commander", ["hand_teleop", "sim_inspector"]),
        ("waldo,mcp_commander,scene_commander", ["hand_teleop", "sim_inspector"]),
        ("isaac_sim,scene_commander", []),
    ]
    for words, expected in cases:
        resolved = subprocess.run(
            [args.peppy, "stack", "resolve", "openarm/openarm_v2.json5", "--with", words],
            cwd=root, capture_output=True, text=True, check=True,
            env={**os.environ, "RUST_LOG": "error"},
        )
        launcher = _Parser(resolved.stdout, f"resolved {words}").parse_document()
        engines = [
            instance
            for deployment in launcher["deployments"]
            for instance in deployment["instances"]
            if instance["instance_id"] == "sim_inst"
        ]
        assert len(engines) == 1, f"{words}: one simulation engine"
        plugins = [name.strip() for name in engines[0]["arguments"].get("plugins", "").split(",") if name.strip()]
        assert plugins == expected, f"{words}: plugins {plugins}, expected {expected}"
        print(f"{words}: plugins {plugins}")


if __name__ == "__main__":
    main()
