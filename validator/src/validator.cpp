// SPDX-License-Identifier: MIT
#include "tas_validator/validator.hpp"

#include "tas_validator/helpers.hpp"

#include <cctype>
#include <map>
#include <regex>
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
        // A connector ACCESSORY is named after the connector it fits -- that is
        // what an accessory is. "Backshell for ... Connector", "Connector Cap",
        // "Connector Marker Strip": 3,796 of the 18,227 live accessory rows say
        // "connector" in their description, and every one of them is correct.
        // Without this line the noun test accuses all 3,796 of being a connector
        // mis-filed as an accessory -- a check firing on a fifth of a catalogue
        // is measuring its own vocabulary, not the data.
        if (c == "connectorAccessory") return "connector";
        // Likewise a potentiometer is a resistor with a moving tap, and lives in
        // RAS beside the resistor: "Trimmer Resistor" is its own family's noun.
        if (c == "potentiometer") return "resistor";
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

// GEN_FABRICATED_MPN: the exact MPN templates emitted by the April-2026
// fabrication scripts (ABT #247). These shapes are provably synthetic — no
// vendor sells them (verified against the WE released database and
// we-online.com; real ST duals end in ...CT, not ...C; case-sensitive unit
// suffixes nH/uH/mH are the generators', vendors use NF/MF/PF codes). Kept in
// lockstep with scripts/check_no_fabricated_parts.py KNOWN_TEMPLATES so the
// signature is enforced at physics-validation time (librarian promote gate,
// imports), not only at shard-build time.
void check_fabricated_mpn(const json& ds, const Ctx& ctx, std::vector<Finding>& out) {
    static const std::regex TEMPLATES[] = {
        std::regex(R"(^7443HCF-\d{4}-\d{4}$)"),
        std::regex(R"(^7443MAPI-\d{4}-\d{4}$)"),
        std::regex(R"(^WE-HCF-\d+(nH|uH|mH)-(STD|HC|XC)$)"),
        std::regex(R"(^WE-HCI-\d{4}-\d+$)"),
        std::regex(R"(^CC-[A-Z0-9]+-\d+(nH|uH|mH)$)"),
        std::regex(R"(^TDK-SPM-\d+(nH|uH|mH)$)"),
        std::regex(R"(^SRR-\d+(nH|uH|mH)$)"),
        std::regex(R"(^IHLP-\d+(nH|uH|mH)$)"),
        std::regex(R"(^WCAP-(ATH|MLCC)-[\d.]+(uF|nF)-[\d.]+V$)"),
        std::regex(R"(^7443\d{3,4}$)"),
        std::regex(R"(^STPS\d{2}H\d{3}C$)"),
        std::regex(R"(^SiC\d{2}H\d{4}$)"),
        // ABT #256 audit: the phase2-5 'reach 100K entries' generators
        std::regex(R"(^(Coi|Bou|TDK|Wur|Vis|Mur|Pul|Sum)\d{3}u[A-Za-z0-9]+_\d+$)"),
        std::regex(R"(^(Vis|Yag|Bou|Pan|KOA)(wir|car|mel|met|thi|MCS|PTF)\d+R\d{4}\d{4}$)"),
        std::regex(R"(^(GRM|CL|FK)\d{4}\d{4}\d{3}V$)"),
        std::regex(R"(^MLCC\d{6}$)"),
        // ABT #507 audit (2026-08-02): the "wave 2" SiC-diode ladder generator.
        // Real Infineon SiC Schottky numbering carries the voltage class in the
        // token (IDH06S60C, IDH03G65C6, IDH05G120C5 -> 60/65/120); the generator
        // minted 10C/11C/12C/20C/21C/22C and swept EVERY integer amp 2..30 A
        // across three package variants of one die.
        std::regex(R"(^IDH\d{2}S[GO]?(?:1[0-2]|2[0-2])C$)"),
    };
    // The phase2-5 generators wrote part.partNumber ONLY (no reference) — that
    // is how their output evaded reference-keyed checks. Test both identifiers.
    std::vector<std::string> ids;
    if (!ctx.reference.empty()) ids.push_back(ctx.reference);
    if (const json* part = at(ds, "part")) {
        if (part->is_object() && part->contains("partNumber") &&
            (*part)["partNumber"].is_string())
            ids.push_back((*part)["partNumber"].get<std::string>());
    }
    for (const auto& id : ids) {
        for (const auto& rx : TEMPLATES) {
            if (std::regex_match(id, rx)) {
                emit(out, ctx, "GEN_FABRICATED_MPN", Severity::Impossible, 0, 0,
                     "part number '" + id + "' matches a known fabrication-script "
                     "MPN template — it was invented, not sourced");
                return;
            }
        }
    }
}

