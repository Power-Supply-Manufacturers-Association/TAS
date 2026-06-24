// SPDX-License-Identifier: Apache-2.0
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
#include <sstream>
#include <unordered_map>
#include <vector>

namespace tas {
namespace {

constexpr std::size_t MIN_COHORT = 8;   // need enough mates for robust stats
constexpr double Z_OUTLIER = 5.0;       // conservative (batch screen)

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
