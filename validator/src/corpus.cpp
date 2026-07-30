// SPDX-License-Identifier: MIT
// Corpus-level batch screen (P6). Within each (manufacturer, component) cohort, a
// numeric electrical field value that is a robust-z outlier (Iglewicz-Hoaglin
// modified z-score from the median/MAD) far from its cohort mates is a likely typo
// or fabricated value — one that per-record physics bounds pass. Worked in log10
// space for the (strictly-positive) multiplicative electrical quantities.
//
// Deliberately NOT included: cross-manufacturer identical-spec "clone" detection.
// Measured on the live catalog, identical electrical blocks are dominated by
// legitimate second-source equivalents and part-number-family variants (33-51% of
// parts share a block), so it is not a reliable synthesis signal.
#include "tas_validator/helpers.hpp"
#include "tas_validator/validator.hpp"

#include <algorithm>
#include <cmath>
#include <set>
#include <sstream>
#include <unordered_map>
#include <vector>

namespace tas {
namespace {

constexpr std::size_t MIN_COHORT = 8;   // need enough mates for robust stats
constexpr double Z_OUTLIER = 5.0;       // conservative (batch screen)

// ---- ceiling pile-up (P7) -------------------------------------------------
// A CLAMP is invisible to the robust-z screen above, and in fact defeats it: many
// records sharing one value shrink the MAD, so the clamped group looks like the
// norm and its honest neighbours start looking like the outliers.
//
// Found on the live catalogue via the capacitor ESR field: every major ceramic
// cohort has a pile-up at an identical ROUND ceiling — 200 Ω (Murata, TDK, Samsung),
// 160 Ω (YAGEO), 120 Ω (KEMET), 60/45 Ω for class-1 — at up to 1,880x the cohort
// median. Four independent manufacturers do not converge on exactly 200.0000 Ω for
// two dozen parts each; that is our own pipeline clamping, and the value is a
// placeholder wearing a measurement's clothes. Per-record physics cannot catch it:
// 200 Ω on a 10 pF ceramic implies tanδ≈0.013, perfectly plausible in isolation.
//
// Conditions, deliberately conservative so a genuine shared spec does not trip it:
// the value must be the cohort MAXIMUM, shared by several parts, AND sit far above
// the cohort median. A family quoting one honest ESR for its members sits NEAR the
// median, not at a 10x+ extreme.
// Only MEASURED, per-part continuous quantities are eligible. Catalogue AXES
// (capacitance, ratedVoltage, positions…) are chosen from standard value lists, so
// their largest value is shared by many parts BY CONSTRUCTION — screening those
// produced 1,310 false positives on the first pass ("ratedVoltage=400 shared by 9
// parts", which is simply what a product family looks like).
bool ceiling_eligible(const std::string& field) {
    static const std::set<std::string> MEASURED = {
        // capacitors
        "esr", "dissipationFactor", "leakageCurrent", "insulationResistance",
        "rippleCurrent", "capacitanceMinimumLongTerm",
        // semiconductors
        "rdsOn", "onResistance", "vf", "vceSat", "gateCharge", "peakPulseCurrent",
        // timing devices / crystals
        "equivalentSeriesResistance",
        // magnetics / connectors
        "dcResistance", "contactResistance",
    };
    return MEASURED.count(field) != 0;
}

// Surveyed every catalogue before choosing the list above. Two families of hit were
// DELIBERATELY excluded because the pile-up is how the spec is written, not a defect:
//   - frequencyTolerance / frequencyStability (Abracon ×23, ECS ×12): crystals are sold
//     at standard ±5/10/25/50 ppm grades, so the loosest grade is shared by design.
//   - updateRate (TI ×9): a converter family shares one top-end sample rate; that is a
//     product decision, not a clamp.
// What the list DOES catch, beyond the capacitor ESR ceiling this screen was written
// for: capacitanceMinimumLongTerm pinned at 8.55e-05 across Murata (64), TDK (40) AND
// Samsung (13) — the same cross-manufacturer signature, in a different field.
//
// Single-manufacturer hits are screening-grade and can be legitimate family structure
// (Nexperia sharing 4.5 Ω onResistance across 11 small-signal parts is plausible).
// The finding is Suspicious for exactly that reason; a value repeated at the same
// ceiling by SEVERAL independent manufacturers is the one that is nearly always ours.

constexpr std::size_t CEILING_MIN_AT_MAX = 5;   // enough repeats to be systematic
// max/median. Tuned on the live capacitor catalogue: at 10x this also catches honest
// family structure (a Rubycon series whose smallest can legitimately sits 12x above
// its cohort median); at 100x the survivors are exactly the round clamps — 120, 160
// and 200 Ω — and nothing else. Deliberately biased to precision: a missed clamp is
// a data-quality ticket, a false one wastes a human's afternoon.
constexpr double CEILING_MIN_RATIO = 100.0;

std::optional<double> field_scalar(const json& v) {
    if (v.is_number()) {
        double d = v.get<double>();
        return std::isfinite(d) ? std::optional<double>(d) : std::nullopt;
    }
    if (v.is_object())
        for (const char* k : {"nominal", "minimum", "maximum"})
            if (v.contains(k) && v[k].is_number()) {
                double d = v[k].get<double>();
                if (std::isfinite(d)) return d;
            }
    return std::nullopt;
}

// Locate the component discriminator and its object.
const json* find_component(const json& part, std::string& comp) {
    static const char* SIMPLE[] = {"magnetic", "capacitor", "resistor", "varistor", "connector"};
    for (const char* k : SIMPLE)
        if (part.contains(k)) { comp = k; return &part[k]; }
    if (part.contains("semiconductor") && part["semiconductor"].is_object()) {
        const json& s = part["semiconductor"];
        for (const char* k : {"mosfet", "diode", "igbt", "bjt"})
            if (s.contains(k)) { comp = k; return &s[k]; }
    }
    static const char* AAS[] = {"operationalAmplifier", "comparator", "instrumentationAmplifier",
                                "differenceAmplifier", "programmableGainAmplifier", "buffer",
                                "sampleHold", "analogSwitch", "multiplexer", "adc", "dac",
                                "multiplier", "integrator", "summer"};
    for (const char* k : AAS)
        if (part.contains(k)) { comp = k; return &part[k]; }
    return nullptr;
}

struct Rec {
    std::string comp, mfr, ref, sub;  // sub = technology/series sub-cohort key
    const json* elec = nullptr;       // the electrical object (electrical[0] for magnetics)
};

Rec describe(const json& part) {
    Rec r;
    if (!part.is_object()) return r;
    const json* cobj = find_component(part, r.comp);
    if (cobj == nullptr) return r;
    const json* mi = at(*cobj, "manufacturerInfo");
    if (mi == nullptr) return r;
    if (mi->contains("name") && (*mi)["name"].is_string()) r.mfr = (*mi)["name"].get<std::string>();
    if (mi->contains("reference") && (*mi)["reference"].is_string())
        r.ref = (*mi)["reference"].get<std::string>();
    // Sub-cohort key: compare like-with-like. (manufacturer, component) alone lumps
    // different dielectrics / voltage classes together, collapsing the MAD and
    // exploding z-scores. technology+subType+series narrows to comparable parts.
    if (const json* p = at(*mi, "datasheetInfo", "part"))
        for (const char* k : {"technology", "subType", "series"})
            if (p->contains(k) && (*p)[k].is_string()) {
                if (!r.sub.empty()) r.sub += "/";
                r.sub += (*p)[k].get<std::string>();
            }
    const json* elec = at(*mi, "datasheetInfo", "electrical");
    if (elec && elec->is_array() && !elec->empty() && elec->front().is_object())
        r.elec = &elec->front();
    else if (elec && elec->is_object())
        r.elec = elec;
    return r;
}

double median_sorted(std::vector<double>& xs) {
    std::sort(xs.begin(), xs.end());
    std::size_t n = xs.size();
    return n % 2 ? xs[n / 2] : 0.5 * (xs[n / 2 - 1] + xs[n / 2]);
}

}  // namespace

std::vector<CorpusFinding> validate_corpus(const std::vector<json>& records) {
    std::vector<CorpusFinding> out;

    // Describe every record once.
    std::vector<Rec> recs(records.size());
    for (std::size_t i = 0; i < records.size(); ++i) recs[i] = describe(records[i]);

    // Group record indices by (manufacturer | component).
    std::unordered_map<std::string, std::vector<std::size_t>> cohorts;
    for (std::size_t i = 0; i < recs.size(); ++i) {
        const Rec& r = recs[i];
        if (r.elec == nullptr || r.mfr.empty()) continue;
        cohorts[r.mfr + "|" + r.comp + "|" + r.sub].push_back(i);
    }

    for (auto& [key, idxs] : cohorts) {
        if (idxs.size() < MIN_COHORT) continue;

        // Collect (index, value) per field across the cohort.
        std::unordered_map<std::string, std::vector<std::pair<std::size_t, double>>> byField;
        for (std::size_t i : idxs) {
            const json& e = *recs[i].elec;
            for (auto it = e.begin(); it != e.end(); ++it) {
                auto v = field_scalar(it.value());
                if (v) byField[it.key()].push_back({i, *v});
            }
        }

        for (auto& [field, vals] : byField) {
            if (vals.size() < MIN_COHORT) continue;
            // Work in log10 space iff every value is strictly positive (multiplicative
            // electrical quantities); otherwise use raw values.
            bool allPos = std::all_of(vals.begin(), vals.end(),
                                      [](const auto& p) { return p.second > 0; });
            std::vector<double> xs;
            xs.reserve(vals.size());
            for (const auto& p : vals) xs.push_back(allPos ? std::log10(p.second) : p.second);

            // ---- ceiling pile-up screen, in RAW units (a clamp is a raw-value
            // artifact, and it must be reported before the MAD gate below, which the
            // clamp itself can defeat by collapsing the spread to zero).
            if (allPos && ceiling_eligible(field)) {
                double mx = 0.0;
                for (const auto& p : vals) mx = std::max(mx, p.second);
                std::size_t at_max = 0;
                for (const auto& p : vals)
                    if (p.second == mx) ++at_max;
                std::vector<double> raw;
                raw.reserve(vals.size());
                for (const auto& p : vals) raw.push_back(p.second);
                double raw_med = median_sorted(raw);
                if (at_max >= CEILING_MIN_AT_MAX && raw_med > 0 &&
                    mx / raw_med >= CEILING_MIN_RATIO) {
                    for (const auto& p : vals) {
                        if (p.second != mx) continue;
                        std::ostringstream m;
                        m << field << "=" << mx << " is shared by " << at_max << " parts at the "
                          << "TOP of its " << key << " cohort (n=" << vals.size() << ", median "
                          << raw_med << ", " << (mx / raw_med)
                          << "x) — a clamp or placeholder, not a measurement";
                        out.push_back({p.first, "GEN_COHORT_CEILING", recs[p.first].ref, m.str(),
                                       mx, mx / raw_med});
                    }
                }
            }

            std::vector<double> sorted = xs;
            double med = median_sorted(sorted);
            std::vector<double> dev;
            dev.reserve(xs.size());
            for (double x : xs) dev.push_back(std::fabs(x - med));
            double mad = median_sorted(dev);
            if (mad <= 0) continue;  // cohort is (near-)constant; no spread to judge

            for (std::size_t j = 0; j < vals.size(); ++j) {
                double z = 0.6745 * (xs[j] - med) / mad;
                if (std::fabs(z) > Z_OUTLIER) {
                    std::size_t ri = vals[j].first;
                    std::ostringstream m;
                    m << field << "=" << vals[j].second << " is a robust-z=" << z
                      << " outlier within its " << key << " cohort (n=" << vals.size() << ")";
                    out.push_back({ri, "GEN_COHORT_OUTLIER", recs[ri].ref, m.str(), vals[j].second,
                                   z});
                }
            }
        }
    }
    return out;
}

}  // namespace tas
