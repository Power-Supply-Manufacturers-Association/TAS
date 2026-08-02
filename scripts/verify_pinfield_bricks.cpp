// verify_pinfield_bricks — end-to-end proof that the connector pin-field bricks in
// TAS/data/circuits.ndjson CONVERT (libCIAS) and SIMULATE (Kirchhoff's in-process
// libngspice, never the CLI), and that what comes out is physics.
//
// The schema gate says the brick's shape is legal and the CIAS lowering says its
// inductance matrix is not unphysical. Neither runs the circuit. This does: a brick
// whose coupled-inductor matrix is positive-definite on paper can still be one ngspice
// refuses to solve, and a brick that solves can still produce crosstalk that goes the
// wrong way. Run it after any regeneration of the conn-pinfield-* archetypes.
//
// Bench, per brick with N pins (all archetypes are single-row, so pin index == position):
//   Vdrv -- Rs(50) -- pin1_a   (aggressor drive, board end)
//   pin1_b -- Rl(50) -- 0      (aggressor far end)
//   pin<i>_a -- 50 -- 0, pin<i>_b -- 50 -- 0   for every victim i > 1
//   ref -- 0
//   .ac dec 4 1e5 1e10
//
// Checks (at 100 MHz — inside the model's stated ~1-2 GHz MQS validity window):
//   C1  the deck solves at all (ngspice rejects a K set it cannot factor)
//   C2  near-end |V(pin_i_a)| decays strictly monotonically with distance
//   C3  every victim couples less than the aggressor's own through-voltage
//   C4  adjacent coupling grows ~20 dB/decade from 1 to 10 MHz (quasi-static regime)
//
// C2 measures the NEAR end deliberately. At the far end the inductively- and
// capacitively-coupled terms SUBTRACT, so far-end crosstalk legitimately goes
// non-monotonic wherever Lm/L ~ Cm/C — 333 of 1,636 bricks trip a far-end monotonicity
// test for that reason alone, with nothing wrong with them. Near-end coupling is the
// additive one and is the only sound monotonicity oracle here.
//
// KNOWN EXCEPTION, not a defect: the outermost pin of a long array is unshielded on one
// side, so its direct coupling capacitance to the aggressor survives while its node
// loading drops. That lifts the last pin (occasionally the last two or three) above its
// neighbour and trips C2. Verified as the finite-array edge effect rather than assumed:
// on p2540-40x1-l8540-hi the mutual inductance stays monotone to the end (77.6 -> 75.5
// -> 73.6 pH for pins 38/39/40) while C_1i jumps 1.29 -> 2.14 fF and the node total
// drops 1026 -> 878 fF.
//
// Result on the 2026-08-02 generation (7,554 bricks, the provenance-carrying set):
//   7554 converted, 7554 SOLVED (zero ngspice failures), 7230 pass all four checks.
//   All 324 violations are C2, all on the 'hi' interval endpoint, and all at the END of
//   their array — 311 at the last pin, 9 at the second-to-last, 4 at the third-to-last,
//   ZERO mid-array. In all 324 the coupling capacitance to the aggressor RISES at the
//   violating pin, which is the edge-effect signature; there are no counterexamples.
//
// So the pass criterion for a future run is NOT "324 failures". It is: no ERROR lines at
// all, every violation is C2, and every violation sits at the end of its array with a
// rising C_1i. A violation in the MIDDLE of an array is a real regression.
//
// (Earlier 1,636-brick generation, for reference: 1636 solved, 1634 passed, 2 edge cases.)
//
// build:
//   g++ -std=c++20 -O2 -DENABLE_NGSPICE \
//       -I../CIAS/src -I../PEAS/src -I<Kirchhoff>/src -I<nlohmann-json>/include \
//       scripts/verify_pinfield_bricks.cpp <Kirchhoff>/src/NgspiceRunner.cpp \
//       -L../CIAS/build -lCIAS -lngspice -pthread -o verify_pinfield_bricks
//
// usage: verify_pinfield_bricks <circuits.ndjson> <brickName>|ALL

