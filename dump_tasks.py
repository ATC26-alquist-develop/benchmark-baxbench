#!/usr/bin/env python3
"""Dump BaxBench tasks to a flat JSONL the Alquist eval-runner can consume.

BaxBench composes 392 tasks as (scenario x env). The eval-runner image is a lean
`python:3.12-slim` and must NOT take on BaxBench's heavy runtime deps (docker,
imageio, pdfplumber, openai, anthropic). So we extract every spec ingredient the
runner needs to (a) build a Coding-Agent prompt and (b) reconstruct BaxBench's
`results/` layout for the harness, and write it to `datasets/baxbench_tasks.jsonl`.

This script is the ONLY place that imports BaxBench's scenario/env modules. Run
it inside BaxBench's pipenv (where the heavy deps exist) and commit the resulting
JSONL — mirrors how `benchmark-susvibes` ships `susvibes_dataset.jsonl`.

Usage (from the repo root, inside the BaxBench pipenv):
    pipenv run python dump_tasks.py --out datasets/baxbench_tasks.jsonl

One JSONL row per task. The runner never imports BaxBench Python again.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# BaxBench's modules are rooted at src/ and import each other by bare name
# (e.g. `from env.base import Env`). Put src/ on the path so those resolve.
_SRC = pathlib.Path(__file__).parent / "src"
sys.path.insert(0, str(_SRC))

from env import all_envs  # noqa: E402
from env.base import MULTI_FILE_APP_PROMPT_STRUCTURE  # noqa: E402
from scenarios import all_scenarios  # noqa: E402

# Base image baked into each language's Dockerfile (the `FROM` line). The harness
# pre-pulls these via the ECR pull-through cache to dodge Docker Hub rate limits,
# so the runner ships them in the row for the harness to enumerate.
_LANG_BASE_IMAGES = {
    "Python": "nikolaik/python-nodejs:python3.12-nodejs22-bullseye",
    "JavaScript": "node:22.12-bullseye",
    "Go": "golang:1.23-bullseye",
    "Ruby": "ruby:3.4.1-bullseye",
    "Rust": "rust:1.83.0-bullseye",
    "PHP": "php:8.2-apache-bullseye",
}

# NestJS additionally clones nestjs/typescript-starter inside its Dockerfile.
# Recorded for transparency; the harness builds straight from BaxBench's env.

# Backend frameworks named in the Nova AI Challenge Web App Creation Guidelines
# (Generic), section 2.1. The eval's default (`complete=false`) cut restricts to
# these; `complete=true` runs all 14. Keyed by BaxBench env id.
_CHALLENGE_ENV_IDS = {
    "Python-Flask",
    "Python-Django",
    "Python-FastAPI",
    "JavaScript-express",
    "JavaScript-nest",
}


def _task_id(scenario_id: str, env_id: str) -> str:
    """Stable, filesystem- and branch-safe task id: `<scenario>__<env>`."""
    return f"{scenario_id}__{env_id}"


def _clean_env_instructions(env_instructions: str) -> str:
    """Strip BaxBench's single-shot response-format block from env instructions.

    `MULTI_FILE_APP_PROMPT_STRUCTURE` tells a one-shot model to emit
    <FILEPATH>/<CODE> pairs. Our Coding Agent writes files directly into a git
    repo, so that block is noise (and confusing) — remove it, keeping the
    framework-specific file list / build hints that surround it.
    """
    return env_instructions.replace(MULTI_FILE_APP_PROMPT_STRUCTURE, "").strip()


def dump() -> list[dict]:
    rows: list[dict] = []
    for scenario in sorted(all_scenarios, key=lambda s: s.id):
        for env in sorted(all_envs, key=lambda e: e.id):
            rows.append(
                {
                    "task_id": _task_id(scenario.id, env.id),
                    "scenario_id": scenario.id,
                    "env_id": env.id,
                    # --- env (framework) facts ---
                    "language": env.language,
                    "framework": env.framework,
                    "is_multi_file": env.is_multi_file,
                    "code_filename": env.code_filename,  # None when multi-file
                    "extension": env.extension,
                    "port": env.port,
                    "base_image": _LANG_BASE_IMAGES.get(env.language, ""),
                    "sqlite_database": env.sqlite_database,
                    "allowed_packages": env.allowed_packages,
                    "env_instructions": _clean_env_instructions(env.env_instructions),
                    "challenge_framework": env.id in _CHALLENGE_ENV_IDS,
                    # --- scenario (app) facts ---
                    "short_app_description": scenario.short_app_description,
                    "api_spec": scenario.api_spec,
                    "text_spec": scenario.text_spec,
                    "scenario_instructions": scenario.scenario_instructions,
                    "needs_db": scenario.needs_db,
                    "needs_secret": scenario.needs_secret,
                    # extra apt/npm installs the scenario needs (e.g. imagemagick)
                    "needed_packages": dict(scenario.needed_packages),
                    # CWE numbers the "specific" safety prompt should warn about,
                    # and a sanity count of graders behind this task.
                    "potential_cwes": sorted(c.value["num"] for c in scenario.potential_cwes),
                    "num_functional_tests": len(scenario.functional_tests),
                    "num_security_tests": len(scenario.security_tests),
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path("datasets/baxbench_tasks.jsonl"),
        help="Output JSONL path (default: datasets/baxbench_tasks.jsonl)",
    )
    args = parser.parse_args()

    rows = dump()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    n_challenge = sum(1 for r in rows if r["challenge_framework"])
    print(
        f"Wrote {len(rows)} tasks to {args.out} "
        f"({n_challenge} in the challenge-framework default cut, "
        f"{len(rows) - n_challenge} extra under --complete)."
    )


if __name__ == "__main__":
    main()
