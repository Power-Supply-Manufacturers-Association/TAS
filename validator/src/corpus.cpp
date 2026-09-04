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
#include <regex>
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
    std::string url;                  // manufacturerInfo.datasheetUrl, if a string
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
    if (mi->contains("datasheetUrl") && (*mi)["datasheetUrl"].is_string())
        r.url = (*mi)["datasheetUrl"].get<std::string>();
    // Sub-cohort key: compare like-with-like. (manufacturer, component) alone lumps
    // different dielectrics / voltage classes together, collapsing the MAD and
    // exploding z-scores. technology+subType+series narrows to comparable parts.
    const json* part_obj = at(*mi, "datasheetInfo", "part");
    if (part_obj)
        for (const char* k : {"technology", "subType", "series"})
            if (part_obj->contains(k) && (*part_obj)[k].is_string()) {
                if (!r.sub.empty()) r.sub += "/";
                r.sub += (*part_obj)[k].get<std::string>();
            }
    // ABT (adversarial review, 2026-09-04): manufacturerInfo.reference is absent on
    // some real records (the KEMET T495 rows below carry only datasheetInfo.part.
    // partNumber) -- falling back to it here, rather than treating the record as
    // unidentifiable, is what keeps a corpus finding on such a row ACTIONABLE by
    // part number instead of reporting an empty '' reference.
    if (r.ref.empty() && part_obj && part_obj->contains("partNumber") &&
        (*part_obj)["partNumber"].is_string())
        r.ref = (*part_obj)["partNumber"].get<std::string>();
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

// Extract a named field from an electrical object via field_scalar (bare number OR
// {nominal|minimum|maximum}). Returns nullopt if the key is absent.
std::optional<double> elec_field(const json& elec, const char* key) {
    if (!elec.is_object() || !elec.contains(key)) return std::nullopt;
    return field_scalar(elec[key]);
}

// Quantize a double to 9 significant digits for exact-repeat bucketing. A raw
// double as a map key is brittle (two computations of the "same" value can differ
// in the last ULP); a fabrication generator's output does not — every member was
// produced by literally the same formula call, so this ties together only true
// bit-for-bit (or near enough) repeats, never two independently-measured values
// that merely round to the same few significant figures.
std::string quantize9(double x) {
    std::ostringstream oss;
    oss.precision(9);
    oss << x;
    return oss.str();
}

