---
name: homebrew-tools-release
description: Prepare an enrolled CLI project's stable GitHub release assets and configure its verified release workflow to notify the Tilmon Engineering Homebrew tools tap.
---

# Homebrew tools release integration

Use this skill when a maintainer asks to enroll a CLI in the Tilmon Engineering Homebrew tap or configure an already-enrolled producer to notify the tap on releases. The enrolled projects are `sqlite-mcp`, `pg-mcp`, and `typedb-mcp`; typedb-mcp's first verified release is `v0.3.7`. This skill is committed to the tap repository; it is **not** automatically loaded in another project's workspace. From another repository, check out `tilmon-engineering/homebrew-tools` and read `.agents/skills/homebrew-tools-release/SKILL.md` before proposing producer workflow edits.

## Boundaries and enrollment

- `Casks/<token>.rb` contains the installable Homebrew cask. `tap-projects.json` is a separate, manually reviewed inventory allowlist mapping cask token to canonical `owner/repo`, executable, stable tag, and release assets.
- Do not treat a dispatch as project enrollment. Do not change the tap inventory from a producer release workflow. Ask the tap maintainers to review the inventory/cask addition first; enrolled releases then update only the version and checksums automatically.
- This procedure is for a project maintainer who is authorized to change the source repository's release workflow and provision its secret. This tap-only change does not edit another repository or provision credentials.
- Before implementing in a producer, inspect that project’s release workflow and confirm the exact successful publish and verification job dependencies. Never infer job names from this example.
- `typedb-mcp` was enrolled after published release `v0.3.7` passed its producer verification jobs. Its cask token and exact four-asset contract are now in `tap-projects.json`. The producer still requires a post-verification dispatch job and the scoped `HOMEBREW_TOOLS_DISPATCH_TOKEN` secret before tag pushes update this tap automatically.

## Current enrolled project: sqlite-mcp

The inventory specifies binary `sqlite-mcp`, stable tags exactly `vX.Y.Z`, and the `SHA256SUMS` checksum file. It expects exactly these four gzip tar archives and no additional release assets:

| Rust target | Release asset | Runner OS / architecture |
| --- | --- | --- |
| `x86_64-unknown-linux-gnu` | `sqlite-mcp-x86_64-unknown-linux-gnu.tar.gz` | Ubuntu 24.04 x86_64, GNU/glibc |
| `aarch64-unknown-linux-gnu` | `sqlite-mcp-aarch64-unknown-linux-gnu.tar.gz` | Ubuntu 24.04 aarch64, GNU/glibc |
| `aarch64-apple-darwin` | `sqlite-mcp-aarch64-apple-darwin.tar.gz` | macOS 15 arm64 |
| `x86_64-apple-darwin` | `sqlite-mcp-x86_64-apple-darwin.tar.gz` | macOS 15 Intel |

Every archive must contain exactly one executable regular file at its root named `sqlite-mcp`; no folders, extra files, symlinks, or hard links. `SHA256SUMS` must have exactly one line per four archive files in this format (two literal spaces between digest and asset name):

```text
<64 lowercase-or-uppercase hexadecimal SHA-256 characters><two spaces><exact asset filename>
```

Tags must be immutable published stable `vX.Y.Z` releases, with no leading-zero numeric components, prerelease suffix, or build metadata. The GitHub release must be published (not draft or prerelease) and contain exactly those archives plus `SHA256SUMS`. Release asset verification jobs should check uploaded archive bytes and metadata before notification.

## Enrolled project: typedb-mcp

`typedb-mcp` is enrolled under cask token `typedb-mcp`, with release binary `typedb-mcp`. The verified baseline is release `v0.3.7`, built on Ubuntu 24.04 GNU/glibc x86_64 and aarch64 and macOS 15 Intel and arm64. It uses stable `vX.Y.Z` releases with `SHA256SUMS` and exactly these four archives:

