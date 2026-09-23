#!/usr/bin/env python3
"""Validate published GitHub release assets and update only allowlisted tap casks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tap-projects.json"
EVENT_TYPE = "homebrew-tools-release"
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})  (.+)$")


class ValidationError(ValueError):
    """Input or downloaded release violates the tap contract."""


@dataclass(frozen=True)
class Project:
    token: str
    repository: str
    binary: str
    checksum_asset: str
    assets: dict[str, str]
    cask_path: Path


def load_projects(path: Path = MANIFEST_PATH) -> dict[str, Project]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot read inventory: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValidationError("Inventory must be an object with schema_version 1")
    rows = document.get("projects")
    if not isinstance(rows, list) or not rows:
        raise ValidationError("Inventory must contain a non-empty projects list")

    projects: dict[str, Project] = {}
    repositories: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError("Every inventory project must be an object")
        token, repository, binary = row.get("token"), row.get("repository"), row.get("binary")
        assets = row.get("assets")
        checksum_asset, cask_path = row.get("checksum_asset"), row.get("cask_path")
        if not isinstance(token, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", token):
            raise ValidationError("Invalid cask token in inventory")
        if not isinstance(repository, str) or not REPO_RE.fullmatch(repository):
            raise ValidationError(f"Invalid canonical repository for {token}")
        if not isinstance(binary, str) or not re.fullmatch(r"[A-Za-z0-9._+-]+", binary):
            raise ValidationError(f"Invalid binary for {token}")
        if not isinstance(checksum_asset, str) or not re.fullmatch(r"[A-Za-z0-9._+-]+", checksum_asset):
            raise ValidationError(f"Invalid checksum asset for {token}")
        publish_job = row.get("producer_publish_job")
        verification_jobs = row.get("producer_verification_jobs")
        if (
            not isinstance(publish_job, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", publish_job)
            or not isinstance(verification_jobs, list)
            or not verification_jobs
            or any(not isinstance(job, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", job) for job in verification_jobs)
        ):
            raise ValidationError(f"{token} must inventory producer publish and verification jobs")
        required_targets = {
            "x86_64-unknown-linux-gnu",
            "aarch64-unknown-linux-gnu",
            "aarch64-apple-darwin",
            "x86_64-apple-darwin",
        }
        if (
            not isinstance(assets, dict)
            or set(assets) != required_targets
            or not isinstance(row.get("support_note"), str)
            or not row["support_note"].strip()
        ):
            raise ValidationError(f"{token} must define all four supported targets and a support note")
        if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._+-]+", name) for name in assets.values()):
            raise ValidationError(f"Invalid release asset name for {token}")
        if set(assets.values()) != {f"{binary}-{target}.tar.gz" for target in required_targets}:
            raise ValidationError(f"{token} asset names must match its binary and exact target triples")
        if len(set(assets.values())) != len(assets):
            raise ValidationError(f"Asset names must be unique for {token}")
        if cask_path != f"Casks/{token}.rb":
            raise ValidationError(f"Cask path must be the fixed inventory path Casks/{token}.rb")
        if token in projects or repository in repositories:
            raise ValidationError("Cask tokens and source repositories must be unique")
        projects[token] = Project(token, repository, binary, checksum_asset, dict(assets), ROOT / cask_path)
        repositories.add(repository)
    return projects


def parse_tag(tag: Any) -> tuple[str, tuple[int, int, int]]:
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValidationError("Release tag must have the exact stable vX.Y.Z form")
    version = tag[1:]
    match = SEMVER_RE.fullmatch(version)
    if not match:
        raise ValidationError("Release tag must have the exact stable vX.Y.Z form")
    return version, tuple(int(part) for part in match.groups())


def validate_event(event: Any, projects: dict[str, Project]) -> tuple[Project, str]:
    if not isinstance(event, dict) or event.get("action") != EVENT_TYPE:
        raise ValidationError("Unexpected or missing repository_dispatch event type")
    payload = event.get("client_payload")
    if not isinstance(payload, dict) or set(payload) != {"repository", "tag"}:
        raise ValidationError("Dispatch payload must contain only repository and tag")
    repository, tag = payload["repository"], payload["tag"]
    project = next((p for p in projects.values() if p.repository == repository), None)
    if project is None:
        raise ValidationError("Source repository is not enrolled in the tap inventory")
    parse_tag(tag)
    return project, tag


def validate_release(release: Any, project: Project, expected_tag: str | None = None) -> tuple[str, tuple[int, int, int]]:
    if not isinstance(release, dict):
        raise ValidationError("Release metadata is not an object")
    tag = release.get("tag_name")
    version, version_tuple = parse_tag(tag)
    if expected_tag is not None and tag != expected_tag:
        raise ValidationError("Release tag does not match the requested tag")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValidationError("Drafts and prereleases are not eligible for the cask")
    if not isinstance(release.get("published_at"), str) or not release["published_at"]:
        raise ValidationError("Release is not published")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValidationError("Release asset list is missing")
    if any(not isinstance(asset, dict) for asset in assets):
        raise ValidationError("Release asset entries must be objects")
    names = [asset.get("name") for asset in assets]
    if any(not isinstance(name, str) or not name for name in names):
        raise ValidationError("Every release asset must have a string name")
    expected_names = set(project.assets.values()) | {project.checksum_asset}
    if len(names) != len(set(names)) or set(names) != expected_names:
        raise ValidationError("Release must contain exactly the four target archives and SHA256SUMS")
    if any(
        asset.get("state") != "uploaded"
        or not isinstance(asset.get("browser_download_url"), str)
        or not asset["browser_download_url"]
        for asset in assets
    ):
        raise ValidationError("Every release asset must be uploaded and downloadable")
    for asset in assets:
        digest = asset.get("digest")
        if digest is not None and digest != "":
            if not isinstance(digest, str):
                raise ValidationError(f"GitHub reports invalid SHA-256 metadata for {asset['name']}")
            normalized = digest.removeprefix("sha256:").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", normalized):
                raise ValidationError(f"GitHub reports invalid SHA-256 metadata for {asset['name']}")
    return version, version_tuple


def parse_checksums(text: str, project: Project) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        match = SHA_LINE_RE.fullmatch(line)
        if not match:
            raise ValidationError("SHA256SUMS contains malformed or unsupported syntax")
        digest, listed_path = match.groups()
        if "\\" in listed_path or listed_path.startswith("/") or re.match(r"^[A-Za-z]:", listed_path):
            raise ValidationError("SHA256SUMS paths must be relative and use forward slashes")
        components = listed_path.split("/")
        if any(component in ("", ".", "..") for component in components):
            raise ValidationError(f"SHA256SUMS contains an unsafe path: {listed_path}")
        name = components[-1]
        if name not in project.assets.values():
            raise ValidationError(f"SHA256SUMS contains unexpected file: {listed_path}")
        if name in checksums:
            raise ValidationError(f"SHA256SUMS lists {name} more than once")
        checksums[name] = digest.lower()
    if set(checksums) != set(project.assets.values()):
        raise ValidationError("SHA256SUMS must name each expected archive exactly once")
    return checksums


def verify_archive(path: Path, expected_binary: str) -> None:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise ValidationError(f"Invalid gzip tar archive: {path.name}") from exc
    if len(members) != 1:
        raise ValidationError(f"{path.name} must contain exactly one archive member")
    member = members[0]
    if member.name != expected_binary or not member.isfile() or member.issym() or member.islnk():
        raise ValidationError(f"{path.name} must contain only executable regular file {expected_binary}")
    if member.mode & 0o111 == 0:
        raise ValidationError(f"{expected_binary} in {path.name} is not executable")


def render_cask(project: Project, version: str, checksums: dict[str, str], current: str) -> str:
    # Update only the version and checksum stanza; all other reviewed cask content is immutable here.
    version_pattern = re.compile(r'(?m)^(  version ")[^"]+("\n)')
    updated, version_count = version_pattern.subn(rf"\g<1>{version}\g<2>", current)
    checksum_pattern = re.compile(r"(?ms)^  sha256 .*?\n\n(?=  url )")
    stanza = (
        '  sha256 arm:          "{arm}",\n'
        '         intel:        "{intel}",\n'
        '         arm64_linux:  "{arm64_linux}",\n'
        '         x86_64_linux: "{x86_64_linux}"\n\n'
    ).format(
        arm=checksums[project.assets["aarch64-apple-darwin"]],
        intel=checksums[project.assets["x86_64-apple-darwin"]],
        arm64_linux=checksums[project.assets["aarch64-unknown-linux-gnu"]],
        x86_64_linux=checksums[project.assets["x86_64-unknown-linux-gnu"]],
    )
    updated, checksum_count = checksum_pattern.subn(stanza, updated)
    expected_url = (
        f'  url "https://github.com/{project.repository}/releases/download/v#{{version}}/'
        f'{project.binary}-#{{arch}}-#{{os}}.tar.gz"\n'
    )
    url_count = updated.count(expected_url)
    if version_count != 1 or checksum_count != 1 or url_count != 1:
        raise ValidationError("Cask does not match the safe, deterministic version/checksum template")
    return updated


def fetch_bytes(url: str, token: str | None = None) -> bytes:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "tilmon-homebrew-tools-updater"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValidationError(f"Failed to retrieve a required GitHub release resource: {exc}") from exc


def github_api(url: str, token: str | None = None) -> Any:
    try:
        return json.loads(fetch_bytes(url, token).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("GitHub API returned invalid JSON") from exc


def materialize_release(
    project: Project,
    release: Any,
    expected_tag: str | None,
    fixture_assets: Path | None = None,
    token: str | None = None,
) -> tuple[str, tuple[int, int, int], dict[str, str]]:
    version, version_tuple = validate_release(release, project, expected_tag)
    tag = release["tag_name"]
    listed_assets = {asset["name"]: asset for asset in release["assets"]}
    checksum_metadata = listed_assets[project.checksum_asset]
    checksum_name = project.checksum_asset
    if fixture_assets is not None:
        checksum_data = (fixture_assets / checksum_name).read_bytes()
    else:
        checksum_data = fetch_bytes(release_asset_url(project, tag, checksum_name), token)
    try:
        checksum_text = checksum_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("SHA256SUMS is not valid UTF-8") from exc
    checksums = parse_checksums(checksum_text, project)
    if checksum_metadata.get("digest"):
        expected_manifest_digest = checksum_metadata["digest"].removeprefix("sha256:").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_digest):
            raise ValidationError("GitHub reports an invalid checksum-manifest digest")
        if hashlib.sha256(checksum_data).hexdigest() != expected_manifest_digest:
            raise ValidationError("Downloaded SHA256SUMS does not match the GitHub asset digest")

    with tempfile.TemporaryDirectory(prefix="tap-release-") as temp_dir:
        temp = Path(temp_dir)
        for target, name in project.assets.items():
            metadata = listed_assets[name]
            data = (fixture_assets / name).read_bytes() if fixture_assets is not None else fetch_bytes(
                release_asset_url(project, tag, name), token
            )
            if metadata.get("digest"):
                expected_asset_digest = metadata["digest"].removeprefix("sha256:").lower()
                if not re.fullmatch(r"[0-9a-f]{64}", expected_asset_digest):
                    raise ValidationError(f"GitHub reports invalid SHA-256 metadata for {name}")
                if hashlib.sha256(data).hexdigest() != expected_asset_digest:
                    raise ValidationError(f"Downloaded bytes do not match GitHub's release asset digest for {name}")
            if hashlib.sha256(data).hexdigest() != checksums[name]:
                raise ValidationError(f"Downloaded bytes do not match SHA256SUMS for {name}")
            archive_path = temp / name
            archive_path.write_bytes(data)
            verify_archive(archive_path, project.binary)
    return version, version_tuple, checksums


def release_asset_url(project: Project, tag: str, asset: str) -> str:
    # Callers have already validated the canonical inventory project, strict tag and exact asset name.
    return f"https://github.com/{project.repository}/releases/download/{tag}/{asset}"


def atomic_write_text(path: Path, text: str) -> None:
    """Write a complete UTF-8 sibling then atomically replace its target."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def validate_cask_path(project: Project) -> None:
    """Require the allowlisted token to map only to its canonical repository cask file."""
    expected_path = ROOT / "Casks" / f"{project.token}.rb"
    if project.cask_path != expected_path:
        raise ValidationError(f"Allowlisted cask path does not match token {project.token}")
    if (ROOT / "Casks").is_symlink():
        raise ValidationError("Allowlisted Casks directory must not be a symlink")
    try:
        relative = project.cask_path.relative_to(ROOT)
        resolved = project.cask_path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        if project.cask_path.is_symlink():
            raise ValidationError(f"Allowlisted cask must be a regular non-symlink file: {project.cask_path}") from exc
        raise ValidationError(f"Invalid allowlisted cask path for {project.token}") from exc
    if not re.fullmatch(r"Casks/[a-z0-9-]+\.rb", relative.as_posix()):
        raise ValidationError(f"Cask path is outside the fixed Casks directory for {project.token}")
    if project.cask_path.is_symlink() or not project.cask_path.is_file():
        raise ValidationError(f"Allowlisted cask must be a regular non-symlink file: {relative}")
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValidationError(f"Allowlisted cask resolves outside the repository: {relative}")


