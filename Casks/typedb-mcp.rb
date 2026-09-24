cask "typedb-mcp" do
  arch arm: "aarch64", intel: "x86_64"
  os macos: "apple-darwin", linux: "unknown-linux-gnu"

  version "0.3.8"
  sha256 arm:          "3617fdfa97b479398834126cd58e65f31086d1f99e3e387be6895cba03ff8d4f",
         intel:        "48f8de1bac51711d64ce411ebd970cdbe7bab26d86d3533bc3b5e0a9575e41e2",
         arm64_linux:  "9e52007cc3acdbcab7915b982dad3e67ccf2f0e900b4fc2403feaa7e56a2e481",
         x86_64_linux: "59389f42da71fc717cf941542405e7cb2bac9ab160b8bd8e0c9129689b57eedf"

  url "https://github.com/tilmon-engineering/typedb-mcp/releases/download/v#{version}/typedb-mcp-#{arch}-#{os}.tar.gz"
  name "typedb-mcp"
  desc "Safety-focused TypeDB Model Context Protocol server"
  homepage "https://github.com/tilmon-engineering/typedb-mcp"

  depends_on macos: :sequoia

  binary "typedb-mcp"
end