// GEN_PACKAGE_MOUNT: mechanical.assemblyType contradicting the package named in
// mechanical.case. A package outline's mount class is definitional, not a vendor
// option — TO-252 (DPAK) is surface mount (gull-wing leads + solderable tab), and
// its through-hole relatives are separate outlines with their own numbers (TO-251/
// IPAK, TO-262/I2PAK). A wrong mount silently turns a THT->SMT process change into
// a "different land pattern" note in cross-reference (ABT #507), so it is a data
// impossibility, not a style difference.
//
// Only outlines whose class is definitional are listed. Screw-terminal and module
// bricks (SOT-227/ISOTOP, INT-A-PAK, EMIPAK, ACEPACK, "Module", 62mm, SEMITOP,
// ECONO) are deliberately absent: they are neither smt nor tht and the catalogue
// legitimately files them as chassis/pcbPad/smt.
void check_package_mount(const json& ds, const Ctx& ctx, std::vector<Finding>& out) {
    const json* mech = at(ds, "mechanical");
    if (mech == nullptr || !mech->is_object()) return;
    if (!mech->contains("case") || !(*mech)["case"].is_string()) return;
    if (!mech->contains("assemblyType") || !(*mech)["assemblyType"].is_string()) return;
    const std::string kase = (*mech)["case"].get<std::string>();
    const std::string mount = (*mech)["assemblyType"].get<std::string>();

    struct Outline {
        const char* pattern;
        const char* mount;
    };
    static const Outline OUTLINES[] = {
        // Surface mount.
        {R"((PG-)?TO-?25[23]\b.*)", "smt"},        // TO-252/253 = DPAK
        {R"(D-?PAK\b.*)", "smt"},
        {R"((PG-)?TO-?263\b.*)", "smt"},           // TO-263 = D2PAK
        {R"(D2PAK\b.*)", "smt"},
        {R"((TO-?268|D3PAK)\b.*)", "smt"},
        {R"(DO-214.*)", "smt"},                    // SMA/SMB/SMC bodies
        {R"(SM[ABC]\b.*)", "smt"},
        {R"(SOD-(80|123|128|323|523|882|962)\b.*)", "smt"},
        {R"(SOT-(23|223|323|346|363|5X3|9X3|SC70)\b.*)", "smt"},
        {R"(SC-?70\b.*)", "smt"},
        {R"((SO|SOP|SOIC|MSOP|TSSOP|DSO)-?8?\b.*)", "smt"},
        {R"(([UWVX][12]?)?SON\b.*)", "smt"},
        {R"([UPHD]?QFN\b.*)", "smt"},
        {R"(DFN.*)", "smt"},
        {R"((DS)?BGA\b.*)", "smt"},
        {R"(LGA\b.*)", "smt"},
        {R"((TOLL|LFPAK|PowerPAK|PowerFLAT|TDSON|TSDSON|SuperSO8|H2PAK)\b.*)", "smt"},
        {R"(TO-?277\b.*)", "smt"},
        // CFP15 = SOT1289, Nexperia's "thermal enhanced ultra thin SMD package".
        // \d+ and not \d*: the latter also swallows IQD's CFPS-/CFPX- crystal
        // outlines, which are a different family this rule has not been checked on.
        {R"(CFP\d+)", "smt"},
        // The surface-mount bridge-rectifier outlines. Each of these three is titled
        // "... Surface Mount Bridge Rectifier Diode" on its own vendor datasheet
        // (Bourns CD-HD0x = TO-269AA, CD-DF4xxS(L) = DFS-4, CD-MBL1xxS = MBLS), and
        // each ships on EIA-481 tape and reel with a recommended footprint.
        {R"(TO-?269.*)", "smt"},
        {R"(DFS-?\d*.*)", "smt"},
        {R"(MBL?S\b.*)", "smt"},
        // Through hole.
        {R"((PG-)?TO-?220\b.*)", "tht"},           // incl. FullPAK/FP — still leaded
        {R"((PG-)?TO-?24[47]\b.*)", "tht"},        // TO-247 / TO-264
        {R"((HiP|MAX|PLUS|ISOPLUS)-?247.*)", "tht"},
        {R"(ISOPLUS-?264.*)", "tht"},
        {R"(TO-?3P.*)", "tht"},
        {R"((TO-?251|IPAK)\b.*)", "tht"},          // the through-hole DPAK relative
        {R"((TO-?262|I2PAK)\b.*)", "tht"},         // the through-hole D2PAK relative
        {R"(DO-(14|15|27|35|41|201|204|247)\b.*)", "tht"},
        {R"(SOD-(57|64|68)\b.*)", "tht"},          // leaded glass, not the SMD SODs
        {R"((PG-)?[QC]?DIP-?\d*\b.*)", "tht"},
        {R"(ITO-220.*)", "tht"},
    };
    for (const auto& o : OUTLINES) {
        if (!std::regex_match(kase, std::regex(o.pattern, std::regex::icase))) continue;
        if (mount == o.mount) return;
        emit(out, ctx, "GEN_PACKAGE_MOUNT", Severity::Impossible, 0, 0,
             "mechanical.case '" + kase + "' is a " + std::string(o.mount) +
                 " package outline but mechanical.assemblyType is '" + mount + "'");
        return;
    }
}