def build_cask_update(project: Project, version: str, checksums: dict[str, str], current: str) -> str:
    """Validate monotonicity and safely render a cask without writing it."""
    match = re.search(r'(?m)^  version "([^"]+)"$', current)
    if match is None:
        raise ValidationError("Current cask has no single top-level version")
    current_match = SEMVER_RE.fullmatch(match.group(1))
    next_match = SEMVER_RE.fullmatch(version)
    if current_match is None or next_match is None:
        raise ValidationError("Current or proposed cask version is not stable X.Y.Z")
    current_tuple = tuple(int(part) for part in current_match.groups())
    next_tuple = tuple(int(part) for part in next_match.groups())
    if next_tuple < current_tuple:
        raise ValidationError("Refusing to downgrade an allowlisted cask")
    if next_tuple == current_tuple:
        checksum_match = re.search(r"(?ms)^  sha256 (.*?)\n\n(?=  url )", current)
        current_hashes = re.findall(r'"([0-9a-fA-F]{64})"', checksum_match.group(1)) if checksum_match else []
        proposed_hashes = [
            checksums[project.assets[target]]
            for target in (
                "aarch64-apple-darwin",
                "x86_64-apple-darwin",
                "aarch64-unknown-linux-gnu",
                "x86_64-unknown-linux-gnu",
            )
        ]
        if current_hashes != proposed_hashes:
            raise ValidationError("Refusing same-version release checksum mutation")
    return render_cask(project, version, checksums, current)


