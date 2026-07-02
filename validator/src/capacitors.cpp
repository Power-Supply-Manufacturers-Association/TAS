// SPDX-License-Identifier: MIT
// Capacitor physics checks. `datasheet` is the datasheetInfo object:
//   electrical.{capacitance,ratedVoltage,dissipationFactor,esr,leakageCurrent,
//               insulationResistance}, part.technology,
//   mechanical.shape.volume (or mechanical.dimensions).
#include "tas_validator/eseries.hpp"
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <sstream>
#include <string>

namespace tas {
namespace {

// Dissipation-factor ceiling for a normalised technology token.
double df_ceiling(const std::string& t) {
    // Ceramic class-1 (C0G/NP0). Cover NP0-with-zero and "Class 1"/"Class I" forms
    // so a class-1 part is not given the looser X7R ceiling.
    if (tech_has(t, "npo") || tech_has(t, "np0") || tech_has(t, "c0g") ||
        tech_has(t, "classi") || tech_has(t, "class1"))
        return thr::CAP_DF_CERAMIC_NPO;
    if (tech_has(t, "y5v") || tech_has(t, "z5u")) return thr::CAP_DF_CERAMIC_Y5V;
    if (tech_has(t, "x7r") || tech_has(t, "x5r") || tech_has(t, "mlcc") || tech_has(t, "ceramic"))
        return thr::CAP_DF_CERAMIC_X7R;
    if (tech_has(t, "tantalum")) return thr::CAP_DF_TANTALUM;
    if (tech_has(t, "polymer")) return thr::CAP_DF_POLYMER;
    if (tech_has(t, "electrolytic") || tech_has(t, "alum")) return thr::CAP_DF_ELECTROLYTIC;
    if (tech_has(t, "film")) return thr::CAP_DF_FILM;
    return thr::CAP_DF_DEFAULT;
}

// Energy-density suspicious ceiling for a normalised technology token [J/m^3].
double energy_density_ceiling(const std::string& t) {
    if (tech_has(t, "tantalum")) return thr::CAP_ENERGY_DENSITY_SUS_TANT;
    if (tech_has(t, "electrolytic") || tech_has(t, "alum")) return thr::CAP_ENERGY_DENSITY_SUS_ALUM;
    if (tech_has(t, "film")) return thr::CAP_ENERGY_DENSITY_SUS_FILM;
    if (tech_has(t, "ceramic") || tech_has(t, "mlcc")) return thr::CAP_ENERGY_DENSITY_SUS_CERAMIC;
    return thr::CAP_ENERGY_DENSITY_IMP;  // unknown tech: only the hard ceiling applies
}

std::optional<double> cap_volume_m3(const json& datasheet) {
    // Preferred: explicit mechanical.shape.volume (seen in real records).
    if (auto v = scalar_at(datasheet, {"mechanical", "shape", "volume"})) return v;
    // Fallback: box from mechanical.dimensions {length,width,height}.
    if (const json* d = at(datasheet, "mechanical", "dimensions"))
        if (d->is_object()) return box_volume_m3(*d);
    return std::nullopt;
}

}  // namespace

void check_capacitors(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                      std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("CAP_*");
        return;
    }
    auto C = scalar_at(*elec, {"capacitance"});
    auto V = scalar_at(*elec, {"ratedVoltage"});
    std::string tech = norm_tech(at(datasheet, "part", "technology"));

    // CHECK: positivity.
    if (C && *C <= 0)
        emit(out, ctx, "CAP_POSITIVITY", Severity::Impossible, *C, 0, "capacitance <= 0");
    if (V && *V <= 0)
        emit(out, ctx, "CAP_POSITIVITY", Severity::Impossible, *V, 0, "ratedVoltage <= 0");

