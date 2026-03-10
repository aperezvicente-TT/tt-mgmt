# TT-MGMT Quick Start Guide

## 🎯 Two Ways to Use TT-MGMT

### Option A: Interactive Mode (No Setup - Just Works!)

**Fastest way to get started:**

```bash
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi/tools/tt-mgmt
./install.sh
tt-mgmt --interactive
```

Tab completion works **immediately** - no shell setup needed!

### Option B: Standard CLI Mode (Scriptable)

**For scripts and automation:**

```bash
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi/tools/tt-mgmt
source ./install_and_enable.sh
tt-mgmt device list
```

Requires one-time shell setup for tab completion.

---

## Enable Tab Completion (Standard CLI Mode)

### Step 1: Install tt-mgmt

```bash
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi/tools/tt-mgmt
./install.sh
```

### Step 2: Enable Completion for Current Shell

After installation, pick ONE option:

**Option A - Quick enable (current session only):**
```bash
source ./enable_completion_now.sh
```

**Option B - Permanent setup (adds to ~/.bashrc):**
```bash
source ./setup_completion.sh
```

**Option C - Use env_vars_setup.sh (already configured!):**
```bash
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi
source ./env_vars_setup.sh  # Now includes auto-completion!
```

> **Important:** You must use `source` (not `./`) for these scripts to work in your current shell!

### Step 3: Test It!

```bash
tt-mgmt <TAB><TAB>           # Should show: debug, device, memory, system
tt-mgmt device <TAB><TAB>    # Should show: info, list, monitor, reset
tt-mgmt system <TAB><TAB>    # Should show: status, topology, version
```

## How Tab Completion Works

Click (the CLI framework) generates completion scripts dynamically:

1. When you press `<TAB>`, bash calls the completion function
2. The completion function runs `tt-mgmt` with special environment variables
3. Click returns available commands/options based on context
4. Bash displays the suggestions

## Completion Features

✅ **Command completion** - `tt-mgmt <TAB>` shows main commands  
✅ **Subcommand completion** - `tt-mgmt device <TAB>` shows device commands  
✅ **Option completion** - `tt-mgmt device info --<TAB>` shows available flags  
✅ **Context-aware** - Only shows valid options for current command

## Advanced: Dynamic Completions

You can add custom completions for arguments (e.g., device IDs). Example:

```python
import click

def get_device_ids(ctx, param, incomplete):
    """Return list of available device IDs."""
    # This could query actual devices
    return ['0', '1', '2', '3']

@device.command()
@click.argument('device_id', shell_complete=get_device_ids)
def info(device_id):
    """Show device info."""
    pass
```

Now `tt-mgmt device info <TAB>` will suggest actual device IDs!

## Troubleshooting

**Tab completion not working?**

1. Make sure tt-mgmt is installed: `which tt-mgmt`
2. Check if completion is loaded: `type _tt_mgmt_completion`
3. Re-source your environment: `source ./env_vars_setup.sh`
4. Try manual activation: `eval "$(_TT_CTL_COMPLETE=bash_source tt-mgmt)"`

**Completion shows wrong options?**

- Restart your shell or re-source: `source ~/.bashrc`
- Click caches completions, so changes may require a new shell

**Using Zsh?**

Replace `bash_source` with `zsh_source`:
```zsh
eval "$(_TT_CTL_COMPLETE=zsh_source tt-mgmt)"
```

## Next Steps

- Read [README.md](README.md) for full documentation
- Add new commands (they'll automatically get completion!)
- Bind to C++ backend with nanobind
- Implement dynamic completions for device IDs, etc.
