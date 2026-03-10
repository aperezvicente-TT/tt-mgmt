# TT-MGMT Usage Modes

TT-MGMT supports two modes of operation, each with different trade-offs for tab completion.

## 📋 Quick Comparison

| Feature | Standard CLI Mode | Interactive Mode |
|---------|------------------|------------------|
| **Tab Completion** | ✅ Yes (requires shell setup) | ✅ Yes (works immediately!) |
| **Setup Required** | One-time shell integration | None |
| **Command Style** | `tt-mgmt device list` | `tt-mgmt> device list` |
| **Scriptable** | ✅ Yes | ❌ No |
| **Pipe/Redirect** | ✅ Yes | ❌ No |
| **History** | Shell history | Built-in history |
| **Auto-suggest** | ❌ No | ✅ Yes |
| **Best For** | Automation, scripts | Exploration, manual use |

---

## Mode 1: Standard CLI Mode

**Traditional UNIX command-line interface**

### Usage

```bash
tt-mgmt device list
tt-mgmt device info 0
tt-mgmt system status
```

Each command runs independently, just like `git`, `docker`, or `kubectl`.

### Tab Completion

**Requires one-time shell setup:**

```bash
# Quick setup (current session):
source ./enable_completion_now.sh

# Permanent setup:
source ./install_and_enable.sh
```

After setup:
```bash
tt-mgmt <TAB><TAB>           # Shows: debug  device  memory  system
tt-mgmt device <TAB><TAB>    # Shows: info  list  monitor  reset
```

### Why Shell Setup is Required

When you press `<TAB>` in your terminal, **your shell** (bash/zsh/fish) handles it, not your program. The shell needs to be configured to know what completions to suggest for `tt-mgmt`.

**This is how ALL standard CLI tools work:**
- `git <TAB>` - requires git completion setup
- `docker <TAB>` - requires docker completion setup
- `kubectl <TAB>` - requires kubectl completion setup

There is **no way around this** for standard CLI mode. It's a limitation of how shells work.

### Advantages

✅ **Scriptable** - Works in bash scripts
✅ **Pipe-friendly** - `tt-mgmt device list | grep active`
✅ **Automation** - Easy to automate in CI/CD
✅ **Standard UX** - Familiar to all Linux users
✅ **Exit codes** - Proper exit codes for scripting

### Example Workflow

```bash
# One-time setup
source ./enable_completion_now.sh

# Use anywhere
tt-mgmt device list
tt-mgmt system status > status.txt
tt-mgmt device info 0 | jq .

# In scripts
for id in $(tt-mgmt device list --format ids); do
    tt-mgmt device info $id
done
```

---

## Mode 2: Interactive Mode

**Built-in shell with native tab completion**

### Usage

```bash
# Start interactive session
tt-mgmt --interactive
# or
tt-mgmt -i
```

Then use commands interactively:
```bash
tt-mgmt> device list
tt-mgmt> device info 0
tt-mgmt> system status
tt-mgmt> exit
```

### Tab Completion

**Works immediately - no setup needed!**

```bash
tt-mgmt> device <TAB>        # Shows: info  list  monitor  reset
tt-mgmt> system <TAB>        # Shows: status  topology  version
```

Press `<TAB>` at any time to see available options.

### Why No Setup is Needed

In interactive mode, **your program** (tt-mgmt) is running and handling keyboard input directly using `prompt-toolkit`. When you press `<TAB>`, the Python library catches it and shows completions.

**Similar tools that work this way:**
- `ipython` - Python REPL with tab completion
- `psql` - PostgreSQL shell
- `redis-cli` - Redis shell
- `mysql` - MySQL shell

### Additional Features

✅ **Auto-suggestions** - Shows suggestions from history as you type
✅ **Command history** - Use arrow keys to navigate previous commands
✅ **Multi-line editing** - Edit long commands easily
✅ **Immediate completion** - No shell configuration needed

### Example Session

```bash
$ tt-mgmt -i

╔═══════════════════════════════════════════════════╗
║  TT-MGMT Interactive Mode                         ║
╚═══════════════════════════════════════════════════╝

✓ Tab completion enabled (press TAB to see commands)
✓ History enabled (use arrow keys)
ℹ Type 'help' for available commands
ℹ Type 'exit' or press Ctrl+D to quit

tt-mgmt> help

Available Commands:

device   - Device management (list, info, reset, monitor)
system   - System-level commands (status, topology, version)
memory   - Memory operations (stats, dump, clear)
debug    - Debug utilities (info, dump-regs, enable, disable)
help     - Show this help
exit     - Exit interactive mode

Tip: Press TAB to see available options at any time!

tt-mgmt> device <TAB>
info  list  monitor  reset

tt-mgmt> device list
[... output ...]

tt-mgmt> system status
[... output ...]

tt-mgmt> exit

Goodbye!
```

### Advantages

✅ **No setup required** - Works immediately
✅ **Exploration** - Great for discovering commands
✅ **History** - Built-in command history
✅ **Auto-suggest** - Suggests commands from history
✅ **Better UX** - For interactive use

### Disadvantages

❌ **Not scriptable** - Can't use in bash scripts
❌ **No pipes** - Can't pipe output between commands
❌ **Session-based** - Must stay in the session

---

## 🤔 Which Mode Should I Use?

### Use Standard CLI Mode When:

- Writing scripts or automation
- Need to pipe/redirect output
- Want standard UNIX tool behavior
- Integrating with other tools
- One-off commands are common

**Example:**
```bash
# CI/CD pipeline
if ! tt-mgmt system status | grep -q "healthy"; then
    tt-mgmt device reset all
    exit 1
fi
```

### Use Interactive Mode When:

- Exploring commands and features
- Running multiple commands in sequence
- Don't want to set up shell completion
- Working on a shared/temporary machine
- Want command history and auto-suggestions

**Example:**
```bash
# Debugging session
tt-mgmt -i
tt-mgmt> device list
tt-mgmt> device info 2      # Found issue with device 2
tt-mgmt> memory stats 2     # Check memory
tt-mgmt> debug info 2       # Get debug info
tt-mgmt> device reset 2     # Reset it
```

### Use Both!

The modes complement each other:

```bash
# Use interactive for exploration
tt-mgmt -i
tt-mgmt> device list
tt-mgmt> device info 0
# (discover what you need)

# Then use standard mode in scripts
echo "tt-mgmt device info 0 | grep temperature" >> monitoring.sh
```

---

## 🔧 Technical Details

### Standard CLI Mode: How Tab Completion Works

1. User presses `<TAB>` → Shell intercepts it
2. Shell calls registered completion function
3. Function runs `_TT_CTL_COMPLETE=bash_source tt-mgmt`
4. Click returns available completions
5. Shell displays them

**Program is not running when you press TAB!**

### Interactive Mode: How Tab Completion Works

1. Program is already running
2. User presses `<TAB>` → prompt-toolkit intercepts it
3. prompt-toolkit looks up completions in its dictionary
4. Displays completions immediately

**Program is running and handling input directly.**

---

## 📚 Summary

**You asked: "Can Option 1 work without shell setup?"**

**Answer: No.** Standard CLI mode (`tt-mgmt device list`) fundamentally requires shell integration because:
1. The shell handles keyboard input BEFORE your program runs
2. The shell needs to be told how to complete your commands
3. This is true for ALL standard CLI tools (git, docker, kubectl, etc.)

**Solution: Use both modes!**
- Standard mode for scripting and automation (one-time shell setup)
- Interactive mode for exploration and manual use (no setup needed)

Both are useful in different scenarios. Think of interactive mode like `ipython` vs `python` - same functionality, different interface.