// GEN_PACKAGE_ENVELOPE: a body a named package outline cannot physically have.
// A flat plastic outline's THICKNESS is definitional, not a vendor option: a
// SOIC-8 is 1.75 mm max (JEDEC MS-012, narrow body) or 2.65 mm (MS-013, wide
// body), and no variant of it is 4 mm thick. Copying a power package's body onto
// a small-outline record is how importers lose the distinction — ABT #508: 58
// diodes filed as SO-8 carried their DPAK sibling's 10 x 8 x 4 mm body, and the
// cross-reference tool then showed that envelope to the user as a real footprint.
//
// The catalogue fixes NO axis convention — length/width/height are whichever way
// the vendor drawing happened to be read (real TO-220 records store the 15.2 mm
// tab axis under "height"), so this bounds the SMALLEST of the three dimensions,
// which is the body thickness whichever field holds it. That also sidesteps the
// lead span, which varies 6.0 -> 10.3 mm between the narrow and wide SOIC bodies.
//
// Only outlines whose thickness is fixed by one drawing are listed. Power and
// module bricks are absent, and so are QFN/DFN/SON: their thickness genuinely
// varies with the vendor's top-side-cooling variant, so no single bound holds.
void check_package_envelope(const json& ds, const Ctx& ctx, std::vector<Finding>& out) {
    const json* mech = at(ds, "mechanical");
    if (mech == nullptr || !mech->is_object()) return;
    if (!mech->contains("case") || !(*mech)["case"].is_string()) return;
    const std::string kase = (*mech)["case"].get<std::string>();

    // All three axes must be present: the thinnest of a partial set is not the
    // thickness, and guessing which axis is missing would invent geometry.
    double thickness = 0.0;
    for (const char* axis : {"length", "width", "height"}) {
        auto v = scalar_at(*mech, {axis, "nominal"});
        if (!v || *v <= 0.0) return;
        if (thickness == 0.0 || *v < thickness) thickness = *v;
    }

    struct Envelope {
        const char* pattern;
        double max_thickness;  // [m], the tallest legal variant of the outline
    };
    static const Envelope ENVELOPES[] = {
        // Small-outline plastic bodies. 2.65 mm is the tallest legal SOIC
        // (MS-013 wide); the bound is set at 3 mm so only gross contradictions
        // — a power body, not a thick-variant rounding — are called impossible.
        {R"((SO|SOIC|SOP|DSO)-?\d*\b.*)", 3.0e-3},
        {R"((MSOP|TSSOP|SSOP|VSSOP|QSOP)-?\d*\b.*)", 2.2e-3},
        {R"(SOT-?(23|323|343|346|363|416|523|723)\b.*)", 1.6e-3},  // not SOT-223/227
        {R"(SC-?70\b.*)", 1.4e-3},
        {R"(SOD-?(80|123|128|323|523|882|923|962)\b.*)", 1.3e-3},  // SMD SODs only
        // Tab-mount power bodies, where the tab side is still a fixed thickness.
        {R"(((PG-)?TO-?25[23]|D-?PAK)\b.*)", 2.6e-3},  // DPAK, 2.38 mm max
        {R"(((PG-)?TO-?263|D2PAK)\b.*)", 5.0e-3},      // D2PAK, 4.70 mm max
    };
    for (const auto& e : ENVELOPES) {
        if (!std::regex_match(kase, std::regex(e.pattern, std::regex::icase))) continue;
        if (thickness <= e.max_thickness) return;
        emit(out, ctx, "GEN_PACKAGE_ENVELOPE", Severity::Impossible, thickness,
             e.max_thickness,
             "mechanical.case '" + kase + "' has no variant thicker than " +
                 std::to_string(e.max_thickness * 1e3) +
                 " mm, but the record's smallest body dimension is " +
                 std::to_string(thickness * 1e3) + " mm — a larger package's body");
        return;
    }
}

// GEN_SERIES_IS_MANUFACTURER: the series/family slot holding a verbatim copy of the
// record's OWN manufacturer name. A vendor parametric feed reports a sub-brand column
// (TE: AMP, Buchanan, Raychem, DEUTSCH) and, where the part belongs to no sub-brand,
// repeats the house name in it; an importer that copies the column unconditionally
// writes 'TE Connectivity' into series (ABT #506, 12,914 connectors). It is not a
// series — it carries no device-class information, and series-based family matching
// reads it as if it did. The honest value is null. SUSPICIOUS: a provenance defect,
// not a physics impossibility. The test is equality with the record's own
// manufacturer, not a list of vendor names, so it holds as the catalogue grows.
void check_series_is_manufacturer(const json& ds, const Ctx& ctx, std::vector<Finding>& out) {
    if (ctx.component_obj == nullptr) return;
    const json* mi = at(*ctx.component_obj, "manufacturerInfo");
    if (mi == nullptr) return;
    const std::string name = norm_tech(at(*mi, "name"));
    if (name.empty()) return;
    struct Slot { const char* label; const json* value; };
    const Slot SLOTS[] = {{"datasheetInfo.part.series", at(ds, "part", "series")},
                          {"manufacturerInfo.family", at(*mi, "family")}};
    for (const auto& s : SLOTS) {
        if (norm_tech(s.value) != name) continue;
        emit(out, ctx, "GEN_SERIES_IS_MANUFACTURER", Severity::Suspicious, 0, 0,
             std::string(s.label) + " is a verbatim copy of the manufacturer name '" +
                 (*at(*mi, "name")).get<std::string>() + "' — that is not a product series");
        return;
    }
}

// GEN_CITATION_SEARCH_QUERY: the record's only citation is a search-engine query
// ("...?q=...", ".../search?...", "...?search=..."), not a direct link to a
// document. Cheap and general on purpose: in the 2026-09-04 fabrication sweep it
// co-occurred with nearly every batch found that day (the letter-suffix diode/
// IGBT clones, the mosfet capacitance-formula cohort) because a generator that
// invents a part number invents its "citation" the same way, by pasting the part
// number into a search box, rather than by finding a real document. SUSPICIOUS
// only, never IMPOSSIBLE: measured live, 659 records across mosfets/diodes/igbts
// still carry this shape and most are real-but-unverified parts awaiting a proper
// citation, not fabrications — this is an advisory flag for review, one signal
// among several, not a verdict on its own.
//
// ABT (adversarial review, 2026-09-04): the regex alone does not distinguish a
// THIRD-PARTY aggregator (datasheetpdf.com — the actual target this check was
// written for) from a MANUFACTURER'S OWN product-finder/search page. 276 of 872
// matches were first-party (213 sitime.com/products?search=, 63
// vishay.com/search?searchText=, including real, iconic parts — IRF530NPBF,
// IRFP250NPBF, SIR158DP). The old wording ("not verified against a source") is a
// FALSE STATEMENT for those: the citation IS the manufacturer, just not a direct
// PDF link. Detect the first-party case (host label matches the record's own
// manufacturer name) and use accurate wording for it instead — still SUSPICIOUS
// (a search-style URL is still not a citation to the specific document either
// way), but no longer claiming the record is unverified when it plainly names its
// own maker.
std::string url_host_label(const std::string& url) {
    static const std::regex RE(R"(^[a-zA-Z][a-zA-Z0-9+.-]*://(?:www\.)?([^/:?#]+))");
    std::smatch m;
    if (!std::regex_search(url, m, RE)) return "";
    std::string host = m[1].str();
    std::string label = host.substr(0, host.find('.'));
    std::string out;
    for (char c : label) out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return out;
}