// ---- letter-suffix degenerate cohort (P9, ABT #1014/#1011-class, 2026-09-04) --
// A part-number STEM with a run of 1-2 trailing uppercase letters appended
// directly after a digit (FFSP2012 + {A..T}, DSSK40 + {A..Y}, NGB20N60 + {A..T}).
//
// ABT (adversarial review, 2026-09-04): the FIRST version of this check gated on
// byte-identical block AND a search-query citation, and that was wrong on two
// counts, both now fixed:
//
// 1. Citation shape is not a property of the PART, it is a property of the
//    SOURCING BATCH that happened to fetch it. 1,561 real cohorts / 12,641 real
//    records (KEMET T222A105K020+CS/BS/PS/SS packaging codes, Molex
//    MM-212-009-161-00+WV/YM/CB plating codes, Vanguard CRFB1206A-900+PS/PR/MS/MR
//    tolerance-x-reel codes, Nexperia BAT854+AW/CW/SW, and Vishay PLZ10A..D -- the
//    live instance of the exact TLZ39A..G trap this file already warned about)
//    were saved ONLY because today's citations happen not to be search queries.
//    Re-source any of them through an aggregator and the OLD rule condemns real
//    parts. The fix does not use citation shape as a gate at all: the real,
//    PERMANENT signature is in the trailing letters THEMSELVES -- see
//    suffix_alphabet_run_includes_io below. (Citation shape is still noted in the
//    finding message as corroborating context when EVERY member happens to carry
//    one, but it decides nothing.)
// 2. The cohort was built only from records with a non-null electrical block --
//    screening the rows that happen to have data, not the family. A real 10-
//    member family where 6 rows are still unsourced would misreport as a
//    "4-member byte-identical cohort". Fixed by grouping on ALL matching-reference
//    records regardless of whether electrical data is present, then requiring
//    FULL coverage (every member of the matched-stem group must carry electrical
//    data) before judging byte-identity at all -- a real, partially-sourced
//    family is left alone; a generator's complete ladder (every row filled) is
//    not.
//
// suffix_alphabet_run_includes_io is the part-number-shape signature that
// replaced the citation gate: the cohort's trailing letters must decompose into
// exactly ONE varying position (the whole suffix if it is 1 letter; if it is 2,
// one character held fixed across the whole cohort and the OTHER varying) whose
// sorted values form a CONTIGUOUS run of the alphabet that includes 'I' or 'O' --
// real vendor grade-letter schemes (tolerance, voltage class, tape orientation)
// almost always skip those two to avoid 1/0 confusion, so a contiguous run
// spanning one is the tell of an unconstrained sequential generator, not a human
// assigned scheme. This is also what correctly rejects every false positive
// above: KEMET's varying character (C/B/P/S) is non-contiguous; Molex's and
// Vanguard's suffixes vary in BOTH character positions at once (no single axis to
// test); Vishay PLZ10A..D is contiguous but stops at D, never reaching I or O.
// Measured on the corrected rule across the full live catalogue (capacitors/
// resistors/magnetics/connectors/varistors/mosfets/diodes/igbts/bjts, ~1M
// records): 0 false positives, including against all five families named above.
// True-positive cost of dropping the citation OR-path: the Infineon
// IKW75N65/40N120/100N120 T..X cohort (15 rows, confirmed fabricated, uniformly
// search-query cited) is no longer caught here -- T..X is contiguous but contains
// neither I nor O. Accepted: a citation-based OR-path would have restored it but
// reopens exactly the re-sourcing vulnerability this fix exists to close.
// SUSPICIOUS: a statistical/generative cohort signature, not a physics violation.
constexpr std::size_t MIN_LETTER_COHORT = 4;

// See the block comment above for what this tests and why. Returns false (not
// suspicious) for anything that is not a clean single-axis enumeration: mixed
// suffix lengths, both 2-char positions varying at once, a repeated letter, a
// gap in the run, or a run that never reaches 'I' or 'O'.
bool suffix_alphabet_run_includes_io(const std::vector<std::string>& suffixes) {
    if (suffixes.empty()) return false;
    const std::size_t len = suffixes.front().size();
    for (const auto& s : suffixes)
        if (s.size() != len) return false;  // mixed 1-and-2-letter cohort: no single axis

    std::string varying;
    if (len == 1) {
        for (const auto& s : suffixes) varying += s[0];
    } else if (len == 2) {
        bool pos0_fixed = std::all_of(suffixes.begin(), suffixes.end(),
                                      [&](const std::string& s) { return s[0] == suffixes.front()[0]; });
        bool pos1_fixed = std::all_of(suffixes.begin(), suffixes.end(),
                                      [&](const std::string& s) { return s[1] == suffixes.front()[1]; });
        if (pos0_fixed && !pos1_fixed)
            for (const auto& s : suffixes) varying += s[1];
        else if (pos1_fixed && !pos0_fixed)
            for (const auto& s : suffixes) varying += s[0];
        else
            return false;  // both positions vary (an arbitrary 2D code) or neither: no single axis
    } else {
        return false;
    }

    std::set<char> uniq(varying.begin(), varying.end());
    if (uniq.size() != varying.size()) return false;  // a repeated letter is not a clean enumeration
    char lo = *uniq.begin(), hi = *uniq.rbegin();
    if (static_cast<std::size_t>(hi - lo + 1) != uniq.size()) return false;  // gap -> not contiguous
    return uniq.count('I') != 0 || uniq.count('O') != 0;
}

