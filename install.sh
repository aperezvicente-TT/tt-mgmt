#!/bin/bash
# Installation script for tt-mgmt (standalone build with tt-umd submodule)

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Ensure the submodule is populated
if [ ! -f "third_party/tt-umd/CMakeLists.txt" ]; then
    echo "Initializing tt-umd submodule..."
    git submodule update --init --recursive
fi

echo "Installing tt-mgmt..."
pip install -e .

echo ""
echo "tt-mgmt installed successfully!"
echo ""
echo "Usage:"
echo ""
echo "  Interactive shell (default):"
echo "    tt-mgmt"
echo ""
echo "  Direct commands:"
echo "    tt-mgmt smi status     # snapshot"
echo "    tt-mgmt smi monitor    # live dashboard"
echo "    tt-mgmt device list"
echo "    tt-mgmt system status"
echo ""