| Rust target | Release asset | Runner OS / architecture |
| --- | --- | --- |
| `x86_64-unknown-linux-gnu` | `typedb-mcp-x86_64-unknown-linux-gnu.tar.gz` | Ubuntu 24.04 x86_64, GNU/glibc |
| `aarch64-unknown-linux-gnu` | `typedb-mcp-aarch64-unknown-linux-gnu.tar.gz` | Ubuntu 24.04 aarch64, GNU/glibc |
| `aarch64-apple-darwin` | `typedb-mcp-aarch64-apple-darwin.tar.gz` | macOS 15 arm64 |
| `x86_64-apple-darwin` | `typedb-mcp-x86_64-apple-darwin.tar.gz` | macOS 15 Intel |

Each archive must contain only one executable regular file at its root named `typedb-mcp`, and the published release must contain only these four archives and `SHA256SUMS`.

## Enrolled project: pg-mcp

`pg-mcp` is enrolled under cask token `pg-mcp`; its release binary is named `postgres-mcp`, not `pg-mcp`. It uses stable `vX.Y.Z` releases with `SHA256SUMS` and exactly these four archives:

| Rust target | Release asset | Runner mapping |
| --- | --- | --- |
| `x86_64-unknown-linux-gnu` | `postgres-mcp-x86_64-unknown-linux-gnu.tar.gz` | x86_64 GNU/glibc Linux |
| `aarch64-unknown-linux-gnu` | `postgres-mcp-aarch64-unknown-linux-gnu.tar.gz` | aarch64 GNU/glibc Linux |
| `aarch64-apple-darwin` | `postgres-mcp-aarch64-apple-darwin.tar.gz` | arm64 macOS |
| `x86_64-apple-darwin` | `postgres-mcp-x86_64-apple-darwin.tar.gz` | Intel macOS |

The tap verified `pg-mcp` release `v0.1.0`: archive digests match `SHA256SUMS` and each tar contains exactly one executable regular file named `postgres-mcp`. Those facts verify the downloaded release artifact contract, not the oldest OS versions supported by the built executables.

## Producer workflow ordering

Create a dedicated notification job in the producer's release workflow. It must depend on **all** jobs that publish the release and verify the published release artifacts/checksums; run only after every dependency succeeds. In both sqlite-mcp and pg-mcp, map this to successful completion of `publish`, `verify-published`, and `verify-release-metadata`. In pg-mcp, `verify-published` downloads each public release asset, checks the archive and runs a target-native smoke test. Use `needs` for every dependency and an explicit success condition; do not run notification merely because the tag was pushed.

A notification failure must fail visibly in the producer Actions run without deleting or rolling back the already-published release. Maintainers can fix token access and rerun the notification job, or use the tap's manual replay/scheduled reconciliation. Do not silently swallow a non-2xx HTTP response.

## Credential and API contract

Prefer a short-lived GitHub App installation token restricted to `tilmon-engineering/homebrew-tools`; grant repository **Contents: write** to invoke this repository-dispatch endpoint (Metadata: read is implicit). A fine-grained PAT alternative must be restricted to this one tap repository, use Contents: write, have a planned expiration and rotation owner, and comply with organization token policy. Do not assume the producer repository's default `GITHUB_TOKEN` can dispatch across repositories. Provision the selected token only as the source repository Actions secret `HOMEBREW_TOOLS_DISPATCH_TOKEN`; never echo it, place it in a URL/payload, or put it in logs.

The producer notification call is fixed and has no arbitrary owner/repository/path inputs. Each producer must use its reviewed executable/package name and the successful publish and verification job names from its own release workflow; never copy another producer's dependencies without checking them. For typedb-mcp the repository is `tilmon-engineering/typedb-mcp` and the local workflow dependencies are `publish-release`, `verify-published`, and `verify-release-metadata`.

