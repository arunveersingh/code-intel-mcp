#!/bin/bash
# Cross-compile Zoekt binaries for macOS (arm64 + amd64)
# Requires: Go installed (brew install go)
set -e

OUT_DIR="zoekt-binaries"
mkdir -p "$OUT_DIR/darwin-arm64" "$OUT_DIR/darwin-amd64"

echo "Building zoekt-index and zoekt-webserver..."

for GOARCH in arm64 amd64; do
    echo ""
    echo "=== darwin/$GOARCH ==="
    for CMD in zoekt-index zoekt-webserver; do
        echo "  Building $CMD..."
        GOOS=darwin GOARCH=$GOARCH go install "github.com/sourcegraph/zoekt/cmd/$CMD@latest"
    done

    # Copy from GOPATH
    GOBIN="$HOME/go/bin/darwin_$GOARCH"
    if [ "$GOARCH" = "$(uname -m)" ]; then
        GOBIN="$HOME/go/bin"
    fi

    cp "$GOBIN/zoekt-index" "$OUT_DIR/darwin-$GOARCH/"
    cp "$GOBIN/zoekt-webserver" "$OUT_DIR/darwin-$GOARCH/"

    # Create tar.gz
    tar czf "$OUT_DIR/zoekt-darwin-$GOARCH.tar.gz" \
        -C "$OUT_DIR/darwin-$GOARCH" zoekt-index zoekt-webserver

    echo "  Created $OUT_DIR/zoekt-darwin-$GOARCH.tar.gz"
done

echo ""
echo "Done! Archives:"
ls -lh "$OUT_DIR"/*.tar.gz
