// SPDX-License-Identifier: MIT
// pybind11 module `tas_metrics`.
//
//   import tas_metrics
//   s = tas_metrics.Scorer(spec_json_text)
//   p = s.score(record_json_text, "mosfets", schema_ok=True, url_alive=0.8)   # dict or None
//   t = s.score_file(path, "mosfets", url_alive, schema_mask)                 # dict of totals
//   tas_metrics.quarantine_counts(path) -> {"mosfets.ndjson": n, ...}
#include "tas_metrics/metrics.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using namespace tas::metrics;

namespace {

py::dict checks_dict(const Checks& c) {
    py::dict d;
    d["existence"] = c.existence;
    d["traced"] = c.traced;
    d["fields"] = c.fields;
    d["curves"] = c.curves ? py::object(py::float_(*c.curves)) : py::object(py::none());
    d["url"] = c.url;
    d["valid"] = c.valid;
    return d;
}

py::object part_dict(const std::optional<PartScore>& ps) {
    if (!ps) return py::none();
    py::dict d;
    d["score"] = ps->score;
    d["checks"] = checks_dict(ps->checks);
    d["kind"] = ps->kind;
    d["pn"] = ps->identity_pn;
    d["manufacturer"] = ps->identity_mfr;
    d["blade_codes"] = ps->blade_codes;
    d["blade_error"] = ps->blade_error;
    return std::move(d);
}

}  // namespace

PYBIND11_MODULE(tas_metrics, m) {
    m.doc() = "TAS database quality score (scripts/metrics_spec.json rules).";

    py::class_<Scorer>(m, "Scorer")
        .def(py::init([](const std::string& spec) { return Scorer(json::parse(spec)); }), py::arg("spec_json"))
        .def(py::init([](const std::string& spec, const std::string& aliases) {
                 return Scorer(json::parse(spec), json::parse(aliases));
             }),
             py::arg("spec_json"), py::arg("aliases_json"))
        .def(
            "score",
            [](const Scorer& s, const std::string& record, const std::string& catalogue, bool schema_ok,
               double url_alive) { return part_dict(s.score(json::parse(record), catalogue, schema_ok, url_alive)); },
            py::arg("record_json"), py::arg("catalogue"), py::arg("schema_ok"), py::arg("url_alive"))
        .def(
            "score_file",
            [](const Scorer& s, const std::string& path, const std::string& catalogue, double url_alive,
               const py::bytes& mask) {
                const std::string m = mask;
                CatalogueTotals t;
                {
                    py::gil_scoped_release release;
                    t = s.score_file(path, catalogue, url_alive, m);
                }
                py::dict d;
                d["live"] = t.live;
                d["malformed"] = t.malformed;
                d["schema_failed"] = t.schema_failed;
                d["blade_errors"] = t.blade_errors;
                d["distinct"] = t.distinct;
                d["sum"] = t.sum;
                d["check_sum"] = t.check_sum;
                d["check_n"] = t.check_n;
                d["blade_code_counts"] = t.blade_code_counts;
                return d;
            },
            py::arg("path"), py::arg("catalogue"), py::arg("url_alive"), py::arg("schema_mask"))
        .def("catalogues", &Scorer::catalogues)
        .def("weights", &Scorer::weights);

    m.def("quarantine_counts", &quarantine_counts, py::arg("path"));
    m.def("normalise_pn", &normalise_pn, py::arg("pn"));
    m.def("relative_field", &relative_field, py::arg("path"));
    m.def("manufacturer_key", &manufacturer_key, py::arg("name"));
}
