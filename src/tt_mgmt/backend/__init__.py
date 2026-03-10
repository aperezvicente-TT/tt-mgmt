"""Backend modules for tt-mgmt.

This package contains the backend implementations that interact with
the C++ tt-metal APIs through Python bindings (nanobind/pybind11).

Each module provides a clean Python interface that can be called from
the CLI commands.
"""

from tt_mgmt.backend import device as device_backend
from tt_mgmt.backend import system as system_backend
from tt_mgmt.backend import memory as memory_backend
from tt_mgmt.backend import debug as debug_backend
from tt_mgmt.backend import env as env_backend

__all__ = [
    'device_backend',
    'system_backend',
    'memory_backend',
    'debug_backend',
    'env_backend',
]
