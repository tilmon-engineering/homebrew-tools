from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts.update_cask import (
    EVENT_TYPE,
    ValidationError,
    apply_release,
    build_cask_update,
    load_projects,
    parse_checksums,
    render_cask,
    validate_event,
    validate_release,
    verify_archive,
)

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def temporary_casks(root: Path, projects: dict):
    """Provide an isolated repository root for cask-path validation in fixtures."""
    from dataclasses import replace
    from scripts import update_cask

    cask_dir = root / "Casks"
    cask_dir.mkdir(parents=True, exist_ok=True)
    isolated = {}
    for token, project in projects.items():
        path = cask_dir / f"{token}.rb"
        path.write_text(project.cask_path.read_text(encoding="utf-8"), encoding="utf-8")
        isolated[token] = replace(project, cask_path=path)
    with patch.object(update_cask, "ROOT", root):
        yield isolated


def archive_bytes(binary: str = "sqlite-mcp", payload: bytes = b"test executable") -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(binary)
        info.size = len(payload)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def release_fixture(project, version: str | None = None, extra_assets: bool = False):
    if version is None:
        if project.token == "sqlite-mcp":
            cask_text = (ROOT / "Casks/sqlite-mcp.rb").read_text(encoding="utf-8")
            current = re.search(r'(?m)^  version "([^\"]+)"$', cask_text).group(1)
            major, minor, patch = (int(part) for part in current.split("."))
            version = f"{major}.{minor}.{patch + 1}"
        else:
            version = "0.3.2"
    tag = f"v{version}"
    artifacts = {name: archive_bytes(project.binary) for name in project.assets.values()}
    checksums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in artifacts.items())
    assets = [{"name": name, "state": "uploaded", "browser_download_url": f"https://example.invalid/{name}"} for name in artifacts]
    assets.append({"name": project.checksum_asset, "state": "uploaded", "browser_download_url": "https://example.invalid/SHA256SUMS"})
    if extra_assets:
        assets.append({"name": "extra.zip"})
    release = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-09-23T00:00:00Z",
        "assets": assets,
    }
    return release, artifacts, checksums.encode()


class ReleaseUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.project = load_projects()["sqlite-mcp"]
        current = self.project.cask_path.read_text(encoding="utf-8")
        self.current_version = re.search(r'(?m)^  version "([^"]+)"$', current).group(1)
        major, minor, patch = (int(part) for part in self.current_version.split("."))
        self.next_version = f"{major}.{minor}.{patch + 1}"

    def test_inventory_allowlist_is_single_and_exact(self):
        projects = load_projects()
        self.assertEqual(set(projects), {"sqlite-mcp", "pg-mcp", "typedb-mcp"})
        self.assertEqual(projects["typedb-mcp"].repository, "tilmon-engineering/typedb-mcp")
        self.assertEqual(projects["typedb-mcp"].binary, "typedb-mcp")
        self.assertEqual(len(projects["typedb-mcp"].assets), 4)
        self.assertEqual(projects["sqlite-mcp"].repository, "tilmon-engineering/sqlite-mcp")
        self.assertEqual(projects["pg-mcp"].repository, "tilmon-engineering/pg-mcp")
        self.assertEqual(projects["pg-mcp"].binary, "postgres-mcp")
        self.assertEqual(projects["typedb-mcp"].repository, "tilmon-engineering/typedb-mcp")
        self.assertEqual(projects["typedb-mcp"].binary, "typedb-mcp")
        self.assertEqual(len(projects["sqlite-mcp"].assets), 4)
        self.assertEqual(len(projects["pg-mcp"].assets), 4)
        self.assertEqual(len(projects["typedb-mcp"].assets), 4)
        with self.assertRaises(ValidationError):
            load_projects(Path(__file__).resolve().parents[1] / "tests/fixtures/typedb-without-assets.json")

    def test_event_accepts_only_fixed_event_and_canonical_inventory_repo(self):
        event = {"action": EVENT_TYPE, "client_payload": {"repository": self.project.repository, "tag": "v1.2.3"}}
        project, tag = validate_event(event, {self.project.token: self.project})
        self.assertIs(project, self.project)
        self.assertEqual(tag, "v1.2.3")
        for bad in (
            {"action": "wrong", "client_payload": event["client_payload"]},
            {"action": EVENT_TYPE, "client_payload": {"repository": "attacker/repo", "tag": "v1.2.3"}},
            {"action": EVENT_TYPE, "client_payload": {"repository": self.project.repository, "tag": "v1.2.3", "path": "../../evil"}},
            {"action": EVENT_TYPE, "client_payload": {"repository": self.project.repository, "tag": "v1.2.3; touch pwned"}},
        ):
            with self.subTest(event=bad), self.assertRaises(ValidationError):
                validate_event(bad, {self.project.token: self.project})

    def test_valid_published_release_metadata_and_exact_asset_set(self):
        release, _, _ = release_fixture(self.project)
        expected = tuple(int(part) for part in self.next_version.split("."))
        self.assertEqual(validate_release(release, self.project), (self.next_version, expected))
        bad_state = dict(release)
        bad_state["assets"] = [dict(asset) for asset in release["assets"]]
        bad_state["assets"][0]["state"] = "new"
        with self.assertRaises(ValidationError):
            validate_release(bad_state, self.project)
        for change in (
            {"draft": True},
            {"prerelease": True},
            {"published_at": None},
            {"tag_name": "v0.3.2-rc.1"},
        ):
            bad = dict(release, **change)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                validate_release(bad, self.project)
        bad_assets = dict(release)
        bad_assets["assets"] = release["assets"][:-1] + [{"name": "wrong"}]
        with self.assertRaises(ValidationError):
            validate_release(bad_assets, self.project)
        malformed_assets = (
            [dict(release["assets"][0], name=[])]+release["assets"][1:],
            [dict(release["assets"][0], digest=[])]+release["assets"][1:],
            [dict(release["assets"][0], browser_download_url=[])]+release["assets"][1:],
            [[], *release["assets"][1:]],
        )
        for assets in malformed_assets:
            with self.subTest(assets=assets[0]), self.assertRaises(ValidationError):
                validate_release(dict(release, assets=assets), self.project)

    def test_reconciliation_skips_malformed_newest_metadata_for_older_valid_release(self):
        from scripts import update_cask
        from scripts.update_cask import reconcile_projects

        valid, archives, manifest = release_fixture(self.project, self.next_version)
        major, minor, patch_component = (int(part) for part in self.next_version.split("."))
        malformed = dict(valid, tag_name=f"v{major}.{minor}.{patch_component + 1}", assets=[dict(valid["assets"][0], name=[]), *valid["assets"][1:]])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets_root = root / "fixtures" / self.project.token
            assets_root.mkdir(parents=True)
            (assets_root / self.project.checksum_asset).write_bytes(manifest)
            for name, data in archives.items():
                (assets_root / name).write_bytes(data)
            with temporary_casks(root, {self.project.token: self.project}) as isolated:
                project = isolated[self.project.token]
                original_materialize = update_cask.materialize_release

                def materialize_fixture(project_arg, release_arg, expected_tag, fixture_assets=None, token=None):
                    return original_materialize(
                        project_arg,
                        release_arg,
                        expected_tag,
                        fixture_assets=assets_root,
                        token=token,
                    )

                with patch.object(update_cask, "latest_release", return_value=[malformed, valid]):
                    with patch.object(update_cask, "materialize_release", side_effect=materialize_fixture):
                        self.assertTrue(reconcile_projects({project.token: project}))
                self.assertIn(f'version "{self.next_version}"', project.cask_path.read_text())

    def test_checksum_manifest_must_be_exact_and_well_formed(self):
        _, _, manifest = release_fixture(self.project)
        checksums = parse_checksums(manifest.decode(), self.project)
        self.assertEqual(set(checksums), set(self.project.assets.values()))
        with self.assertRaises(ValidationError):
            parse_checksums(manifest.decode() + manifest.decode().splitlines()[0] + "\n", self.project)
        with self.assertRaises(ValidationError):
            parse_checksums(manifest.decode() + "0" * 64 + "  unexpected.tar.gz\n", self.project)
        with self.assertRaises(ValidationError):
            parse_checksums("bad checksum line\n", self.project)
        first_digest, first_name = manifest.decode().splitlines()[0].split("  ", 1)
        for unsafe in (
            f"{first_digest}  ../{first_name}",
            f"{first_digest}  build//{first_name}",
            f"{first_digest}  /tmp/{first_name}",
            f"{first_digest}  C:/release-assets/{first_name}",
        ):
            with self.subTest(path=unsafe), self.assertRaises(ValidationError):
                parse_checksums(unsafe + "\n", self.project)
        # pg-mcp's real producer records relative build directory prefixes; safe relative paths are accepted.
        pg_project = load_projects()["pg-mcp"]
        prefixed = "".join(
            f"{hashlib.sha256(name.encode()).hexdigest()}  release-assets/{name}\n"
            for name in pg_project.assets.values()
        )
        self.assertEqual(set(parse_checksums(prefixed, pg_project)), set(pg_project.assets.values()))

    def test_archive_member_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid.tar.gz"
            valid.write_bytes(archive_bytes())
            verify_archive(valid, "sqlite-mcp")
            bad = Path(directory) / "wrong.tar.gz"
            bad.write_bytes(archive_bytes("other"))
            with self.assertRaises(ValidationError):
                verify_archive(bad, "sqlite-mcp")
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                info = tarfile.TarInfo("sqlite-mcp")
                info.size = 1
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(b"x"))
            bad.write_bytes(stream.getvalue())
            with self.assertRaises(ValidationError):
                verify_archive(bad, "sqlite-mcp")

    def test_cask_contract_has_real_pinned_values_for_all_targets(self):
        cask = (ROOT / "Casks/sqlite-mcp.rb").read_text()
        self.assertIn(f'version "{self.current_version}"', cask)
        self.assertIn('binary "sqlite-mcp"', cask)
        self.assertNotIn("version :latest", cask)
        self.assertNotIn("sha256 :no_check", cask)
        self.assertNotIn("auto_updates", cask)
        for target in self.project.assets:
            self.assertIn(target.split("-")[0], cask)
        hashes = re.findall(r'"([0-9a-f]{64})"', cask)
        self.assertEqual(len(hashes), 4)
        self.assertEqual(len(set(hashes)), 4)
        typedb_cask = (ROOT / "Casks/typedb-mcp.rb").read_text()
        self.assertIn('version "0.3.7"', typedb_cask)
        self.assertIn('binary "typedb-mcp"', typedb_cask)
        self.assertEqual(len(re.findall(r'"([0-9a-f]{64})"', typedb_cask)), 4)

    def test_render_updates_only_version_and_four_checksum_fields(self):
        release, artifacts, manifest = release_fixture(self.project)
        checksums = parse_checksums(manifest.decode(), self.project)
        current = (ROOT / "Casks/sqlite-mcp.rb").read_text()
        updated = render_cask(self.project, self.next_version, checksums, current)
        self.assertIn(f'version "{self.next_version}"', updated)
        self.assertEqual(updated.count('"' + '"'), 0)
        for name, digest in checksums.items():
            self.assertIn(f'"{digest}"', updated)
        def normalize(text):
            return re.sub(r"(?ms)^  sha256 .*?\n\n(?=  url )", "CHECKSUMS\n\n", text)

        expected_non_checksum = current.replace(f'version "{self.current_version}"', f'version "{self.next_version}"')
        self.assertEqual(normalize(updated), normalize(expected_non_checksum))

    def test_fixture_update_and_duplicate_event_are_noop(self):
        release, artifacts, manifest = release_fixture(self.project)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "assets"
            fixture.mkdir()
            (fixture / self.project.checksum_asset).write_bytes(manifest)
            for name, data in artifacts.items():
                (fixture / name).write_bytes(data)
            with temporary_casks(root, {self.project.token: self.project}) as isolated:
                project = isolated[self.project.token]
                tag = release["tag_name"]
                self.assertTrue(apply_release(project, release, tag, fixture))
                updated = project.cask_path.read_text()
                self.assertIn(f'version "{self.next_version}"', updated)
                self.assertFalse(apply_release(project, release, tag, fixture))
                self.assertEqual(project.cask_path.read_text(), updated)

    def test_invalid_fixture_does_not_write(self):
        release, artifacts, manifest = release_fixture(self.project)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "assets"
            fixture.mkdir()
            (fixture / self.project.checksum_asset).write_bytes(manifest)
            for name, data in artifacts.items():
                (fixture / name).write_bytes(data + b"tampered")
            with temporary_casks(root, {self.project.token: self.project}) as isolated:
                project = isolated[self.project.token]
                original = project.cask_path.read_text()
                with self.assertRaisesRegex(ValidationError, "Downloaded bytes do not match SHA256SUMS"):
                    apply_release(project, release, release["tag_name"], fixture)
                self.assertEqual(project.cask_path.read_text(), original)

    def test_stale_release_cannot_downgrade(self):
        release, artifacts, manifest = release_fixture(self.project, "0.2.9")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "assets"
            fixture.mkdir()
            (fixture / self.project.checksum_asset).write_bytes(manifest)
            for name, data in artifacts.items():
                (fixture / name).write_bytes(data)
            with temporary_casks(root, {self.project.token: self.project}) as isolated:
                project = isolated[self.project.token]
                original = project.cask_path.read_text()
                with self.assertRaisesRegex(ValidationError, "Refusing to downgrade"):
                    apply_release(project, release, "v0.2.9", fixture)
                self.assertEqual(project.cask_path.read_text(), original)

    def test_reconciliation_catches_missed_dispatch(self):
        release, artifacts, manifest = release_fixture(self.project, self.next_version)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / self.project.token
            fixture.mkdir()
            (fixture / "release.json").write_text(json.dumps(release))
            (fixture / self.project.checksum_asset).write_bytes(manifest)
            for name, data in artifacts.items():
                (fixture / name).write_bytes(data)
            from scripts.update_cask import reconcile_projects
            with temporary_casks(Path(directory), {self.project.token: self.project}) as isolated:
                project = isolated[self.project.token]
                self.assertTrue(reconcile_projects({project.token: project}, fixture_dir=fixture.parent))
                self.assertIn(f'version "{self.next_version}"', project.cask_path.read_text())

    def test_github_asset_digest_must_match_download_and_checksum_manifest(self):
        release, artifacts, manifest = release_fixture(self.project, self.next_version)
        release["assets"][0]["digest"] = "sha256:" + "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "assets"
            fixture.mkdir()
            (fixture / self.project.checksum_asset).write_bytes(manifest)
            for name, data in artifacts.items():
                (fixture / name).write_bytes(data)
            with temporary_casks(root, {self.project.token: self.project}) as isolated:
                project = isolated[self.project.token]
                original = project.cask_path.read_text()
                with self.assertRaisesRegex(ValidationError, "GitHub's release asset digest"):
                    apply_release(project, release, release["tag_name"], fixture)
                self.assertEqual(project.cask_path.read_text(), original)

    def test_same_version_cannot_change_pinned_checksums(self):
        current = self.project.cask_path.read_text()
        changed_checksums = {name: "0" * 64 for name in self.project.assets.values()}
        with self.assertRaisesRegex(ValidationError, "same-version"):
            build_cask_update(self.project, self.current_version, changed_checksums, current)

    def test_reconciliation_rejects_rollback_without_mutating_earlier_casks(self):
        from dataclasses import replace
        from scripts.update_cask import reconcile_projects

        projects = load_projects()
        sqlite_project = projects["sqlite-mcp"]
        pg_project = projects["pg-mcp"]
        sqlite_release, sqlite_archives, sqlite_manifest = release_fixture(sqlite_project)
        pg_release, pg_archives, pg_manifest = release_fixture(pg_project, "0.0.9")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_root = root / "fixtures"
            for project, release, archives, manifest in (
                (sqlite_project, sqlite_release, sqlite_archives, sqlite_manifest),
                (pg_project, pg_release, pg_archives, pg_manifest),
            ):
                assets_dir = fixture_root / project.token
                assets_dir.mkdir(parents=True)
                (assets_dir / "release.json").write_text(json.dumps(release))
                (assets_dir / project.checksum_asset).write_bytes(manifest)
                for name, data in archives.items():
                    (assets_dir / name).write_bytes(data)
            with temporary_casks(root, {"sqlite-mcp": sqlite_project, "pg-mcp": pg_project}) as isolated:
                sqlite_path = isolated["sqlite-mcp"].cask_path
                pg_path = isolated["pg-mcp"].cask_path
                sqlite_original = sqlite_path.read_text()
                pg_original = pg_path.read_text()
                sqlite_external = root / "sqlite-mcp-external.rb"
                pg_external = root / "pg-mcp-external.rb"
                sqlite_external.write_text(sqlite_original)
                pg_external.write_text(pg_original)
                sqlite_path.unlink()
                pg_path.unlink()
                sqlite_path.symlink_to(sqlite_external)
                pg_path.symlink_to(pg_external)
                projects_for_test = {"sqlite-mcp": isolated["sqlite-mcp"], "pg-mcp": isolated["pg-mcp"]}
                with self.assertRaisesRegex(ValidationError, "non-symlink"):
                    reconcile_projects(projects_for_test, fixture_dir=fixture_root)
                with self.assertRaisesRegex(ValidationError, "Refusing to downgrade"):
                    build_cask_update(pg_project, "0.0.9", {name: "0" * 64 for name in pg_project.assets.values()}, pg_original)
                self.assertEqual(sqlite_external.read_text(), sqlite_original)
                self.assertEqual(pg_external.read_text(), pg_original)

    def test_cask_path_outside_inventory_root_is_rejected(self):
        from dataclasses import replace

        release, artifacts, manifest = release_fixture(self.project, self.next_version)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "assets"
            fixture.mkdir()
            (fixture / self.project.checksum_asset).write_bytes(manifest)
            for name, data in artifacts.items():
                (fixture / name).write_bytes(data)
            outside = root / "outside.rb"
            sentinel = "outside content must not change\\n"
            outside.write_text(sentinel)
            with temporary_casks(root, {self.project.token: self.project}) as isolated:
                wrong_path = replace(isolated[self.project.token], cask_path=outside)
                with self.assertRaisesRegex(ValidationError, "does not match token"):
                    apply_release(wrong_path, release, release["tag_name"], fixture)
                self.assertEqual(outside.read_text(), sentinel)

    def test_cask_path_cannot_alias_another_inventory_token(self):
        from dataclasses import replace

        projects = load_projects()
        sqlite_project = projects["sqlite-mcp"]
        pg_project = projects["pg-mcp"]
        sqlite_release, archives, manifest = release_fixture(sqlite_project)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "assets"
            fixture.mkdir()
            (fixture / sqlite_project.checksum_asset).write_bytes(manifest)
            for name, data in archives.items():
                (fixture / name).write_bytes(data)
            with temporary_casks(root, projects) as isolated:
                wrong_path = replace(isolated["sqlite-mcp"], cask_path=isolated["pg-mcp"].cask_path)
                pg_before = isolated["pg-mcp"].cask_path.read_text()
                with self.assertRaisesRegex(ValidationError, "does not match token"):
                    apply_release(wrong_path, sqlite_release, sqlite_release["tag_name"], fixture)
                self.assertEqual(isolated["pg-mcp"].cask_path.read_text(), pg_before)

    def test_symlinked_casks_directory_is_rejected(self):
        from dataclasses import replace
        from scripts import update_cask

        project = self.project
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_casks = root / "real-casks"
            real_casks.mkdir()
            outside = real_casks / "sqlite-mcp.rb"
            outside.write_text(project.cask_path.read_text())
            (root / "Casks").symlink_to(real_casks, target_is_directory=True)
            with patch.object(update_cask, "ROOT", root):
                aliased = replace(project, cask_path=root / "Casks" / "sqlite-mcp.rb")
                with self.assertRaisesRegex(ValidationError, "Casks directory must not be a symlink"):
                    update_cask.validate_cask_path(aliased)

    def test_reconciliation_restores_earlier_cask_if_later_write_fails(self):
        from dataclasses import replace
        from unittest.mock import patch
        from scripts.update_cask import reconcile_projects

        projects = load_projects()
        sqlite_project = projects["sqlite-mcp"]
        pg_project = projects["pg-mcp"]
        sqlite_release, sqlite_archives, sqlite_manifest = release_fixture(sqlite_project)
        pg_release, pg_archives, pg_manifest = release_fixture(pg_project, "0.1.1")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_root = root / "fixtures"
            for project, release, archives, manifest in (
                (sqlite_project, sqlite_release, sqlite_archives, sqlite_manifest),
                (pg_project, pg_release, pg_archives, pg_manifest),
            ):
                assets_dir = fixture_root / project.token
                assets_dir.mkdir(parents=True)
                (assets_dir / "release.json").write_text(json.dumps(release))
                (assets_dir / project.checksum_asset).write_bytes(manifest)
                for name, data in archives.items():
                    (assets_dir / name).write_bytes(data)
            with temporary_casks(root, {"sqlite-mcp": sqlite_project, "pg-mcp": pg_project}) as projects_for_test:
                sqlite_path = projects_for_test["sqlite-mcp"].cask_path
                pg_path = projects_for_test["pg-mcp"].cask_path
                sqlite_before = sqlite_path.read_text()
                pg_before = pg_path.read_text()
                from scripts import update_cask
                original_atomic_write = update_cask.atomic_write_text

                failed_once = False

                def fail_on_pg(path, text):
                    nonlocal failed_once
                    if path == pg_path and not failed_once:
                        failed_once = True
                        path.write_text(text[:20], encoding="utf-8")
                        raise OSError("simulated disk-full failure after partial truncation")
                    return original_atomic_write(path, text)

                with patch.object(update_cask, "atomic_write_text", fail_on_pg):
                    with self.assertRaisesRegex(ValidationError, "all attempted cask writes were restored"):
                        reconcile_projects(projects_for_test, fixture_dir=fixture_root)
                self.assertEqual(sqlite_path.read_text(), sqlite_before)
                self.assertEqual(pg_path.read_text(), pg_before)

    def test_reconciliation_rejects_later_malformed_cask_without_partial_write(self):
        from dataclasses import replace
        from scripts.update_cask import reconcile_projects

        projects = load_projects()
        sqlite_project = projects["sqlite-mcp"]
        pg_project = projects["pg-mcp"]
        sqlite_release, sqlite_archives, sqlite_manifest = release_fixture(sqlite_project)
        pg_release, pg_archives, pg_manifest = release_fixture(pg_project, "0.1.1")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_root = root / "fixtures"
            for project, release, archives, manifest in (
                (sqlite_project, sqlite_release, sqlite_archives, sqlite_manifest),
                (pg_project, pg_release, pg_archives, pg_manifest),
            ):
                assets_dir = fixture_root / project.token
                assets_dir.mkdir(parents=True)
                (assets_dir / "release.json").write_text(json.dumps(release))
                (assets_dir / project.checksum_asset).write_bytes(manifest)
                for name, data in archives.items():
                    (assets_dir / name).write_bytes(data)
            with temporary_casks(root, {"sqlite-mcp": sqlite_project, "pg-mcp": pg_project}) as projects_for_test:
                sqlite_path = projects_for_test["sqlite-mcp"].cask_path
                pg_path = projects_for_test["pg-mcp"].cask_path
                sqlite_original = sqlite_path.read_text()
                pg_original = pg_path.read_text()
                pg_path.write_text("this is not a cask\\n")
                with self.assertRaisesRegex(ValidationError, "Current cask has no single"):
                    reconcile_projects(projects_for_test, fixture_dir=fixture_root)
                self.assertEqual(sqlite_path.read_text(), sqlite_original)
                self.assertEqual(pg_path.read_text(), "this is not a cask\\n")
            self.assertEqual(sqlite_project.cask_path.read_text(), sqlite_original)
            self.assertEqual(pg_project.cask_path.read_text(), pg_original)

    def test_workflow_and_skill_contracts_match_inventory(self):
        workflow = (ROOT / ".github/workflows/update-cask.yml").read_text()
        skill = (ROOT / ".agents/skills/homebrew-tools-release/SKILL.md").read_text()
        readme = (ROOT / "README.md").read_text()
        for expected in (EVENT_TYPE, "repository_dispatch", "workflow_dispatch", "schedule", "max", "cancel-in-progress: false"):
            self.assertIn(expected, workflow)
        for expected in ("verify-published", "verify-release-metadata", "Contents: write", "HOMEBREW_TOOLS_DISPATCH_TOKEN"):
            self.assertIn(expected, skill)
        self.assertIn("Casks/", readme)
        self.assertIn("tap-projects.json", readme)

    def test_reconciliation_fails_when_enrolled_project_has_no_release(self):
        from scripts.update_cask import reconcile_projects

        class NoReleaseProject:
            token = self.project.token

        project = NoReleaseProject()
        projects = {self.project.token: project}
        for field in ("assets", "cask_path", "binary", "checksum_asset", "repository"):
            setattr(project, field, getattr(self.project, field))
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, self.project.token).mkdir()
            with self.assertRaisesRegex(ValidationError, "No published stable release"):
                reconcile_projects(projects, fixture_dir=Path(directory))

    def test_workflow_inventory_selector_covers_exact_active_projects(self):
        workflow = (ROOT / ".github/workflows/update-cask.yml").read_text()
        selected = re.search(r"(?ms)^        options: \[(.*?)\]$", workflow)
        self.assertIsNotNone(selected)
        tokens = {value.strip() for value in selected.group(1).split(",")}
        self.assertEqual(tokens, set(load_projects()))

    def test_skill_and_manifest_have_all_contract_details(self):
        skill = (ROOT / ".agents/skills/homebrew-tools-release/SKILL.md").read_text()
        project = load_projects()["sqlite-mcp"]
        for target, asset in project.assets.items():
            self.assertIn(target, skill)
            self.assertIn(asset, skill)
        self.assertIn(project.checksum_asset, skill)
        pg_project = load_projects()["pg-mcp"]
        for target, asset in pg_project.assets.items():
            self.assertIn(target, skill)
            self.assertIn(asset, skill)
        self.assertIn(pg_project.binary, skill)
        self.assertIn("`typedb-mcp` was enrolled after published release `v0.3.7`", skill)
        self.assertIn("postgres-mcp`", skill)
        self.assertIn("both sqlite-mcp and pg-mcp", skill)
        self.assertIn("successful completion of `publish`, `verify-published`, and `verify-release-metadata`", skill)
        self.assertIn("`publish`, `verify-published`, and `verify-release-metadata`", skill)
        self.assertIn("tar -tzf <asset>`; it must print only the enrolled project's `binary` value", skill)
        self.assertRegex(skill, r"(?m)^description: .+")


if __name__ == "__main__":
    unittest.main()