```yaml
permissions:
  contents: read

jobs:
  notify-homebrew-tap:
    needs: [publish-release, verify-published, verify-release-metadata]
    if: ${{ success() }}
    runs-on: ubuntu-latest
    steps:
      - name: Notify the enrolled tap
        env:
          DISPATCH_TOKEN: ${{ secrets.HOMEBREW_TOOLS_DISPATCH_TOKEN }}
          RELEASE_TAG: ${{ github.ref_name }}
        run: |
          set -euo pipefail
          case "${GITHUB_REPOSITORY}" in
            tilmon-engineering/sqlite-mcp|tilmon-engineering/pg-mcp|tilmon-engineering/typedb-mcp) ;;
            *) echo "repository is not enrolled" >&2; exit 1 ;;
          esac
          [[ "${RELEASE_TAG}" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
          curl --fail-with-body --silent --show-error \
            -X POST \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer ${DISPATCH_TOKEN}" \
            -H "X-GitHub-Api-Version: 2026-03-10" \
            https://api.github.com/repos/tilmon-engineering/homebrew-tools/dispatches \
            -d "{\"event_type\":\"homebrew-tools-release\",\"client_payload\":{\"repository\":\"${GITHUB_REPOSITORY}\",\"tag\":\"${RELEASE_TAG}\"}}"
```

The dispatch API is `POST /repos/{owner}/{repo}/dispatches`; the fixed event type is `homebrew-tools-release`, with only `repository` and `tag` in `client_payload`. The tap compares the canonical repository to its inventory, validates the exact stable tag, fetches release metadata from that inventory repository, verifies exact asset names, SHA256SUMS, bytes, archive members, and refuses rollback. Payload values never choose a repository URL or filesystem path. All three casks use exactly four GNU/glibc Linux and Apple Darwin target triples; sqlite-mcp and typedb-mcp build validation names Ubuntu 24.04/macOS 15, while pg-mcp's specific oldest supported OS versions need confirmation before making broader claims.

## Validation before enabling producer dispatch

1. Confirm the tap inventory includes the canonical project repo, cask token, binary, target-to-asset mapping, checksum filename, and reviewed supported-platform limits.
2. Build each archive from the specified target and runner. Inspect `tar -tzf <asset>`; it must print only the enrolled project's `binary` value from `tap-projects.json` (`sqlite-mcp`, `postgres-mcp`, or `typedb-mcp` for the current casks). Confirm the member is an executable regular file.
3. Generate and compare the checksum file: `sha256sum <four assets> > SHA256SUMS`; verify no unexpected file names or duplicate entries.
4. Verify a stable `vX.Y.Z` tag and published non-draft/non-prerelease GitHub Release. Wait until every release verification job is green before notification runs.
5. Check the workflow job dependency names and secret reference. Do not use `pull_request` builds or untrusted event content to expose the token.
6. In a disposable/local fixture, run `python3 -m unittest discover -s tests -v`. For a maintainer-authorized real producer adoption, a release job may test the real endpoint only after the credential is provisioned. No live dispatch is part of the tap fixture tests.
7. Smoke-test `brew tap tilmon-engineering/tools`, `brew install --cask typedb-mcp`, and `typedb-mcp --version` (and the existing casks as appropriate) on a supported macOS or Ubuntu 24.04 runner. Verify `brew outdated --cask` / `brew upgrade --cask sqlite-mcp` after a newer tap cask commit where representative runner access exists.

## Failure handling and recovery

- A non-success dispatch job means no tap event was accepted; read the producer Actions failure, verify the secret name/token scope/organization policy, then safely rerun that notification job. Do not republish or delete a valid release just to retry dispatch.
- If a release is valid but older than the tap version, the tap rejects it as stale. Do not force a downgrade.
- A missed event is recovered by the tap's daily reconciliation, which paginates the inventory repository's Releases API and checks stable semantic versions from newest to older until it finds the highest valid release. A tap maintainer may also run `update-cask.yml` manually with an allowlisted cask token and exact tag.
- A failed checksum, unexpected asset, invalid tar member, unlisted repository, draft, prerelease, or tag mismatch is a hard rejection with no cask write or commit. Fix the upstream release contract; do not bypass validation.
- Tap automation commits only generated cask paths and does not modify `tap-projects.json`, this skill, or another project's files.

The typedb-mcp release `v0.3.7` and tap enrollment are verified; automated cross-repository dispatch remains unverified until the producer workflow adds the post-verification notification job, configures its scoped secret, and successfully runs it.