    // CHECK: capacitance magnitude — dimension-free unit-error guard. Only
    // supercapacitors exceed ~1 F, so a 100 F MLCC/electrolytic is a uF/F slip.
    if (C && *C > 0) {
        bool super = tech_has(tech, "supercapacitor") || tech_has(tech, "edlc");
        double imp = super ? thr::CAP_MAGNITUDE_SUPER_IMP : thr::CAP_MAGNITUDE_IMP;
        double sus = super ? thr::CAP_MAGNITUDE_SUPER_SUS : thr::CAP_MAGNITUDE_SUS;
        if (*C > imp)
            emit(out, ctx, "CAP_MAGNITUDE", Severity::Impossible, *C, imp,
                 fmt("capacitance implausibly large (likely uF/F unit error) [F]", *C, imp));
        else if (*C > sus)
            emit(out, ctx, "CAP_MAGNITUDE", Severity::Suspicious, *C, sus,
                 fmt("capacitance large for a non-supercapacitor [F]", *C, sus));
    }

    // CHECK: capacitance tolerance ordering.
    if (elec->contains("capacitance") && (*elec)["capacitance"].is_object()) {
        const json& cc = (*elec)["capacitance"];
        auto nom = scalar_at(cc, {"nominal"});
        auto mn = scalar_at(cc, {"minimum"});
        auto mx = scalar_at(cc, {"maximum"});
        if (nom && mn && *mn > *nom)
            emit(out, ctx, "CAP_TOLERANCE", Severity::Impossible, *mn, *nom, "capacitance minimum > nominal");
        if (nom && mx && *mx < *nom)
            emit(out, ctx, "CAP_TOLERANCE", Severity::Impossible, *mx, *nom, "capacitance maximum < nominal");
    }

    // CHECK: dissipation factor bounds. DF>=1 is physically possible for cold/HF
    // aluminum electrolytics, so it is SUSPICIOUS (not IMPOSSIBLE); only DF<0 or a
    // grossly-large DF is impossible.
    if (auto df = scalar_at(*elec, {"dissipationFactor"})) {
        if (*df < 0)
            emit(out, ctx, "CAP_DF_BOUNDS", Severity::Impossible, *df, 0,
                 fmt("dissipation factor < 0", *df));
        else if (*df >= 10.0)
            emit(out, ctx, "CAP_DF_BOUNDS", Severity::Impossible, *df, 10.0,
                 fmt("dissipation factor implausibly large", *df, 10.0));
        else if (*df >= 1.0)
            emit(out, ctx, "CAP_DF_BOUNDS", Severity::Suspicious, *df, 1.0,
                 fmt("dissipation factor >= 1 (only cold/HF electrolytics)", *df, 1.0));
        else {
            double ceil = df_ceiling(tech);
            if (*df > ceil)
                emit(out, ctx, "CAP_DF_BOUNDS", Severity::Suspicious, *df, ceil,
                     fmt("dissipation factor high for dielectric", *df, ceil));
        }
    } else {
        skipped.push_back("CAP_DF_BOUNDS");
    }

    // CHECK (NEW): stored-energy density 1/2 C V^2 / volume.
    if (C && V && *C > 0 && *V > 0) {
        if (auto vol = cap_volume_m3(datasheet)) {
            if (*vol > 0) {
                double density = 0.5 * (*C) * (*V) * (*V) / *vol;  // J/m^3
                double sus = energy_density_ceiling(tech);
                if (density > thr::CAP_ENERGY_DENSITY_IMP)
                    emit(out, ctx, "CAP_ENERGY_DENSITY", Severity::Impossible, density,
                         thr::CAP_ENERGY_DENSITY_IMP,
                         fmt("1/2 C V^2 / volume exceeds any capacitor technology [J/m^3]", density,
                             thr::CAP_ENERGY_DENSITY_IMP));
                else if (density > sus)
                    emit(out, ctx, "CAP_ENERGY_DENSITY", Severity::Suspicious, density, sus,
                         fmt("energy density high for technology [J/m^3]", density, sus));
            }
        } else {
            skipped.push_back("CAP_ENERGY_DENSITY");
        }
    }

