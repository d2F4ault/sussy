#!/usr/bin/env bash
set -e

echo "=========================================================================="
echo "   MIDIANO 4-CORE CLOUD RENDERING AGENT (GitHub Codespaces)"
echo "=========================================================================="

CORES=$(nproc || echo 4)
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
echo "Hardware Detected: ${CORES} CPU Cores | ${RAM_MB} MB RAM"
echo "Allocating 4 parallel Xvfb displays & rendering pipelines (:99 to :102)..."

# Ensure Xvfb and FFmpeg are present
if ! command -v Xvfb &> /dev/null; then
    echo "Installing Xvfb virtual display..."
    sudo apt-get update -qq && sudo apt-get install -y -qq xvfb ffmpeg
fi

# Ensure Playwright dependencies
if ! command -v playwright &> /dev/null; then
    python3 -m pip install -r requirements.txt
    python3 -m playwright install chromium
    python3 -m playwright install-deps
fi

# Clean up any leftover virtual X displays
for d in 99 100 101 102; do
    sudo rm -f "/tmp/.X11-unix/X$d" 2>/dev/null || true
done
pkill -f Xvfb 2>/dev/null || true

echo "--------------------------------------------------------------------------"
echo "Starting Cloud Agent on port 8000..."
echo ""
if [ -n "$CODESPACE_NAME" ]; then
    echo "YOUR CODESPACE URL:"
    echo "  https://${CODESPACE_NAME}-8000.app.github.dev"
    echo ""
    echo "IMPORTANT: In the VS Code bottom panel, click 'PORTS' tab,"
    echo "right-click Port 8000 -> 'Port Visibility' -> 'Public'."
else
    echo "Local URL:"
    echo "  http://localhost:8000"
fi
echo "--------------------------------------------------------------------------"

python3 -m midiano_renderer.cloud_server --port 8000
