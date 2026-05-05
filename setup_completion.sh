#!/bin/bash
# Automatic shell completion setup for tt-mgmt
# USAGE: source ./setup_completion.sh  (must be sourced, not executed!)

# Detect shell
CURRENT_SHELL=$(basename "$SHELL")

echo "Setting up tab completion for tt-mgmt..."
echo "Detected shell: $CURRENT_SHELL"
echo ""

case "$CURRENT_SHELL" in
    bash)
        # Bash completion setup
        COMPLETION_LINE='eval "$(_TT_MGMT_COMPLETE=bash_source tt-mgmt)"'
        RC_FILE="$HOME/.bashrc"
        
        # Check if already added
        if grep -q "_TT_MGMT_COMPLETE" "$RC_FILE" 2>/dev/null; then
            echo "[OK] Completion already configured in $RC_FILE"
        else
            echo "" >> "$RC_FILE"
            echo "# tt-mgmt shell completion" >> "$RC_FILE"
            echo "$COMPLETION_LINE" >> "$RC_FILE"
            echo "[OK] Added completion to $RC_FILE"
        fi
        
        # Enable for current session (only works if script is sourced)
        eval "$COMPLETION_LINE"
        echo "[OK] Completion enabled for current session!"
        echo ""
        echo "Try it: tt-mgmt <TAB><TAB>"
        ;;
        
    zsh)
        # Zsh completion setup
        COMPLETION_LINE='eval "$(_TT_MGMT_COMPLETE=zsh_source tt-mgmt)"'
        RC_FILE="$HOME/.zshrc"
        
        # Check if already added
        if grep -q "_TT_MGMT_COMPLETE" "$RC_FILE" 2>/dev/null; then
            echo "[OK] Completion already configured in $RC_FILE"
        else
            echo "" >> "$RC_FILE"
            echo "# tt-mgmt shell completion" >> "$RC_FILE"
            echo "$COMPLETION_LINE" >> "$RC_FILE"
            echo "[OK] Added completion to $RC_FILE"
        fi
        
        echo ""
        echo "Restart your shell or run: source ~/.zshrc"
        ;;
        
    fish)
        # Fish completion setup
        COMPLETION_LINE='eval (env _TT_MGMT_COMPLETE=fish_source tt-mgmt)'
        RC_FILE="$HOME/.config/fish/config.fish"
        
        # Create fish config dir if it doesn't exist
        mkdir -p "$HOME/.config/fish"
        
        # Check if already added
        if grep -q "_TT_MGMT_COMPLETE" "$RC_FILE" 2>/dev/null; then
            echo "[OK] Completion already configured in $RC_FILE"
        else
            echo "" >> "$RC_FILE"
            echo "# tt-mgmt shell completion" >> "$RC_FILE"
            echo "$COMPLETION_LINE" >> "$RC_FILE"
            echo "[OK] Added completion to $RC_FILE"
        fi
        
        echo ""
        echo "Restart your shell or run: source ~/.config/fish/config.fish"
        ;;
        
    *)
        echo "Unsupported shell: $CURRENT_SHELL"
        echo "Please manually add completion support. See install.sh output for instructions."
        exit 1
        ;;
esac

echo ""
echo "Test it by typing: tt-mgmt <TAB><TAB>"
