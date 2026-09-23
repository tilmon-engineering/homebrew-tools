# Tilmon Engineering Homebrew Tools

This repository is the `tilmon-engineering/tools` Homebrew tap, backed by the GitHub repository `tilmon-engineering/homebrew-tools`.

## Install

```sh
brew tap tilmon-engineering/tools
brew install --cask sqlite-mcp
```

The `sqlite-mcp`, `pg-mcp`, and `typedb-mcp` casks provide the upstream four-target release matrices: GNU/glibc Linux x86_64 and aarch64, and macOS Intel and Apple silicon. `sqlite-mcp` and `typedb-mcp` release validation specifically names Ubuntu 24.04 and macOS 15. Do not infer support for other Linux distributions, older macOS versions, or unlisted architectures from target triples alone. `pg-mcp` installs the `postgres-mcp` executable.

## Inventory and automatic release updates

[`tap-projects.json`](tap-projects.json) is the manually maintained allowlist of enrolled projects and their exact release asset contracts. Adding or removing a project is a reviewed tap change. A release event can never enroll a project or select a cask path.

For an enrolled project, a published stable release may trigger an automatic commit that updates only that project's version and four pinned SHA-256 values in `Casks/<token>.rb`. The casks live in `Casks/` and are what Homebrew installs; they are distinct from the inventory allowlist. `typedb-mcp` is enrolled at verified release `v0.3.7`, with four native target archives containing only the `typedb-mcp` executable and a `SHA256SUMS` manifest. Its producer workflow verifies both draft and published assets before any future dispatch. These bot commits are intentional: pinned versions and checksums let Homebrew discover and install upgrades conventionally. Duplicate releases are no-ops, older releases cannot roll a cask back, and a daily reconciliation recovers missed notifications.

The receiver accepts only the fixed `homebrew-tools-release` repository-dispatch event, an allowlisted canonical source repository, an exact stable `vX.Y.Z` tag, and a published release with the exact expected assets. It verifies `SHA256SUMS`, downloaded bytes, and that each archive contains only the expected executable before changing a cask. It does not check out or run code from source projects.

### Producer integration and credentials

Producer notification wiring and its secret are separate from enrollment. `typedb-mcp` is enrolled, but its `release.yml` does not yet dispatch to this tap; until that producer workflow adopts the notification job and its narrowly scoped credential is provisioned, updates are recoverable by manual replay or scheduled reconciliation. A producer maintainer should load `.agents/skills/homebrew-tools-release/SKILL.md` from this tap checkout and add notification only after release publication and verification succeed. The source repository's ordinary `GITHUB_TOKEN` is not assumed to authorize a cross-repository event. Configure a GitHub App installation token if possible, or a fine-grained token limited to this tap with **Contents: write** (Metadata: read is implicit), in the producer repository secret `HOMEBREW_TOOLS_DISPATCH_TOKEN`. Fine-grained tokens need an expiry and rotation plan. In sqlite-mcp and pg-mcp, dispatch depends on successful `publish`, `verify-published`, and `verify-release-metadata` jobs; typedb-mcp must depend on `publish-release`, `verify-published`, and `verify-release-metadata`. Never pass credentials in the dispatch payload or URL.

The API call is `POST https://api.github.com/repos/tilmon-engineering/homebrew-tools/dispatches` with `event_type: "homebrew-tools-release"` and `client_payload: {"repository": "tilmon-engineering/<enrolled-repo>", "tag": "vX.Y.Z"}`. A failed dispatch fails the producer notification job but does not undo its already-published release; fix authorization and rerun that job or use this tap's manual replay. `workflow_dispatch` can replay an enrolled project/tag (`sqlite-mcp`, `pg-mcp`, or `typedb-mcp`), while the scheduled reconciliation finds the latest stable release for every enrolled project.

The cross-repository credential path is documented, not exercised here: a producer must separately adopt the skill, provision its credential, and send a real event before end-to-end dispatch is verified.

## Maintainer checks and recovery

Run the local fixture checks and contract validation:

```sh
python3 -m unittest discover -s tests -v
actionlint .github/workflows/update-cask.yml .github/workflows/ci.yml # use actionlint >= 1.7.12 for queue: max
brew audit --cask --strict Casks/sqlite-mcp.rb
brew style --cask Casks/sqlite-mcp.rb
```

`update-cask.yml` can be run manually for any enrolled token (`sqlite-mcp`, `pg-mcp`, or `typedb-mcp`) and an exact release tag; it validates the published release from the GitHub API before any write. Scheduled reconciliation is the automatic recovery path for a missed dispatch. It does not modify the inventory, skill, or unrelated casks.
