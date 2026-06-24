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

# --- core library (pure C++, no Python) --------------------------------------
add_library(tas_validator_core STATIC
  src/helpers.cpp
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
  src/analog.cpp)
target_include_directories(tas_validator_core PUBLIC
  "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_link_libraries(tas_validator_core PUBLIC nlohmann_json::nlohmann_json)

# --- Python module -----------------------------------------------------------
pybind11_add_module(tas_validator src/bindings.cpp)
target_link_libraries(tas_validator PRIVATE tas_validator_core)

# --- C++ unit tests ----------------------------------------------------------
if(BUILD_TAS_VALIDATOR_TESTS)
  FetchContent_Declare(UnitTestpp
    GIT_REPOSITORY https://github.com/unittest-cpp/unittest-cpp.git
    GIT_TAG tags/v2.0.0)
  set(UTPP_INCLUDE_TESTS_IN_BUILD OFF CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(UnitTestpp)

  add_executable(tas_validator_tests tests/test_validator.cpp)
  target_include_directories(tas_validator_tests PRIVATE
    "${CMAKE_BINARY_DIR}/_deps/unittestpp-src")
  target_link_libraries(tas_validator_tests PRIVATE tas_validator_core UnitTest++)
endif()
