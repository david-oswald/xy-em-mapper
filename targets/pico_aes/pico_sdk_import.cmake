# Locate the Raspberry Pi Pico SDK.
#
# Uses (in order): PICO_SDK_PATH (CMake var or env), or fetches the SDK from
# GitHub when PICO_SDK_FETCH_FROM_GIT is set (CMake var or env).
# Simplified from external/pico_sdk_import.cmake in the pico-sdk repository.

if(DEFINED ENV{PICO_SDK_PATH} AND (NOT PICO_SDK_PATH))
    set(PICO_SDK_PATH $ENV{PICO_SDK_PATH})
endif()
if(DEFINED ENV{PICO_SDK_FETCH_FROM_GIT} AND (NOT PICO_SDK_FETCH_FROM_GIT))
    set(PICO_SDK_FETCH_FROM_GIT $ENV{PICO_SDK_FETCH_FROM_GIT})
endif()
if(NOT PICO_SDK_FETCH_FROM_GIT_TAG)
    set(PICO_SDK_FETCH_FROM_GIT_TAG "2.1.1")
endif()

set(PICO_SDK_PATH "${PICO_SDK_PATH}" CACHE PATH "Path to the Raspberry Pi Pico SDK")
set(PICO_SDK_FETCH_FROM_GIT "${PICO_SDK_FETCH_FROM_GIT}" CACHE BOOL "Fetch the Pico SDK from git if not found")

if(NOT PICO_SDK_PATH)
    if(PICO_SDK_FETCH_FROM_GIT)
        include(FetchContent)
        FetchContent_Declare(
            pico_sdk
            GIT_REPOSITORY https://github.com/raspberrypi/pico-sdk
            GIT_TAG ${PICO_SDK_FETCH_FROM_GIT_TAG}
        )
        FetchContent_GetProperties(pico_sdk)
        if(NOT pico_sdk_POPULATED)
            message(STATUS "Downloading Raspberry Pi Pico SDK ${PICO_SDK_FETCH_FROM_GIT_TAG}")
            FetchContent_Populate(pico_sdk)
        endif()
        set(PICO_SDK_PATH ${pico_sdk_SOURCE_DIR})
    else()
        message(FATAL_ERROR
            "Pico SDK not found. Set PICO_SDK_PATH, or set PICO_SDK_FETCH_FROM_GIT=on to download it.")
    endif()
endif()

get_filename_component(PICO_SDK_PATH "${PICO_SDK_PATH}" REALPATH BASE_DIR "${CMAKE_BINARY_DIR}")
if(NOT EXISTS ${PICO_SDK_PATH}/pico_sdk_init.cmake)
    message(FATAL_ERROR "'${PICO_SDK_PATH}' does not appear to contain the Raspberry Pi Pico SDK")
endif()

set(PICO_SDK_PATH ${PICO_SDK_PATH} CACHE PATH "Path to the Raspberry Pi Pico SDK" FORCE)
include(${PICO_SDK_PATH}/pico_sdk_init.cmake)
