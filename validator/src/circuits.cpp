// SPDX-License-Identifier: MIT
// CIAS circuit (brick) physics checks — "Blade Runner for circuits".
//
// A brick is not a part, so this does not go through PartValidator's discriminator
// dispatch; it has its own entry point (validate_circuit). What it checks is what the
// three existing gates structurally cannot:
//
//   JSON Schema (CIAS.json)          the brick's SHAPE is legal
//   validate_cias_structure          the GRAPH is sound: unique names, endpoints resolve,
//                                    a net is exposed at no more than one port
//   CiasCircuitConverter lowering    the brick can be EMITTED as a netlist
//   >>> this file                    the emitted netlist would be PHYSICS
//
// The lowering is the closest of the three and still not enough, verified against the
// live corpus rather than argued: 750811612 and 750315229 each carry a 0 F capacitor and
// lower cleanly to `CCpri2 Cpri2__pi 1 0`; SC70_82400274 lowers 1,196 resistors of 1e100
// ohm. Every one of those passes schema, structure and emission.
//
// DELIBERATELY NOT DUPLICATED HERE: dangling nets, unwired pins, endpoints that name a
// component that does not exist. Those are graph integrity and belong to
// validate_cias_structure. This file does physics only.
//
// SEVERITIES ARE CALIBRATED, NOT ASSUMED. Every rule below was run against all 19,533
// bricks in TAS/data/circuits.ndjson before being given a severity, because a rule that
// fires on correct physics retroactively invalidates good data. Two of the first three
// draft rules were wrong and were fixed rather than shipped:
//   - a floating-node rule that ignored PORTS fired on 38.84% of the corpus, including
//     all 7,554 connector pin-field bricks, which are known-good (7,554/7,554 solve in
//     ngspice). A net exposed at a port is not floating: the DC path is the consumer's.
//   - a 1 F capacitance ceiling fired on 87 supercapacitor bricks (851617031001_100F is
//     a 100 F cell; commercial modules reach ~10 kF).
// Fire rates after correction are recorded per check below.
#include "tas_validator/helpers.hpp"
#include "tas_validator/validator.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace tas {

