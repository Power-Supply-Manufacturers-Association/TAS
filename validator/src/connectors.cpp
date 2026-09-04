// SPDX-License-Identifier: MIT
// Connector (CONAS) physics checks. `datasheet` is the connector datasheetInfo:
//   electrical.{ratedCurrentPerContact,ratedVoltage,contactResistance,
//     insulationResistance,dielectricWithstandingVoltage,clearance,creepage}
//   mechanical.{pitch,positions,rows,matingCycles}
//   material.{contactPlating.{matingAreaMaterialRef,matingAreaThickness}}
//   environmental.operatingTemperature.{minimum,maximum}
//   familyDetails.{family,tailLength,stackHeight,characteristicImpedance,
//                  frequencyRange,maxVswr}
//   mating.matesWith[].matedHeight
// Bounds: contact resistance ~0.15 mOhm (power) to ~0.1 Ohm (signal); power
// contacts to ~50 A / 600 V, busbars to a few hundred A, HV connectors to tens
// of kV. Air breakdown uses the ideal uniform-field Paschen curve, not a linear
// kV/mm rule. Sources: Holm "Electric Contacts" 4th ed. (voltage-temperature
// relation and the softening/melting voltage table), IEC 60664-1 (insulation
// coordination), IEC 60512-2-1 (contact-resistance test method), IEC 60512-9-1
// (durability), Arrow/Harwin connector ratings.
//
// CALIBRATION. Every check below was measured against all 392,346 records of
// TAS/data/connectors.ndjson before being written, and its fire rate is quoted
// at the check. THREE further checks were designed, measured and REJECTED —
// recorded here so they are not re-proposed:
//
//   * body-envelope vs pin-field extent ("max(length,width,height) must be >=
//     (ceil(positions/rows)-1)*pitch"). Fired on 78,044 / 104,260 = 74.9%.
//     CONAS mechanical length/width/height carry no axis convention, so the
//     largest STATED dimension is routinely not the axis the pin field runs
//     along. The bound is sound; the data cannot say which axis it applies to.
//   * clearance <= pitch. Fired on 623 / 1,399 = 44.5%.
//   * creepage <= pitch (the flat-surface geodesic bound). Fired on
//     1,321 / 1,561 = 84.6%, median ratio 1.26, max 5.76.
//     Both fail for the same reason: CONAS does not say WHICH pair of conductors
//     a clearance or creepage figure refers to (adjacent contacts? contact to
//     shell? group to group?), and vendors meet creepage with ribs and barriers
//     that no parametric field can see. These are schema gaps to raise, not
//     part defects to flag.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <algorithm>
#include <cmath>
#include <optional>
#include <sstream>
#include <string>