bool is_first_party_url(const std::string& url, const std::string& mfr_norm) {
    if (mfr_norm.empty()) return false;
    std::string host_label = url_host_label(url);
    // A label under 3 chars ("st", "ti"...) is too short to trust as a match on
    // its own merit -- it would be a substring of dozens of unrelated words.
    if (host_label.size() < 3) return false;
    return mfr_norm.find(host_label) != std::string::npos ||
           host_label.find(mfr_norm) != std::string::npos;
}

void check_citation_search_query(const json& ds, const Ctx& ctx, std::vector<Finding>& out) {
    std::string mfr_norm;
    if (ctx.component_obj != nullptr) {
        const json* mi = at(*ctx.component_obj, "manufacturerInfo");
        if (mi != nullptr) mfr_norm = norm_tech(at(*mi, "name"));
        if (mi != nullptr && mi->contains("datasheetUrl") && (*mi)["datasheetUrl"].is_string()) {
            const std::string url = (*mi)["datasheetUrl"].get<std::string>();
            if (is_search_query_url(url)) {
                std::string msg = is_first_party_url(url, mfr_norm)
                    ? "manufacturerInfo.datasheetUrl is a product-finder/search query on the "
                      "manufacturer's OWN site ('" + url + "'), not a direct link to the "
                      "datasheet PDF"
                    : "manufacturerInfo.datasheetUrl is a search-query link ('" + url +
                      "'), not a document — the record was not verified against a source";
                emit(out, ctx, "GEN_CITATION_SEARCH_QUERY", Severity::Suspicious, 0, 0, msg);
                return;
            }
        }
    }
    const json* prov = at(ds, "provenance");
    if (prov != nullptr && prov->is_array())
        for (const auto& p : *prov) {
            if (!p.is_object() || !p.contains("sourceUrl") || !p["sourceUrl"].is_string())
                continue;
            const std::string url = p["sourceUrl"].get<std::string>();
            if (is_search_query_url(url)) {
                std::string msg = is_first_party_url(url, mfr_norm)
                    ? "datasheetInfo.provenance cites a product-finder/search query on the "
                      "manufacturer's OWN site ('" + url + "'), not a direct link to the "
                      "datasheet PDF"
                    : "datasheetInfo.provenance cites a search-query link ('" + url +
                      "'), not a document — the record was not verified against a source";
                emit(out, ctx, "GEN_CITATION_SEARCH_QUERY", Severity::Suspicious, 0, 0, msg);
                return;
            }
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

    check_fabricated_mpn(ds, ctx, out);
    check_package_mount(ds, ctx, out);
    check_package_envelope(ds, ctx, out);
    check_family_coherence(ds, ctx, out);
    check_series_is_manufacturer(ds, ctx, out);
    check_citation_search_query(ds, ctx, out);
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

// Diodes are scored per part.subType, because the SAS diode schema itself says the
// subtypes carry disjoint datasheet field sets: diode.json's datasheetInfo.allOf
// requires {breakdownVoltage, powerDissipation} of a zener, {standoffVoltage,
// clampingVoltage, one of peakPulseCurrent/peakPulsePower} of a TVS,
// {standoffVoltage, one of peakPulseCurrent/peakPulsePower/esdVoltageContact} of an
// ESD diode and {reverseVoltage, forwardVoltage, forwardCurrent} of everything else.
// That schema conditional is the non-circular ground truth for "what a real
// datasheet of this subtype always carries" — it is what made a single diode
// manifest false-flag ~45% of real parts and the family unscored until now. Each
// list adds ONE field beyond the schema's required set that every real datasheet of
// that subtype publishes but the schema leaves optional, so the score has range
// instead of being a constant 1.0: I_ZT/Z_ZT for a zener (V_Z is meaningless without
// the current it is measured at), V_BR for a TVS, C_j and V_C for an ESD diode
// (line capacitance is that part's headline spec), I_FSM/I_R for a rectifying diode.
const std::vector<std::string>* diode_core_fields(const json& datasheet) {
    static const std::vector<std::string> ZENER = {"breakdownVoltage", "powerDissipation",
                                                   "zenerTestCurrent|zenerImpedance"};
    static const std::vector<std::string> TVS = {"standoffVoltage", "clampingVoltage",
                                                 "peakPulseCurrent|peakPulsePower",
                                                 "breakdownVoltage"};
    static const std::vector<std::string> ESD = {
        "standoffVoltage", "peakPulseCurrent|peakPulsePower|esdVoltageContact|esdVoltageAir",
        "junctionCapacitance", "clampingVoltage|breakdownVoltage"};
    // Every other subType (rectifier/schottky/sicSchottky/fastRecovery/ultrafast/
    // switching/pin) AND a record with no subType at all: the schema's else-branch.
    static const std::vector<std::string> RECTIFYING = {"reverseVoltage", "forwardVoltage",
                                                        "forwardCurrent",
                                                        "surgeCurrent|reverseLeakageCurrent"};
    const json* st = at(datasheet, "part", "subType");
    if (st != nullptr && st->is_string()) {
        const std::string s = st->get<std::string>();
        if (s == "zener") return &ZENER;
        if (s == "tvs") return &TVS;
        if (s == "esd") return &ESD;
    }
    return &RECTIFYING;
}

// Per-family core-field manifest: the electrical fields a real datasheet of this
// family always (or nearly always) carries, curated from live-catalog field-
// presence statistics (2026-06-24). completeness = fraction present; a record well
// below the floor is sparse — the signature of a near-empty fabricated record.
//
// An entry containing a '.' is a path from datasheetInfo rather than a key inside
// datasheetInfo.electrical. Connector accessories need it: their content lives in
// accessoryDetails and hostSystem, and four fifths of them have no electrical
// object at all.
// Connectors are scored per familyDetails.family, for the reason diodes are
// scored per part.subType: the families do not share one defining rating, and
// CONAS says so itself. connector.json's datasheetInfo requires
// `electrical.ratedCurrentPerContact` of every family EXCEPT rf, whose $comment
// reads "RF/coaxial connectors are characterised by characteristicImpedance
// (required on familyRf), frequency and VSWR, not a published per-contact DC
// current". A manifest that knew only the current spelling called 10,615 of
// 10,619 live rf rows sparse -- 100.0% of the family, and 6.5% of every
// GEN_SPARSE finding on the 393,710-row catalogue -- while those rows carry a
// characteristic impedance on 100% of them and a dielectric withstanding
// voltage on 7,393.
//
// Per-family rather than a '|' alternation on one shared list: the alternations
// elsewhere in the table (relay `contacts|input`, the controller categories)
// exist where the RECORD's own shape picks the spelling and there is no declared
// discriminator to branch on. A connector declares its family, so the rule can
// say what CONAS says -- only rf is characterised by impedance -- instead of
// letting any family satisfy the slot with a mis-filed impedance. Adding the
// next family whose published rating differs is one line here, the same way a
// diode subtype is.
//
// ratedVoltage stays required of every family, rf included. The other eleven
// fill it thinly (pinHeaderSocket 24.8%, circular 25.3%, power 35.0%) and that
// is a real sourcing gap, not a vocabulary mismatch; so is the 4,182 rf rows'
// missing working voltage. Note the PATH: characteristicImpedance lives under
// familyDetails, not under electrical.
const std::vector<std::string>* connector_core_fields(const json& datasheet) {
    static const std::vector<std::string> RF = {"ratedVoltage",
                                                "familyDetails.characteristicImpedance"};
    static const std::vector<std::string> CONTACTED = {"ratedVoltage",
                                                       "ratedCurrentPerContact"};
    const json* fam = at(datasheet, "familyDetails", "family");
    if (fam != nullptr && fam->is_string() && fam->get<std::string>() == "rf") return &RF;
    return &CONTACTED;
}

const std::vector<std::string>* core_fields(const std::string& c, const json& datasheet) {
    if (c == "diode") return diode_core_fields(datasheet);
    if (c == "connector") return connector_core_fields(datasheet);
    static const std::map<std::string, std::vector<std::string>> M = {
        // "a|b" lists ALTERNATE spellings of one field; present in either form counts.
        // An inductor carries a singular `dcResistance`, a common-mode choke or
        // transformer a plural `dcResistances[]`. Counting only the singular capped
        // every plural-shape row at 0.50 completeness however complete it really was -
        // and 4,450 rows use that shape. This is the ABT #387 blindness in a fourth
        // place (ABT #448), and it was self-concealing: the floor below was calibrated
        // to "real-part min ~0.50", but that 0.50 was this bug, not a property of the
        // catalogue.
        {"magnetic", {"inductance", "dcResistance|dcResistances"}},
        // The AC spelling is the SAME field for a safety capacitor that has no DC
        // rating at all: CAS 96278ad (user-approved) lets an AC-only class-X/Y part
        // omit `ratedVoltage` and carry `voltageRatedAcMax` instead. Counting only
        // the DC spelling dropped 261 migrated TDK rows from 1.00 to 0.50
        // completeness and fired GEN_SPARSE on every one of them -- the data is not
        // sparse, the manifest did not know the second spelling. Exactly the
        // blindness `dcResistance|dcResistances` above exists for, and the same
        // self-concealing shape: the "real-part min" a floor is calibrated against
        // is only real if the manifest can see every spelling of the field.
        {"capacitor", {"capacitance", "ratedVoltage|voltageRatedAcMax"}},
        {"resistor", {"resistance", "powerRating", "tolerance"}},
        {"mosfet",
         {"onResistance", "drainSourceVoltage", "continuousDrainCurrent", "gateThresholdVoltage"}},
        // "diode" is not in this map: it is scored per part.subType by
        // diode_core_fields() above, which core_fields() dispatches to first.
        {"igbt",
         {"collectorEmitterVoltage", "collectorEmitterSaturation", "continuousCollectorCurrent"}},
        {"bjt", {"collectorEmitterVoltage", "collectorCurrent"}},
        {"varistor", {"varistorVoltage", "clampingVoltage", "peakSurgeCurrent"}},
        // "connector" is not in this map: it is scored per familyDetails.family by
        // connector_core_fields() above, which core_fields() dispatches to first.
        // Relay / switch: the electrical block is NESTED (contacts / input /
        // isolation / trip are objects, not scalars), so the manifest names those
        // sub-objects. A single '|'-joined entry, not several AND'd fields, for the
        // controller reason: a solid-state relay carries `input` and a bare switch
        // carries only `contacts`, and AND-ing them would call every correct
        // single-block record sparse. Measured live, 97.8% of relays and 99.5% of
        // switches carry at least one; what scores 0.0 is a row whose electrical
        // block is absent entirely -- an identity-only stub, which is the signature
        // this score exists to catch.
        {"relay", {"contacts|input"}},
        {"switch", {"contacts|trip"}},
        // Potentiometer: totalResistance and powerRating are on 100% of the live
        // rows and are the two numbers that make the part a potentiometer rather
        // than a knob. Both AND'd, so a row carrying one of them scores 0.50 and
        // trips the 0.60 floor.
        {"potentiometer", {"totalResistance", "powerRating"}},
        // connectorAccessory is NOT an electrical family: a marker strip, a coding
        // key, a gasket, a crimp tool have no ratings, and 14,735 of the 18,227 live
        // rows (80.8%) carry no `electrical` object whatsoever. Scoring them off
        // `electrical` would put four fifths of a sound catalogue at 0.0, which is
        // why the family went unscored. What an accessory record actually carries is
        // (a) a class descriptor -- the CONAS accessoryDetails union's own per-class
        // fields, plus the contact branch's ratings -- and (b) the host system it
        // fits. Both entries are '|'-joined, not AND'd, because the twenty-one
        // classes are disjoint by construction: a hood publishes a shell size and a
        // cable exit, a gland a thread, a marker its legend, a contact a wire gauge
        // and a termination. Measured live: 41.9% carry both, 30.7% one, 27.4%
        // neither. The 27.4% are real vendor parts (TE tooling and hoods whose only
        // structured content is kind + part number), so the family's real-part
        // minimum is 0.0 and it gets NO sparse floor -- see sparse_floor().
        {"connectorAccessory",
         {"accessoryDetails.cableExit|accessoryDetails.cableDiameterRange|"
          "accessoryDetails.wireGaugeRange|accessoryDetails.terminationStyle|"
          "accessoryDetails.contactSize|accessoryDetails.contactRetention|"
          "accessoryDetails.markingText|accessoryDetails.shielded|"
          "accessoryDetails.hardwareType|accessoryDetails.sealType|"
          "accessoryDetails.threadDesignation|accessoryDetails.shellSize|"
          "accessoryDetails.codingPosition|accessoryDetails.portsWide|"
          "accessoryDetails.portsHigh|accessoryDetails.interfaceA|"
          "accessoryDetails.interfaceB|accessoryDetails.toolType|"
          "accessoryDetails.tethered|electrical.ratedCurrentPerContact|"
          "electrical.ratedVoltage|electrical.contactResistance|"
          "electrical.characteristicImpedance",
          "hostSystem.series|hostSystem.standard|hostSystem.matesWithPartNumbers|"
          "hostSystem.manufacturer|hostSystem.shellSize"}},
        // Thermistor: R25 is the single universal field; B constant is NTC-only and
        // absent on PTC, so it is not in the core manifest (would false-flag PTC).
        {"thermistor", {"resistanceAt25C"}},
        // Time bases (TDAS). The oscillator schema requires no electrical field at
        // all, so the manifest comes from what a timing-device datasheet always
        // publishes: the nominal/resonant frequency, an accuracy figure, and the
        // interface it presents. The third entry is '|'-joined because active and
        // passive devices are disjoint by design -- an XO/TCXO/OCXO states a supply
        // and an output type, a bare crystal or ceramic resonator states its load
        // capacitance and ESR (or resonant impedance / built-in capacitance)
        // instead. AND-ing them would call every real crystal sparse. `technology`
        // is deliberately NOT counted: it is on 100% of rows because it is
        // parametric tagging, and a manifest entry no record can fail measures
        // nothing.
        {"oscillator",
         {"frequency", "frequencyStability|frequencyTolerance",
          "supply|outputType|loadCapacitance|equivalentSeriesResistance|resonantImpedance|"
          "builtInCapacitance"}},
        // Timer / latch: supply rail, devices per package (a 556 is two timers), and
        // the timing figure the part exists for. All 15 live timer rows carry the
        // first two and neither of the last, so they score 0.67 -- a real gap the
        // score now reports, not a reason to doubt the parts.
        {"timer", {"supply", "numberOfChannels", "maximumFrequency|timingAccuracy"}},
        {"latch", {"supply", "numberOfChannels", "propagationDelay"}},
        //
        // ABT #1015: "controller" and 11 of the 14 AAS analog-IC discriminators had
        // NO manifest at all -- compute_completeness() returned -1 unconditionally,
        // so GEN_SPARSE could never fire no matter how empty a row was. Measured
        // live: 1,665 of 2,133 controller rows (78%) carry ONLY identity/category/
        // provenance and no datasheetInfo.electrical object whatsoever -- a full
        // validator sweep of that catalog returned 0 findings, indistinguishable
        // from "checked and sound". A single '|'-joined entry (not several AND'd
        // fields) is used deliberately: controller categories (pwmController vs
        // gateDriver vs pfcController...) and several AAS subtypes carry disjoint
        // field sets by DESIGN (a gate driver has gateDrive+isolation, a PWM
        // controller has switchingFrequencyMax+currentMode, neither has the
        // other's fields) -- an AND'd manifest across categories would false-flag
        // real parts as sparse; presence of ANY ONE real spec field is what
        // distinguishes a sourced record from a bare parametric-tagging stub.
        {"controller",
         {"supplyVoltage|switchingFrequencyMin|switchingFrequencyMax|gateDrive|isolation|"
          "currentMode|referenceVoltage|shuntReference|syncRectifier|maxDutyCycle|deadTime|"
          "uvlo|supplyVoltageAbsoluteMax"}},
        // AAS amplifier family (operationalAmplifier/buffer/differenceAmplifier/
        // instrumentationAmplifier/programmableGainAmplifier/sampleHold) all route
        // through check_amplifier() in analog.cpp -- same shared field set.
        {"operationalAmplifier",
         {"numberOfChannels|supply|slewRate|inputOffsetVoltage|gainBandwidthProduct|"
          "commonModeRejectionRatio|gain|minimumGain"}},
        {"buffer",
         {"numberOfChannels|supply|slewRate|inputOffsetVoltage|gainBandwidthProduct|"
          "commonModeRejectionRatio|gain|minimumGain"}},
        {"differenceAmplifier",
         {"numberOfChannels|supply|slewRate|inputOffsetVoltage|gainBandwidthProduct|"
          "commonModeRejectionRatio|gain|minimumGain"}},
        {"instrumentationAmplifier",
         {"numberOfChannels|supply|slewRate|inputOffsetVoltage|gainBandwidthProduct|"
          "commonModeRejectionRatio|gain|minimumGain"}},
        {"programmableGainAmplifier",
         {"numberOfChannels|supply|slewRate|inputOffsetVoltage|gainBandwidthProduct|"
          "commonModeRejectionRatio|gain|minimumGain"}},
        {"sampleHold",
         {"numberOfChannels|supply|slewRate|inputOffsetVoltage|gainBandwidthProduct|"
          "commonModeRejectionRatio|gain|minimumGain"}},
        {"comparator", {"numberOfChannels|supply|propagationDelay|inputOffsetVoltage"}},
        {"adc", {"resolution|sampleRate|updateRate|referenceVoltage|numberOfChannels|supply"}},
        {"dac", {"resolution|sampleRate|updateRate|referenceVoltage|numberOfChannels|supply"}},
        // analogSwitch's channel-count field is `numberOfSwitches`, NOT
        // `numberOfChannels` (multiplexer's field, one line down) -- a minimal-but-
        // real TI TMDS/DisplayPort switch (numberOfSwitches + switchConfiguration
        // only, no onResistance/supply extracted) false-fired GEN_SPARSE against
        // `numberOfChannels` before this was caught (ABT #1015 counter-check).
        {"analogSwitch",
         {"onResistance|offLeakageCurrent|numberOfSwitches|switchConfiguration|supply"}},
        {"multiplexer",
         {"onResistance|offLeakageCurrent|numberOfChannels|multiplexerConfiguration|supply"}},
        {"multiplier", {"scaleFactor|totalError|bandwidth|supply"}},
        // integrator/summer are intentionally omitted: check_analog() (analog.cpp)
        // treats them as behavioral-only atoms with NO electrical block by design
        // (schema has nothing to put there) -- a manifest would score every real
        // one of them 0.0 and false-flag the entire subtype as sparse.
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
    // Diode real-part min is 0.50: 169 live Infineon ESD parts publish only V_RWM
    // and an IEC 61000-4-2 contact rating (2 of the ESD manifest's 4). Zeners bottom
    // at 0.67, TVS and rectifying diodes at 0.75.
    if (c == "diode") return 0.40;
    // Oscillator real-part min is 0.33: four Abracon/Murata bare crystals carry only
    // technology and outputType 'none'. Timer/latch mirror the other 3-entry
    // families; live timer min is 0.67.
    if (c == "oscillator") return 0.30;
    if (c == "timer" || c == "latch") return 0.50;
    // connectorAccessory has NO floor. Its real-part minimum is measured at 0.0 --
    // 4,999 live rows are genuine TE tooling, hoods and covers whose only structured
    // content is the accessory kind and the part number -- so any floor above zero
    // would call real parts unreal. `comp < 0.0` is never true, so the score is
    // published for the enrichment queue and GEN_SPARSE stays silent on the family.
    if (c == "connectorAccessory") return 0.0;
    return 0.60;                       // cap / res / mosfet / varistor / connector (real min 1.0)
}

// Fraction of the family's core fields present in datasheetInfo.electrical (or
// electrical[0] for the magnetics array). Returns -1 if no manifest exists.
double compute_completeness(const std::string& component, const json& datasheet) {
    const std::vector<std::string>* core = core_fields(component, datasheet);
    if (core == nullptr || core->empty()) return -1.0;
    const json* elec = at(datasheet, "electrical");
    const json* obj = nullptr;
    if (elec && elec->is_array() && !elec->empty() && elec->front().is_object())
        obj = &elec->front();
    else if (elec && elec->is_object())
        obj = elec;
    // A manifest whose every alternative is a bare key can only be answered from
    // `electrical`; with no such object the record is maximally sparse and the walk
    // below would say so anyway. Returning early keeps the electrical-only families
    // bit-for-bit as they were, and is skipped for a manifest that names sections
    // outside `electrical` (connectorAccessory), where an absent electrical object
    // says nothing about the record.
    bool any_path = false;
    for (const auto& f : *core)
        if (f.find('.') != std::string::npos) any_path = true;
    if (obj == nullptr && !any_path) return 0.0;
    int present = 0;
    for (const auto& f : *core) {
        // A manifest entry may name alternate spellings of the same field, "a|b".
        bool found = false;
        for (size_t start = 0; start <= f.size() && !found;) {
            const size_t bar = f.find('|', start);
            const std::string name =
                f.substr(start, bar == std::string::npos ? std::string::npos : bar - start);
            if (!name.empty()) {
                const size_t dot = name.find('.');
                if (dot == std::string::npos) {
                    if (obj != nullptr && obj->contains(name) && !(*obj)[name].is_null())
                        found = true;
                } else {
                    // "section.field" — a path from datasheetInfo, not a key inside
                    // electrical.
                    const std::string section = name.substr(0, dot);
                    const std::string leaf = name.substr(dot + 1);
                    if (datasheet.is_object() && datasheet.contains(section) &&
                        datasheet[section].is_object() && datasheet[section].contains(leaf) &&
                        !datasheet[section][leaf].is_null())
                        found = true;
                }
            }
            if (bar == std::string::npos) break;
            start = bar + 1;
        }
        if (found) ++present;
    }
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
        "magnetic", "capacitor", "resistor", "varistor", "thermistor", "connector", "controller",
        "semiconductor", "timeBase", "relay", "switch", "potentiometer", "connectorAccessory",
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
        ctx.component_obj = &comp_obj;
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
    } else if (part.contains("thermistor")) {
        run("thermistor", part["thermistor"], &check_thermistors);
    } else if (part.contains("relay")) {
        run("relay", part["relay"], &check_relays);
    } else if (part.contains("switch")) {
        run("switch", part["switch"], &check_switches);
    } else if (part.contains("potentiometer")) {
        run("potentiometer", part["potentiometer"], &check_potentiometers);
    } else if (part.contains("connectorAccessory")) {
        run("connectorAccessory", part["connectorAccessory"], &check_connector_accessories);
    } else if (part.contains("controller")) {
        run("controller", part["controller"], &check_controllers);
    } else if (part.contains("timeBase")) {
        // TDAS: {"timeBase": {"oscillator"|"timer"|"latch": {...}}}. A record may
        // be a part-less behavioral atom (no manufacturerInfo), so the behavioral
        // screen runs independently of the datasheet pipeline.
        const json& tb = part["timeBase"];
        const char* sub = nullptr;
        void (*fn)(const json&, const Ctx&, std::vector<Finding>&,
                   std::vector<std::string>&) = nullptr;
        if (tb.contains("oscillator")) { sub = "oscillator"; fn = &check_oscillators; }
        else if (tb.contains("timer")) { sub = "timer"; fn = &check_timers; }
        else if (tb.contains("latch")) { sub = "latch"; fn = &check_latches; }
        else
            throw std::invalid_argument("timeBase record has no oscillator/timer/latch sub-object");
        const json& comp = tb[sub];
        if (comp.contains("manufacturerInfo"))
            run(sub, comp, fn);
        else if (!comp.contains("behavioral"))  // empty pre-sourcing seed
            v.skipped.push_back(std::string(sub) + ":no-datasheetInfo");
        if (comp.contains("behavioral")) {
            Resolved r = resolve(comp);
            ctx.component = sub;
            ctx.reference = r.reference;
            check_time_base_behavioral(comp["behavioral"], ctx, v.findings, v.skipped);
        }
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
        // AAS analog ICs. The subtype (operationalAmplifier / comparator / analogSwitch /
        // multiplexer / adc / dac / ...) is the discriminator. It appears either at the top
        // level (a bare AAS document, e.g. the schema examples) OR nested under the `analog`
        // PEAS discriminator (`{"analog": {"<subtype>": {...}}}`, the shape stored in
        // TAS/data/analog_ics.ndjson) — accept both.
        static const char* AAS[] = {
            "operationalAmplifier", "comparator", "instrumentationAmplifier",
            "differenceAmplifier", "programmableGainAmplifier", "buffer", "sampleHold",
            "analogSwitch", "multiplexer", "adc", "dac", "multiplier", "integrator", "summer"};
        const json& aas = part.contains("analog") && part["analog"].is_object() ? part["analog"]
                                                                                 : part;
        const char* hit = nullptr;
        for (const char* k : AAS)
            if (aas.contains(k)) { hit = k; break; }
        if (hit != nullptr)
            run(hit, aas[hit], &check_analog);
        else if (part.contains("ports") && part.contains("components"))
            // A CIAS brick, not a part. It has its own entry point; saying so is
            // the difference between "this record cannot be judged" and "this
            // record was handed to the wrong gate". TAS/data/circuits.ndjson is
            // 25,234 rows of exactly this, and a sweep that pushes them through
            // validate() counts every one as unjudgeable.
            throw std::invalid_argument(
                "record is a CIAS circuit brick (it has ports[] and components[]), not a "
                "component part — use validate_circuit()");
        else if (part.contains("topology"))
            // A whole TAS converter document (inputs + topology + stages of CIAS
            // bricks). It is a DESIGN, not a catalogue part: there is nothing for
            // a per-part physics gate to judge, and its bricks are validated
            // individually by validate_circuit(). TAS/data/converters.ndjson does
            // not belong in a part sweep at all.
            throw std::invalid_argument(
                "record is a TAS converter document (it has a topology), not a component part "
                "— validate its stages' CIAS bricks with validate_circuit() instead");
        else
            throw std::invalid_argument(
                "no known component discriminator (magnetic/capacitor/resistor/varistor/"
                "connector/thermistor/relay/switch/potentiometer/connectorAccessory/"
                "semiconductor/analog-AAS)");
    }

    for (const auto& f : v.findings)
        if (f.severity == Severity::Impossible) v.valid = false;

    return v;
}

Verdict PartValidator::validate_json(const std::string& text) const {
    return validate(json::parse(text));
}

// ABT #549: this list used to be hand-typed here and drifted from the actual
// emit() call sites twice (GEN_PACKAGE_ENVELOPE, GEN_FABRICATED_MPN; then
// DIO_LEAKAGE_VS_IF was mid-flight when the drift was found a third time).
// tools/gen_check_codes.py derives this .inc from every emit()/forwarded-code/
// CorpusFinding call site under src/ (everything except circuits.cpp, which has
// its own circuit_check_codes() inventory below) at BUILD time, via the CMake
// custom command in CMakeLists.txt -- nobody edits this list by hand anymore.
// tests/test_validator.cpp re-derives it again at TEST time and asserts it
// still matches, so a stale generated file (this command not re-run, or the
// .inc hand-edited) fails the suite instead of silently drifting again.
std::vector<std::string> PartValidator::check_codes() {
    return
#include "tas_validator/check_codes.inc"
        ;
}

}  // namespace tas