#include "CiasCircuitConverter.hpp"
#include "NgspiceRunner.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

using json = nlohmann::json;

// Number of pin<i>_a ports declared by the brick.
static size_t pin_count(const json& brick) {
    size_t n = 0;
    for (const auto& p : brick.at("ports")) {
        const std::string pn = p.at("name").get<std::string>();
        if (pn.size() > 5 && pn.compare(0, 3, "pin") == 0 &&
            pn.compare(pn.size() - 2, 2, "_a") == 0)
            ++n;
    }
    return n;
}

struct Bench {
    size_t n = 0;
    std::vector<double> freq;
    // magnitude[i][s] — |V| at node pin<i+1>_b, sample s
    std::vector<std::vector<double>> mag;
};

static const std::vector<double>* lookup(
        const std::map<std::string, std::vector<double>>& m, const std::string& key) {
    for (const auto& kv : m) {
        std::string k = kv.first;
        for (auto& ch : k) ch = static_cast<char>(::tolower(ch));
        if (k == key) return &kv.second;
        if (k.size() > key.size() &&
            k.compare(k.size() - key.size(), key.size(), key) == 0 &&
            k[k.size() - key.size() - 1] == '.')
            return &kv.second;
    }
    return nullptr;
}

static Bench simulate(const json& brick, const std::string& name, bool dumpDeck) {
    Bench out;
    out.n = pin_count(brick);

    CIAS::CiasCircuitConverter conv(CIAS::CircuitSimulator::Ngspice);
    const std::string subckt = conv.to_subckt_json(brick);

    std::string deck = "* connector pin-field brick bench (" + name + ")\n";
    deck += subckt + "\n";
    deck += "Vdrv drv 0 DC 0 AC 1\n";
    deck += "Rs drv p1a 50\n";
    deck += "Rl p1b 0 50\n";
    std::string inst = "Xdut";
    for (size_t i = 1; i <= out.n; ++i) {
        const std::string si = std::to_string(i);
        inst += " p" + si + "a p" + si + "b";
        if (i > 1) {
            deck += "Rna" + si + " p" + si + "a 0 50\n";
            deck += "Rnb" + si + " p" + si + "b 0 50\n";
        }
    }
    inst += " 0 " + name + "\n";   // trailing port is `ref`
    deck += inst;
    deck += ".ac dec 4 1e5 1e10\n";
    deck += ".end\n";
    if (dumpDeck) std::cout << deck << "\n";

    auto res = Kirchhoff::run_ngspice_in_process(deck, 300.0);
    if (!res.success) throw std::runtime_error("ngspice: " + res.error);

    const std::vector<double>* f = lookup(res.vectors, "frequency");
    if (!f) throw std::runtime_error("no frequency vector in the .ac result");
    out.freq = *f;

    // NEAR-end nodes (p<i>a). The victim's near end is the one on the aggressor's driven
    // side, where the inductively- and capacitively-coupled terms ADD; the far end (p<i>b)
    // is their DIFFERENCE and legitimately goes non-monotonic with distance whenever
    // Lm/L ~ Cm/C, so it is not a monotonicity oracle.
    out.mag.resize(out.n);
    for (size_t i = 1; i <= out.n; ++i) {
        const std::string key = "p" + std::to_string(i) + "a";
        const auto* re = lookup(res.vectors, key);
        const auto* im = lookup(res.vectorsImag, key);
        if (!re) throw std::runtime_error("node " + key + " absent from the result");
        out.mag[i - 1].resize(re->size());
        for (size_t s = 0; s < re->size(); ++s)
            out.mag[i - 1][s] = std::hypot((*re)[s], im ? (*im)[s] : 0.0);
    }
    return out;
}

