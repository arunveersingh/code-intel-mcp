# code-intel-mcp

[![CI](https://github.com/arunveersingh/code-intel-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/arunveersingh/code-intel-mcp/actions)
[![PyPI](https://img.shields.io/pypi/v/code-intel-mcp)](https://pypi.org/project/code-intel-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

MCP server that gives AI agents deep code understanding across multiple git repositories. Combines git lifecycle management, [Zoekt](https://github.com/sourcegraph/zoekt)-based trigram code search, and cross-repo dependency analysis.

## Quick Start

### 1. Install & Setup

```bash
# Install
pip install code-intel-mcp

# Setup (creates directories, installs Zoekt binaries)
code-intel-mcp setup --zoekt-url "https://github.com/arunveersingh/code-intel-mcp/releases/download/v0.1.0"
```

Or with `uvx` (no install needed):

```bash
uvx code-intel-mcp setup --zoekt-url "https://github.com/arunveersingh/code-intel-mcp/releases/download/v0.1.0"
```

### 2. Add to your MCP client

**Kiro** — add to `~/.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "code-intel-mcp": {
      "command": "uvx",
      "args": ["code-intel-mcp", "serve"],
      "env": {
        "GITLAB_URL": "https://your-gitlab.com",
        "GITLAB_TOKEN": "<your-token>",
        "PATH": "~/.code-intel-mcp/bin:/usr/local/bin:/usr/bin:/bin"
      }
    }
  }
}
```

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "code-intel-mcp": {
      "command": "uvx",
      "args": ["code-intel-mcp", "serve"],
      "env": {
        "GITLAB_URL": "https://your-gitlab.com",
        "GITLAB_TOKEN": "<your-token>",
        "PATH": "~/.code-intel-mcp/bin:/usr/local/bin:/usr/bin:/bin"
      }
    }
  }
}
```

### 3. Use it

Your AI agent now has access to 15 tools for code intelligence.

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
| `ZOEKT_BINARY_URL` | For auto-install | URL prefix for Zoekt binary downloads |

## How It Works

The server manages a local repository store at `~/.code-intel-mcp/`:

```
~/.code-intel-mcp/
├── repos/       # Cloned git repositories
├── index/       # Zoekt search index
├── bin/         # Zoekt binaries (auto-installed)
└── config.json  # Registry of managed repos
```

Every git mutation (clone, pull, checkout) automatically triggers Zoekt re-indexing, keeping search results current.

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
