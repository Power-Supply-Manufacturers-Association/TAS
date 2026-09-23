// SPDX-License-Identifier: MIT
// pybind11 module `tas_validator`.
//
//   import tas_validator
//   v = tas_validator.validate(record)        # record: dict or JSON string
//   v.valid            -> bool                 (False iff any IMPOSSIBLE finding)
//   v.findings         -> [Finding, ...]
//   v.skipped          -> [str, ...]           (checks skipped for missing data)
//   f.code, f.severity, f.component, f.reference, f.message, f.value, f.threshold
//   tas_validator.check_codes() -> [str, ...]
//   tas_validator.build_fingerprint() -> str   (ABT #397 staleness guard, see tools/check_freshness.py)
//
//   v = tas_validator.validate_circuit(brick)  # a CIAS brick, NOT a part
//   tas_validator.circuit_check_codes() -> [str, ...]
#include "tas_validator/helpers.hpp"
#include "tas_validator/validator.hpp"

#include <pybind11/eval.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <string>

namespace py = pybind11;
using namespace tas;

namespace {

// Accept a JSON string directly, or any Python object (dict/list) which we
// serialise with the stdlib `json` module and parse with nlohmann.
json to_json(const py::object& obj) {
    if (py::isinstance<py::str>(obj)) {
        return json::parse(obj.cast<std::string>());
    }
    py::object dumps = py::module_::import("json").attr("dumps");
    // allow_nan=False so NaN/Infinity are rejected at serialization (they are not
    // valid JSON and would otherwise slip past scalar()'s isfinite guard).
    std::string text = dumps(obj, py::arg("allow_nan") = false).cast<std::string>();
    return json::parse(text);
}

Verdict do_validate(const py::object& obj) {
    static const PartValidator validator;
    return validator.validate(to_json(obj));
}

Verdict do_validate_circuit(const py::object& obj) { return validate_circuit(to_json(obj)); }

}  // namespace

