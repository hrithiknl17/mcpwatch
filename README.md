# MCPwatch

Continuous health auditing of the official MCP registry.

Most registry servers are stdio packages, not remote endpoints — you can't ping
them, you have to spawn them. MCPwatch spawns every one on disposable CI runners,
completes the MCP handshake, and publishes what actually works.

Status: pre-v0. See CLAUDE.md for architecture, KICKOFF.md for the current milestone.
