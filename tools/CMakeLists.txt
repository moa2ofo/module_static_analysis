cmake_minimum_required(VERSION 3.16)
project(projectName C)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

file(GLOB SOURCES
    "${CMAKE_CURRENT_SOURCE_DIR}/pltf/*.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/cfg/*.c"
)

add_library(projectName STATIC ${SOURCES})

target_include_directories(projectName PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/pltf
    ${CMAKE_CURRENT_SOURCE_DIR}/cfg
)

target_compile_options(projectName PRIVATE
    -Wall
    -Wextra
    -Wpedantic
)