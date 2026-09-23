cask "typedb-mcp" do
  arch arm: "aarch64", intel: "x86_64"
  os macos: "apple-darwin", linux: "unknown-linux-gnu"

  version "0.3.7"
  sha256 arm:          "768886f17883e04df3e0b3da6be5ea302c5e06139014d8b6fd84e3b277b12817",
         intel:        "3c04b789f701b5ced32c3998c331b2338c16572cd978188cc366d5ddcfc3a301",
         arm64_linux:  "991c6760c9d3eb06432bf97ec2fadf1e280d7349aa72c0df18b2cec60c21649a",
         x86_64_linux: "0df6f88966990f9bbeb9a0b07994496a33fc0b02b3bde5842dd9f7e4d9dcdefb"

  url "https://github.com/tilmon-engineering/typedb-mcp/releases/download/v#{version}/typedb-mcp-#{arch}-#{os}.tar.gz"
  name "typedb-mcp"
  desc "Safety-focused TypeDB Model Context Protocol server"
  homepage "https://github.com/tilmon-engineering/typedb-mcp"

  depends_on macos: :sequoia

  binary "typedb-mcp"
end
