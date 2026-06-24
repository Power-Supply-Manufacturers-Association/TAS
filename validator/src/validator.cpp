// SPDX-License-Identifier: Apache-2.0
#include "tas_validator/validator.hpp"

#include "tas_validator/helpers.hpp"

#include <cctype>
#include <map>
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

// Foreign-component-noun contamination: a record filed under one family whose
// human description names a DIFFERENT component family (e.g. an inductor filed as a
// connector — real contamination that every physics bound passes). SUSPICIOUS only.
void check_family_coherence(const json& ds, const Ctx& ctx, std::vector<Finding>& out) {
    const json* d = at(ds, "part", "description");
    if (d == nullptr || !d->is_string()) return;
    std::string desc;
    for (char c : d->get<std::string>())
        desc += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    auto group = [](const std::string& c) -> std::string {
        if (c == "mosfet" || c == "diode" || c == "igbt" || c == "bjt") return "semiconductor";
        return c;
    };
    const std::string g = group(ctx.component);
    struct Noun { const char* noun; const char* fam; };
    static const Noun NOUNS[] = {
        {"inductor", "magnetic"},   {"transformer", "magnetic"}, {"choke", "magnetic"},
        {"capacitor", "capacitor"}, {"resistor", "resistor"},    {"varistor", "varistor"},
        {"connector", "connector"}, {"mosfet", "semiconductor"}, {"transistor", "semiconductor"},
    };
    auto has = [&](const char* s) { return desc.find(s) != std::string::npos; };
    // If the own-family noun appears, trust the discriminator (no mismatch).
    for (const auto& n : NOUNS)
        if (g == n.fam && has(n.noun)) return;
    for (const auto& n : NOUNS)
        if (g != n.fam && has(n.noun)) {
            emit(out, ctx, "GEN_FAMILY_MISMATCH", Severity::Suspicious, 0, 0,
                 "description names a '" + std::string(n.noun) +
                     "' but the record is filed as " + ctx.component);
            return;
        }
}

// Generic checks applicable to every family, run on the datasheetInfo object.
void check_generic(const json& ds, const Ctx& ctx, std::vector<Finding>& out) {
    // GEN_TEMP_ORDER: a temperature min/max pair where min > max. Restricted to
    // genuine temperature keys (min==max is a valid single point; non-temperature
    // thermal sub-objects like tcc / temperatureRise are not ranges).
    const json* thermal = at(ds, "thermal");
    if (thermal && thermal->is_object()) {
        for (auto it = thermal->begin(); it != thermal->end(); ++it) {
            if (it.key().find("emperature") == std::string::npos) continue;
            const json& v = it.value();
            if (v.is_object() && v.contains("minimum") && v.contains("maximum") &&
                v["minimum"].is_number() && v["maximum"].is_number()) {
                double mn = v["minimum"].get<double>();
                double mx = v["maximum"].get<double>();
                if (mn > mx)
                    emit(out, ctx, "GEN_TEMP_ORDER", Severity::Impossible, mn, mx,
                         "thermal." + it.key() + " minimum > maximum");
            }
        }
    }

    // GEN_PROVENANCE_MISSING: every PEAS child must carry a data-provenance trail
    // (datasheetInfo.provenance). A missing/empty trail means the data's origin is
    // untracked — a warning, not a physics violation.
    const json* prov = at(ds, "provenance");
    if (prov == nullptr || !prov->is_array() || prov->empty())
        emit(out, ctx, "GEN_PROVENANCE_MISSING", Severity::Suspicious, 0, 0,
             "datasheetInfo.provenance is not set — data origin is untracked");

    check_family_coherence(ds, ctx, out);
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

// Per-family core-field manifest: the electrical fields a real datasheet of this
// family always (or nearly always) carries, curated from live-catalog field-
// presence statistics (2026-06-24). completeness = fraction present; a record well
// below the floor is sparse — the signature of a near-empty fabricated record.
const std::vector<std::string>* core_fields(const std::string& c) {
    static const std::map<std::string, std::vector<std::string>> M = {
        {"magnetic", {"inductance", "dcResistance"}},
        {"capacitor", {"capacitance", "ratedVoltage"}},
        {"resistor", {"resistance", "powerRating", "tolerance"}},
        {"mosfet",
         {"onResistance", "drainSourceVoltage", "continuousDrainCurrent", "gateThresholdVoltage"}},
        // Diodes are intentionally omitted: the subtypes (rectifier/Schottky/TVS/
        // Zener/ESD) carry disjoint field sets, so no single core manifest fits —
        // a fraction-of-core score false-flags ~45% of real parts. Completeness is
        // not scored for diodes (returns -1).
        {"igbt",
         {"collectorEmitterVoltage", "collectorEmitterSaturation", "continuousCollectorCurrent"}},
        {"bjt", {"collectorEmitterVoltage", "collectorCurrent"}},
        {"varistor", {"varistorVoltage", "clampingVoltage", "peakSurgeCurrent"}},
        {"connector", {"ratedVoltage", "ratedCurrentPerContact"}},
    };
    auto it = M.find(c);
    return it == M.end() ? nullptr : &it->second;
}

// Per-family GEN_SPARSE floor, set safely below each family's measured real-part
// minimum completeness (magnetics bottoms at 0.50, igbt at 0.67; every other family
// is always 1.0 — so their floor can be high enough to catch a record missing even
// one core field). A record below the floor is too sparse to be a real part.
double sparse_floor(const std::string& c) {
    if (c == "magnetic") return 0.40;  // real-part min ~0.50
    if (c == "igbt") return 0.50;      // real-part min ~0.67
    return 0.60;                       // cap / res / mosfet / varistor / connector (real min 1.0)
}

// Fraction of the family's core fields present in datasheetInfo.electrical (or
// electrical[0] for the magnetics array). Returns -1 if no manifest exists.
double compute_completeness(const std::string& component, const json& datasheet) {
    const std::vector<std::string>* core = core_fields(component);
    if (core == nullptr || core->empty()) return -1.0;
    const json* elec = at(datasheet, "electrical");
    const json* obj = nullptr;
    if (elec && elec->is_array() && !elec->empty() && elec->front().is_object())
        obj = &elec->front();
    else if (elec && elec->is_object())
        obj = elec;
    if (obj == nullptr) return 0.0;  // electrical absent/empty => maximally sparse
    int present = 0;
    for (const auto& f : *core)
        if (obj->contains(f) && !(*obj)[f].is_null()) ++present;
    return static_cast<double>(present) / static_cast<double>(core->size());
}

}  // namespace