// index of the sample nearest frequency f
static size_t at(const Bench& b, double f) {
    size_t best = 0;
    double bd = 1e300;
    for (size_t s = 0; s < b.freq.size(); ++s) {
        const double d = std::abs(std::log10(b.freq[s]) - std::log10(f));
        if (d < bd) { bd = d; best = s; }
    }
    return best;
}

// Returns "" if the brick passes, else the first violated check.
static std::string check(const Bench& b) {
    if (b.n < 2) return "brick has fewer than 2 pins";
    const size_t s100M = at(b, 1e8), s1M = at(b, 1e6), s10M = at(b, 1e7);

    for (size_t i = 1; i + 1 < b.n; ++i) {
        const double near = b.mag[i][s100M], far = b.mag[i + 1][s100M];
        if (!(near > far))
            return "C2 non-monotonic NEAR-END coupling at 100 MHz: pin" + std::to_string(i + 1) +
                   " = " + std::to_string(near) + " V <= pin" + std::to_string(i + 2) +
                   " = " + std::to_string(far) + " V";
    }
    for (size_t i = 1; i < b.n; ++i)
        if (!(b.mag[i][s100M] < b.mag[0][s100M]))
            return "C3 victim pin" + std::to_string(i + 1) +
                   " couples at or above the aggressor's own through-voltage at 100 MHz";

    const double d = b.mag[1][s10M] / b.mag[1][s1M];      // decade slope, adjacent victim
    if (!(d > 8.0 && d < 12.0))
        return "C4 adjacent coupling is not ~20 dB/decade in the quasi-static band: "
               "x" + std::to_string(d) + " per decade (1->10 MHz)";
    return "";
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "usage: brick_sim <circuits.ndjson> <brickName>|ALL\n";
        return 2;
    }
    const std::string path = argv[1], want = argv[2];
    const bool all = (want == "ALL");

    std::ifstream in(path);
    if (!in) { std::cerr << "cannot open " << path << "\n"; return 2; }

    size_t seen = 0, passed = 0, failed = 0;
    std::string line;
    while (std::getline(in, line)) {
        if (line.find("conn-pinfield") == std::string::npos) continue;
        json brick = json::parse(line);
        const std::string name = brick.at("name").get<std::string>();
        if (!all && name != want) continue;
        ++seen;

        try {
            const Bench b = simulate(brick, name, !all && ::getenv("DUMP_DECK"));
            const std::string bad = check(b);
            if (bad.empty()) {
                ++passed;
                if (!all) {
                    std::printf("brick %s — %zu pins, PASS\n\n", name.c_str(), b.n);
                    std::printf("  f [Hz]    ");
                    for (size_t i = 1; i <= b.n && i <= 9; ++i)
                        std::printf("   |V(p%zua)|", i);
                    std::printf("\n");
                    for (size_t s = 0; s < b.freq.size(); ++s) {
                        const double lg = std::log10(b.freq[s]);
                        if (std::abs(lg - std::round(lg)) > 1e-6) continue;
                        std::printf("  %-9.3g ", b.freq[s]);
                        for (size_t i = 0; i < b.n && i < 9; ++i)
                            std::printf("  %10.3e", b.mag[i][s]);
                        std::printf("\n");
                    }
                }
            } else {
                ++failed;
                std::printf("FAIL %s: %s\n", name.c_str(), bad.c_str());
            }
        } catch (const std::exception& e) {
            ++failed;
            std::printf("ERROR %s: %s\n", name.c_str(), e.what());
        }
        if (all && seen % 100 == 0)
            std::printf("  ... %zu simulated (%zu pass, %zu fail)\n", seen, passed, failed);
        std::fflush(stdout);
    }
    if (seen == 0) { std::cerr << "no brick matched " << want << "\n"; return 2; }
    if (all) std::printf("\n%zu bricks: %zu pass, %zu fail\n", seen, passed, failed);
    return failed ? 1 : 0;
}
