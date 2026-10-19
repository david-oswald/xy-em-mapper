# Writes OUT with PICO_AES_VERSION set to the short git commit hash of
# SRC_DIR, suffixed with "-dirty" if there are uncommitted changes.
# Only rewrites the file when the content changes.

execute_process(
    COMMAND git rev-parse --short=8 HEAD
    WORKING_DIRECTORY ${SRC_DIR}
    OUTPUT_VARIABLE GIT_HASH
    RESULT_VARIABLE GIT_RESULT
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)

if(NOT GIT_RESULT EQUAL 0 OR GIT_HASH STREQUAL "")
    set(GIT_HASH "unknown")
else()
    execute_process(
        COMMAND git status --porcelain --untracked-files=no -- .
        WORKING_DIRECTORY ${SRC_DIR}
        OUTPUT_VARIABLE GIT_STATUS
        ERROR_QUIET
    )
    if(NOT GIT_STATUS STREQUAL "")
        set(GIT_HASH "${GIT_HASH}-dirty")
    endif()
endif()

set(CONTENT "#pragma once\n#define PICO_AES_VERSION \"${GIT_HASH}\"\n")

if(EXISTS ${OUT})
    file(READ ${OUT} OLD_CONTENT)
endif()
if(NOT "${CONTENT}" STREQUAL "${OLD_CONTENT}")
    file(WRITE ${OUT} "${CONTENT}")
endif()
