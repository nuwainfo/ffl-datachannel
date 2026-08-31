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

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/third_party/libdatachannel/CMakeLists.txt")
    message(FATAL_ERROR "third_party/libdatachannel is missing; run python scripts/bootstrap.py")
endif()

find_package(MbedTLS 3 REQUIRED)
if(NOT TARGET MbedTLS::MbedTLS)
    add_library(MbedTLS::MbedTLS ALIAS MbedTLS::mbedtls)
endif()

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_DEPS_LIBS OFF CACHE BOOL "" FORCE)
set(USE_MBEDTLS ON CACHE BOOL "" FORCE)
set(USE_GNUTLS OFF CACHE BOOL "" FORCE)
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

if(MSVC)
    target_compile_options(ffl_datachannel_native_core PRIVATE /W4)
    target_compile_options(_ffl_datachannel PRIVATE /W4)
else()
    target_compile_options(ffl_datachannel_native_core PRIVATE -Wall -Wextra -Wpedantic)
    target_compile_options(_ffl_datachannel PRIVATE -Wall -Wextra -Wpedantic)
endif()

install(TARGETS _ffl_datachannel
    LIBRARY DESTINATION ffl_datachannel
    RUNTIME DESTINATION ffl_datachannel
)