    // CHECK (NEW): leakage current vs C*V (charge bled per second).
    if (C && V && *C > 0 && *V > 0) {
        if (auto leak = scalar_at(*elec, {"leakageCurrent"})) {
            if (*leak < 0) {
                emit(out, ctx, "CAP_POSITIVITY", Severity::Impossible, *leak, 0,
                     "leakageCurrent < 0");
            }
            double per_cv = *leak / (*C * *V);  // 1/s
            if (per_cv > thr::CAP_LEAKAGE_PER_CV_IMP)
                emit(out, ctx, "CAP_LEAKAGE_CV", Severity::Impossible, per_cv,
                     thr::CAP_LEAKAGE_PER_CV_IMP,
                     fmt("leakage / (C*V) physically impossible [1/s]", per_cv,
                         thr::CAP_LEAKAGE_PER_CV_IMP));
            else if (per_cv > thr::CAP_LEAKAGE_PER_CV_SUS)
                emit(out, ctx, "CAP_LEAKAGE_CV", Severity::Suspicious, per_cv,
                     thr::CAP_LEAKAGE_PER_CV_SUS, fmt("leakage high for C*V [1/s]", per_cv,
                                                      thr::CAP_LEAKAGE_PER_CV_SUS));
        }
    }

    // CHECK: ESR sanity (positivity + ESR*C time-constant upper bound).
    if (auto esr = scalar_at(*elec, {"esr"})) {
        if (*esr < 0)
            emit(out, ctx, "CAP_ESR_C", Severity::Impossible, *esr, 0, "ESR < 0");
        else if (C && *C > 0) {
            double tau = *esr * *C;  // seconds; even bulk electrolytics stay well under 1 s
            if (tau > 1.0)
                emit(out, ctx, "CAP_ESR_C", Severity::Suspicious, tau, 1.0,
                     fmt("ESR*C suspiciously high [s]", tau, 1.0));
        }
    }

    // CHECK (NEW): insulation time constant Riso*C. The low bound applies only to
    // BULK caps (C > 1 uF); ceramics legitimately compute sub-second RC. A negative
    // insulation resistance is impossible.
    if (C && *C > 0) {
        if (auto riso = scalar_at(*elec, {"insulationResistance"})) {
            if (*riso < 0)
                emit(out, ctx, "CAP_POSITIVITY", Severity::Impossible, *riso, 0,
                     "insulationResistance < 0");
            double rc = *riso * *C;  // seconds
            if (rc >= 0 && *C > thr::CAP_RC_GATE_FARAD && rc < thr::CAP_RC_SECONDS_SUS_LOW)
                emit(out, ctx, "CAP_INSULATION_RC", Severity::Suspicious, rc,
                     thr::CAP_RC_SECONDS_SUS_LOW, fmt("Riso*C suspiciously short [s]", rc,
                                                      thr::CAP_RC_SECONDS_SUS_LOW));
            else if (rc > thr::CAP_RC_SECONDS_SUS_HIGH)
                emit(out, ctx, "CAP_INSULATION_RC", Severity::Suspicious, rc,
                     thr::CAP_RC_SECONDS_SUS_HIGH, fmt("Riso*C suspiciously long [s]", rc,
                                                       thr::CAP_RC_SECONDS_SUS_HIGH));
        }
    }

    // CHECK (NEW, anti-synthesis): the nominal capacitance should land on an IEC
    // 60063 E-series preferred value, and not be over-precise. SUSPICIOUS only —
    // a real-vs-fabricated signal, not a physics bound.
    if (const json* cf = at(*elec, "capacitance")) {
        std::optional<double> cnom;
        if (cf->is_number())
            cnom = cf->get<double>();
        else if (cf->is_object() && cf->contains("nominal") && (*cf)["nominal"].is_number())
            cnom = (*cf)["nominal"].get<double>();
        if (cnom && *cnom > 0) {
            if (!eseries::on_grid(*cnom))
                emit(out, ctx, "CAP_E_SERIES", Severity::Suspicious, *cnom, 0,
                     fmt("capacitance is not an IEC 60063 E-series preferred value [F]", *cnom));
            if (eseries::sig_figs(*cnom) > 4)
                emit(out, ctx, "GEN_OVERPRECISION", Severity::Suspicious, *cnom, 0,
                     fmt("capacitance nominal carries more significant figures than a preferred "
                         "value [F]",
                         *cnom));
        }
    }
}

}  // namespace tas