Verdict PartValidator::validate(const json& part) const {
    if (!part.is_object())
        throw std::invalid_argument("part record is not a JSON object");

    Verdict v;
    Ctx ctx;

    // GEN_MULTI_DISCRIMINATOR: a well-formed PEAS document carries exactly one
    // component discriminator. More than one is a structural error (the dispatcher
    // below would silently pick the first).
    static const char* DISCRIMINATORS[] = {
        "magnetic", "capacitor", "resistor", "varistor", "connector", "controller", "semiconductor",
        "operationalAmplifier", "comparator", "instrumentationAmplifier", "differenceAmplifier",
        "programmableGainAmplifier", "buffer", "sampleHold", "analogSwitch", "multiplexer",
        "adc", "dac", "multiplier", "integrator", "summer"};
    int disc_count = 0;
    for (const char* k : DISCRIMINATORS)
        if (part.contains(k)) ++disc_count;
    if (disc_count > 1) {
        Ctx gctx;
        gctx.component = "(multiple)";
        emit(v.findings, gctx, "GEN_MULTI_DISCRIMINATOR", Severity::Impossible,
             static_cast<double>(disc_count), 1,
             "more than one component discriminator present in a single record");
    }

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
        // GEN_SPARSE: authenticity/completeness signal. Below the real-part floor,
        // a record carries too few of its family's core fields to be a real part.
        double comp = compute_completeness(component, *r.datasheet);
        if (comp >= 0.0) {
            v.completeness = comp;
            double floor = sparse_floor(component);
            if (comp < floor)
                emit(v.findings, ctx, "GEN_SPARSE", Severity::Suspicious, comp, floor,
                     "record carries only " + std::to_string(static_cast<int>(comp * 100)) +
                         "% of the core datasheet fields expected for a " + component);
        }
    };

    if (part.contains("magnetic")) {
        run("magnetic", part["magnetic"], &check_magnetics);
    } else if (part.contains("capacitor")) {
        run("capacitor", part["capacitor"], &check_capacitors);
    } else if (part.contains("resistor")) {
        run("resistor", part["resistor"], &check_resistors);
    } else if (part.contains("varistor")) {
        run("varistor", part["varistor"], &check_varistors);
    } else if (part.contains("connector")) {
        run("connector", part["connector"], &check_connectors);
    } else if (part.contains("controller")) {
        run("controller", part["controller"], &check_controllers);
    } else if (part.contains("semiconductor")) {
        const json& semi = part["semiconductor"];
        if (semi.contains("mosfet")) run("mosfet", semi["mosfet"], &check_mosfets);
        else if (semi.contains("diode")) run("diode", semi["diode"], &check_diodes);
        else if (semi.contains("igbt")) run("igbt", semi["igbt"], &check_igbts);
        else if (semi.contains("bjt")) run("bjt", semi["bjt"], &check_bjts);
        else
            throw std::invalid_argument(
                "semiconductor record has no mosfet/diode/igbt/bjt sub-object");
    } else {
        // AAS analog ICs: top-level discriminator is the subtype name.
        static const char* AAS[] = {
            "operationalAmplifier", "comparator", "instrumentationAmplifier",
            "differenceAmplifier", "programmableGainAmplifier", "buffer", "sampleHold",
            "analogSwitch", "multiplexer", "adc", "dac", "multiplier", "integrator", "summer"};
        const char* hit = nullptr;
        for (const char* k : AAS)
            if (part.contains(k)) { hit = k; break; }
        if (hit != nullptr)
            run(hit, part[hit], &check_analog);
        else
            throw std::invalid_argument(
                "no known component discriminator (magnetic/capacitor/resistor/varistor/"
                "connector/semiconductor/analog-AAS)");
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
        "GEN_TEMP_ORDER", "GEN_PROVENANCE_MISSING", "GEN_FAMILY_MISMATCH", "GEN_MULTI_DISCRIMINATOR",
        "GEN_OVERPRECISION", "GEN_SPARSE", "GEN_COHORT_OUTLIER",
        // magnetics
        "MAG_DCR_GEOM", "MAG_DCR_PER_H", "MAG_ISAT_POWER", "MAG_SRF_L", "MAG_SRF_SANE",
        "MAG_ENERGY_DENSITY", "MAG_L_TOLERANCE", "MAG_L_MAGNITUDE", "MAG_RATED_LE_SAT",
        "MAG_DIM_NONPOSITIVE", "MAG_E_SERIES",
        // capacitors
        "CAP_POSITIVITY", "CAP_MAGNITUDE", "CAP_TOLERANCE", "CAP_DF_BOUNDS", "CAP_ESR_C",
        "CAP_ENERGY_DENSITY", "CAP_LEAKAGE_CV", "CAP_INSULATION_RC", "CAP_E_SERIES",
        // resistors
        "RES_R_RANGE", "RES_POWER_SIZE", "RES_MAXV_SIZE", "RES_TEMPCO",
        "RES_TOLERANCE", "RES_E_SERIES",
        // mosfets
        "MOS_CAP_HIERARCHY", "MOS_CHARGE_HIERARCHY", "MOS_VTH_WINDOW", "MOS_VGS_VS_VTH",
        "MOS_BODY_DIODE_VF", "MOS_POWER_THERMAL", "MOS_RON_FLOOR", "MOS_IPULSE_VS_IDC",
        // diodes
        "DIO_POSITIVITY", "DIO_VF_RANGE", "DIO_SURGE_VS_IF", "DIO_VF_POWER", "DIO_QRR_SCHOTTKY",
        "DIO_CJ_VR", "DIO_TVS_ORDERING",
        // igbts
        "IGBT_POSITIVITY", "IGBT_VCESAT_RANGE", "IGBT_VCESAT_VS_VCES", "IGBT_IC_RANGE",
        "IGBT_VCES_RANGE", "IGBT_VCESAT_RATIO",
        // bjts
        "BJT_POSITIVITY", "BJT_VCESAT_RANGE", "BJT_VCESAT_VS_VCEO", "BJT_HFE_RANGE",
        "BJT_VCBO_VS_VCEO", "BJT_FT_RANGE",
        // varistors
        "VAR_POSITIVITY", "VAR_MCOV_VS_VNOM", "VAR_CLAMP_VS_VNOM", "VAR_CLAMP_RATIO",
        "VAR_NONLINEARITY", "VAR_SURGE_RANGE", "VAR_CAPACITANCE", "VAR_CLAMP_CURRENT",
        "VAR_ENERGY_RANGE",
        // connectors
        "CONN_POSITIVITY", "CONN_CURRENT_RANGE", "CONN_VOLTAGE_RANGE", "CONN_CONTACT_RESISTANCE",
        "CONN_INSULATION_R", "CONN_CLEARANCE_BREAKDOWN", "CONN_CREEPAGE_CLEARANCE",
        "CONN_DWV_VS_RATED",
        // controllers (CTAS)
        "CTL_POSITIVITY", "CTL_PHASE_COUNT", "CTL_SUPPLY_ORDER", "CTL_SUPPLY_ABSMAX",
        "CTL_FREQ_ORDER", "CTL_UVLO_ORDER", "CTL_ISO_ORDER", "CTL_ISO_CREEP", "CTL_SHUNT_CATHODE",
        "CTL_SR_THRESHOLD", "CTL_DUTY_RANGE", "CTL_THERMAL_ORDER", "CTL_SUPPLY_RANGE",
        "CTL_FREQ_RANGE", "CTL_REF_RANGE", "CTL_CS_THRESHOLD", "CTL_GATE_DRIVE", "CTL_ISO_RANGE",
        "CTL_TJMAX", "CTL_DEADTIME",
        // analog ICs (AAS)
        "ANA_CHANNELS", "ANA_VOS", "ANA_IBIAS", "ANA_SUPPLY", "ANA_CMRR", "ANA_PSRR",
        "ANA_OL_GAIN", "ANA_SLEW", "ANA_VNOISE", "ANA_GBW", "ANA_GAIN_ORDER", "ANA_SLEW_GBW",
        "CMP_TPD", "CMP_HYST",
        "CONV_RES", "CONV_RATE", "CONV_VREF", "CONV_SNR", "SW_RON", "SW_LEAK",
        "MULT_SCALE", "MULT_ERROR", "MULT_BW",
    };
}

}  // namespace tas
