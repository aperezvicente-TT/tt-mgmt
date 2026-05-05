#!/bin/bash
# Quick script to enable completion in current shell
# USAGE: source ./enable_completion_now.sh

if command -v tt-mgmt &> /dev/null; then
    echo "Enabling tt-mgmt tab completion for current session..."
    eval "$(_TT_MGMT_COMPLETE=bash_source tt-mgmt)"
    echo "[OK] Done! Try: tt-mgmt <TAB><TAB>"
else
    echo "Error: tt-mgmt not found. Run ./install.sh first."
fi
