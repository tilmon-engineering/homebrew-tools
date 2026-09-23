cask "sqlite-mcp" do
  arch arm: "aarch64", intel: "x86_64"
  os macos: "apple-darwin", linux: "unknown-linux-gnu"

  version "0.3.1"
  sha256 arm:          "492d328805b42b921af92306de1e38fb8a30c6ffd080f776b55fc17266fabc7c",
         intel:        "10ddbbd7becb77166ca9cc27b9e4c60c4a1bc44f036d8aebbc3a567e9288747b",
         arm64_linux:  "c0ffbdd9edbf9c821dd878a062da0ac2c029737152c89961feb4533041c2556f",
         x86_64_linux: "d48ab461429ec957fc80d165bfe34c545cf1a824ab4b14d654bb49d80e21e293"

  url "https://github.com/tilmon-engineering/sqlite-mcp/releases/download/v#{version}/sqlite-mcp-#{arch}-#{os}.tar.gz"
  name "sqlite-mcp"
  desc "SQLite Model Context Protocol server"
  homepage "https://github.com/tilmon-engineering/sqlite-mcp"

  depends_on macos: :sequoia

  binary "sqlite-mcp"
end