PYBIND11_MODULE(tas_validator, m) {
    m.doc() = "TAS physics validator — is a catalog part physically valid?";

    // `Severity` is a str-derived Python Enum, and `Finding.severity` /
    // `CorpusFinding.severity` return one of its members.
    //
    // It used to be a plain uppercase str ('IMPOSSIBLE') while the module ALSO
    // exported a pybind11 enum whose members stringify as 'Severity.Impossible'.
    // The obvious severity test --
    //     f.severity == tas_validator.Severity.Impossible
    // -- was therefore SILENTLY False: "do we have any impossible parts?"
    // answered "no" vacuously, for every corpus, with nothing raised anywhere.
    //
    // BOTH spellings must work, or the transition itself produces the same
    // silent false pass in the other direction: a consumer written against the
    // string spelling gets 0 IMPOSSIBLE the day the enum lands, and finds out by
    // spot-checking a record it already knew was bad. (That is not hypothetical
    // -- it happened to a parallel agent on 2026-09-23 the moment an earlier,
    // pure-py::enum_ version of this fix landed on the shared build.)
    //
    // A py::enum_ CANNOT give both: pybind11 installs its own __eq__, __str__
    // and __hash__ on the enum type, and a .def() of those names afterwards does
    // not displace them -- the built-in compares against ints and enums only and
    // answers a plain False for a str, which is precisely the silent wrong
    // answer being removed. So the type is built as a real Python
    // `class Severity(str, Enum)` instead: its members ARE the uppercase strings
    // they print, so ==, `in`, str(), f-strings, dict/set keys and
    // isinstance(x, str) all behave exactly as they did before, while
    // `== Severity.Impossible` now also answers True.
    py::exec(R"py(
import enum


class Severity(str, enum.Enum):
    """A finding's severity.

    Both spellings compare equal, on purpose:
        f.severity == tas_validator.Severity.Impossible   -> True
        f.severity == "IMPOSSIBLE"                        -> True
    """

    Ok = "OK"
    Suspicious = "SUSPICIOUS"
    Impossible = "IMPOSSIBLE"

    def __str__(self):
        return self.value

    def __format__(self, spec):
        return format(self.value, spec)

    def __repr__(self):
        return "<Severity.%s>" % self.name
)py",
             m.attr("__dict__"));
    const py::object severity_cls = m.attr("Severity");
    auto to_severity = [severity_cls](Severity s) {
        return severity_cls(py::str(to_string(s)));
    };

    py::class_<Finding>(m, "Finding")
        .def_readonly("code", &Finding::code)
        .def_property_readonly("severity",
                               [to_severity](const Finding& f) { return to_severity(f.severity); })
        .def_readonly("component", &Finding::component)
        .def_readonly("reference", &Finding::reference)
        .def_readonly("message", &Finding::message)
        .def_readonly("value", &Finding::value)
        .def_readonly("threshold", &Finding::threshold)
        .def("__repr__", [](const Finding& f) {
            return "<Finding " + std::string(to_string(f.severity)) + " " + f.code + ": " +
                   f.message + ">";
        });

    py::class_<Verdict>(m, "Verdict")
        .def_readonly("valid", &Verdict::valid)
        .def_readonly("findings", &Verdict::findings)
        .def_readonly("skipped", &Verdict::skipped)
        .def_readonly("completeness", &Verdict::completeness)
        .def("__repr__", [](const Verdict& v) {
            return "<Verdict valid=" + std::string(v.valid ? "True" : "False") + " findings=" +
                   std::to_string(v.findings.size()) + ">";
        });

    py::class_<CorpusFinding>(m, "CorpusFinding")
        .def_readonly("index", &CorpusFinding::index)
        .def_readonly("code", &CorpusFinding::code)
        .def_property_readonly(
            "severity",
            [to_severity](const CorpusFinding& f) { return to_severity(f.severity); })
        .def_readonly("reference", &CorpusFinding::reference)
        .def_readonly("message", &CorpusFinding::message)
        .def_readonly("value", &CorpusFinding::value)
        .def_readonly("score", &CorpusFinding::score)
        .def("__repr__", [](const CorpusFinding& f) {
            return "<CorpusFinding " + f.code + " #" + std::to_string(f.index) + ": " + f.message +
                   ">";
        });

    m.def("validate", &do_validate, py::arg("record"),
          "Validate one part record (dict or JSON string). Returns a Verdict.");
    m.def(
        "validate_corpus",
        [](const py::iterable& records) {
            std::vector<json> recs;
            for (const py::handle& h : records)
                recs.push_back(to_json(py::reinterpret_borrow<py::object>(h)));
            return validate_corpus(recs);
        },
        py::arg("records"),
        "Batch-screen a list of records for cohort statistical outliers. Returns "
        "[CorpusFinding, ...].");
    m.def(
        "validate_json",
        [](const std::string& text) {
            static const PartValidator validator;
            return validator.validate_json(text);
        },
        py::arg("text"), "Validate one part record given as a JSON string.");
    m.def("check_codes", &PartValidator::check_codes, "All check codes the validator can emit.");
    m.def("build_fingerprint", &build_fingerprint,
          "Content fingerprint (sha256 over validator/src + validator/include) baked in at "
          "build time (ABT #397). Compare against tools.gen_build_fingerprint.compute_fingerprint "
          "on the current checkout -- see tools/check_freshness.py -- to detect a stale build "
          "before trusting it; a mismatch means this module predates source changes it should "
          "reflect, regardless of which build-* directory produced it or its file mtime.");

    m.def("validate_circuit", &do_validate_circuit, py::arg("brick"),
          "Validate one CIAS circuit brick (dict or JSON string) — the physics gate for "
          "circuits, complementary to CIAS.json (shape), validate_cias_structure (graph) "
          "and CiasCircuitConverter (emittability). Returns a Verdict.");
    m.def("circuit_check_codes", &circuit_check_codes,
          "All CIR_* check codes the circuit validator can emit.");
}
