cask "pg-mcp" do
  arch arm: "aarch64", intel: "x86_64"
  os macos: "apple-darwin", linux: "unknown-linux-gnu"

  version "0.1.0"
  sha256 arm:          "81b439b2bfe0b3be5384de8cc3ae2674589095de03f40acb610e925ad9919af5",
         intel:        "aa19773a65eb72cdad4be7da3cb50425b587bfa33cdfaea3b72bc4aaa3ea0065",
         arm64_linux:  "8826faf2b7a6ec33cc81d1c134648392e3a24bf23237a53253b5b9931d54e808",
         x86_64_linux: "2c987d10f7033c7a04d8718ffd3929879a72a63b407c9af12243cc091b217d28"

  url "https://github.com/tilmon-engineering/pg-mcp/releases/download/v#{version}/postgres-mcp-#{arch}-#{os}.tar.gz"
  name "pg-mcp"
  desc "PostgreSQL Model Context Protocol server"
  homepage "https://github.com/tilmon-engineering/pg-mcp"

  binary "postgres-mcp"
end
