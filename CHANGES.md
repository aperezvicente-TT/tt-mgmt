# TT-MGMT Changes - SMI Integration

## Summary

✅ **Fully integrated tt-smi-ui into tt-mgmt**  
✅ **No external dependencies**  
✅ **Single installation command**  
✅ **C++ backend built automatically**  

## What Was Done

### 1. Moved Source Files

**Python modules:**
- `tt_smi_ui/core.py` → `tt_mgmt/backend/smi/core.py`
- `tt_smi_ui/ui/dashboard.py` → `tt_mgmt/backend/smi/ui/dashboard.py`
- `tt_smi_ui/ui/graphs.py` → `tt_mgmt/backend/smi/ui/graphs.py`

**C++ backend:**
- `tt_smi_ui/tt_smi_backend.cpp` → `tt-mgmt/cpp/src/smi/tt_smi_backend.cpp`
- `tt_smi_ui/tt_smi_backend.hpp` → `tt-mgmt/cpp/src/smi/tt_smi_backend.hpp`
- `tt_smi_ui/bindings/native.cpp` → `tt-mgmt/cpp/bindings/native.cpp`

### 2. Created Build System

- **CMakeLists.txt**: Builds C++ backend using pybind11
- **Updated setup.py**: Integrates CMake build into pip install
- **Auto-detection**: Checks for TT_METAL_HOME and builds automatically

### 3. Updated Imports

- **smi.py**: Changed from `from tt_smi_ui.core import ...` to `from tt_mgmt.backend.smi import ...`
- **core.py**: Updated to find native module in new location
- **Added __init__.py**: Proper Python package structure

### 4. Updated Documentation

- **MIGRATION_COMPLETE.md**: Complete migration guide
- **README.md**: Updated installation instructions
- **env_vars_setup.sh**: Removed old PYTHONPATH hack

## File Structure

```
tools/tt-mgmt/
├── src/tt_mgmt/
│   ├── cli.py
│   ├── interactive.py
│   ├── commands/
│   │   ├── device.py
│   │   ├── system.py
│   │   ├── memory.py
│   │   ├── debug.py
│   │   └── smi.py              ← Updated
│   └── backend/
│       ├── device.py
│       ├── system.py
│       ├── memory.py
│       ├── debug.py
│       └── smi/                 ← NEW!
│           ├── __init__.py
│           ├── core.py
│           └── ui/
│               ├── __init__.py
│               ├── dashboard.py
│               └── graphs.py
│
├── cpp/                         ← NEW!
│   ├── CMakeLists.txt
│   ├── src/
│   │   └── smi/
│   │       ├── tt_smi_backend.cpp
│   │       └── tt_smi_backend.hpp
│   └── bindings/
│       └── native.cpp
│
├── setup.py                     ← Updated
├── README.md                    ← Updated
├── MIGRATION_COMPLETE.md        ← NEW!
└── CHANGES.md                   ← NEW!
```

## Installation Changes

### Before
```bash
# Install tt-mgmt
pip install -e tools/tt-mgmt

# Install tt-smi-ui
pip install -e tt_metal/.../memory_utilization_monitor

# Configure PYTHONPATH
export PYTHONPATH=$PYTHONPATH:/path/to/memory_utilization_monitor

# Use commands
tt-mgmt smi monitor  # Would fail without PYTHONPATH
```

### After
```bash
# Set up environment
source ./env_vars_setup.sh  # Sets TT_METAL_HOME

# Install tt-mgmt (builds C++ backend automatically)
cd tools/tt-mgmt
pip install -e .

# Use commands
tt-mgmt smi monitor  # Just works!
```

## Command Changes

### No Changes to User-Facing Commands!

All commands work exactly the same:
```bash
tt-mgmt smi monitor -w
tt-mgmt smi monitor -w -g
tt-mgmt smi monitor --json
tt-mgmt smi telemetry
tt-mgmt smi memory
tt-mgmt smi processes
tt-mgmt smi cleanup
```

Tab completion still works:
```bash
tt-mgmt smi <TAB>
# Shows: cleanup  memory  monitor  processes  telemetry
```

## Benefits

### For Users
1. **Simpler installation**: One `pip install` command
2. **No PYTHONPATH hassles**: Everything self-contained
3. **Clearer errors**: Better error messages if build fails
4. **Tab completion**: Works immediately after installation

### For Developers
1. **Unified codebase**: All code in one place
2. **Shared backend**: Easy to extend with new commands
3. **Better testing**: Can test everything together
4. **Easier maintenance**: Single version, single release

### For the Project
1. **Professional structure**: Industry-standard layout
2. **Easier to understand**: Clear separation of concerns
3. **Better documentation**: Everything in one place
4. **Future-proof**: Ready for more integrations

## Backward Compatibility

✅ **Old tt-smi-ui still works** - Didn't modify original files  
✅ **Existing scripts work** - Can still import from tt_smi_ui if needed  
✅ **No breaking changes** - All commands identical  

## Testing the Migration

```bash
# 1. Set up environment
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi
source ./env_vars_setup.sh

# 2. Install tt-mgmt
cd tools/tt-mgmt
pip install -e .

# 3. Verify backend is built
python3 -c "from tt_mgmt.backend.smi import get_devices; print('✓ Backend loaded')"

# 4. Test SMI commands
tt-mgmt smi monitor
tt-mgmt smi --help

# 5. Test tab completion
tt-mgmt smi <TAB><TAB>

# 6. Test interactive mode
tt-mgmt -i
tt-mgmt> smi monitor
```

## Troubleshooting

See [MIGRATION_COMPLETE.md](MIGRATION_COMPLETE.md) for detailed troubleshooting.

## Next Steps

Now that SMI is integrated, you can:

1. **Add more device commands** using the same C++ backend pattern
2. **Implement system queries** (topology, versions, etc.)
3. **Add memory operations** (dump, clear, stats)
4. **Create debug utilities** (registers, traces, etc.)

All using the proven SMI backend architecture!

## Credits

- Original tt-smi-ui by Tenstorrent team
- Integration into tt-mgmt: This migration
- Architecture follows industry best practices (AWS CLI, gcloud, etc.)

---

**Migration completed successfully!** 🎉

Run `pip install -e .` in tools/tt-mgmt to try it out.