void letter_suffix_screen(std::vector<CorpusFinding>& out, const std::vector<Rec>& recs) {
    static const std::regex STEM_RE(R"(^(.*[0-9])([A-Z]{1,2})$)");
    // Group ALL matching-reference records regardless of whether electrical data
    // is present (see point 2 above) -- coverage is checked per-group below.
    std::unordered_map<std::string, std::vector<std::size_t>> groups;
    for (std::size_t i = 0; i < recs.size(); ++i) {
        const Rec& r = recs[i];
        if (r.mfr.empty() || r.ref.empty()) continue;
        std::smatch mm;
        if (!std::regex_match(r.ref, mm, STEM_RE)) continue;
        groups[r.mfr + "|" + mm[1].str()].push_back(i);
    }
    for (auto& [key, idxs] : groups) {
        if (idxs.size() < MIN_LETTER_COHORT) continue;

        // Full-family coverage: one sibling with no electrical data at all means
        // this is (at best) a still-being-sourced real family, not a generator's
        // complete ladder -- do not judge byte-identity on a subset.
        if (std::any_of(idxs.begin(), idxs.end(),
                        [&](std::size_t i) { return recs[i].elec == nullptr; }))
            continue;

        const json* first = recs[idxs.front()].elec;
        bool all_identical = std::all_of(idxs.begin(), idxs.end(), [&](std::size_t i) {
            return *recs[i].elec == *first;
        });
        if (!all_identical) continue;

        std::vector<std::string> suffixes;
        suffixes.reserve(idxs.size());
        for (std::size_t idx : idxs) {
            std::smatch mm;
            std::regex_match(recs[idx].ref, mm, STEM_RE);  // re-match: group(2) is the suffix
            suffixes.push_back(mm[2].str());
        }
        if (!suffix_alphabet_run_includes_io(suffixes)) continue;

        bool all_search_query = std::all_of(idxs.begin(), idxs.end(), [&](std::size_t idx) {
            return is_search_query_url(recs[idx].url);
        });
        for (std::size_t idx : idxs) {
            std::ostringstream m;
            m << "reference '" << recs[idx].ref << "' is one of " << idxs.size()
              << " letter-suffix siblings sharing stem '" << key
              << "' -- a contiguous alphabet run (including I or O, the pair real vendor grade "
                 "codes almost always skip) with a byte-identical electrical block on EVERY "
                 "member of the family -- a generated grade ladder, not "
              << idxs.size() << " distinct datasheet parts";
            if (all_search_query)
                m << " (every member also cites a search-query URL, not a document -- "
                     "corroborating, not the reason this fired)";
            out.push_back({idx, "GEN_COHORT_LETTER_SUFFIX", Severity::Suspicious, recs[idx].ref,
                           m.str(), static_cast<double>(idxs.size()), 0.0});
        }
    }
}

