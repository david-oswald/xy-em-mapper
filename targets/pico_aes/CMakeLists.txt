cmake_minimum_required(VERSION 3.13)

# Default board; override with -DPICO_BOARD=pico2 (RP2350), pico_w, etc.
if(NOT DEFINED PICO_BOARD)
    set(PICO_BOARD pico)
endif()

include(pico_sdk_import.cmake)

project(pico_aes C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

add_executable(pico_aes
    src/main.c
    src/aes.c
)

# Keep the AES a plain, unoptimised textbook implementation regardless of
# build type, so the leakage is easy to see.
set_source_files_properties(src/aes.c PROPERTIES COMPILE_OPTIONS "-O0")

# Optional overrides, e.g. -DTRIGGER_PIN=15 -DBLOCK_DELAY_MS=5
foreach(opt TRIGGER_PIN BLOCK_DELAY_MS TRIGGER_DISABLE_IRQ)
    if(DEFINED ${opt})
        target_compile_definitions(pico_aes PRIVATE ${opt}=${${opt}})
    endif()
endforeach()

# Generate version.h (git commit hash) on every build.
set(VERSION_HEADER ${CMAKE_CURRENT_BINARY_DIR}/generated/version.h)
add_custom_target(pico_aes_version
    COMMAND ${CMAKE_COMMAND}
        -DSRC_DIR=${CMAKE_CURRENT_SOURCE_DIR}
        -DOUT=${VERSION_HEADER}
        -P ${CMAKE_CURRENT_SOURCE_DIR}/cmake/version.cmake
    BYPRODUCTS ${VERSION_HEADER}
    COMMENT "Generating version.h"
)
add_dependencies(pico_aes pico_aes_version)
target_include_directories(pico_aes PRIVATE ${CMAKE_CURRENT_BINARY_DIR}/generated)

target_link_libraries(pico_aes pico_stdlib hardware_sync)

# Run entirely from SRAM: avoids XIP flash cache misses adding jitter.
pico_set_binary_type(pico_aes copy_to_ram)

# Serial over both USB CDC and UART0 (GP0 TX / GP1 RX).
pico_enable_stdio_usb(pico_aes 1)
pico_enable_stdio_uart(pico_aes 1)

pico_add_extra_outputs(pico_aes)