namespace {

// Absurd-only bounds. These bracket the REALIZABLE world, not the typical one — a brick
// is a lumped model, so parasitic values run very small and bulk values very large.
constexpr double kRMin = 1e-9;    // 1 nOhm  — below this is a short, not a resistor
constexpr double kRMax = 1e15;    // 1 POhm  — above this is an open, not a resistor
constexpr double kCMin = 1e-18;   // 1 aF
constexpr double kCMax = 1e5;     // 100 kF  — admits supercapacitors (calibrated: a 1 F
                                  //           ceiling fired on 87 real bricks)
constexpr double kLMin = 1e-15;   // 1 fH
constexpr double kLMax = 1e3;     // 1 kH

// A resistance at or above this is not a wrong number, it is an in-band SENTINEL meaning
// "open circuit". Nothing physical is 1e30 ohm: the best electrometers resolve ~1e18, and
// even then across a real insulator. Diagnosed separately from a range violation because
// the fix is different — express the open structurally (omit the element) rather than
// with a magic value.
constexpr double kROpenSentinel = 1e30;

// Symmetry tolerance, relative to the largest magnitude in the matrix. Matches the
// CiasCircuitConverter's own 1e-9 so the two cannot disagree about the same matrix.
constexpr double kSymTol = 1e-9;

std::string num(double v) {
    std::ostringstream os;
    os.precision(6);
    os << v;
    return os.str();
}

// Cholesky positive-definiteness test. Returns 0 if the matrix is positive-definite, or
// the 1-based index of the leading principal minor at which factorisation fails.
//
// This is the whole reason the check exists. CiasCircuitConverter validates coupling
// PAIRWISE (|k_ij| = |M_ij|/sqrt(L_ii*L_jj) <= 1), which for N >= 3 does NOT imply the
// matrix is positive-definite:
//
//     L = [[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1]]
//
// has every pairwise |k| = 0.9 and eigenvalues {-0.8, 1.9, 1.9}. The current vector
// i = [1, -1, 1] stores 0.5*i^T L i = -1.2 J. Negative stored energy is a magnetic
// network that generates energy from nothing; the converter emits it without complaint.
std::size_t first_nonpd_minor(const std::vector<std::vector<double>>& M) {
    const std::size_t n = M.size();
    std::vector<std::vector<double>> Lf(n, std::vector<double>(n, 0.0));
    for (std::size_t i = 0; i < n; ++i) {
        for (std::size_t j = 0; j <= i; ++j) {
            double s = M[i][j];
            for (std::size_t k = 0; k < j; ++k) s -= Lf[i][k] * Lf[j][k];
            if (i == j) {
                if (!(s > 0.0)) return i + 1;   // includes NaN: !(NaN > 0) is true
                Lf[i][j] = std::sqrt(s);
            } else {
                Lf[i][j] = s / Lf[j][j];
            }
        }
    }
    return 0;
}

struct Element {
    std::string name;
    char kind = '?';        // 'R' 'C' 'L' or '?' (behavioral / URI / unclassified)
    bool has_value = false;
    double value = 0.0;
    bool conducts_dc = true;  // capacitors do not; anything unclassified is assumed to,
                              // which SUPPRESSES a floating finding rather than inventing one
};

// Collapse a dimensionWithTolerance-or-scalar. Absent -> no value (the check skips);
// present-but-uninterpretable throws MalformedField via scalar(), per the no-fallbacks rule.
bool value_of(const json& dr, const char* key, double& out) {
    const json* f = at(dr, key);
    if (f == nullptr) return false;
    std::optional<double> v = scalar(f, key);
    if (!v.has_value()) return false;
    out = *v;
    return true;
}

std::vector<Element> elements_of(const json& brick) {
    std::vector<Element> els;
    const json* comps = at(brick, "components");
    if (comps == nullptr || !comps->is_array()) return els;
    for (const json& c : *comps) {
        Element e;
        e.name = c.contains("name") && c["name"].is_string() ? c["name"].get<std::string>() : "?";
        const json* data = at(c, "data");
        if (data == nullptr || !data->is_object()) {
            els.push_back(e);           // URI reference — gated where its catalogue is gated
            continue;
        }
        const json* dr = at(*data, "inputs", "designRequirements");
        const json empty = json::object();
        const json& req = dr ? *dr : empty;
        if (data->contains("resistor")) {
            e.kind = 'R';
            e.has_value = value_of(req, "resistance", e.value);
        } else if (data->contains("capacitor")) {
            e.kind = 'C';
            e.conducts_dc = false;
            e.has_value = value_of(req, "capacitance", e.value);
        } else if (data->contains("magnetic")) {
            e.kind = 'L';
            e.has_value = value_of(req, "magnetizingInductance", e.value);
        }
        els.push_back(e);
    }
    return els;
}

}  // namespace

// ABT #549: generated from this file's own emit() call sites at build time
// (tools/gen_check_codes.py, wired in CMakeLists.txt) instead of hand-typed --
// see the longer note on PartValidator::check_codes() in validator.cpp.
std::vector<std::string> circuit_check_codes() {
    return
#include "tas_validator/circuit_check_codes.inc"
        ;
}

Verdict validate_circuit(const json& brick) {
    if (!brick.is_object()) throw std::invalid_argument("circuit brick is not a JSON object");
    Ctx ctx;
    ctx.component = "circuit";
    if (brick.contains("name") && brick["name"].is_string())
        ctx.reference = brick["name"].get<std::string>();
    ctx.component_obj = &brick;

    Verdict v;
    check_circuit(brick, ctx, v.findings, v.skipped);
    for (const Finding& f : v.findings)
        if (f.severity == Severity::Impossible) v.valid = false;
    // `completeness` is a part-authenticity signal driven by a datasheet-field manifest.
    // A brick has no datasheetInfo, so there is nothing to be complete against; it stays
    // at the "no manifest" value rather than being given a meaningless score.
    return v;
}

