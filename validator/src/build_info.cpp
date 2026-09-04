// SPDX-License-Identifier: MIT
// tas::build_fingerprint() (ABT #397) -- see the doc comment on its
// declaration in validator.hpp and tools/gen_build_fingerprint.py for the
// full rationale. The literal below is generated at build time by a CMake
// custom command (CMakeLists.txt) that hashes validator/src/*.cpp and
// validator/include/tas_validator/*.hpp; nobody hand-edits it.
#include "tas_validator/validator.hpp"

namespace tas {

std::string build_fingerprint() {
    return
#include "tas_validator/build_fingerprint.inc"
        ;
}

}  // namespace tas
