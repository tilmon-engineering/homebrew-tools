cask "sqlite-mcp" do
  arch arm: "aarch64", intel: "x86_64"
  os macos: "apple-darwin", linux: "unknown-linux-gnu"

  version "0.4.0"
  sha256 arm:          "b16a6ab3cd89b71f7e5bdbac2b2c1dac8504af33b8c8fc6cc05fdd494b249e17",
         intel:        "1a427a5128d60e25271a13c184387cec524625c5e8c53e1f42aa9baabb4e61ce",
         arm64_linux:  "f502f1b2d1946ba4639cd38238fa2eb29d7a934a825d1cca4b624f9cd26735b1",
         x86_64_linux: "f696cdd98151efa7324ec6b977490bd1d289c34eb88977244634a704798cdaa8"

  url "https://github.com/tilmon-engineering/sqlite-mcp/releases/download/v#{version}/sqlite-mcp-#{arch}-#{os}.tar.gz"
  name "sqlite-mcp"
  desc "SQLite Model Context Protocol server"
  homepage "https://github.com/tilmon-engineering/sqlite-mcp"

  depends_on macos: :sequoia

  binary "sqlite-mcp"
end