void check_circuit(const json& brick, const Ctx& ctx, std::vector<Finding>& out,
                   std::vector<std::string>& skipped) {
    const json* comps = at(brick, "components");
    if (comps == nullptr || !comps->is_array()) {
        skipped.push_back("CIR_*");
        return;
    }

    // ---- coupled-inductor matrices ------------------------------------------
    // Fire rate on the live corpus: 0 of 19,533. A pure FORWARD guard — it invalidates
    // nothing that exists today, which is exactly what an IMPOSSIBLE severity is for.
    for (const json& c : *comps) {
        const json* beh = at(c, "data", "behavioral");
        if (beh == nullptr) continue;
        const json* nat = at(*beh, "nature");
        if (nat == nullptr || !nat->is_string() || nat->get<std::string>() != "coupledInductors")
            continue;
        const std::string cname =
            c.contains("name") && c["name"].is_string() ? c["name"].get<std::string>() : "?";
        const json* lm = at(*beh, "inductanceMatrix");
        if (lm == nullptr || !lm->is_array() || lm->empty()) {
            skipped.push_back("CIR_L_*");
            continue;
        }
        const std::size_t n = lm->size();
        std::vector<std::vector<double>> M(n, std::vector<double>(n, 0.0));
        bool shape_ok = true;
        for (std::size_t i = 0; i < n && shape_ok; ++i) {
            const json& row = (*lm)[i];
            if (!row.is_array() || row.size() != n) {
                emit(out, ctx, "CIR_L_NOT_SQUARE", Severity::Impossible,
                     static_cast<double>(row.is_array() ? row.size() : 0),
                     static_cast<double>(n),
                     cname + ": inductanceMatrix row " + std::to_string(i + 1) + " has " +
                         std::to_string(row.is_array() ? row.size() : 0) +
                         " entries, expected " + std::to_string(n) +
                         " — an inductance matrix that is not square is not a matrix");
                shape_ok = false;
                break;
            }
            for (std::size_t j = 0; j < n; ++j) {
                if (!row[j].is_number() || !std::isfinite(row[j].get<double>())) {
                    throw MalformedField(cname + ": inductanceMatrix[" + std::to_string(i + 1) +
                                         "][" + std::to_string(j + 1) + "] is not a finite number");
                }
                M[i][j] = row[j].get<double>();
            }
        }
        if (!shape_ok) continue;

        // reciprocity: M_ij == M_ji
        double worst_asym = 0.0, scale = 0.0;
        std::size_t ai = 0, aj = 0;
        for (std::size_t i = 0; i < n; ++i)
            for (std::size_t j = 0; j < n; ++j) {
                scale = std::max(scale, std::abs(M[i][j]));
                if (j > i) {
                    const double d = std::abs(M[i][j] - M[j][i]);
                    if (d > worst_asym) { worst_asym = d; ai = i; aj = j; }
                }
            }
        if (scale <= 0.0) scale = 1.0;
        if (worst_asym > kSymTol * scale) {
            emit(out, ctx, "CIR_L_ASYMMETRIC", Severity::Impossible, worst_asym, kSymTol * scale,
                 cname + ": M_" + std::to_string(ai + 1) + "_" + std::to_string(aj + 1) + " = " +
                     num(M[ai][aj]) + " H but M_" + std::to_string(aj + 1) + "_" +
                     std::to_string(ai + 1) + " = " + num(M[aj][ai]) +
                     " H — mutual inductance is reciprocal; an asymmetric matrix describes "
                     "no physical magnetic system");
        }

        bool diag_ok = true;
        for (std::size_t i = 0; i < n; ++i) {
            if (M[i][i] <= 0.0) {
                emit(out, ctx, "CIR_L_NONPOS_DIAG", Severity::Impossible, M[i][i], 0.0,
                     cname + ": self inductance L_" + std::to_string(i + 1) + "_" +
                         std::to_string(i + 1) + " = " + num(M[i][i]) +
                         " H — a winding with no self inductance is a short, not an inductor");
                diag_ok = false;
            }
        }
        if (!diag_ok) continue;   // Cholesky on a non-positive diagonal says nothing new

        // Symmetrise before factorising so an asymmetry already reported above does not
        // masquerade as a second, different finding.
        std::vector<std::vector<double>> S(n, std::vector<double>(n));
        for (std::size_t i = 0; i < n; ++i)
            for (std::size_t j = 0; j < n; ++j) S[i][j] = 0.5 * (M[i][j] + M[j][i]);

        const std::size_t minor = first_nonpd_minor(S);
        if (minor != 0) {
            double kmax = 0.0;
            for (std::size_t i = 0; i < n; ++i)
                for (std::size_t j = i + 1; j < n; ++j)
                    kmax = std::max(kmax, std::abs(S[i][j]) / std::sqrt(S[i][i] * S[j][j]));
            emit(out, ctx, "CIR_L_NOT_PD", Severity::Impossible,
                 static_cast<double>(minor), static_cast<double>(n),
                 cname + ": inductance matrix is not positive-definite (Cholesky fails at "
                         "leading minor " + std::to_string(minor) + " of " + std::to_string(n) +
                     "); max pairwise |k| = " + num(kmax) +
                     " <= 1, so the converter's pairwise coupling bound does NOT catch this. "
                     "Some current vector stores negative energy — the network would generate "
                     "energy from nothing");
        }

        // seriesResistance, when present, is one entry per winding
        const json* sr = at(*beh, "seriesResistance");
        if (sr != nullptr && sr->is_array() && sr->size() != n) {
            emit(out, ctx, "CIR_L_R_LENGTH", Severity::Impossible,
                 static_cast<double>(sr->size()), static_cast<double>(n),
                 cname + ": seriesResistance has " + std::to_string(sr->size()) +
                     " entries but the matrix defines " + std::to_string(n) + " windings");
        }
    }

    // ---- element values -----------------------------------------------------
    // Fire rates on the live corpus: CIR_NONPOSITIVE 9 bricks (0.05%, every one a genuine
    // defect — 0 F capacitors and 0 H inductors); CIR_SENTINEL_VALUE 78 bricks (0.40%, all
    // 1e100 ohm "open" idioms); CIR_VALUE_RANGE 0 after the supercapacitor recalibration.
    const std::vector<Element> els = elements_of(brick);
    for (const Element& e : els) {
        if (e.kind == '?' || !e.has_value) continue;
        const char* unit = e.kind == 'R' ? "ohm" : (e.kind == 'C' ? "F" : "H");
        const char* what = e.kind == 'R' ? "resistance"
                                         : (e.kind == 'C' ? "capacitance" : "inductance");
        if (e.value <= 0.0) {
            emit(out, ctx, "CIR_NONPOSITIVE", Severity::Impossible, e.value, 0.0,
                 e.name + ": " + what + " = " + num(e.value) + " " + unit +
                     " — a non-positive passive value is not an element; it is a short "
                     "(or, negative, an energy source)");
            continue;
        }
        if (e.kind == 'R' && e.value >= kROpenSentinel) {
            emit(out, ctx, "CIR_SENTINEL_VALUE", Severity::Suspicious, e.value, kROpenSentinel,
                 e.name + ": resistance = " + num(e.value) +
                     " ohm is an in-band sentinel for 'open', not a measurement — nothing "
                     "physical is that resistive. Express the open structurally (omit the "
                     "element) rather than with a magic value");
            continue;
        }
        const double lo = e.kind == 'R' ? kRMin : (e.kind == 'C' ? kCMin : kLMin);
        const double hi = e.kind == 'R' ? kRMax : (e.kind == 'C' ? kCMax : kLMax);
        if (e.value < lo || e.value > hi) {
            emit(out, ctx, "CIR_VALUE_RANGE", Severity::Suspicious, e.value,
                 e.value < lo ? lo : hi,
                 e.name + ": " + what + " = " + num(e.value) + " " + unit +
                     " is outside the realizable range [" + num(lo) + ", " + num(hi) + "] " +
                     unit);
        }
    }

    // ---- DC connectivity ----------------------------------------------------
    // A net with no DC path to the rest of the brick is SPICE's singular matrix. Capacitors
    // are open at DC, so a net reachable only through capacitance floats.
    //
    // A net EXPOSED AT A PORT is exempt: the DC path is the consuming circuit's job, not
    // this brick's. That exemption is not a nicety — without it this check fires on 38.84%
    // of the live corpus, including all 7,554 connector pin-field bricks, whose `ref` net is
    // reached only through capacitors internally and which all solve in ngspice.
    //
    // SUSPICIOUS, not IMPOSSIBLE, and deliberately so: the first formulation of this rule
    // was wrong about a third of the corpus. One bad formulation is reason enough not to
    // hand it the power to invalidate data. Promote it if a real ngspice singular-matrix
    // failure is ever traced to a brick it flags. Fire rate after the port exemption: 0.
    const json* conns = at(brick, "connections");
    if (conns == nullptr || !conns->is_array()) {
        skipped.push_back("CIR_FLOATING_NODE");
        return;
    }
    std::map<std::string, std::set<std::string>> comp_nets;   // component -> nets it touches
    std::set<std::string> all_nets, ported;
    for (const json& conn : *conns) {
        const json* nm = at(conn, "name");
        if (nm == nullptr || !nm->is_string()) continue;
        const std::string net = nm->get<std::string>();
        all_nets.insert(net);
        const json* eps = at(conn, "endpoints");
        if (eps == nullptr || !eps->is_array()) continue;
        for (const json& ep : *eps) {
            if (ep.contains("port")) ported.insert(net);
            const json* cn = at(ep, "component");
            if (cn != nullptr && cn->is_string()) comp_nets[cn->get<std::string>()].insert(net);
        }
    }
    if (all_nets.empty()) return;

    std::map<std::string, std::set<std::string>> adj;
    for (const Element& e : els) {
        if (!e.conducts_dc) continue;
        auto it = comp_nets.find(e.name);
        if (it == comp_nets.end()) continue;
        const std::vector<std::string> ns(it->second.begin(), it->second.end());
        for (std::size_t i = 0; i < ns.size(); ++i)
            for (std::size_t j = i + 1; j < ns.size(); ++j) {
                adj[ns[i]].insert(ns[j]);
                adj[ns[j]].insert(ns[i]);
            }
    }
    // Grow the island from the ported nets when there are any — that is the island the
    // external circuit anchors.
    std::set<std::string> seen;
    std::vector<std::string> stack(ported.begin(), ported.end());
    if (stack.empty()) stack.push_back(*all_nets.begin());   // no ports: any net will do
    while (!stack.empty()) {
        const std::string n = stack.back();
        stack.pop_back();
        if (!seen.insert(n).second) continue;
        for (const std::string& m : adj[n])
            if (seen.find(m) == seen.end()) stack.push_back(m);
    }
    std::vector<std::string> floating;
    for (const std::string& n : all_nets)
        if (seen.find(n) == seen.end() && ported.find(n) == ported.end())
            floating.push_back(n);
    if (!floating.empty()) {
        std::string list;
        for (std::size_t i = 0; i < floating.size() && i < 6; ++i)
            list += (i ? ", " : "") + floating[i];
        if (floating.size() > 6) list += ", …";
        emit(out, ctx, "CIR_FLOATING_NODE", Severity::Suspicious,
             static_cast<double>(floating.size()), 0.0,
             std::to_string(floating.size()) +
                 " net(s) with no DC path to the rest of the brick and not exposed at a port: " +
                 list + " — capacitors are open at DC, so this is a singular matrix");
    }
}

}  // namespace tas