// ---- derived-quantity collapse (P10, ABT #560/#531, 2026-09-04) --------------
// A raw field hitting a shared CEILING (P7 above) is one fabrication signature; a
// quantity DERIVED from two fields (a product) collapsing onto a single exact
// repeated constant across an ENTIRE cohort is another, invisible to the ceiling
// screen because neither operand needs to repeat, only their product does.
//
// ABT (adversarial review, 2026-09-04) on the two consumers below:
//
// MOS_RON_IDC_COLLAPSE fired, at the time of the first review, for the WRONG
// reason: in every one of the then-live hits (a Power Integrations PIX cohort),
// onResistance itself was constant AND continuousDrainCurrent itself was
// constant -- distinctRon=1, distinctIdc=1. That is a duplicate-row / same-die-
// many-references signature, not a derived quantity collapsing while its
// operands vary (what ABT #560's real ROHM/Infineon cohorts actually look like:
// 8-21 distinct onResistance values per cohort, forced to one product). Left as
// it was, this check could not tell that duplicate-row shape apart from a REAL,
// common one it never had a test for: package variants of ONE die (TO-220 /
// D2PAK / TO-263 listings of the same part) legitimately share identical Ron AND
// Idc too. mosfet_ron_idc_collapse now requires >= 3 DISTINCT onResistance
// values in the cohort before it will call the product a "derived" collapse --
// exactly what the check's own justification always claimed to require. Cost:
// the PIX cohort (Ron constant, an actual duplicate-row artifact) is no longer
// caught HERE -- that is a different shape, for a different check, not this
// one's job.
//
// CAP_ESR_CAPACITANCE_COLLAPSE's comment claimed to target
// esr = min(DF/(2*pi*f*C), ceiling), and that specific claim does not hold: on
// the CLAMPED branch esr = ceiling (a constant regardless of C), so esr*C SCALES
// WITH C, not constant; on the UNCLAMPED branch esr = DF/(2*pi*f*C), so esr*C is
// constant only if DF and f are ALSO held fixed across the cohort (a common real
// convention -- many vendors quote one DF at one fixed test frequency for an
// entire technology/series, not per part -- but a narrower target than "the
// clamp batch" implied). A cohort mixing clamped and unclamped members has TWO
// different esr*C values and this check correctly bails (counts.size() != 1) --
// it cannot, and was never able to, catch a genuinely mixed clamp batch; that
// needs a separate check that clamps its OWN prediction from DF/f/C per record,
// not a corpus-wide product collapse. What this check DOES catch, confirmed
// live: 50 KEMET T495D107M010ATE0..49 rows -- real polymer-tantalum parts
// (mislabelled aluminum-electrolytic-wet elsewhere in the pipeline) whose
// trailing integer 0..49 is an import-duplication artifact, not 50 independent
// ESR measurements, all quoting esr=1.0 at a fixed 100 Hz. The message below no
// longer claims the min(.,ceiling) mechanism for this one confirmed hit.
//
// A real product family CAN share a design constant — GaN Systems' 650 V line
// targets Ron*Id ~ 1.5 ohm*A across several dice reused under B/P/T package
// suffixes — but never EXACTLY across the WHOLE cohort: measured live, GaN
// Systems' own 650 V group (16 members, >= 3 distinct Ron) collapses to one value
// on only 9/16 (56%) — the other 7 dice sit at 1.35-1.8 ohm*A, a real spread the
// fabricated cohorts do not have. Requiring 100% of a cohort (not just a
// majority) at the identical value is what tells a real die-sharing family from
// a generator that computed one formula's worth of rows; a fixture pinning this
// (a ~95%-collapse cohort that must NOT fire) lives in test_validator.cpp so a
// future relaxation to a majority bar fails loudly instead of just drifting.
// SUSPICIOUS, never IMPOSSIBLE, for every finding this file emits: a cohort-
// shaped statistical signature is evidence for a human to look at, not a physics
// violation.
constexpr std::size_t MIN_PRODUCT_COHORT = 8;
// Below this many distinct onResistance values, "the product is constant" is
// fully explained by "the operands are constant" (duplicate rows / package
// variants of one die) -- see the MOS_RON_IDC_COLLAPSE note above.
constexpr std::size_t MIN_DISTINCT_OPERAND = 3;

struct CollapseHit {
    std::size_t idx;
    std::string key;
    double val;
    std::size_t cohort_size;
};

// Shared detector: within each `groups` key (cohort), if EVERY member's derived
// value quantizes to the SAME bucket, return one hit per member. Deliberately
// returns data rather than emitting itself -- CorpusFinding's `code` must be a
// STRING LITERAL at the push_back call site for tools/gen_check_codes.py's static
// analysis to find it (it recognises aggregate_codes()'s brace-init literal, not a
// code forwarded through a helper parameter), so each caller below does its own
// push_back with its own literal code.
std::vector<CollapseHit> find_full_collapse(
    const std::unordered_map<std::string, std::vector<std::pair<std::size_t, double>>>& groups) {
    std::vector<CollapseHit> hits;
    for (auto& [key, vals] : groups) {
        if (vals.size() < MIN_PRODUCT_COHORT) continue;
        std::unordered_map<std::string, std::size_t> counts;
        for (auto& [idx, val] : vals) counts[quantize9(val)]++;
        if (counts.size() != 1) continue;  // every member must land in the SAME bucket
        for (auto& [idx, val] : vals) hits.push_back({idx, key, val, vals.size()});
    }
    return hits;
}

