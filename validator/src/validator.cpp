// SPDX-License-Identifier: Apache-2.0
#include "tas_validator/validator.hpp"

#include "tas_validator/helpers.hpp"

#include <stdexcept>

namespace tas {

const char* to_string(Severity s) {
    switch (s) {
        case Severity::Ok: return "OK";
        case Severity::Suspicious: return "SUSPICIOUS";
        case Severity::Impossible: return "IMPOSSIBLE";
    }
    return "OK";
}

namespace {

// Generic checks applicable to every family, run on the datasheetInfo object.
void check_generic(const json& ds, const Ctx& ctx, std::vector<Finding>& out) {
    // GEN_TEMP_ORDER: any thermal min/max pair where min >= max.
    const json* thermal = at(ds, "thermal");
    if (thermal && thermal->is_object()) {
        for (auto it = thermal->begin(); it != thermal->end(); ++it) {
            const json& v = it.value();
            if (v.is_object() && v.contains("minimum") && v.contains("maximum") &&
                v["minimum"].is_number() && v["maximum"].is_number()) {
                double mn = v["minimum"].get<double>();
                double mx = v["maximum"].get<double>();
                if (mn >= mx)
                    emit(out, ctx, "GEN_TEMP_ORDER", Severity::Impossible, mn, mx,
                         "thermal." + it.key() + " minimum >= maximum");
            }
        }
    }
}

// Resolve the datasheetInfo object and a part reference for a discriminator.
struct Resolved {
    const json* datasheet = nullptr;
    std::string reference;
};

Resolved resolve(const json& component_obj) {
    Resolved r;
    if (const json* mi = at(component_obj, "manufacturerInfo")) {
        if (mi->is_object() && mi->contains("reference") && (*mi)["reference"].is_string())
            r.reference = (*mi)["reference"].get<std::string>();
        r.datasheet = at(*mi, "datasheetInfo");
    }
    return r;
}

}  // namespace

Verdict PartValidator::validate(const json& part) const {
    if (!part.is_object())
        throw std::invalid_argument("part record is not a JSON object");

    Verdict v;
    Ctx ctx;

    auto run = [&](const std::string& component, const json& comp_obj,
                   void (*fn)(const json&, const Ctx&, std::vector<Finding>&,
                              std::vector<std::string>&)) {
        Resolved r = resolve(comp_obj);
        if (r.datasheet == nullptr) {
            v.skipped.push_back(component + ":no-datasheetInfo");
            return;
        }
        ctx.component = component;
        ctx.reference = r.reference;
        check_generic(*r.datasheet, ctx, v.findings);
        fn(*r.datasheet, ctx, v.findings, v.skipped);
    };

    if (part.contains("magnetic")) {
        run("magnetic", part["magnetic"], &check_magnetics);
    } else if (part.contains("capacitor")) {
        run("capacitor", part["capacitor"], &check_capacitors);
    } else if (part.contains("resistor")) {
        run("resistor", part["resistor"], &check_resistors);
    } else if (part.contains("semiconductor")) {
        const json& semi = part["semiconductor"];
        if (semi.contains("mosfet")) run("mosfet", semi["mosfet"], &check_mosfets);
        else if (semi.contains("diode")) run("diode", semi["diode"], &check_diodes);
        else if (semi.contains("igbt")) run("igbt", semi["igbt"], &check_igbts);
        else
            throw std::invalid_argument(
                "semiconductor record has no mosfet/diode/igbt sub-object");
    } else {
        throw std::invalid_argument(
            "no known component discriminator (magnetic/capacitor/resistor/semiconductor)");
    }

    for (const auto& f : v.findings)
        if (f.severity == Severity::Impossible) v.valid = false;

    return v;
}

Verdict PartValidator::validate_json(const std::string& text) const {
    return validate(json::parse(text));
}

std::vector<std::string> PartValidator::check_codes() {
    return {
        "GEN_TEMP_ORDER",
        // magnetics
        "MAG_DCR_GEOM", "MAG_DCR_PER_H", "MAG_ISAT_POWER", "MAG_SRF_L", "MAG_SRF_SANE",
        "MAG_ENERGY_DENSITY", "MAG_L_TOLERANCE", "MAG_L_MAGNITUDE", "MAG_RATED_LE_SAT",
        // capacitors
        "CAP_POSITIVITY", "CAP_MAGNITUDE", "CAP_TOLERANCE", "CAP_DF_BOUNDS", "CAP_ESR_C",
        "CAP_ENERGY_DENSITY", "CAP_LEAKAGE_CV", "CAP_INSULATION_RC",
        // resistors
        "RES_R_RANGE", "RES_POWER_SIZE", "RES_MAXV_SIZE", "RES_POWER_V_R", "RES_TEMPCO",
        "RES_TOLERANCE",
        // mosfets
        "MOS_CAP_HIERARCHY", "MOS_CHARGE_HIERARCHY", "MOS_VTH_WINDOW", "MOS_VGS_VS_VTH",
        "MOS_BODY_DIODE_VF", "MOS_POWER_THERMAL", "MOS_RON_FLOOR",
        // diodes
        "DIO_POSITIVITY", "DIO_VF_RANGE", "DIO_SURGE_VS_IF", "DIO_VF_POWER", "DIO_QRR_SCHOTTKY",
        "DIO_CJ_VR",
        // igbts
        "IGBT_POSITIVITY", "IGBT_VCESAT_RANGE", "IGBT_VCESAT_VS_VCES",
    };
}

}  // namespace tas
