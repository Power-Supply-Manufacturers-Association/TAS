// SPDX-License-Identifier: MIT
// TAS Physics Validator — public API.
//
// Given a single TAS catalog part record (one NDJSON line as JSON), decide
// whether it is physically valid and explain why. Verdict model (per user
// decision): each check yields a Finding only when it fires; a part is INVALID
// iff it has at least one IMPOSSIBLE finding. SUSPICIOUS findings are warnings
// that do not, by themselves, invalidate a part.
#pragma once

#include <nlohmann/json.hpp>

#include <string>
#include <vector>

namespace tas {

using json = nlohmann::json;

enum class Severity { Ok, Suspicious, Impossible };

const char* to_string(Severity s);

struct Finding {
    std::string code;       // stable check id, e.g. "MAG_ENERGY_DENSITY"
    Severity severity = Severity::Ok;
    std::string component;  // "magnetic" | "capacitor" | "resistor" | "mosfet" | "diode" | "igbt"
    std::string reference;  // manufacturer part reference, for traceability
    std::string message;    // human-readable explanation
    double value = 0.0;     // the computed quantity that tripped the check
    double threshold = 0.0; // the bound it violated (0 if not applicable)
};

struct Verdict {
    bool valid = true;                 // false iff any finding is Impossible
    std::vector<Finding> findings;     // all fired checks (Suspicious + Impossible)
    std::vector<std::string> skipped;  // check codes skipped for missing input data
    // Fraction of the family's core datasheet fields that are present (0..1), or
    // -1 if no manifest exists for the component. A non-binary authenticity signal:
    // a near-empty fabricated record scores low. Does NOT affect `valid`.
    double completeness = -1.0;
};

// Context threaded into every per-family check.
struct Ctx {
    std::string component;  // discriminator, set by the dispatcher
    std::string reference;  // part reference for findings
};

// Append a fired finding. `message` should already name the offending quantity.
inline void emit(std::vector<Finding>& out, const Ctx& ctx, std::string code, Severity sev,
                 double value, double threshold, std::string message) {
    Finding f;
    f.code = std::move(code);
    f.severity = sev;
    f.component = ctx.component;
    f.reference = ctx.reference;
    f.message = std::move(message);
    f.value = value;
    f.threshold = threshold;
    out.push_back(std::move(f));
}

class PartValidator {
public:
    // Validate one part record (top-level object with a magnetic/capacitor/
    // resistor/semiconductor discriminator). Throws std::invalid_argument if no
    // known discriminator is present; throws MalformedField if a field a check
    // needs is present but uninterpretable.
    Verdict validate(const json& part) const;

    // Parse `text` as JSON and validate. Throws nlohmann::json::parse_error on
    // bad JSON.
    Verdict validate_json(const std::string& text) const;

    // All check codes this validator can emit (for documentation / tests).
    static std::vector<std::string> check_codes();
};

// A corpus-level finding: produced by validate_corpus, which screens a whole batch
// of records for anomalies invisible to per-record validation.
struct CorpusFinding {
    std::size_t index = 0;    // index into the input records vector
    std::string code;         // e.g. "GEN_COHORT_OUTLIER"
    std::string reference;    // the offending part's reference
    std::string message;
    double value = 0.0;       // the outlier value
    double score = 0.0;       // robust z-score (Iglewicz-Hoaglin) that tripped it
};

// Batch screen: within each (manufacturer, component) cohort, flag a numeric field
// value that is a robust-z (median/MAD) outlier far from its cohort mates — a typo
// or fabricated value that per-record physics bounds pass. SCREENING only; this is
// kept entirely separate from validate() so the per-record contract is unchanged.
std::vector<CorpusFinding> validate_corpus(const std::vector<json>& records);

// --- per-family check entry points (implemented in the matching .cpp) ---------
// Each appends Findings for fired checks and skipped-codes for missing inputs.
void check_magnetics(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_capacitors(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_resistors(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_mosfets(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_diodes(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_igbts(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_bjts(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_varistors(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_connectors(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_analog(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_controllers(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
// Time bases (TBAS): the timeBase wrap dispatches on the oscillator/timer/latch
// sub-discriminator (ctx.component carries the subtype).
void check_oscillators(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_timers(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
void check_latches(const json& datasheet, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);
// Light unit-slip screening of the ideal `behavioral` atom (a TBAS record may be
// a part-less behavioral-only document, so this runs even without datasheetInfo).
void check_time_base_behavioral(const json& behavioral, const Ctx&, std::vector<Finding>&, std::vector<std::string>& skipped);

}  // namespace tas
