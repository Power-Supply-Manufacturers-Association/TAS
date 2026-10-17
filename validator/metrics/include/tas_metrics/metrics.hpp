// SPDX-License-Identifier: MIT
// tas_metrics — the QUALITY score of a TAS catalogue part (Alf, 2026-10-17).
//
// One implementation, three consumers: the Python driver scripts/db_metrics.py (pybind11
// module `tas_metrics`), the C++ tests, and the browser (emscripten module). The scoring
// rules live in scripts/metrics_spec.json; this file owns the arithmetic.
//
// A part's score is a weighted checklist over six checks:
//   existence  the best provenance entry that proves the exact part exists
//   traced     mean evidence credit over the present core fields
//   fields     present core fields / applicable core fields (a contradicted or known
//              placeholder value counts -1; floored at 0)
//   curves     present expected curves / applicable curves (absent when a type expects none;
//              its weight then moves to the other checks)
//   url        the part cites a URL, times the catalogue's measured alive rate
//   valid      schema-valid (decided by the caller) and Blade Runner: none 1, SUSPICIOUS 0.5,
//              IMPOSSIBLE 0
#pragma once

#include <nlohmann/json.hpp>

#include <cstddef>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace tas::metrics {

using json = nlohmann::json;

// The six check scores of one part, each 0..1. `curves` is empty when the part's type
// expects no curves.
struct Checks {
    double existence = 0.0;
    double traced = 0.0;
    double fields = 0.0;
    std::optional<double> curves;
    double url = 0.0;
    double valid = 0.0;
};

struct PartScore {
    double score = 0.0;
    Checks checks;
    std::string kind;                       // inner wrap key for '*' wraps (analog, timeBase)
    std::string identity_pn;                // normalised part number
    std::string identity_mfr;               // manufacturer_key() of the canonical manufacturer name
    std::vector<std::string> blade_codes;   // Blade Runner findings (SUSPICIOUS + IMPOSSIBLE)
    std::string blade_error;                // Blade Runner threw on this record (scored invalid)
};

// Sums over a catalogue, ready for the driver to divide.
struct CatalogueTotals {
    std::size_t live = 0;          // rows scored
    std::size_t malformed = 0;     // rows that do not unwrap to a part object (score 0)
    std::size_t schema_failed = 0; // rows the caller marked schema-invalid
    std::size_t blade_errors = 0;  // rows Blade Runner could not evaluate
    std::size_t distinct = 0;      // distinct (normalised pn, manufacturer)
    double sum = 0.0;              // sum of part scores (malformed rows add 0)
    std::map<std::string, double> check_sum;
    std::map<std::string, std::size_t> check_n;
    std::map<std::string, std::size_t> blade_code_counts;
};

class Scorer {
public:
    // `spec` is scripts/metrics_spec.json. Throws std::invalid_argument on a malformed spec.
    explicit Scorer(const json& spec);

    // Manufacturer alias table (scripts/manufacturer_aliases.json): {"manufacturers": [{"name": canonical,
    // "aliases": [...]}, ...]}. Names match under manufacturer_key(); a part's identity then carries the
    // canonical name, so "Tyco Electronics" and "TE Connectivity" rows of one mpn count as one part.
    // Throws std::invalid_argument when one alias maps to two canonical names.
    Scorer(const json& spec, const json& aliases);

    // Score one record of catalogue `catalogue` (a key of spec.catalogues).
    // `schema_ok` comes from the caller's JSON-Schema validation (there is no 2020-12
    // validator in C++); `url_alive` is the catalogue's measured alive rate (0..1).
    // Returns nullopt when the record does not unwrap to a part object.
    std::optional<PartScore> score(const json& record, const std::string& catalogue,
                                   bool schema_ok, double url_alive) const;

    // Stream an NDJSON file. `schema_mask` holds one byte per line: '0' marks a
    // schema-invalid line, anything else valid; only the first schema_mask.size() lines are
    // read, so a file appended to after the caller built the mask is cut at the same line.
    CatalogueTotals score_file(const std::string& path, const std::string& catalogue,
                               double url_alive, const std::string& schema_mask) const;

    std::vector<std::string> catalogues() const;
    const std::map<std::string, double>& weights() const { return weights_; }

    struct Condition {
        bool wrap = false;                   // the '$wrap' pseudo-path
        std::vector<std::string> segs;
        std::vector<json> allowed;
    };
    struct Entry {
        std::vector<std::string> alt_text;   // alternatives as written in the spec
        std::vector<std::vector<std::string>> alt_segs;
        std::vector<Condition> when;
    };
    struct Placeholder {
        std::string path;
        std::vector<std::string> segs;
        json value;
        std::vector<Condition> when;
    };
    struct Catalogue {
        std::string file;
        std::vector<std::string> wrap;
        std::vector<Entry> fields;
        std::vector<Entry> curves;
        std::vector<Placeholder> placeholders;
    };

private:
    std::map<std::string, double> weights_;
    std::map<std::string, Catalogue> catalogues_;
    std::map<std::string, std::vector<std::string>> code_fields_;  // Blade Runner code -> field names
    std::map<std::string, std::string> aliases_;                   // manufacturer_key -> canonical key
};

// The match key of a manufacturer name: ASCII lower-cased, every non-alphanumeric byte removed
// ("Texas-Instruments" -> "texasinstruments").
std::string manufacturer_key(const std::string& name);

// Counts of quarantined rows per source catalogue file ("mosfets.ndjson"), keyed by the stem
// of each row's first _quarantineSource; rows without one are counted under "".
std::map<std::string, std::size_t> quarantine_counts(const std::string& path);

// Part-number normalisation shared with the URL sampler: whitespace, '-', '_' and '/'
// removed, ASCII upper-cased.
std::string normalise_pn(const std::string& pn);

// A provenance field path reduced to its datasheetInfo-relative form with every bracket
// group dropped: "manufacturerInfo.datasheetInfo.electrical[0].inductance" ->
// "electrical.inductance".
std::string relative_field(const std::string& path);

}  // namespace tas::metrics
