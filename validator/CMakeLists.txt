cmake_minimum_required(VERSION 3.18.0)
project(tas_validator CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT CMAKE_CXX_FLAGS MATCHES "-W")
  set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -Wextra")
endif()

option(BUILD_TAS_VALIDATOR_TESTS "Build the C++ unit tests" ON)

include(FetchContent)

# nlohmann/json — same tag MAS pins.
FetchContent_Declare(json
  GIT_REPOSITORY https://github.com/nlohmann/json/
  GIT_TAG tags/v3.11.2)

# pybind11 for the Python module.
FetchContent_Declare(pybind11
  GIT_REPOSITORY https://github.com/pybind/pybind11.git
  GIT_TAG tags/v2.13.6)

FetchContent_MakeAvailable(json pybind11)

find_package(Python3 COMPONENTS Interpreter REQUIRED)

# --- generated check-code inventories (ABT #549) ------------------------------
# PartValidator::check_codes() / circuit_check_codes() must list exactly the
# codes emit() (or an equivalent Finding/CorpusFinding constructor) can produce.
# A hand-typed literal drifts the moment a check is added without also editing
# this second place -- it already had twice, and a third addition was mid-flight
# when this was found. tools/gen_check_codes.py derives the list straight from
# the emitting call sites, so there is nothing left to hand-maintain: adding a
# check's emit() call is enough, the next build regenerates the inventory.
# tests/test_validator.cpp's "check_codes matches emit() call sites" TEST_CASE
# independently re-derives the same sets at test time and asserts equality with
# the compiled-in check_codes()/circuit_check_codes(), so a stale generated file
# (this custom command not re-run, or a hand-edit) still fails loudly.
set(GEN_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated/tas_validator")
file(MAKE_DIRECTORY "${GEN_DIR}")
set(CHECK_CODES_GEN_SCRIPT "${CMAKE_CURRENT_SOURCE_DIR}/tools/gen_check_codes.py")

# Every source that can raise a per-part Finding: every src/*.cpp EXCEPT the
# CIAS circuit checker (circuits.cpp), which has its own separate
# circuit_check_codes() inventory below. Globbed, not enumerated, so a new
# check-family .cpp is picked up automatically -- no list here to forget.
file(GLOB PART_CHECK_SRC_PATHS CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp")
list(REMOVE_ITEM PART_CHECK_SRC_PATHS "${CMAKE_CURRENT_SOURCE_DIR}/src/circuits.cpp")

add_custom_command(
  OUTPUT "${GEN_DIR}/check_codes.inc"
  COMMAND Python3::Interpreter "${CHECK_CODES_GEN_SCRIPT}"
          --src-dir "${CMAKE_CURRENT_SOURCE_DIR}/src"
          --exclude circuits.cpp
          --aggregate push_back:1
          --format cpp-list
          --out "${GEN_DIR}/check_codes.inc"
  DEPENDS "${CHECK_CODES_GEN_SCRIPT}" ${PART_CHECK_SRC_PATHS}
  COMMENT "Deriving PartValidator::check_codes() from emit() call sites (ABT #549)"
  VERBATIM)

add_custom_command(
  OUTPUT "${GEN_DIR}/circuit_check_codes.inc"
  COMMAND Python3::Interpreter "${CHECK_CODES_GEN_SCRIPT}"
          --src-dir "${CMAKE_CURRENT_SOURCE_DIR}/src"
          --file circuits.cpp
          --format cpp-list
          --out "${GEN_DIR}/circuit_check_codes.inc"
  DEPENDS "${CHECK_CODES_GEN_SCRIPT}" "${CMAKE_CURRENT_SOURCE_DIR}/src/circuits.cpp"
  COMMENT "Deriving circuit_check_codes() from emit() call sites (ABT #549)"
  VERBATIM)

add_custom_target(tas_validator_generated_check_codes
  DEPENDS "${GEN_DIR}/check_codes.inc" "${GEN_DIR}/circuit_check_codes.inc")

# --- core library (pure C++, no Python) --------------------------------------
add_library(tas_validator_core STATIC
  src/helpers.cpp
  src/eseries.cpp
  src/corpus.cpp
  src/validator.cpp
  src/magnetics.cpp
  src/capacitors.cpp
  src/resistors.cpp
  src/mosfets.cpp
  src/diodes.cpp
  src/igbts.cpp
  src/bjts.cpp
  src/varistors.cpp
  src/connectors.cpp
  src/thermistors.cpp
  src/analog.cpp
  src/controllers.cpp
  src/time_bases.cpp
  src/circuits.cpp)
add_dependencies(tas_validator_core tas_validator_generated_check_codes)
target_include_directories(tas_validator_core PUBLIC
  "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_include_directories(tas_validator_core PRIVATE
  "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(tas_validator_core PUBLIC nlohmann_json::nlohmann_json)

# --- Python module -----------------------------------------------------------
pybind11_add_module(tas_validator src/bindings.cpp)
target_link_libraries(tas_validator PRIVATE tas_validator_core)

# --- C++ unit tests (Catch2, run the binary directly — no ctest) -------------
if(BUILD_TAS_VALIDATOR_TESTS)
  FetchContent_Declare(Catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.8.1)
  FetchContent_MakeAvailable(Catch2)

  add_executable(tas_validator_tests tests/test_validator.cpp)
  target_link_libraries(tas_validator_tests PRIVATE tas_validator_core Catch2::Catch2WithMain)
  # ABT #549 guard test: re-derive the check-code inventories at test time and
  # assert they equal the compiled-in check_codes()/circuit_check_codes(), so a
  # code emitted from a check but never regenerated into (or hand-removed from)
  # the generated header still fails the suite instead of only drifting the docs.
  target_compile_definitions(tas_validator_tests PRIVATE
    TAS_VALIDATOR_GEN_SCRIPT="${CHECK_CODES_GEN_SCRIPT}"
    TAS_VALIDATOR_SRC_DIR="${CMAKE_CURRENT_SOURCE_DIR}/src")
endif()
