cmake_minimum_required(VERSION 3.16)
project(tt_mgmt LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)

# ---------------------------------------------------------------------------
# tt-umd submodule
# ---------------------------------------------------------------------------
if(NOT DEFINED TT_UMD_HOME)
    set(TT_UMD_HOME "${CMAKE_CURRENT_SOURCE_DIR}/third_party/tt-umd")
endif()

if(NOT EXISTS "${TT_UMD_HOME}/CMakeLists.txt")
    message(FATAL_ERROR
        "tt-umd not found at '${TT_UMD_HOME}'.\n"
        "Run:  git submodule update --init --recursive\n"
        "Or pass -DTT_UMD_HOME=<path> pointing at an existing tt-umd checkout.")
endif()

option(TT_UMD_BUILD_STATIC "Build tt-umd as a static library" OFF)
set(TT_UMD_ENABLE_CLANG_TIDY OFF CACHE BOOL "" FORCE)

add_subdirectory(${TT_UMD_HOME} third_party/tt-umd EXCLUDE_FROM_ALL)

# ---------------------------------------------------------------------------
# Python / nanobind
# ---------------------------------------------------------------------------
find_package(Python 3.9 REQUIRED COMPONENTS Interpreter Development)
find_package(nanobind CONFIG REQUIRED)

# ---------------------------------------------------------------------------
# Native extension module (modular provider architecture)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Optional: tt-fabric-manager SDK (for cluster-level fabric operations)
# ---------------------------------------------------------------------------
option(TT_MGMT_FABRIC_MANAGER "Enable fabric-manager SDK integration (requires gRPC)" OFF)

if(TT_MGMT_FABRIC_MANAGER)
    if(NOT DEFINED TT_FABRIC_MANAGER_HOME)
        set(TT_FABRIC_MANAGER_HOME "${CMAKE_CURRENT_SOURCE_DIR}/../tt-fabric-manager")
    endif()
    if(EXISTS "${TT_FABRIC_MANAGER_HOME}/sdk/cpp/include/fabric_manager/client.hpp")
        message(STATUS "tt-fabric-manager SDK found at ${TT_FABRIC_MANAGER_HOME}")
    else()
        message(WARNING "tt-fabric-manager SDK not found; fabric features will be stubbed out")
        set(TT_MGMT_FABRIC_MANAGER OFF)
    endif()
endif()

nanobind_add_module(native
    cpp/bindings/native.cpp
    cpp/src/device_manager.cpp
    cpp/src/providers/helpers.cpp
    cpp/src/providers/umd_discovery.cpp
    cpp/src/providers/umd_telemetry.cpp
    cpp/src/providers/shm_memory.cpp
    cpp/src/providers/sysfs_discovery.cpp
    cpp/src/providers/sysfs_telemetry.cpp
    cpp/src/providers/fabric_manager_provider.cpp
    cpp/src/providers/umd_noc_access.cpp
)

target_include_directories(native PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/cpp/include
    ${CMAKE_CURRENT_SOURCE_DIR}/cpp/src
)

target_link_libraries(native PRIVATE tt-umd fmt::fmt-header-only)

if(TT_MGMT_FABRIC_MANAGER)
    target_compile_definitions(native PRIVATE TT_MGMT_HAS_FABRIC_MANAGER)
    target_include_directories(native PRIVATE
        ${TT_FABRIC_MANAGER_HOME}/sdk/cpp/include
    )
    target_link_libraries(native PRIVATE fabric_manager_sdk)
endif()

target_compile_options(native PRIVATE -O3)

target_link_options(native PRIVATE "LINKER:--disable-new-dtags")
set_target_properties(native PROPERTIES
    INSTALL_RPATH "$ORIGIN"
    BUILD_WITH_INSTALL_RPATH TRUE
)

install(TARGETS native LIBRARY DESTINATION tt_mgmt/backend/smi)
install(FILES $<TARGET_FILE:tt-umd> $<TARGET_SONAME_FILE:tt-umd> DESTINATION tt_mgmt/backend/smi)