namespace tas {
namespace {

// Ideal uniform-field breakdown voltage of air at 1 atm for a gap of `gap_m`.
// Returns nullopt left of the Paschen minimum (gap below ~10 um), where the
// expression is not defined — the caller must then decline to fire rather than
// substitute anything.
std::optional<double> paschen_air_v(double gap_m) {
    if (!(gap_m > 0)) return std::nullopt;
    const double pd = thr::CONN_ATM_PA * gap_m;              // Pa*m
    const double arg = thr::CONN_PASCHEN_A * pd;
    if (!(arg > 1.0)) return std::nullopt;
    const double denom = std::log(arg) - thr::CONN_PASCHEN_LNLN;
    if (!(denom > 0)) return std::nullopt;
    return thr::CONN_PASCHEN_B * pd / denom;
}

// Holm softening / melting voltage [V] for a contact plating, by normalised
// material token. Holm, "Electric Contacts", 4th ed., voltage-temperature table.
struct HolmVoltages {
    double softening;
    double melting;
};

// Returns false when the plating is absent or not a metal we tabulate; the
// caller then falls back to the plating-agnostic ceiling. Never guesses a metal.
bool holm_voltages(const std::string& plating, HolmVoltages& out) {
    // Order matters only in that each token is distinctive; the catalog uses
    // ids of the form "au-gold", "sn-tin", "ag-silver", "ni-nickel".
    if (tech_has(plating, "gold")) { out = {0.08, 0.43}; return true; }
    if (tech_has(plating, "silver")) { out = {0.09, 0.37}; return true; }
    if (tech_has(plating, "nickel")) { out = {0.22, 0.65}; return true; }
    if (tech_has(plating, "palladium")) { out = {0.57, 0.71}; return true; }
    if (tech_has(plating, "platinum")) { out = {0.25, 0.65}; return true; }
    if (tech_has(plating, "tungsten")) { out = {0.40, 1.10}; return true; }
    if (tech_has(plating, "copper")) { out = {0.12, 0.43}; return true; }
    if (tech_has(plating, "tin")) { out = {0.07, 0.13}; return true; }
    return false;
}

// True if the record documents a per-contact current rating of its own, i.e. it
// says WHICH contact a high current belongs to. CONAS puts this in tier-2
// contactSystem.contacts[].currentRating, whose own description is "overrides the
// shared electrical rating when contacts differ, e.g. power vs signal pins" — so
// its presence is exactly the hybrid connector's declaration. `component` is the
// whole connector object; contactSystem is a sibling of manufacturerInfo, not part
// of datasheetInfo, which is why Ctx carries the component.
bool declares_per_contact_current(const json* component) {
    if (component == nullptr) return false;
    const json* contacts = at(*component, "contactSystem", "contacts");
    if (contacts == nullptr || !contacts->is_array()) return false;
    for (const auto& c : *contacts)
        if (scalar_at(c, {"currentRating"})) return true;
    return false;
}

// True if a Celsius value looks like an unconverted Fahrenheit reading: high
// enough to be implausible, and landing on a round Celsius value when converted
// back. One helper used for BOTH temperature ends so the two branches cannot
// drift apart.
bool looks_like_fahrenheit(double celsius) {
    if (!(std::fabs(celsius) > thr::CONN_FAHRENHEIT_PROBE_MIN_C)) return false;
    const double back = (celsius - 32.0) / 1.8;
    const double grid = std::round(back / thr::CONN_FAHRENHEIT_GRID_C) * thr::CONN_FAHRENHEIT_GRID_C;
    return std::fabs(back - grid) < thr::CONN_FAHRENHEIT_TOL_C;
}

// Range guard for a length-like field stored in metres. Emits IMPOSSIBLE on a
// value outside the physically-possible band — the signature of a mm-for-m or
// um-for-m unit slip. Fires on 0 records today; it is a guard for future
// sourcing passes, which is when unit slips actually enter.
void check_si_length(const std::optional<double>& v, const char* label, double lo, double hi,
                     const Ctx& ctx, std::vector<Finding>& out) {
    if (!v) return;
    if (*v <= 0) {
        emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *v, 0,
             std::string(label) + " <= 0");
        return;
    }
    if (*v < lo)
        emit(out, ctx, "CONN_UNIT_SCALE", Severity::Impossible, *v, lo,
             fmt(std::string(label) + " below the smallest physically-possible value [m] "
                                      "(unit slip: value stored in a sub-metre unit?)",
                 *v, lo));
    else if (*v > hi)
        emit(out, ctx, "CONN_UNIT_SCALE", Severity::Impossible, *v, hi,
             fmt(std::string(label) + " above the largest physically-possible value [m] "
                                      "(unit slip: mm or um stored as m?)",
                 *v, hi));
}

}  // namespace

