#!/bin/bash
# One-step install and enable completion
# USAGE: source ./install_and_enable.sh

echo "================================================="
echo "Installing tt-mgmt..."
echo "================================================="
echo ""

pip install --upgrade -q "pip>=23.1"

pip install -e . -q

if [ $? -eq 0 ]; then
    echo "tt-mgmt installed successfully."
else
    echo "Installation failed."
    return 1
fi

echo ""
echo "================================================="
echo "Enabling tab completion..."
echo "================================================="
echo ""

eval "$(_TT_MGMT_COMPLETE=bash_source tt-mgmt)"
echo "Tab completion enabled for current session."

RC_FILE="$HOME/.bashrc"
if grep -q "_TT_MGMT_COMPLETE" "$RC_FILE" 2>/dev/null; then
    echo "Already configured in $RC_FILE."
else
    echo "" >> "$RC_FILE"
    echo "# tt-mgmt shell completion" >> "$RC_FILE"
    echo 'eval "$(_TT_MGMT_COMPLETE=bash_source tt-mgmt)"' >> "$RC_FILE"
    echo "Added to $RC_FILE (for future sessions)."
fi

echo ""
echo "================================================="
echo "Done. Try it now:"
echo "================================================="
echo ""
echo "  tt-mgmt <TAB><TAB>"
echo "  tt-mgmt device <TAB><TAB>"
echo "  tt-mgmt smi <TAB><TAB>"
echo ""
