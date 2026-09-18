cmake_minimum_required(VERSION 3.24)

project(ffl_datachannel LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(MSVC)
    cmake_policy(SET CMP0091 NEW)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
endif()

option(FFL_DATACHANNEL_MANYLINUX "Statically link the GNU C++ runtime for a manylinux wheel" OFF)

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/third_party/libdatachannel/CMakeLists.txt")
    message(FATAL_ERROR "third_party/libdatachannel is missing; run python scripts/bootstrap.py")
endif()

# GnuTLS has no CMake package of its own; resolve it (and, on Windows, its
# full static transitive closure: Nettle, GMP, libtasn1, zlib) through
# pkg-config, matching the validated ffl-p2p GnuTLS toolchain. Force the
# result into GNUTLS_INCLUDE_DIR/GNUTLS_LIBRARY cache variables before
# find_package(GnuTLS) runs so both this call and libdatachannel's own
# find_package(GnuTLS REQUIRED) (invoked later, inside add_subdirectory)
# resolve to the same installation instead of searching again.
#
# Floor is 3.7, not 3.8: ffl-datachannel uses no GnuTLS API newer than 3.7
# (gnutls_session_get_desc, gnutls_strerror_name, etc. all predate it), and
# some manylinux-compatible GnuTLS builds (e.g. the one ffl-p2p already
# builds against) only carry symbols up to the GNUTLS_3_7_x tag even when
# the release itself is newer. Windows is unaffected either way: vcpkg pins
# an exact 3.8.7.
find_package(PkgConfig REQUIRED)
pkg_check_modules(GNUTLS REQUIRED gnutls>=3.7)
find_path(FFL_DATACHANNEL_GNUTLS_INCLUDE_DIR gnutls/gnutls.h
    PATHS ${GNUTLS_INCLUDE_DIRS} NO_DEFAULT_PATH)
find_library(FFL_DATACHANNEL_GNUTLS_LIBRARY NAMES gnutls
    PATHS ${GNUTLS_LIBRARY_DIRS} NO_DEFAULT_PATH)
if(NOT FFL_DATACHANNEL_GNUTLS_INCLUDE_DIR OR NOT FFL_DATACHANNEL_GNUTLS_LIBRARY)
    message(FATAL_ERROR "pkg-config located GnuTLS but not its headers or library")
endif()
set(GNUTLS_INCLUDE_DIR "${FFL_DATACHANNEL_GNUTLS_INCLUDE_DIR}" CACHE PATH "" FORCE)
set(GNUTLS_LIBRARY "${FFL_DATACHANNEL_GNUTLS_LIBRARY}" CACHE FILEPATH "" FORCE)
find_package(GnuTLS 3.7 REQUIRED)

if(WIN32)
    # vcpkg supplies a static GnuTLS on Windows. MSVC does not load a static
    # library's own dependencies, so link its full pkg-config closure
    # (Nettle, GMP, libtasn1, zlib, and the Windows CNG/socket libraries)
    # directly onto the extension.
    set(FFL_DATACHANNEL_GNUTLS_LIBRARIES ${GNUTLS_STATIC_LIBRARIES})
    set(FFL_DATACHANNEL_GNUTLS_LINK_DIRECTORIES ${GNUTLS_STATIC_LIBRARY_DIRS})
else()
    # System GnuTLS (apt/Homebrew) ships as a shared library that owns its
    # own transitive dependencies, so link only libgnutls itself.
    set(FFL_DATACHANNEL_GNUTLS_LIBRARIES ${GNUTLS_LIBRARIES})
    set(FFL_DATACHANNEL_GNUTLS_LINK_DIRECTORIES ${GNUTLS_LIBRARY_DIRS})
endif()

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_DEPS_LIBS OFF CACHE BOOL "" FORCE)
set(USE_MBEDTLS OFF CACHE BOOL "" FORCE)
set(USE_GNUTLS ON CACHE BOOL "" FORCE)
# libdatachannel defaults this to ON alongside USE_GNUTLS so libjuice can
# reuse GnuTLS's Nettle for hashing. Keep libjuice's own hash functions
# instead, so Nettle stays purely GnuTLS's internal dependency rather than a
# second, independently-resolved requirement of this project.
set(USE_NETTLE OFF CACHE BOOL "" FORCE)
set(USE_NICE OFF CACHE BOOL "" FORCE)
set(PREFER_SYSTEM_LIB OFF CACHE BOOL "" FORCE)
set(USE_SYSTEM_JUICE OFF CACHE BOOL "" FORCE)
set(USE_SYSTEM_USRSCTP OFF CACHE BOOL "" FORCE)
set(USE_SYSTEM_PLOG OFF CACHE BOOL "" FORCE)
set(NO_MEDIA ON CACHE BOOL "" FORCE)
set(NO_WEBSOCKET ON CACHE BOOL "" FORCE)
set(NO_EXAMPLES ON CACHE BOOL "" FORCE)
set(NO_TESTS ON CACHE BOOL "" FORCE)
set(WARNINGS_AS_ERRORS OFF CACHE BOOL "" FORCE)

add_subdirectory(third_party/libdatachannel EXCLUDE_FROM_ALL)

add_library(ffl_datachannel_native_core STATIC
    native/native_peer_connection.cpp
)
target_include_directories(ffl_datachannel_native_core PUBLIC native)
target_compile_definitions(ffl_datachannel_native_core PUBLIC RTC_STATIC=1)
target_link_libraries(ffl_datachannel_native_core PUBLIC datachannel-static)

Python_add_library(_ffl_datachannel MODULE WITH_SOABI
    native/python_module.cpp
)
target_include_directories(_ffl_datachannel PRIVATE native)
target_compile_definitions(_ffl_datachannel PRIVATE RTC_STATIC=1)
target_link_libraries(_ffl_datachannel PRIVATE ffl_datachannel_native_core)
target_link_directories(_ffl_datachannel PRIVATE ${FFL_DATACHANNEL_GNUTLS_LINK_DIRECTORIES})
target_link_libraries(_ffl_datachannel PRIVATE ${FFL_DATACHANNEL_GNUTLS_LIBRARIES})

if(MSVC)
    target_compile_options(ffl_datachannel_native_core PRIVATE /W4)
    target_compile_options(_ffl_datachannel PRIVATE /W4)
else()
    target_compile_options(ffl_datachannel_native_core PRIVATE -Wall -Wextra -Wpedantic)
    target_compile_options(_ffl_datachannel PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(FFL_DATACHANNEL_MANYLINUX)
    if(MSVC)
        message(FATAL_ERROR "FFL_DATACHANNEL_MANYLINUX requires a GNU-compatible Linux compiler")
    endif()
    # auditwheel cannot lower a wheel's own GLIBCXX/CXXABI symbol versions;
    # the build host's libstdc++/libgcc are typically newer than a target
    # manylinux policy allows. Static-linking the GNU C++ runtime removes
    # that dynamic dependency entirely, matching ffl-p2p's own manylinux fix.
    target_link_options(_ffl_datachannel PRIVATE -static-libgcc -static-libstdc++)
endif()

install(TARGETS _ffl_datachannel
    LIBRARY DESTINATION ffl_datachannel
    RUNTIME DESTINATION ffl_datachannel
)