void check_connectors(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                      std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    const json* mech = at(datasheet, "mechanical");
    const json* matl = at(datasheet, "material");
    const json* envr = at(datasheet, "environmental");
    const json* fdet = at(datasheet, "familyDetails");

    if (elec == nullptr) skipped.push_back("CONN_ELECTRICAL_*");
    if (mech == nullptr) skipped.push_back("CONN_MECHANICAL_*");

    std::optional<double> I, V, Rc, clearance, creepage, dwv;
    if (elec != nullptr) {
        I = scalar_at(*elec, {"ratedCurrentPerContact"});
        V = scalar_at(*elec, {"ratedVoltage"});
        Rc = scalar_at(*elec, {"contactResistance"});
        clearance = scalar_at(*elec, {"clearance"});
        creepage = scalar_at(*elec, {"creepage"});
        dwv = scalar_at(*elec, {"dielectricWithstandingVoltage"});
    }

    // ---- existing electrical bounds ----------------------------------------

    // CHECK: positivity.
    if (I && *I <= 0)
        emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *I, 0,
             "ratedCurrentPerContact <= 0");
    if (V && *V <= 0)
        emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *V, 0, "ratedVoltage <= 0");

    // CHECK: per-contact current range.
    if (I && *I > 0) {
        if (*I > thr::CONN_CURRENT_IMP)
            emit(out, ctx, "CONN_CURRENT_RANGE", Severity::Impossible, *I, thr::CONN_CURRENT_IMP,
                 fmt("ratedCurrentPerContact exceeds any single contact [A]", *I, thr::CONN_CURRENT_IMP));
        else if (*I > thr::CONN_CURRENT_SUS)
            emit(out, ctx, "CONN_CURRENT_RANGE", Severity::Suspicious, *I, thr::CONN_CURRENT_SUS,
                 fmt("ratedCurrentPerContact high for a single contact [A]", *I, thr::CONN_CURRENT_SUS));
    }

    // CHECK: rated voltage range.
    if (V && *V > 0) {
        if (*V > thr::CONN_VOLTAGE_IMP)
            emit(out, ctx, "CONN_VOLTAGE_RANGE", Severity::Impossible, *V, thr::CONN_VOLTAGE_IMP,
                 fmt("ratedVoltage implausibly high [V]", *V, thr::CONN_VOLTAGE_IMP));
        else if (*V > thr::CONN_VOLTAGE_SUS)
            emit(out, ctx, "CONN_VOLTAGE_RANGE", Severity::Suspicious, *V, thr::CONN_VOLTAGE_SUS,
                 fmt("ratedVoltage very high [V]", *V, thr::CONN_VOLTAGE_SUS));
    }

    // CHECK: mated-pair contact resistance.
    if (Rc) {
        if (*Rc <= 0)
            emit(out, ctx, "CONN_CONTACT_RESISTANCE", Severity::Impossible, *Rc, 0,
                 "contactResistance <= 0");
        else if (*Rc > thr::CONN_RCONTACT_IMP_HI)
            emit(out, ctx, "CONN_CONTACT_RESISTANCE", Severity::Impossible, *Rc,
                 thr::CONN_RCONTACT_IMP_HI,
                 fmt("contactResistance too high to be a contact [Ohm]", *Rc, thr::CONN_RCONTACT_IMP_HI));
        else if (*Rc < thr::CONN_RCONTACT_SUS_LO || *Rc > thr::CONN_RCONTACT_SUS_HI)
            emit(out, ctx, "CONN_CONTACT_RESISTANCE", Severity::Suspicious, *Rc, 0,
                 fmt("contactResistance outside typical 0.01 mOhm..1 Ohm band", *Rc));
    }

    // CHECK: insulation resistance.
    if (elec != nullptr) {
        if (auto ir = scalar_at(*elec, {"insulationResistance"})) {
            if (*ir <= 0)
                emit(out, ctx, "CONN_INSULATION_R", Severity::Impossible, *ir, 0,
                     "insulationResistance <= 0");
            else if (*ir < thr::CONN_INSULATION_IMP_LO)
                emit(out, ctx, "CONN_INSULATION_R", Severity::Impossible, *ir,
                     thr::CONN_INSULATION_IMP_LO,
                     fmt("insulationResistance below 1 Ohm (a short, not an insulator) [Ohm]", *ir,
                         thr::CONN_INSULATION_IMP_LO));
            else if (*ir < thr::CONN_INSULATION_SUS_LO)
                emit(out, ctx, "CONN_INSULATION_R", Severity::Suspicious, *ir,
                     thr::CONN_INSULATION_SUS_LO,
                     fmt("insulationResistance low for an insulator [Ohm]", *ir,
                         thr::CONN_INSULATION_SUS_LO));
        }
    }

    // ---- insulation coordination: the ideal Paschen bound -------------------
    // Both checks use the uniform-field air breakdown curve rather than a linear
    // kV/mm rule. Anything that fires is beyond the ideal physics limit, so it
    // cannot be explained by a favourable electrode shape. Fires on 0 records
    // today (1,210 carry clearance + ratedVoltage), and the linear 3 kV/mm rule
    // it replaces also fired on 0 — this is a correctness fix, not a yield change.
    if (clearance && *clearance <= 0)
        emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *clearance, 0, "clearance <= 0");
    if (clearance && *clearance > 0) {
        const auto vb = paschen_air_v(*clearance);
        if (vb) {
            if (V && *V > 0 && *V > *vb)
                emit(out, ctx, "CONN_CLEARANCE_BREAKDOWN", Severity::Impossible, *V, *vb,
                     fmt("ratedVoltage exceeds the ideal uniform-field air breakdown of its own "
                         "stated clearance (Paschen, 1 atm) [V]",
                         *V, *vb));
            if (dwv && *dwv > 0 && *dwv > *vb)
                emit(out, ctx, "CONN_DWV_VS_CLEARANCE", Severity::Impossible, *dwv, *vb,
                     fmt("dielectricWithstandingVoltage exceeds the ideal uniform-field air "
                         "breakdown of its own stated clearance (Paschen, 1 atm) [V]",
                         *dwv, *vb));
        }
    }

    // CHECK: creepage (surface path) must be >= clearance (air path).
    if (creepage) {
        if (*creepage <= 0)
            emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *creepage, 0, "creepage <= 0");
        else if (clearance && *clearance > 0 && *creepage < *clearance)
            emit(out, ctx, "CONN_CREEPAGE_CLEARANCE", Severity::Impossible, *creepage, *clearance,
                 fmt("creepage < clearance (surface path shorter than air path) [m]", *creepage,
                     *clearance));
    }

    // CHECK: dielectric withstanding (proof) voltage must exceed the working voltage.
    if (dwv) {
        if (*dwv <= 0)
            emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *dwv, 0,
                 "dielectricWithstandingVoltage <= 0");
        else if (V && *dwv < *V)
            emit(out, ctx, "CONN_DWV_VS_RATED", Severity::Impossible, *dwv, *V,
                 fmt("dielectricWithstandingVoltage below ratedVoltage [V]", *dwv, *V));
    }

    // ---- Holm contact voltage ----------------------------------------------
    // A closed metallic contact's constriction supertemperature is set by the
    // voltage across it alone. If the part's own rated current driven through
    // the part's own contact resistance exceeds the melting voltage of the
    // part's own plating, the two specifications describe different physical
    // objects. Fires on 464 / 46,927 = 0.99% of parts carrying both fields.
    if (I && Rc && *I > 0 && *Rc > 0) {
        const double u_contact = *I * *Rc;
        const double u_any = thr::CONN_UMELT_MAX_ANY;

        if (u_contact > thr::CONN_UCONTACT_IMP_FACTOR * u_any) {
            emit(out, ctx, "CONN_CONTACT_VOLTAGE", Severity::Impossible, u_contact,
                 thr::CONN_UCONTACT_IMP_FACTOR * u_any,
                 fmt("ratedCurrentPerContact * contactResistance is far above the melting voltage "
                     "of every contact metal (tungsten, 1.10 V) — this is a resistor, not a "
                     "contact [V]",
                     u_contact, thr::CONN_UCONTACT_IMP_FACTOR * u_any));
        } else {
            HolmVoltages hv{};
            const std::string plating =
                norm_tech(matl == nullptr ? nullptr
                                          : at(*matl, "contactPlating", "matingAreaMaterialRef"));
            if (holm_voltages(plating, hv)) {
                // >=, not >: the Holm voltages are round two-decimal constants
                // (tin 0.13, gold 0.43, tungsten 1.10) and placeholder contact
                // resistances are round numbers, so the product lands EXACTLY on
                // the threshold rather than above it. Amphenol 88980-002LF was
                // 20 A x 0.055 ohm = 1.100 V and escaped a strict > entirely.
                if (u_contact >= hv.melting)
                    emit(out, ctx, "CONN_CONTACT_VOLTAGE", Severity::Suspicious, u_contact,
                         hv.melting,
                         fmt("ratedCurrentPerContact * contactResistance exceeds the Holm MELTING "
                             "voltage of the stated plating — at the rated current this contact "
                             "would weld [V]",
                             u_contact, hv.melting));
                else if (u_contact >= hv.softening)
                    emit(out, ctx, "CONN_CONTACT_VOLTAGE", Severity::Suspicious, u_contact,
                         hv.softening,
                         fmt("ratedCurrentPerContact * contactResistance exceeds the Holm SOFTENING "
                             "voltage of the stated plating [V]",
                             u_contact, hv.softening));
            } else if (u_contact >= u_any) {
                emit(out, ctx, "CONN_CONTACT_VOLTAGE", Severity::Suspicious, u_contact, u_any,
                     fmt("ratedCurrentPerContact * contactResistance exceeds the melting voltage of "
                         "every contact metal, and no plating is stated [V]",
                         u_contact, u_any));
            }
        }
    }

    // ---- mechanical ---------------------------------------------------------
    std::optional<double> pitch, positions, rows;
    if (mech != nullptr) {
        pitch = scalar_at(*mech, {"pitch"});
        positions = scalar_at(*mech, {"positions"});
        rows = scalar_at(*mech, {"rows"});

        check_si_length(pitch, "pitch", thr::CONN_PITCH_IMP_LO, thr::CONN_PITCH_IMP_HI, ctx, out);

        // CHECK: a rectangular contact array cannot have more rows than contacts.
        // Fires on 35 records, all multi-level terminal blocks where `positions`
        // counts poles per level rather than total contacts — a real semantic
        // inconsistency, but one the schema invites, so SUSPICIOUS.
        if (positions && rows) {
            if (*rows < 1 || *positions < 1)
                emit(out, ctx, "CONN_ROWS_POSITIONS", Severity::Impossible,
                     *rows < 1 ? *rows : *positions, 1, "rows or positions below 1");
            else if (*rows > *positions)
                emit(out, ctx, "CONN_ROWS_POSITIONS", Severity::Suspicious, *rows, *positions,
                     fmt("rows exceeds positions — either positions counts poles per level rather "
                         "than total contacts, or one of the two is wrong",
                         *rows, *positions));
        }

        // CHECK: a per-contact current rating that belongs to a power terminal
        // while the pitch belongs to a signal field (ABT #486). Unlike the density
        // check below, this one is separable and therefore IMPOSSIBLE: a terminal
        // carrying more than 10 A is a crimp barrel or blade whose body does not
        // fit a <= 2.54 mm grid, and the catalogue's own distribution puts the
        // step there (see the threshold). The hybrid explanation that keeps the
        // density check SUSPICIOUS is not available here, because a record that
        // really is hybrid can SAY so in contactSystem.contacts[].currentRating —
        // and one that does is exempt. Fired on 1,273 / 392,346 = 0.32% before
        // those were quarantined to connectors.quarantine_current_pitch_conflict;
        // fires on 0 today.
        if (I && pitch && *I > thr::CONN_POWER_CONTACT_A &&
            *pitch > 0 && *pitch <= thr::CONN_POWER_CONTACT_PITCH_M &&
            !declares_per_contact_current(ctx.component_obj))
            emit(out, ctx, "CONN_CURRENT_VS_PITCH", Severity::Impossible, *I,
                 thr::CONN_POWER_CONTACT_A,
                 fmt("ratedCurrentPerContact is a power-terminal rating but the pitch is a "
                     "signal field: no contact on a pitch this fine carries this current [A] "
                     "(the two fields describe different contacts; state the power contact in "
                     "contactSystem.contacts[].currentRating if the part really is hybrid)",
                     *I, thr::CONN_POWER_CONTACT_A));

        // CHECK: current density through the contact cross-section the pitch
        // allows. SUSPICIOUS only — see the threshold comment for why a hybrid
        // power+signal connector legitimately exceeds it. Fires on 0.74%.
        if (I && pitch && *I > 0 && *pitch > 0) {
            const double side = thr::CONN_PIN_SIDE_PER_PITCH * *pitch;
            const double j = *I / (side * side);
            if (j > thr::CONN_CURRENT_DENSITY_SUS)
                emit(out, ctx, "CONN_CURRENT_DENSITY", Severity::Suspicious, j,
                     thr::CONN_CURRENT_DENSITY_SUS,
                     fmt("ratedCurrentPerContact implies an impossible current density through the "
                         "contact cross-section its pitch allows [A/m^2] (a hybrid connector rating "
                         "a wide power blade inside a fine signal pitch is the benign explanation)",
                         j, thr::CONN_CURRENT_DENSITY_SUS));
        }

        // CHECK: durability against the plating that has to survive it.
        if (auto cycles = scalar_at(*mech, {"matingCycles"})) {
            const std::string plating =
                norm_tech(matl == nullptr ? nullptr
                                          : at(*matl, "contactPlating", "matingAreaMaterialRef"));
            const auto thick =
                matl == nullptr ? std::nullopt
                                : scalar_at(*matl, {"contactPlating", "matingAreaThickness"});
            if (*cycles < 0)
                emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *cycles, 0,
                     "matingCycles < 0");
            else if (*cycles > thr::CONN_CYCLES_IMP)
                emit(out, ctx, "CONN_DURABILITY", Severity::Impossible, *cycles, thr::CONN_CYCLES_IMP,
                     fmt("matingCycles beyond any connector interface", *cycles, thr::CONN_CYCLES_IMP));
            else if (*cycles > thr::CONN_CYCLES_SUS)
                emit(out, ctx, "CONN_DURABILITY", Severity::Suspicious, *cycles, thr::CONN_CYCLES_SUS,
                     fmt("matingCycles very high — only spring-probe interfaces reach this",
                         *cycles, thr::CONN_CYCLES_SUS));
            else if (tech_has(plating, "tin") && *cycles > thr::CONN_CYCLES_TIN_SUS)
                emit(out, ctx, "CONN_DURABILITY", Severity::Suspicious, *cycles,
                     thr::CONN_CYCLES_TIN_SUS,
                     fmt("matingCycles far beyond the durability of a tin mating surface", *cycles,
                         thr::CONN_CYCLES_TIN_SUS));
            else if (tech_has(plating, "gold") && thick && *thick < thr::CONN_GOLD_FLASH_M &&
                     *cycles > thr::CONN_CYCLES_TIN_SUS)
                emit(out, ctx, "CONN_DURABILITY", Severity::Suspicious, *cycles,
                     thr::CONN_CYCLES_TIN_SUS,
                     fmt("matingCycles far beyond the durability of a gold FLASH (< 0.1 um) mating "
                         "surface",
                         *cycles, thr::CONN_CYCLES_TIN_SUS));
        }
    }

    if (matl != nullptr)
        check_si_length(scalar_at(*matl, {"contactPlating", "matingAreaThickness"}),
                        "contactPlating.matingAreaThickness", thr::CONN_PLATING_IMP_LO,
                        thr::CONN_PLATING_IMP_HI, ctx, out);

    // ---- environmental ------------------------------------------------------
    if (envr != nullptr) {
        const auto tmin = scalar_at(*envr, {"operatingTemperature", "minimum"});
        const auto tmax = scalar_at(*envr, {"operatingTemperature", "maximum"});
        if (tmin && *tmin < thr::CONN_ABSOLUTE_ZERO_C)
            emit(out, ctx, "CONN_TEMPERATURE_RANGE", Severity::Impossible, *tmin,
                 thr::CONN_ABSOLUTE_ZERO_C, fmt("operating temperature minimum below absolute zero [degC]",
                                                *tmin, thr::CONN_ABSOLUTE_ZERO_C));
        if (tmin && tmax && *tmin >= *tmax)
            emit(out, ctx, "CONN_TEMPERATURE_RANGE", Severity::Impossible, *tmin, *tmax,
                 fmt("operating temperature minimum >= maximum [degC]", *tmin, *tmax));
        if (tmax) {
            if (*tmax > thr::CONN_TEMP_MAX_IMP)
                emit(out, ctx, "CONN_TEMPERATURE_RANGE", Severity::Impossible, *tmax,
                     thr::CONN_TEMP_MAX_IMP,
                     fmt("operating temperature maximum beyond any connector [degC]", *tmax,
                         thr::CONN_TEMP_MAX_IMP));
            else if (*tmax > thr::CONN_TEMP_MAX_SUS)
                emit(out, ctx, "CONN_TEMPERATURE_RANGE", Severity::Suspicious, *tmax,
                     thr::CONN_TEMP_MAX_SUS,
                     fmt("operating temperature maximum above any polymer-housed connector [degC]",
                         *tmax, thr::CONN_TEMP_MAX_SUS));
        }
        // CHECK: unconverted Fahrenheit. Fires on 67 records — 392 (= 200 degC),
        // 302 (= 150 degC) and 221 (= 105 degC), all of which are among the
        // catalog's most common genuine Celsius maxima.
        if (tmax && looks_like_fahrenheit(*tmax))
            emit(out, ctx, "CONN_TEMPERATURE_UNIT", Severity::Suspicious, *tmax,
                 (*tmax - 32.0) / 1.8,
                 fmt("operating temperature maximum looks like an unconverted Fahrenheit value; "
                     "it converts to a round Celsius figure [degC]",
                     *tmax, (*tmax - 32.0) / 1.8));
        if (tmin && looks_like_fahrenheit(*tmin))
            emit(out, ctx, "CONN_TEMPERATURE_UNIT", Severity::Suspicious, *tmin,
                 (*tmin - 32.0) / 1.8,
                 fmt("operating temperature minimum looks like an unconverted Fahrenheit value; "
                     "it converts to a round Celsius figure [degC]",
                     *tmin, (*tmin - 32.0) / 1.8));
    }

    // ---- family-specific ----------------------------------------------------
    if (fdet != nullptr) {
        check_si_length(scalar_at(*fdet, {"tailLength"}), "familyDetails.tailLength",
                        thr::CONN_LENGTH_IMP_LO, thr::CONN_LENGTH_IMP_HI, ctx, out);
        check_si_length(scalar_at(*fdet, {"stackHeight"}), "familyDetails.stackHeight",
                        thr::CONN_LENGTH_IMP_LO, thr::CONN_LENGTH_IMP_HI, ctx, out);

        const json* fam = at(*fdet, "family");
        const bool is_rf = fam != nullptr && fam->is_string() && fam->get<std::string>() == "rf";

        if (auto z0 = scalar_at(*fdet, {"characteristicImpedance"})) {
            if (*z0 < thr::CONN_Z0_IMP_LO || *z0 > thr::CONN_Z0_IMP_HI)
                emit(out, ctx, "CONN_RF_BAND", Severity::Impossible, *z0, 0,
                     fmt("characteristicImpedance outside any transmission line [Ohm]", *z0));
            else if (*z0 < thr::CONN_Z0_SUS_LO || *z0 > thr::CONN_Z0_SUS_HI)
                emit(out, ctx, "CONN_RF_BAND", Severity::Suspicious, *z0, 0,
                     fmt("characteristicImpedance far from the 50/75/100 Ohm norms [Ohm]", *z0));
        }
        const auto fmin = scalar_at(*fdet, {"frequencyRange", "minimum"});
        const auto fmax = scalar_at(*fdet, {"frequencyRange", "maximum"});
        if (fmin && fmax && *fmin >= *fmax)
            emit(out, ctx, "CONN_RF_BAND", Severity::Impossible, *fmin, *fmax,
                 fmt("frequencyRange minimum >= maximum [Hz]", *fmin, *fmax));
        if (fmax && *fmax > thr::CONN_FREQ_IMP_HI)
            emit(out, ctx, "CONN_RF_BAND", Severity::Impossible, *fmax, thr::CONN_FREQ_IMP_HI,
                 fmt("frequencyRange maximum beyond any connector [Hz]", *fmax, thr::CONN_FREQ_IMP_HI));
        if (auto vswr = scalar_at(*fdet, {"maxVswr"})) {
            if (*vswr < 1.0)
                emit(out, ctx, "CONN_RF_BAND", Severity::Impossible, *vswr, 1.0,
                     fmt("maxVswr below 1 (a standing-wave ratio cannot be less than unity)", *vswr,
                         1.0));
        }
        if (is_rf && !scalar_at(*fdet, {"characteristicImpedance"}))
            skipped.push_back("CONN_RF_BAND");
    }

    // ---- mated-pair geometry -------------------------------------------------
    // A single part quoting mated heights an order of magnitude apart has merged
    // conflicting series-level facts. Fires on 0 records today.
    if (const json* mates = at(datasheet, "mating", "matesWith")) {
        if (mates->is_array()) {
            bool any = false;
            double lo = 0, hi = 0;
            for (const auto& entry : *mates) {
                if (!entry.is_object()) continue;
                const auto h = scalar_at(entry, {"matedHeight"});
                if (!h) continue;
                if (*h <= 0) {
                    emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *h, 0,
                         "mating.matesWith[].matedHeight <= 0");
                    continue;
                }
                check_si_length(h, "mating.matesWith[].matedHeight", thr::CONN_LENGTH_IMP_LO,
                                thr::CONN_LENGTH_IMP_HI, ctx, out);
                if (!any) {
                    lo = *h;
                    hi = *h;
                    any = true;
                } else {
                    lo = std::min(lo, *h);
                    hi = std::max(hi, *h);
                }
            }
            if (any && lo > 0 && hi > thr::CONN_MATED_HEIGHT_SPREAD_SUS * lo)
                emit(out, ctx, "CONN_MATED_HEIGHT", Severity::Suspicious, hi / lo,
                     thr::CONN_MATED_HEIGHT_SPREAD_SUS,
                     fmt("mated heights on one part span more than an order of magnitude — "
                         "conflicting series-level facts merged into one record [ratio]",
                         hi / lo, thr::CONN_MATED_HEIGHT_SPREAD_SUS));
        }
    }
}

}  // namespace tas
