# Changelog

## 0.1.0 (2025-03-25)

Initial release.

- Git lifecycle management (clone, pull, checkout, sync, remove)
- GitLab group bulk-clone via REST API
- Zoekt-based trigram code search
- File browsing and directory listing
- Dependency analysis (Maven, Gradle, npm)
- Cross-repo symbol reference search
- Auto-install Zoekt binaries on macOS (arm64, x86_64)
- CLI with `serve` and `setup` commands

## 0.1.1 (2025-03-25)

- Auto-prepend `~/.code-intel-mcp/bin` to PATH on server startup (no manual PATH config needed)
- Fix lint issues across codebase
- Add Zoekt auto-installer module
- Add troubleshooting section to README