def apply_release(
    project: Project,
    release: Any,
    expected_tag: str | None = None,
    fixture_assets: Path | None = None,
    token: str | None = None,
) -> bool:
    version, _, checksums = materialize_release(project, release, expected_tag, fixture_assets, token)
    validate_cask_path(project)
    current = project.cask_path.read_text(encoding="utf-8")
    updated = build_cask_update(project, version, checksums, current)
    if updated == current:
        return False
    atomic_write_text(project.cask_path, updated)
    return True


def latest_release(project: Project, token: str | None = None) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    page = 1
    while True:
        encoded = urllib.parse.urlencode({"per_page": 100, "page": page})
        url = f"https://api.github.com/repos/{project.repository}/releases?{encoded}"
        payload = github_api(url, token)
        if not isinstance(payload, list):
            raise ValidationError("GitHub releases endpoint returned a non-list response")
        if not payload:
            break
        releases.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            break
        page += 1
    eligible: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for release in releases:
        if release.get("draft") is not False or release.get("prerelease") is not False:
            continue
        try:
            _, parsed = parse_tag(release.get("tag_name"))
        except ValidationError:
            continue
        eligible.append((parsed, release))
    eligible.sort(key=lambda pair: pair[0], reverse=True)
    return [release for _, release in eligible]


