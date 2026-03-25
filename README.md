# code-intel-mcp

[![CI](https://github.com/arunveersingh/code-intel-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/arunveersingh/code-intel-mcp/actions)
[![PyPI](https://img.shields.io/pypi/v/code-intel-mcp)](https://pypi.org/project/code-intel-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

MCP server that gives AI agents deep code understanding across multiple git repositories. Combines git lifecycle management, [Zoekt](https://github.com/sourcegraph/zoekt)-based trigram code search, and cross-repo dependency analysis.

## Quick Start (macOS)

### Prerequisites

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package runner):

```bash
brew install uv
```

### 1. Setup (one time)

```bash
uvx code-intel-mcp setup --zoekt-url "https://github.com/arunveersingh/code-intel-mcp/releases/download/v0.1.0"
```

This creates `~/.code-intel-mcp/` directories and downloads the Zoekt search engine binaries.

### 2. Add to your MCP client

**Kiro** — add to `~/.kiro/settings/mcp.json` inside `"mcpServers"`:

```json
"code-intel-mcp": {
  "command": "uvx",
  "args": ["code-intel-mcp", "serve"],
  "env": {
    "GITLAB_URL": "https://your-gitlab.com",
    "GITLAB_TOKEN": "<your-personal-access-token>"
  },
  "disabled": false,
  "autoApprove": []
}
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json` inside `"mcpServers"`:

```json
"code-intel-mcp": {
  "command": "uvx",
  "args": ["code-intel-mcp", "serve"],
  "env": {
    "GITLAB_URL": "https://your-gitlab.com",
    "GITLAB_TOKEN": "<your-personal-access-token>"
  }
}
```

### 3. Done

Restart your MCP client. Your AI agent now has access to 15 tools for code intelligence.

## Features

- **Git lifecycle** — clone, pull, checkout, sync, and remove repositories
- **GitLab integration** — bulk-clone entire GitLab groups
- **Code search** — fast trigram-based search powered by Zoekt
- **File browsing** — read files, list directories, get repo overviews
- **Dependency analysis** — parse Maven, Gradle, and npm build configs
- **Symbol references** — word-boundary search across repos

## Available Tools

| Tool | Description |
|------|-------------|
| `repo_add` | Clone and register a git repository |
| `repo_add_gitlab_group` | Bulk-clone all projects from a GitLab group |
| `repo_list` | List all managed repositories |
| `repo_info` | Detailed repo info (branches, tags, commits, size) |
| `repo_checkout` | Switch to a branch, tag, or commit SHA |
| `repo_pull` | Pull latest changes |
| `repo_sync_all` | Pull all managed repositories |
| `repo_remove` | Remove a repository and its index |
| `search_code` | Search code with language/file filters |
| `search_files` | Search for files by name pattern |
| `search_references` | Find symbol references across repos |
| `read_file` | Read file contents |
| `list_directory` | List directory contents |
| `get_repo_overview` | Repo summary (README, structure, build info) |
| `find_dependencies` | Analyze build config dependencies |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITLAB_URL` | For GitLab features | Base URL of your GitLab instance |
| `GITLAB_TOKEN` | For GitLab features | Personal access token with `read_api` scope |

## How It Works

The server manages a local repository store at `~/.code-intel-mcp/`:

```
~/.code-intel-mcp/
├── repos/       # Cloned git repositories
├── index/       # Zoekt search index
├── bin/         # Zoekt binaries (auto-installed)
└── config.json  # Registry of managed repos
```

Every git mutation (clone, pull, checkout) automatically triggers Zoekt re-indexing, keeping search results current. The server auto-prepends `~/.code-intel-mcp/bin` to PATH on startup, so no manual PATH configuration is needed.

## Troubleshooting

**`spawn code-intel-mcp ENOENT`** — Use `"command": "uvx"` with `"args": ["code-intel-mcp", "serve"]`, not `"command": "code-intel-mcp"`.

**`Requires-Python >=3.11`** — Use `uvx` instead of `pip install`. It handles Python versions automatically.

**Zoekt binaries not found** — Re-run setup: `uvx code-intel-mcp setup --zoekt-url "https://github.com/arunveersingh/code-intel-mcp/releases/download/v0.1.0"`

## Development

```bash
git clone https://github.com/arunveersingh/code-intel-mcp.git
cd code-intel-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install hatchling && python3 -m hatchling build -t wheel
pip install dist/*.whl && pip install pytest pytest-asyncio hypothesis
pytest
```

## License

MIT — see [LICENSE](LICENSE).
