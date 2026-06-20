// SPDX-License-Identifier: Apache-2.0
// Magnetics physics checks. `datasheet` is the datasheetInfo object; its
// `electrical` member is an ARRAY of operating points (inductor/transformer
// windings), so every check runs per op-point and tags findings with the index.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
#include <sstream>
#include <string>

namespace tas {
namespace {

std::string fmt(const std::string& s, double a, double b = 0) {
    std::ostringstream os;
    os << s << " (value=" << a;
    if (b != 0) os << ", threshold=" << b;
    os << ")";
    return os.str();
}

void check_point(const json& pt, int idx, const json& dims, const std::string& material,
                 const Ctx& ctx, std::vector<Finding>& out, std::vector<std::string>& skipped) {
    const std::string tag = "[op " + std::to_string(idx) + "] ";
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

    auto DCR = scalar_at(pt, {"dcResistance"});
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

    // CHECK 4: SRF * sqrt(L), plus SRF sanity floor.
    if (srf) {
        if (*srf <= 0 || *srf < thr::MAG_SRF_FLOOR_HZ)
            emit(out, ctx, "MAG_SRF_SANE", Severity::Impossible, *srf, thr::MAG_SRF_FLOOR_HZ,
                 tag + fmt("self-resonant frequency below 1 kHz / non-positive", *srf));
        double prod = (*srf) * std::sqrt(*L);
        if (prod > thr::MAG_SRF_L_IMP)
            emit(out, ctx, "MAG_SRF_L", Severity::Impossible, prod, thr::MAG_SRF_L_IMP,
                 tag + fmt("SRF*sqrt(L) impossibly high", prod, thr::MAG_SRF_L_IMP));
        else if (prod > thr::MAG_SRF_L_SUS && *L > 1e-9)
            emit(out, ctx, "MAG_SRF_L", Severity::Suspicious, prod, thr::MAG_SRF_L_SUS,
                 tag + fmt("SRF*sqrt(L) suspiciously high", prod, thr::MAG_SRF_L_SUS));
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

    // CHECK 7 (NEW): rated current must not exceed saturation current.
    if (Isat && pt.contains("ratedCurrents") && pt["ratedCurrents"].is_array()) {
        for (const auto& rc : pt["ratedCurrents"]) {
            auto irms = scalar_at(rc, {"rms"});
            if (!irms) irms = scalar_at(rc, {"current"});
            if (irms && *irms > *Isat)
                emit(out, ctx, "MAG_RATED_LE_SAT", Severity::Suspicious, *irms, *Isat,
                     tag + fmt("rated current exceeds saturation current", *irms, *Isat));
        }
    }
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

    const json* mech = at(datasheet, "mechanical");
    const json dims = (mech && mech->is_object()) ? *mech : json::object();
    std::string material = norm_tech(at(datasheet, "part", "material"));

    int idx = 0;
    for (const auto& pt : *elec) {
        if (pt.is_object()) check_point(pt, idx, dims, material, ctx, out, skipped);
        ++idx;
    }
}

}  // namespace tas
