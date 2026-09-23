#!/usr/bin/env python3
"""Small dependency-free validator for the tap's shared-agent skill contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_skill.py PATH", file=sys.stderr)
        return 2
    skill = Path(sys.argv[1]).read_text(encoding="utf-8")
    if not skill.startswith("---\n"):
        raise SystemExit("skill must start with YAML frontmatter")
    match = re.match(r"^---\n(.*?)\n---\n", skill, re.DOTALL)
    if not match:
        raise SystemExit("skill frontmatter is not closed")
    frontmatter = match.group(1)
    name = re.search(r"(?m)^name:\s*([a-z0-9][a-z0-9-]*)\s*$", frontmatter)
    description = re.search(r"(?m)^description:\s*(\S.*)$", frontmatter)
    if not name or not description:
        raise SystemExit("frontmatter must include plain name and non-empty description")
    inventory = json.loads(Path("tap-projects.json").read_text(encoding="utf-8"))
    for project in inventory["projects"]:
        for workflow_item in (project["producer_publish_job"], *project["producer_verification_jobs"]):
            if workflow_item not in skill:
                raise SystemExit(f"skill missing inventoried producer workflow job: {workflow_item}")
        for target, asset in project["assets"].items():
            if target not in skill or asset not in skill:
                raise SystemExit(f"skill missing inventory target or asset: {target}/{asset}")
        if project["binary"] not in skill:
            raise SystemExit(f"skill missing inventoried binary name: {project['binary']}")
        for required in (
            project["repository"],
            project["binary"],
            project["checksum_asset"],
            "HOMEBREW_TOOLS_DISPATCH_TOKEN",
        ):
            if required not in skill:
                raise SystemExit(f"skill missing required producer contract text: {required}")
        for job in project.get("producer_verification_jobs", []):
            if job not in skill:
                raise SystemExit(f"skill missing producer verification job: {job}")
    print(f"validated skill {name.group(1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