def release_by_tag(project: Project, tag: str, token: str | None = None) -> dict[str, Any]:
    parse_tag(tag)
    encoded_tag = urllib.parse.quote(tag, safe="")
    url = f"https://api.github.com/repos/{project.repository}/releases/tags/{encoded_tag}"
    release = github_api(url, token)
    if not isinstance(release, dict):
        raise ValidationError("GitHub release endpoint returned an invalid response")
    return release


def commit_changed_casks() -> None:
    staged_before = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.splitlines()
    if staged_before:
        raise ValidationError("Refusing to commit because unrelated changes were already staged")
    changed = subprocess.run(
        ["git", "diff", "--name-only"], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.splitlines()
    allowed_paths = {project.cask_path.relative_to(ROOT).as_posix() for project in load_projects().values()}
    paths = sorted(path for path in changed if path in allowed_paths)
    if set(changed) != set(paths):
        raise ValidationError("Refusing to commit changes outside generated inventory cask paths")
    if not paths:
        return
    subprocess.run(["git", "add", "--", *paths], cwd=ROOT, check=True)
    staged_paths = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.splitlines()
    if staged_paths != paths:
        raise ValidationError("Refusing to commit staged files outside the generated cask paths")
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
    subprocess.run(
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "chore(cask): update " + ", ".join(Path(p).stem for p in paths)],
        cwd=ROOT,
        check=True,
    )


