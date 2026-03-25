#!/bin/bash
# One-command setup for code-intel-mcp on macOS
set -e

INSTALL_DIR="$HOME/.code-intel-mcp"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$INSTALL_DIR/bin"
REPO_DIR="$INSTALL_DIR/repos"
INDEX_DIR="$INSTALL_DIR/index"

# Where this script lives (the cloned repo)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== code-intel-mcp installer ==="
echo ""

# 1. Create directories
echo "Creating directories..."
mkdir -p "$REPO_DIR" "$INDEX_DIR" "$BIN_DIR"

# 2. Create venv with Python 3.11+
echo "Setting up Python environment..."
PYTHON=""
for p in python3.13 python3.12 python3.11 python3; do
    if command -v "$p" &>/dev/null; then
        version=$("$p" -c "import sys; print(sys.version_info[:2])")
        major=$("$p" -c "import sys; print(sys.version_info[1])")
        if [ "$major" -ge 11 ]; then
            PYTHON="$p"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.11+ required. Install with: brew install python@3.13"
    exit 1
fi

echo "  Using $PYTHON ($($PYTHON --version))"
$PYTHON -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet hatchling

# 3. Build and install
echo "Building and installing code-intel-mcp..."
cd "$SCRIPT_DIR"
rm -rf dist
"$VENV_DIR/bin/python3" -m hatchling build -t wheel --quiet 2>/dev/null || "$VENV_DIR/bin/python3" -m hatchling build -t wheel
"$VENV_DIR/bin/pip" install --quiet --force-reinstall dist/code_intel_mcp-*.whl

# 4. Install Zoekt binaries
echo "Installing Zoekt search engine..."
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    ARCHIVE="zoekt-darwin-arm64.tar.gz"
elif [ "$ARCH" = "x86_64" ]; then
    ARCHIVE="zoekt-darwin-amd64.tar.gz"
else
    echo "ERROR: Unsupported architecture: $ARCH"
    exit 1
fi

if [ -f "$SCRIPT_DIR/zoekt-binaries/$ARCHIVE" ]; then
    tar xzf "$SCRIPT_DIR/zoekt-binaries/$ARCHIVE" -C "$BIN_DIR"
    chmod +x "$BIN_DIR/zoekt-index" "$BIN_DIR/zoekt-webserver"
    echo "  Zoekt installed to $BIN_DIR"
else
    echo "  WARNING: $ARCHIVE not found in zoekt-binaries/"
    echo "  You'll need to install Zoekt manually."
fi

# 5. Verify
echo ""
echo "=== Verification ==="
echo "  code-intel-mcp: $VENV_DIR/bin/code-intel-mcp"
echo "  zoekt-index:    $(ls "$BIN_DIR/zoekt-index" 2>/dev/null || echo 'NOT FOUND')"
echo "  zoekt-webserver: $(ls "$BIN_DIR/zoekt-webserver" 2>/dev/null || echo 'NOT FOUND')"
echo "  git:            $(which git || echo 'NOT FOUND')"

# 6. Print MCP config
echo ""
echo "=== Add this to your MCP config ==="
echo ""
cat <<EOF
{
  "code-intel-mcp": {
    "command": "$VENV_DIR/bin/code-intel-mcp",
    "args": ["serve"],
    "env": {
      "GITLAB_URL": "https://gitlab.sbs-software.com",
      "GITLAB_TOKEN": "<YOUR_GITLAB_TOKEN>",
      "PATH": "$BIN_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    },
    "disabled": false,
    "autoApprove": []
  }
}
EOF

echo ""
echo "Done! Paste the config above into:"
echo "  Kiro:   ~/.kiro/settings/mcp.json (inside mcpServers)"
echo "  Claude: ~/Library/Application Support/Claude/claude_desktop_config.json"
