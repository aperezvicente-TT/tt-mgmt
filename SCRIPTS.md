# TT-MGMT Installation Scripts Guide

Quick reference for all installation and setup scripts.

## 📦 Available Scripts

### 1. `install_and_enable.sh` ⭐ **RECOMMENDED**

**One-command install + enable completion**

```bash
source ./install_and_enable.sh
```

- ✅ Installs tt-mgmt
- ✅ Enables completion NOW (current shell)
- ✅ Adds to ~/.bashrc (future shells)
- ✅ Everything just works!

**Use this if:** You want the simplest, fastest setup.

---

### 2. `install.sh`

**Install tt-mgmt only** (no completion setup)

```bash
./install.sh
```

- ✅ Installs tt-mgmt package
- ℹ️ Shows completion instructions
- ❌ Does NOT enable completion

**Use this if:** You want to manually configure completion later.

---

### 3. `enable_completion_now.sh`

**Enable completion in current shell only**

```bash
source ./enable_completion_now.sh
```

- ✅ Works immediately
- ❌ Only for current session
- ❌ Does NOT modify ~/.bashrc

**Use this if:** You want to try completion without permanent changes.

---

### 4. `setup_completion.sh`

**Enable completion + add to ~/.bashrc**

```bash
source ./setup_completion.sh
```

- ✅ Enables for current session
- ✅ Adds to ~/.bashrc permanently
- ✅ Works in future shells

**Use this if:** tt-mgmt is already installed and you just want completion.

---

### 5. `test_completion.sh`

**Test if completion is working**

```bash
./test_completion.sh
```

- ℹ️ Checks if tt-mgmt is installed
- ℹ️ Shows completion status
- ℹ️ Provides help if broken

**Use this if:** Tab completion isn't working and you want to debug.

---

## 🤔 Which Script Should I Use?

### First Time Setup

```bash
source ./install_and_enable.sh
```

### Already Installed, Need Completion

```bash
source ./enable_completion_now.sh
```

### Tab Completion Not Working

```bash
./test_completion.sh
```

---

## ⚠️ Common Mistakes

### ❌ **Don't do this:**
```bash
./enable_completion_now.sh    # WRONG - won't work!
./setup_completion.sh          # WRONG - won't affect current shell!
```

### ✅ **Do this:**
```bash
source ./enable_completion_now.sh    # RIGHT - uses 'source'
source ./setup_completion.sh          # RIGHT - uses 'source'
```

**Why?** When you use `./script.sh`, it runs in a subshell and can't modify your current shell's environment. You must use `source` to run it in your current shell.

---

## 🔄 Workflow Examples

### Example 1: Fresh Install
```bash
cd tools/tt-mgmt
source ./install_and_enable.sh
tt-mgmt <TAB><TAB>  # Test it!
```

### Example 2: Already Installed, New Shell Session
```bash
# Already in ~/.bashrc, just start new shell:
bash
tt-mgmt <TAB><TAB>  # Works automatically!
```

### Example 3: Quick Test Without Permanent Setup
```bash
cd tools/tt-mgmt
./install.sh
source ./enable_completion_now.sh
tt-mgmt <TAB><TAB>  # Test it!
# Close shell - completion is gone
```

### Example 4: Completion Stopped Working
```bash
cd tools/tt-mgmt
./test_completion.sh         # Diagnose issue
source ./enable_completion_now.sh  # Quick fix
```

---

## 🚀 Integration with env_vars_setup.sh

Your main environment setup script already includes tt-mgmt completion:

```bash
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi
source ./env_vars_setup.sh
```

This will:
1. Set up all TT-Metal environment variables
2. Activate Python virtualenv
3. **Auto-enable tt-mgmt completion** (if installed)

So you can just use `env_vars_setup.sh` instead of the individual completion scripts!

---

## 📝 Summary

| Script | Command | Current Shell | Future Shells | Installs tt-mgmt |
|--------|---------|--------------|---------------|-----------------|
| `install_and_enable.sh` | `source` | ✅ | ✅ | ✅ |
| `install.sh` | `./` | ❌ | ❌ | ✅ |
| `enable_completion_now.sh` | `source` | ✅ | ❌ | ❌ |
| `setup_completion.sh` | `source` | ✅ | ✅ | ❌ |
| `test_completion.sh` | `./` | N/A | N/A | ❌ |
| `env_vars_setup.sh` | `source` | ✅ | ✅* | ❌ |

\* Only if you source `env_vars_setup.sh` in each new shell (or add it to ~/.bashrc)