def reconcile_projects(
    projects: dict[str, Project], fixture_dir: Path | None = None, token: str | None = None
) -> bool:
    """Validate all candidate updates before writing; return whether any cask changed."""
    plans: list[tuple[Project, str, str]] = []
    for project in projects.values():
        if fixture_dir is not None:
            fixture_root = fixture_dir / project.token
            release_path = fixture_root / "release.json"
            candidates = (
                [json.loads(release_path.read_text(encoding="utf-8"))]
                if release_path.is_file()
                else []
            )
        else:
            candidates = latest_release(project, token)
        if not candidates:
            raise ValidationError(f"No published stable release exists for enrolled project {project.token}")
        last_error: ValidationError | None = None
        for release in candidates:
            try:
                version, _, checksums = materialize_release(
                    project,
                    release,
                    expected_tag=None,
                    fixture_assets=(fixture_dir / project.token) if fixture_dir else None,
                    token=token,
                )
            except ValidationError as exc:
                last_error = exc
                if fixture_dir is not None:
                    break
                continue
            validate_cask_path(project)
            current = project.cask_path.read_text(encoding="utf-8")
            rendered = build_cask_update(project, version, checksums, current)
            plans.append((project, rendered, current))
            break
        else:
            if last_error is not None:
                raise last_error
    changed_plans = [(project, candidate, current) for project, candidate, current in plans if candidate != current]
    written: list[tuple[Project, str]] = []
    attempted: tuple[Project, str] | None = None
    try:
        for project, candidate, current in changed_plans:
            attempted = (project, current)
            atomic_write_text(project.cask_path, candidate)
            written.append((project, current))
            attempted = None
    except OSError as exc:
        rollback_targets = written + ([attempted] if attempted is not None else [])
        rollback_errors: list[OSError] = []
        for project, original in reversed(rollback_targets):
            try:
                atomic_write_text(project.cask_path, original)
            except OSError as rollback_exc:
                rollback_errors.append(rollback_exc)
        if rollback_errors:
            raise ValidationError(
                f"Reconciliation write failed and rollback also failed for {len(rollback_errors)} cask(s)"
            ) from exc
        raise ValidationError("Reconciliation write failed; all attempted cask writes were restored") from exc
    return bool(changed_plans)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    event_parser = commands.add_parser("event", help="process a GitHub repository_dispatch event")
    event_parser.add_argument("--event-file", type=Path, required=True)
    event_parser.add_argument("--fixture-release", type=Path, help="test-only release metadata fixture")
    event_parser.add_argument("--fixture-assets", type=Path, help="test-only directory containing release assets")
    replay_parser = commands.add_parser("replay", help="manually replay one inventory project and tag")
    replay_parser.add_argument("--project", required=True)
    replay_parser.add_argument("--tag", required=True)
    replay_parser.add_argument("--fixture-release", type=Path)
    replay_parser.add_argument("--fixture-assets", type=Path)
    reconcile_parser = commands.add_parser("reconcile", help="reconcile each inventory entry to newest valid release")
    reconcile_parser.add_argument("--fixture-dir", type=Path, help="test-only fixture root containing <token>/release.json and assets")
    commands.add_parser("commit-changed", help="commit only changed allowlisted cask files")
    parser.add_argument("--token", default=None, help="optional GitHub API token; never printed")
    args = parser.parse_args(argv)
    try:
        projects = load_projects()
        token = args.token or os.environ.get("GITHUB_API_TOKEN")
        updated = False
        if args.command == "event":
            event = json.loads(args.event_file.read_text(encoding="utf-8"))
            project, tag = validate_event(event, projects)
            if bool(args.fixture_release) != bool(args.fixture_assets):
                raise ValidationError("Event fixture mode requires both --fixture-release and --fixture-assets")
            release = json.loads(args.fixture_release.read_text(encoding="utf-8")) if args.fixture_release else release_by_tag(project, tag, token)
            updated = apply_release(project, release, tag, args.fixture_assets, token)
        elif args.command == "replay":
            if args.project not in projects:
                raise ValidationError("Requested project token is not in the inventory")
            project = projects[args.project]
            release = json.loads(args.fixture_release.read_text(encoding="utf-8")) if args.fixture_release else release_by_tag(project, args.tag, token)
            updated = apply_release(project, release, args.tag, args.fixture_assets, token)
        elif args.command == "commit-changed":
            commit_changed_casks()
            return 0
        else:
            updated = reconcile_projects(projects, args.fixture_dir, token)
        if updated and args.command != "reconcile":
            # The tap workflow owns commits, so local replay is safe without an implicit Git write.
            print("cask updated (not committed)")
        elif updated:
            print("one or more casks updated")
        else:
            print("casks already current")
        return 0
    except (ValidationError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"release update rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
