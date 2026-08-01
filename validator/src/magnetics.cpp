// SPDX-License-Identifier: MIT
// Magnetics physics checks. `datasheet` is the datasheetInfo object; its
// `electrical` member is an ARRAY of operating points (inductor/transformer
// windings), so every check runs per op-point and tags findings with the index.
#include "tas_validator/eseries.hpp"
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cctype>
#include <cmath>
#include <sstream>
#include <string>

namespace tas {
namespace {

// Winding resistance, in EITHER shape the catalogue uses. An inductor carries a
// singular `dcResistance`; a common-mode choke carries `dcResistances[]`, one entry
// per winding.
//
// Reading only the singular form does not FAIL on a choke — it finds nothing, which
// is indistinguishable from a clean part. That is how every common-mode choke in the
// catalogue went unexamined by MAG_DCR_GEOM, MAG_DCR_PER_H and MAG_ISAT_POWER
// (ABT #387): the checks reported success on a population they never looked at.
// MAG_DISS_DENSITY was written later and read both shapes, which is precisely why it
// found what the older checks had been silently skipping.
std::optional<double> read_dcr(const json& pt) {
    if (auto d = scalar_at(pt, {"dcResistance"})) return d;
    if (pt.contains("dcResistances") && pt["dcResistances"].is_array() &&
        !pt["dcResistances"].empty())
        return scalar(&pt["dcResistances"][0], "dcResistances[0]");
    return std::nullopt;
}

// Do this operating point's winding resistance and its inductance describe the SAME
// winding? That is the precondition for every DCR-over-L ratio below, and it is not
// always met.
//
// A plain inductor has one winding, and a common-mode choke's windings are identical
// by construction, so pairing the winding resistance with the part's inductance is
// exact. A TRANSFORMER's windings differ by its turns ratio, and the record carries one
// part-level inductance with nothing to say which winding it belongs to. Wuerth's
// WE-CST 7492540500 is a 1:500 current sense transformer: the 0.135 H is the 500-turn
// secondary and the 0.28 mohm is the single-turn primary through the core. Dividing one
// by the other put DCR*size^2/L a quarter of a million times off with the data entirely
// correct, and did it to nine parts (ABT #432).
//
// This is not a new exemption invented to quieten those nine. check_dissipation_density
// below already declines for `transformer` on precisely these grounds - "rated current
// is the PRIMARY current, the DCR a winding resistance; their product is not a
// dissipation" - so the newer check has always known what the three older ones assumed.
// This brings them to the same standard, the way ABT #387 brought them to its DCR reader.
bool dcr_pairs_with_inductance(const json& pt) {
    if (pt.contains("subtype") && pt["subtype"].is_string())
        return pt["subtype"].get<std::string>() != "transformer";
    return true;
}

void check_point(const json& pt, int idx, const json& dims, const std::string& material,
                 const Ctx& ctx, std::vector<Finding>& out, std::vector<std::string>& skipped) {
    const std::string tag = "[op " + std::to_string(idx) + "] ";
    std::string subtype;
    if (pt.contains("subtype") && pt["subtype"].is_string())
        subtype = pt["subtype"].get<std::string>();
    // NOTE ON PLACEMENT: this check is deliberately ABOVE the inductance early-return
    // below. It compares two counts and needs no inductance at all, and when it sat under
    // that return it silently skipped 287 of the 485 affected entries - 59 % - because a
    // transformer that records no magnetizing inductance never reached it. That is the
    // ABT #387 failure exactly: a check that examines nothing and reports success. It was
    // caught by measuring the corpus and finding 198 where 485 were expected.
    // CHECK 3b: a transformer that declares secondaries must record a resistance for each
    // winding. dcResistances is documented in MAS as "DC resistance per winding" and
    // turnsRatios as "one entry per secondary", so 1 + turnsRatios.size() windings are
    // claimed by the record itself and a shorter list is missing data — the SECONDARIES'
    // copper, since the array is positional.
    //
    // This fires only when the list is non-empty and short. An ABSENT list is a visible
    // "no DC resistance here"; a SHORT one looks populated and is not, so a loss
    // calculation silently sees a fraction of the copper and gets no warning. 485 rows are
    // in that state (ABT #458), and nothing in the corpus could tell you without comparing
    // the two fields — which is the same shape as ABT #387, where the check that should
    // have noticed was reading the wrong field and reported success.
    if (subtype == "transformer" && pt.contains("turnsRatios") && pt["turnsRatios"].is_array() &&
        !pt["turnsRatios"].empty() && pt.contains("dcResistances") &&
        pt["dcResistances"].is_array() && !pt["dcResistances"].empty()) {
        const double windings = 1.0 + static_cast<double>(pt["turnsRatios"].size());
        const double have = static_cast<double>(pt["dcResistances"].size());
        if (have < windings)
            emit(out, ctx, "MAG_WINDING_DATA_INCOMPLETE", Severity::Suspicious, have, windings,
                 tag + fmt("transformer declares " + std::to_string(pt["turnsRatios"].size()) +
                               " secondary winding(s) but records fewer DC resistances than "
                               "windings [count]",
                           have, windings));
    }


    auto L = scalar_at(pt, {"inductance"});
    if (!L) {
        skipped.push_back("MAG_*");
        return;  // every magnetic check below needs L
    }
    if (*L <= 0) {
        emit(out, ctx, "MAG_L_TOLERANCE", Severity::Impossible, *L, 0,
             tag + "inductance <= 0");
        return;
    }

    // CHECK: inductance magnitude — dimension-free unit-error guard (uH/mH/H).
    // Real wound magnetics top out near ~10 H; nothing in a catalog reaches 100 H.
    if (*L > thr::MAG_L_MAGNITUDE_IMP)
        emit(out, ctx, "MAG_L_MAGNITUDE", Severity::Impossible, *L, thr::MAG_L_MAGNITUDE_IMP,
             tag + fmt("inductance implausibly large (likely uH/H unit error) [H]", *L,
                       thr::MAG_L_MAGNITUDE_IMP));
    else if (*L > thr::MAG_L_MAGNITUDE_SUS)
        emit(out, ctx, "MAG_L_MAGNITUDE", Severity::Suspicious, *L, thr::MAG_L_MAGNITUDE_SUS,
             tag + fmt("inductance very large for a discrete magnetic [H]", *L,
                       thr::MAG_L_MAGNITUDE_SUS));

    // CHECK (anti-synthesis): power-inductor inductance should be an E-series
    // preferred value (gated to L >= 1 uH; sub-uH ferrite beads / RF chip inductors
    // are characterised by impedance, not a preferred L). SUSPICIOUS only.
    if (*L >= 1e-6 && pt.contains("inductance")) {
        const json& lf = pt["inductance"];
        std::optional<double> lnom;
        if (lf.is_number())
            lnom = lf.get<double>();
        else if (lf.is_object() && lf.contains("nominal") && lf["nominal"].is_number())
            lnom = lf["nominal"].get<double>();
        if (lnom && *lnom > 0) {
            if (!eseries::on_grid(*lnom))
                emit(out, ctx, "MAG_E_SERIES", Severity::Suspicious, *lnom, 0,
                     tag + fmt("inductance is not an E-series preferred value [H]", *lnom));
            if (eseries::sig_figs(*lnom) > 4)
                emit(out, ctx, "GEN_OVERPRECISION", Severity::Suspicious, *lnom, 0,
                     tag + fmt("inductance nominal carries more significant figures than a "
                               "preferred value [H]",
                               *lnom));
        }
    }

    auto DCR = read_dcr(pt);
    // When the resistance cannot be tied to the inductance, the three ratio checks below
    // are SKIPPED and say so. Recording the skip is the whole point: a check that quietly
    // examines nothing looks exactly like a check that found nothing wrong, which is the
    // failure mode ABT #387 was raised for.
    if (DCR && !dcr_pairs_with_inductance(pt)) {
        skipped.push_back(tag +
                          "MAG_DCR_GEOM/MAG_DCR_PER_H/MAG_ISAT_POWER: winding resistance is "
                          "not associated with the inductance on a multi-winding part");
        DCR.reset();
    }
    auto Isat = scalar_at(pt, {"saturationCurrentPeak"});
    auto srf = scalar_at(pt, {"selfResonantFrequency"});

    double L_uH = *L * 1e6;

    // --- dimensions / volume ---
    std::optional<double> vol;
    double max_dim = 0;
    if (dims.is_object()) {
        auto l = scalar_at(dims, {"length"});
        auto w = scalar_at(dims, {"width"});
        auto h = scalar_at(dims, {"height"});
        if (l && w) {
            max_dim = std::max(*l, *w);
            if (h) max_dim = std::max(max_dim, *h);
        }
        vol = box_volume_m3(dims);
    }
    // A non-positive dimension is bad data — surface it (box_volume_m3 now returns
    // nullopt instead of throwing, so the other checks still run for this part).
    if (has_nonpositive_dimension(dims))
        emit(out, ctx, "MAG_DIM_NONPOSITIVE", Severity::Suspicious, 0, 0,
             tag + "a mechanical dimension (length/width/height) is <= 0");

    // The DCR geometric/material ratios assume a wound POWER inductor. For
    // ferrite beads and nH-scale RF chip inductors (L < 1 uH, characterised by
    // impedance not L) these ratios inflate naturally, so we cap their severity
    // at Suspicious rather than Impossible — the signal is kept, not silenced.
    const bool power_inductor = (*L >= 1e-6);

    // CHECK 1: DCR x size^2 / L_uH (geometric constraint).
    if (DCR && max_dim > 0) {
        double size_mm = max_dim * 1000.0;
        double ratio = (*DCR) * (size_mm * size_mm) / L_uH;
        if (ratio > thr::MAG_DCR_GEOM_IMP)
            emit(out, ctx, "MAG_DCR_GEOM", power_inductor ? Severity::Impossible : Severity::Suspicious,
                 ratio, thr::MAG_DCR_GEOM_IMP,
                 tag + fmt("DCR*size^2/L impossibly high", ratio, thr::MAG_DCR_GEOM_IMP));
        else if (ratio > thr::MAG_DCR_GEOM_SUS)
            emit(out, ctx, "MAG_DCR_GEOM", Severity::Suspicious, ratio, thr::MAG_DCR_GEOM_SUS,
                 tag + fmt("DCR*size^2/L suspiciously high", ratio, thr::MAG_DCR_GEOM_SUS));
        else if (ratio < thr::MAG_DCR_GEOM_SUS_LOW)
            emit(out, ctx, "MAG_DCR_GEOM", Severity::Suspicious, ratio, thr::MAG_DCR_GEOM_SUS_LOW,
                 tag + fmt("DCR*size^2/L suspiciously low", ratio, thr::MAG_DCR_GEOM_SUS_LOW));
    }

    // CHECK 2: DCR / L bounds (power inductors only for the impossible tier).
    if (DCR && *DCR > 0) {
        double dcr_per_h = *DCR / *L;
        if (dcr_per_h > thr::MAG_DCR_PER_H_IMP && power_inductor)
            emit(out, ctx, "MAG_DCR_PER_H", Severity::Impossible, dcr_per_h, thr::MAG_DCR_PER_H_IMP,
                 tag + fmt("DCR/L impossibly high [ohm/H]", dcr_per_h, thr::MAG_DCR_PER_H_IMP));
        else if (dcr_per_h > thr::MAG_DCR_PER_H_SUS && *L > 1e-6)
            emit(out, ctx, "MAG_DCR_PER_H", Severity::Suspicious, dcr_per_h, thr::MAG_DCR_PER_H_SUS,
                 tag + fmt("DCR/L suspiciously high [ohm/H]", dcr_per_h, thr::MAG_DCR_PER_H_SUS));
    }

    // CHECK 3: Isat^2 * DCR peak dissipation.
    if (Isat && DCR) {
        double p = (*Isat) * (*Isat) * (*DCR);
        if (p > thr::MAG_ISAT_POWER_IMP)
            emit(out, ctx, "MAG_ISAT_POWER", Severity::Impossible, p, thr::MAG_ISAT_POWER_IMP,
                 tag + fmt("Isat^2*DCR impossibly high [W]", p, thr::MAG_ISAT_POWER_IMP));
        else if (p > thr::MAG_ISAT_POWER_SUS)
            emit(out, ctx, "MAG_ISAT_POWER", Severity::Suspicious, p, thr::MAG_ISAT_POWER_SUS,
                 tag + fmt("Isat^2*DCR suspiciously high [W]", p, thr::MAG_ISAT_POWER_SUS));
    }

    // CHECK 4: SRF sanity + SRF*sqrt(L) parasitic-resonance bound. srf<=0 is a
    // placeholder (skip, not impossible); the band is (1 kHz, 1e11 Hz]. The IMP
    // tier on SRF*sqrt(L) is gated to L>1nH (mirrors the SUS branch) so tiny
    // high-SRF chip beads / common-mode chokes are not wrongly invalidated.
    if (srf) {
        if (*srf <= 0) {
            skipped.push_back("MAG_SRF_SANE");
        } else if (*srf < thr::MAG_SRF_FLOOR_HZ || *srf > thr::MAG_SRF_CEIL_HZ) {
            emit(out, ctx, "MAG_SRF_SANE", Severity::Impossible, *srf, thr::MAG_SRF_FLOOR_HZ,
                 tag + fmt("self-resonant frequency outside (1 kHz, 1e11 Hz]", *srf));
        }
        if (*srf > 0) {
            double prod = (*srf) * std::sqrt(*L);
            if (prod > thr::MAG_SRF_L_IMP && *L > 1e-9)
                emit(out, ctx, "MAG_SRF_L", Severity::Impossible, prod, thr::MAG_SRF_L_IMP,
                     tag + fmt("SRF*sqrt(L) impossibly high", prod, thr::MAG_SRF_L_IMP));
            else if (prod > thr::MAG_SRF_L_SUS && *L > 1e-9)
                emit(out, ctx, "MAG_SRF_L", Severity::Suspicious, prod, thr::MAG_SRF_L_SUS,
                     tag + fmt("SRF*sqrt(L) suspiciously high", prod, thr::MAG_SRF_L_SUS));
        }
    }

    // CHECK 5 (NEW): stored-energy density E = 1/2 L Isat^2 over device volume.
    if (Isat && vol && *vol > 0) {
        double energy = 0.5 * (*L) * (*Isat) * (*Isat);
        double density = energy / *vol;  // J/m^3
        double sus = tech_has(material, "powder") || tech_has(material, "metal") ||
                             tech_has(material, "alloy") || tech_has(material, "iron")
                         ? thr::MAG_ENERGY_DENSITY_SUS_POWDER
                         : thr::MAG_ENERGY_DENSITY_SUS_FERRITE;
        if (density > thr::MAG_ENERGY_DENSITY_IMP)
            emit(out, ctx, "MAG_ENERGY_DENSITY", Severity::Impossible, density,
                 thr::MAG_ENERGY_DENSITY_IMP,
                 tag + fmt("1/2 L Isat^2 / volume exceeds any magnetic material [J/m^3]", density,
                           thr::MAG_ENERGY_DENSITY_IMP));
        else if (density > sus)
            emit(out, ctx, "MAG_ENERGY_DENSITY", Severity::Suspicious, density, sus,
                 tag + fmt("stored-energy density high for material [J/m^3]", density, sus));
    }

    // CHECK 6 (NEW): inductance tolerance ordering / band width.
    if (pt.contains("inductance") && pt["inductance"].is_object()) {
        const json& ind = pt["inductance"];
        auto nom = scalar_at(ind, {"nominal"});
        auto mn = scalar_at(ind, {"minimum"});
        auto mx = scalar_at(ind, {"maximum"});
        if (nom && mn && *mn > *nom)
            emit(out, ctx, "MAG_L_TOLERANCE", Severity::Impossible, *mn, *nom,
                 tag + "inductance minimum > nominal");
        if (nom && mx && *mx < *nom)
            emit(out, ctx, "MAG_L_TOLERANCE", Severity::Impossible, *mx, *nom,
                 tag + "inductance maximum < nominal");
        if (mn && mx && *mn > 0 && (*mx / *mn) > thr::MAG_L_TOL_RATIO_SUS)
            emit(out, ctx, "MAG_L_TOLERANCE", Severity::Suspicious, *mx / *mn,
                 thr::MAG_L_TOL_RATIO_SUS, tag + "inductance tolerance band very wide");
    }

    // CHECK 7: rated current vs saturation current. ratedCurrents elements are
    // bare numbers in the live catalog (or {rms|current} objects). ~20% of real
    // parts legitimately have rated>Isat (RMS-thermal vs peak L-drop spec), so
    // only a gross ratio is a unit error.
    if (Isat && *Isat > 0 && pt.contains("ratedCurrents") && pt["ratedCurrents"].is_array()) {
        for (const auto& rc : pt["ratedCurrents"]) {
            std::optional<double> irms;
            if (rc.is_number()) irms = scalar(&rc, "ratedCurrents[]");
            else { irms = scalar_at(rc, {"rms"}); if (!irms) irms = scalar_at(rc, {"current"}); }
            if (!irms || *irms <= 0) continue;
            double r = *irms / *Isat;
            if (r > thr::MAG_RATED_ISAT_IMP)
                emit(out, ctx, "MAG_RATED_LE_SAT", Severity::Impossible, *irms, *Isat,
                     tag + fmt("rated current grossly exceeds saturation current (unit error?)",
                               *irms, *Isat));
            else if (r > thr::MAG_RATED_ISAT_SUS)
                emit(out, ctx, "MAG_RATED_LE_SAT", Severity::Suspicious, *irms, *Isat,
                     tag + fmt("rated current exceeds saturation current", *irms, *Isat));
        }
    }
}

// MAG_DISS_DENSITY: DCR x Irated^2 over the package's box surface area. One
// symptom, many causes — the ABT #351 campaign found five distinct corruption
// classes behind an absurd DCR*I^2 (identifier-in-field, impedance-as-DCR,
// mA-as-A, field-copied, series stubs) totalling ~1,000 rows that every
// existing window passed, because none of them relates the DCR, the rated
// current and the SIZE to each other. The same campaign also proved three
// FALSE-POSITIVE classes the product is meaningless for, excluded here:
//   F1 current-sense parts — rated current is the PRIMARY current, the DCR a
//      winding resistance; their product is not a dissipation.
//   F2 Isat-quoted molded parts — a very low DCR with a very high current is a
//      saturation rating, not a thermal claim.
//   F3 chip beads — vendor datasheets pair IR (dT=40K) with a NON-simultaneous
//      small-signal RDC max (WE 7427920: 9600 mA next to 0.15 ohm), so I^2*R is
//      not what the part dissipates at rating.
// Reads the winding resistance through read_dcr(), which handles both the singular
// and the common-mode-choke shape. This check was written that way from the start,
// and the older MAG_* checks have since been brought to the same reader (ABT #387).
void check_dissipation_density(const json& pt, int idx, const json& dims,
                               const std::string& desc_lower, const Ctx& ctx,
                               std::vector<Finding>& out) {
    const std::string tag = "[op " + std::to_string(idx) + "] ";

    std::string subtype;
    if (pt.contains("subtype") && pt["subtype"].is_string())
        subtype = pt["subtype"].get<std::string>();

    // F1 / F3 — the product is not a physical quantity for these part classes.
    if (subtype == "transformer" || subtype == "chipBead") return;
    if (desc_lower.find("current sense") != std::string::npos ||
        desc_lower.find("current-sense") != std::string::npos ||
        desc_lower.find("current transformer") != std::string::npos ||
        desc_lower.find("bead") != std::string::npos)
        return;

    auto dcr = read_dcr(pt);
    if (!dcr || *dcr <= 0) return;

    std::optional<double> rated;
    if (pt.contains("ratedCurrents") && pt["ratedCurrents"].is_array() &&
        !pt["ratedCurrents"].empty()) {
        const json& rc = pt["ratedCurrents"][0];
        if (rc.is_number()) rated = scalar(&rc, "ratedCurrents[0]");
        else { rated = scalar_at(rc, {"rms"}); if (!rated) rated = scalar_at(rc, {"current"}); }
    }
    if (!rated || *rated <= 0) return;

    // F2 — a milliohm-class part quoting tens of amps is quoting saturation.
    if (*dcr <= 0.01 && *rated >= 50.0) return;

    double watts = (*dcr) * (*rated) * (*rated);
    if (watts <= thr::MAG_DISS_POWER_FLOOR_W) return;   // pad conduction dominates

    auto l = scalar_at(dims, {"length"});
    auto w = scalar_at(dims, {"width"});
    if (!l || !w || *l <= 0 || *w <= 0) return;
    auto h = scalar_at(dims, {"height"});
    double hh = (h && *h > 0) ? *h : std::min(*l, *w);
    double area_cm2 = 2.0 * ((*l) * (*w) + (*l) * hh + (*w) * hh) * 1e4;
    if (area_cm2 <= 0) return;
    double density = watts / area_cm2;

    if (density > thr::MAG_DISS_DENSITY_IMP)
        emit(out, ctx, "MAG_DISS_DENSITY", Severity::Impossible, density,
             thr::MAG_DISS_DENSITY_IMP,
             tag + fmt("DCR*Irated^2 per package surface impossibly high [W/cm^2]",
                       density, thr::MAG_DISS_DENSITY_IMP));
    else if (density > thr::MAG_DISS_DENSITY_SUS)
        emit(out, ctx, "MAG_DISS_DENSITY", Severity::Suspicious, density,
             thr::MAG_DISS_DENSITY_SUS,
             tag + fmt("DCR*Irated^2 per package surface suspiciously high [W/cm^2]",
                       density, thr::MAG_DISS_DENSITY_SUS));
}

// MAG_SUBTYPE_MISMATCH: the description names a specific magnetic variant but no
// electrical entry declares that subtype. GEN_FAMILY_MISMATCH one level down:
// physics bounds cannot see taxonomy — a common-mode choke's numbers are legal
// *inductor* numbers, which is how 2,635 mistagged CMCs sailed through every
// window (ABT #279) — so the description noun is the only in-record witness.
// SUSPICIOUS only; a matching subtype on ANY electrical entry clears it (multi-
// wiring parts list one entry per variant).
void check_subtype_coherence(const json& datasheet, const json& elec, const Ctx& ctx,
                             std::vector<Finding>& out) {
    const json* d = at(datasheet, "part", "description");
    if (d == nullptr || !d->is_string()) return;
    std::string desc;
    for (char c : d->get<std::string>())
        desc += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    auto has = [&](const char* s) { return desc.find(s) != std::string::npos; };

    const char* expected = nullptr;
    const char* noun = nullptr;
    if ((has("common mode") || has("common-mode")) && (has("choke") || has("filter"))) {
        expected = "commonModeChoke";
        noun = "common-mode choke/filter";
    } else if (has("bead")) {
        expected = "chipBead";
        noun = "bead";
    } else if (has("transformer")) {
        expected = "transformer";
        noun = "transformer";
    }
    if (expected == nullptr) return;

    std::string found = "(none)";
    for (const auto& pt : elec) {
        if (!pt.is_object()) continue;
        auto it = pt.find("subtype");
        if (it != pt.end() && it->is_string()) {
            if (it->get<std::string>() == expected) return;
            found = it->get<std::string>();
        }
    }
    emit(out, ctx, "MAG_SUBTYPE_MISMATCH", Severity::Suspicious, 0, 0,
         std::string("description names a ") + noun + " but no electrical entry has subtype '" +
             expected + "' (found '" + found + "')");
}

}  // namespace

void check_magnetics(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                     std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("MAG_*");
        return;
    }
    if (!elec->is_array())
        throw MalformedField("magnetic.datasheetInfo.electrical: expected array of op-points");

    check_subtype_coherence(datasheet, *elec, ctx, out);

    const json* mech = at(datasheet, "mechanical");
    const json dims = (mech && mech->is_object()) ? *mech : json::object();
    std::string material = norm_tech(at(datasheet, "part", "material"));

    std::string desc_lower;
    if (const json* d = at(datasheet, "part", "description"); d != nullptr && d->is_string())
        for (char c : d->get<std::string>())
            desc_lower += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));

    int idx = 0;
    for (const auto& pt : *elec) {
        if (pt.is_object()) {
            check_point(pt, idx, dims, material, ctx, out, skipped);
            check_dissipation_density(pt, idx, dims, desc_lower, ctx, out);
        }
        ++idx;
    }
}

}  // namespace tas