void mosfet_ron_idc_collapse(std::vector<CorpusFinding>& out, const std::vector<Rec>& recs) {
    std::unordered_map<std::string, std::vector<std::pair<std::size_t, double>>> groups;
    std::unordered_map<std::string, std::set<std::string>> distinct_ron;
    for (std::size_t i = 0; i < recs.size(); ++i) {
        const Rec& r = recs[i];
        if (r.comp != "mosfet" || r.elec == nullptr || r.mfr.empty() || r.ref.empty()) continue;
        auto ron = elec_field(*r.elec, "onResistance");
        auto idc = elec_field(*r.elec, "continuousDrainCurrent");
        auto vds = elec_field(*r.elec, "drainSourceVoltage");
        // continuousDrainCurrent is signed on P-channel parts (746 records, ~8% of
        // the live catalogue) -- the physical quantity this check cares about is
        // the MAGNITUDE of the product, so guard and multiply with std::fabs, the
        // same convention mosfets.cpp already uses everywhere else for this field.
        if (!ron || !idc || !vds || *ron <= 0 || *idc == 0.0) continue;
        std::ostringstream key;
        key << r.mfr << " @ " << *vds << " V";
        std::string k = key.str();
        groups[k].push_back({i, (*ron) * std::fabs(*idc)});
        distinct_ron[k].insert(quantize9(*ron));
    }
    for (const auto& h : find_full_collapse(groups)) {
        if (distinct_ron[h.key].size() < MIN_DISTINCT_OPERAND) continue;
        std::ostringstream m;
        m << "onResistance*continuousDrainCurrent=" << h.val << " is IDENTICAL across all "
          << h.cohort_size << " members of the '" << h.key << "' voltage-class cohort despite "
          << distinct_ron[h.key].size()
          << " distinct onResistance values — a generated constant, not " << h.cohort_size
          << " independent measurements";
        out.push_back({h.idx, "MOS_RON_IDC_COLLAPSE", Severity::Suspicious, recs[h.idx].ref,
                       m.str(), h.val, static_cast<double>(h.cohort_size)});
    }
}

void capacitor_esr_capacitance_collapse(std::vector<CorpusFinding>& out, const std::vector<Rec>& recs) {
    std::unordered_map<std::string, std::vector<std::pair<std::size_t, double>>> groups;
    for (std::size_t i = 0; i < recs.size(); ++i) {
        const Rec& r = recs[i];
        if (r.comp != "capacitor" || r.elec == nullptr || r.mfr.empty() || r.ref.empty()) continue;
        auto esr = elec_field(*r.elec, "esr");
        auto cap = elec_field(*r.elec, "capacitance");
        if (!esr || !cap || *esr <= 0 || *cap <= 0) continue;
        groups[r.mfr + "|" + r.sub].push_back({i, (*esr) * (*cap)});
    }
    for (const auto& h : find_full_collapse(groups)) {
        std::ostringstream m;
        m << "esr*capacitance=" << h.val << " is IDENTICAL across all " << h.cohort_size
          << " members of the '" << h.key
          << "' cohort — esr does not vary independently of capacitance the way a per-part "
             "measurement would, not " << h.cohort_size << " independent ESR measurements";
        out.push_back({h.idx, "CAP_ESR_CAPACITANCE_COLLAPSE", Severity::Suspicious, recs[h.idx].ref,
                       m.str(), h.val, static_cast<double>(h.cohort_size)});
    }
}

}  // namespace

std::vector<CorpusFinding> validate_corpus(const std::vector<json>& records) {
    std::vector<CorpusFinding> out;

    // Describe every record once.
    std::vector<Rec> recs(records.size());
    for (std::size_t i = 0; i < records.size(); ++i) recs[i] = describe(records[i]);

    letter_suffix_screen(out, recs);
    mosfet_ron_idc_collapse(out, recs);
    capacitor_esr_capacitance_collapse(out, recs);

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
                        out.push_back({p.first, "GEN_COHORT_CEILING", Severity::Suspicious,
                                       recs[p.first].ref, m.str(), mx, mx / raw_med});
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
                    out.push_back({ri, "GEN_COHORT_OUTLIER", Severity::Suspicious, recs[ri].ref,
                                   m.str(), vals[j].second, z});
                }
            }
        }
    }
    return out;
}

}  // namespace tas
