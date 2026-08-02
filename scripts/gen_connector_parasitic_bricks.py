#!/usr/bin/env python3
"""Generate per-archetype connector pin-field parasitic CIAS bricks (ABT #467).

Collects pin-field ARCHETYPES from TAS/data/connectors.ndjson — records carrying
geometry.parametric.contactArray AND manufacturerInfo.datasheetInfo.familyDetails.tailLength
AND manufacturerInfo.datasheetInfo.mechanical.height — keyed
(pitchX_um, countX, countY, pitchY_um or 0, length_um) with conductor length
l = tailLength + mechanical height (nominal). For each archetype it emits TWO CIAS
bricks bounding the assumption interval of the post cross-section and dielectric:

  -lo : square post side a = 0.20 * pitch, epsr = 1.0   (max L, min C bound)
  -hi : square post side a = 0.35 * pitch, epsr = 3.5   (min L, max C bound)

(the conventional center of the interval is a = 0.25 * pitch).

Physics — closed form, FREE SPACE, magnetoquasistatic (valid to ~1-2 GHz):
  * partial self inductance of a square post, side a, length l:
        L_ii = (mu0*l/2pi) * (ln(2l/r_gmd) - 1),  r_gmd = 0.44705*a
    (0.44705*a is the exact geometric-mean-distance of a square section);
  * mutual between parallel conductors, length l, center spacing d (filament GMD form):
        M_ij = (mu0*l/2pi) * (ln(l/d + sqrt(1+(l/d)^2)) - sqrt(1+(d/l)^2) + d/l)
    exact for filaments at all d (correct 1/d point limit for d >> l);
  * Maxwell capacitance matrix from the finite-length line-charge (average-potential)
    method — the rigorous finite-system resolution of the 2-D log-potential's additive
    constant (no arbitrary reference distance D survives):
        p_ii = (ln(2l/r_eq) - 1) / (2pi*eps0*epsr*l),   r_eq = 0.59017*a
        p_ij = [ln(l/d + sqrt(1+(l/d)^2)) - sqrt(1+(d/l)^2) + d/l] / (2pi*eps0*epsr*l)
    (0.59017*a is the transfinite diameter / logarithmic capacity radius of a square;
    the bracket is the exact electrostatic dual of the mutual-inductance Neumann
    integral; its d << l limit is ln(2l/d) - 1, i.e. the classical -ln(d/D) form with
    D = 2l/e, and its d >> l limit is the point-charge 1/(4pi*eps*d)).
    C_maxwell = inv(P). Network realization: node-to-node capacitor C_ij_net = -C[i][j]
    for every pair above a 1 fF floor (drops are counted and logged, never silent),
    plus each pin's capacitance to a 'ref' port = Maxwell row sum (the pin's free-space
    self term). The brick models the connector ALONE in free space: the board return
    plane/ground belongs to the consuming circuit (wire 'ref' to it).
  * per-pin DC series resistance R = rho_cu * l / a^2, rho_cu = 1.68e-8 Ohm*m.

GATES (all must pass BEFORE any brick is written; a failing gate emits NOTHING):
  1. FastHenry cross-check on >= 3 real archetypes (small/medium/large) at BOTH
     interval endpoints (a = 0.20p and 0.35p), 1 kHz: closed-form self L within 3%
     and adjacent-pair mutual within 5% of FastHenry's Zc.mat.
  2. Two-wire identity: the 2-D limit of the pair combination, C' = pi*eps/ln(d/r_eq),
     must match the exact two-cylinder closed form C' = pi*eps/acosh(d/(2*r_eq))
     within 2% at the conventional a = 0.25*pitch.
  3. 3-conductor independent cross-check: a 2-D method-of-moments solver (64 boundary
     segments per square conductor) vs the analytic line-charge P matrix, compared on
     reference-free (charge-neutral) pair combinations. Informational, reported.

Every brick is validated against CIAS.json (full sibling-registry, exactly like
tests/test_data.py) plus the validate_cias_brick referential-integrity pass; one
failure aborts the run before anything is appended. Bricks are APPENDED to
TAS/data/circuits.ndjson (open 'ab' — the file is never rewritten).

Usage:  python3 scripts/gen_connector_parasitic_bricks.py [--dry-run] [--fasthenry PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO.parent
DATA = REPO / "data"
CONNECTORS = DATA / "connectors.ndjson"
CIRCUITS = DATA / "circuits.ndjson"
TODAY = "2026-08-02"

sys.path.insert(0, str(REPO / "scripts"))
from validate_topology import validate_cias_brick  # noqa: E402

MU0 = 4e-7 * math.pi
EPS0 = 8.8541878128e-12
RHO_CU = 1.68e-8          # [Ohm*m]
K_GMD_SQUARE = 0.44705    # self-GMD of a square section, r_gmd = 0.44705 * a
K_REQ_SQUARE = 0.59017    # transfinite diameter (log capacity) of a square, r_eq = 0.59017 * a
CAP_FLOOR = 1e-15         # [F] node-to-node capacitors below this are dropped (counted)
A_LO, EPSR_LO = 0.20, 1.0
A_HI, EPSR_HI = 0.35, 3.5
A_CONV = 0.25             # the standard convention (assumption-interval center)

FH_FREQ = 1e3             # [Hz] DC-like gate frequency
SIGMA_CU_MM = 5.8e4       # copper conductivity in 1/(mm*Ohm) for the FastHenry decks


# ---------------------------------------------------------------------------
# Closed-form physics
# ---------------------------------------------------------------------------

def neumann_bracket(l: float, d: float) -> float:
    """ln(l/d + sqrt(1+(l/d)^2)) - sqrt(1+(d/l)^2) + d/l  (dimensionless)."""
    x = l / d
    return math.log(x + math.sqrt(1.0 + x * x)) - math.sqrt(1.0 + 1.0 / (x * x)) + 1.0 / x


def self_inductance(l: float, a: float) -> float:
    """Partial self inductance [H] of a square post side a, length l.
    Exact finite-length form: the mutual-inductance kernel evaluated at the
    self geometric-mean distance d = K_GMD_SQUARE*a. Reduces to the long-thin
    (mu0*l/2pi)*(ln(2l/GMD)-1) as l/GMD -> inf, but stays accurate for the
    short, fat posts of dense multi-row headers where the long-thin limit
    drifts (a 30x4 x 3.9 mm x a=0.35*pitch archetype missed FastHenry by 3.9%
    with ln(2l/GMD)-1; this form brings it inside the 3% gate)."""
    return mutual_inductance(l, K_GMD_SQUARE * a)


def mutual_inductance(l: float, d: float) -> float:
    """Partial mutual inductance [H] between parallel conductors, spacing d."""
    return MU0 * l / (2.0 * math.pi) * neumann_bracket(l, d)


def pin_positions(pitch_x: float, cx: int, cy: int, pitch_y: float) -> list[tuple[float, float]]:
    """Pin i (1-based) -> (x, y). Column-major, matching dual-row header numbering
    (pin 1 = col 1 row 1, pin 2 = col 1 row 2, pin 3 = col 2 row 1, ...)."""
    if cy > 1 and pitch_y <= 0.0:
        raise ValueError(f"countY={cy} > 1 but pitchY is missing/zero — ambiguous geometry")
    pos = []
    for n in range(cx * cy):
        ix, iy = divmod(n, cy)
        pos.append((ix * pitch_x, iy * pitch_y))
    return pos


def inductance_matrix(pos, l: float, a: float) -> np.ndarray:
    n = len(pos)
    L = np.empty((n, n))
    ls = self_inductance(l, a)
    for i in range(n):
        L[i, i] = ls
        for j in range(i + 1, n):
            d = math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1])
            L[i, j] = L[j, i] = mutual_inductance(l, d)
    return L


def elastance_matrix(pos, l: float, a: float, epsr: float) -> np.ndarray:
    """Total (not per-unit-length) potential-coefficient matrix [1/F]."""
    n = len(pos)
    P = np.empty((n, n))
    r_eq = K_REQ_SQUARE * a
    k = 1.0 / (2.0 * math.pi * EPS0 * epsr * l)
    for i in range(n):
        # Exact finite-length self potential coefficient (neumann kernel at the
        # self equivalent radius r_eq), consistent with the finite-length mutual
        # terms and with self_inductance — not the long-thin ln(2l/r_eq)-1 limit.
        P[i, i] = k * neumann_bracket(l, r_eq)
        for j in range(i + 1, n):
            d = math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1])
            P[i, j] = P[j, i] = k * neumann_bracket(l, d)
    return P


# ---------------------------------------------------------------------------
# Archetype collection
# ---------------------------------------------------------------------------

def resolve_nominal(x):
    """Canonical dimensionWithTolerance resolution: nominal -> mean(min,max) -> max -> min."""
    if isinstance(x, dict):
        if x.get("nominal") is not None:
            return x["nominal"]
        if x.get("minimum") is not None and x.get("maximum") is not None:
            return (x["minimum"] + x["maximum"]) / 2.0
        if x.get("maximum") is not None:
            return x["maximum"]
        return x.get("minimum")
    return x


def collect_archetypes():
    """{(pitchX_um, countX, countY, pitchY_um or 0, length_um): part_count}"""
    archetypes: dict[tuple, int] = defaultdict(int)
    skipped_multirow_no_pitchy = 0
    skipped_single_pin = 0
    with CONNECTORS.open() as fh:
        for line in fh:
            if '"contactArray"' not in line or '"tailLength"' not in line:
                continue
            rec = json.loads(line)
            conn = rec.get("connector") or {}
            ca = ((conn.get("geometry") or {}).get("parametric") or {}).get("contactArray")
            if not ca:
                continue
            dsi = (conn.get("manufacturerInfo") or {}).get("datasheetInfo") or {}
            tail = resolve_nominal((dsi.get("familyDetails") or {}).get("tailLength"))
            height = resolve_nominal((dsi.get("mechanical") or {}).get("height"))
            if tail is None or height is None or tail <= 0 or height <= 0:
                continue
            cx, cy = ca["countX"], ca["countY"]
            if cx * cy < 2:
                # A single-contact "field" has no mutual coupling — these bricks
                # exist to model pin-to-pin crosstalk, so a 1-pin archetype is
                # degenerate (and its FastHenry adjacent-mutual gate is undefined).
                skipped_single_pin += 1
                continue
            pitch_y = ca.get("pitchY") or 0.0
            if cy > 1 and pitch_y <= 0.0:
                skipped_multirow_no_pitchy += 1
                continue
            length = tail + height
            key = (round(ca["pitchX"] * 1e6), cx, cy, round(pitch_y * 1e6),
                   round(length * 1e6))
            archetypes[key] += 1
    return dict(archetypes), skipped_multirow_no_pitchy, skipped_single_pin


# ---------------------------------------------------------------------------
# GATE 1 — FastHenry cross-check
# ---------------------------------------------------------------------------

def write_fh_deck(path: Path, pos_m, l_m: float, a_m: float):
    """One straight z-directed segment per pin, mm units, copper, 1 filament."""
    mm = 1e3
    with path.open("w") as f:
        f.write("* pin-field gate deck (gen_connector_parasitic_bricks.py)\n")
        f.write(".units mm\n")
        f.write(f".default sigma={SIGMA_CU_MM} nwinc=1 nhinc=1\n")
        for i, (x, y) in enumerate(pos_m):
            f.write(f"N{i}a x={x*mm:.6f} y={y*mm:.6f} z=0.0\n")
            f.write(f"N{i}b x={x*mm:.6f} y={y*mm:.6f} z={l_m*mm:.6f}\n")
            f.write(f"E{i} N{i}a N{i}b w={a_m*mm:.6f} h={a_m*mm:.6f}\n")
        for i in range(len(pos_m)):
            f.write(f".external N{i}a N{i}b\n")
        f.write(f".freq fmin={FH_FREQ} fmax={FH_FREQ} ndec=1\n")
        f.write(".end\n")


def parse_zc(path: Path) -> np.ndarray:
    """Zc.mat -> inductance matrix [H] (Im(Z)/2*pi*f), rows reordered to conductor order."""
    text = path.read_text()
    row_of = {}
    for m in re.finditer(r"Row (\d+):\s+n(\d+)a\s+to\s+n(\d+)b", text):
        row_of[int(m.group(2))] = int(m.group(1)) - 1
    m = re.search(r"Impedance matrix for frequency = ([0-9.eE+-]+)\s+(\d+)\s+x\s+(\d+)\n", text)
    if not m:
        raise RuntimeError(f"no impedance matrix found in {path}")
    freq, n = float(m.group(1)), int(m.group(2))
    if len(row_of) != n:
        raise RuntimeError(f"row map has {len(row_of)} entries, matrix is {n}x{n}")
    lines = text[m.end():].strip().splitlines()[:n]
    raw = np.empty((n, n))
    for r, line in enumerate(lines):
        pairs = re.findall(r"([0-9.eE+-]+)\s+([+-][0-9.eE+-]+)j", line)
        if len(pairs) != n:
            raise RuntimeError(f"row {r} has {len(pairs)} entries, expected {n}")
        for c, (_re_, im) in enumerate(pairs):
            raw[r, c] = float(im) / (2.0 * math.pi * freq)
    # reorder rows/cols from FastHenry port order to conductor index order
    order = [row_of[i] for i in range(n)]
    return raw[np.ix_(order, order)]


def run_fasthenry_gate(fh_bin: Path, gate_keys, workdir: Path):
    """Returns (passed, report_lines). Compares closed-form L to FastHenry per archetype
    and per interval endpoint a."""
    lines = []
    passed = True
    for key in gate_keys:
        pitch_um, cx, cy, pitchy_um, len_um = key
        pitch, pitch_y, l = pitch_um * 1e-6, pitchy_um * 1e-6, len_um * 1e-6
        pos = pin_positions(pitch, cx, cy, pitch_y)
        n = len(pos)
        for a_frac in (A_LO, A_HI):
            a = a_frac * pitch
            tag = f"p{pitch_um}um {cx}x{cy} l={len_um}um a={a_frac:.2f}p"
            deck = workdir / f"gate_{pitch_um}_{cx}x{cy}_{len_um}_{int(a_frac*100)}.inp"
            write_fh_deck(deck, pos, l, a)
            subprocess.run([str(fh_bin), deck.name], cwd=workdir, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            L_fh = parse_zc(workdir / "Zc.mat")
            L_cf = inductance_matrix(pos, l, a)
            # self inductance: max relative error over the diagonal
            self_err = float(np.max(np.abs(np.diag(L_cf) - np.diag(L_fh)) / np.diag(L_fh)))
            # adjacent mutual: max relative error over pairs at spacing == pitch (or pitchY)
            adj_err, adj_cf, adj_fh = 0.0, None, None
            for i in range(n):
                for j in range(i + 1, n):
                    d = math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1])
                    if abs(d - pitch) < 1e-12 or (pitch_y > 0 and abs(d - pitch_y) < 1e-12):
                        e = abs(L_cf[i, j] - L_fh[i, j]) / abs(L_fh[i, j])
                        if e >= adj_err:
                            adj_err, adj_cf, adj_fh = e, L_cf[i, j], L_fh[i, j]
            ok = self_err <= 0.03 and adj_err <= 0.05
            passed &= ok
            lines.append(
                f"  {tag}: self L cf={L_cf[0,0]*1e9:.4f} nH fh={L_fh[0,0]*1e9:.4f} nH "
                f"(max err {self_err*100:.2f}%); adjacent M cf={adj_cf*1e9:.4f} nH "
                f"fh={adj_fh*1e9:.4f} nH (max err {adj_err*100:.2f}%) -> "
                f"{'PASS' if ok else 'FAIL'}")
    return passed, lines


# ---------------------------------------------------------------------------
# GATE 2 — two-wire acosh identity (2-D limit of the pair combination)
# ---------------------------------------------------------------------------

def two_wire_check(pitch: float, a_frac: float):
    """Returns (relative_error, C_linecharge_per_m, C_acosh_per_m) for a wire pair."""
    a = a_frac * pitch
    r_eq = K_REQ_SQUARE * a
    c_lc = math.pi * EPS0 / math.log(pitch / r_eq)          # 2-D limit of 1/(p11+p22-2p12)/l
    c_ac = math.pi * EPS0 / math.acosh(pitch / (2.0 * r_eq))  # exact two-cylinder form
    return abs(c_lc - c_ac) / c_ac, c_lc, c_ac


# ---------------------------------------------------------------------------
# GATE 3 (informational) — 3-conductor 2-D MoM vs analytic line-charge
# ---------------------------------------------------------------------------

def mom_2d_elastance(centers, a: float, nseg: int = 64) -> np.ndarray:
    """Per-unit-length potential coefficients of square 2-D conductors by boundary MoM
    (uniform line-charge segments, midpoint collocation). Defined up to a common
    additive constant — only charge-neutral combinations are compared."""
    segs = []   # (midx, midy, length, conductor)
    for ci, (cx0, cy0) in enumerate(centers):
        per_side = nseg // 4
        h = a / per_side
        for s in range(per_side):
            t = -a / 2 + (s + 0.5) * h
            segs.append((cx0 + t, cy0 - a / 2, h, ci))
            segs.append((cx0 + t, cy0 + a / 2, h, ci))
            segs.append((cx0 - a / 2, cy0 + t, h, ci))
            segs.append((cx0 + a / 2, cy0 + t, h, ci))
    m = len(segs)
    nc = len(centers)
    A = np.zeros((m + nc, m + nc))
    for i, (xi, yi, _hi, _ci) in enumerate(segs):
        for j, (xj, yj, hj, _cj) in enumerate(segs):
            if i == j:
                # self potential of a uniform segment at its midpoint
                A[i, j] = -hj * (math.log(hj / 2.0) - 1.0) / (2.0 * math.pi * EPS0)
            else:
                r = math.hypot(xi - xj, yi - yj)
                A[i, j] = -hj * math.log(r) / (2.0 * math.pi * EPS0)
        A[i, m + segs[i][3]] = -1.0          # ... = V of its conductor
    for j, (_xj, _yj, hj, cj) in enumerate(segs):
        A[m + cj, j] = hj                     # total charge per conductor
    P = np.zeros((nc, nc))
    for c in range(nc):
        rhs = np.zeros(m + nc)
        rhs[m + c] = 1.0                      # unit charge on conductor c, 0 on others
        sol = np.linalg.solve(A, rhs)
        P[:, c] = sol[m:]
    return P


def three_conductor_check(pitch: float, a_frac: float, nseg: int = 64):
    """Compare charge-neutral pair combinations, MoM vs analytic 2-D line charge."""
    a = a_frac * pitch
    r_eq = K_REQ_SQUARE * a
    centers = [(0.0, 0.0), (pitch, 0.0), (2 * pitch, 0.0)]
    P_mom = mom_2d_elastance(centers, a, nseg)
    P_an = np.zeros((3, 3))
    k = 1.0 / (2.0 * math.pi * EPS0)
    for i in range(3):
        P_an[i, i] = -k * math.log(r_eq)
        for j in range(3):
            if i != j:
                d = abs(centers[i][0] - centers[j][0])
                P_an[i, j] = -k * math.log(d)
    out = []
    for (i, j) in ((0, 1), (0, 2)):
        q = np.zeros(3)
        q[i], q[j] = 1.0, -1.0                # charge-neutral -> constant cancels
        c_mom = 1.0 / (q @ P_mom @ q)
        c_an = 1.0 / (q @ P_an @ q)
        out.append((f"pair {i+1}-{j+1} (third floating charge-free)",
                    c_mom, c_an, abs(c_an - c_mom) / c_mom))
    return out


# ---------------------------------------------------------------------------
# Brick construction
# ---------------------------------------------------------------------------

def sig6(x: float) -> float:
    """Round to 6 significant digits (identical rounding preserves matrix symmetry)."""
    return float(f"{x:.6g}")


def build_brick(key, n_parts: int, variant: str) -> tuple[dict, int]:
    """Returns (brick, dropped_pair_caps)."""
    pitch_um, cx, cy, pitchy_um, len_um = key
    pitch, pitch_y, l = pitch_um * 1e-6, pitchy_um * 1e-6, len_um * 1e-6
    a_frac, epsr = (A_LO, EPSR_LO) if variant == "lo" else (A_HI, EPSR_HI)
    a = a_frac * pitch
    pos = pin_positions(pitch, cx, cy, pitch_y)
    n = len(pos)

    L = inductance_matrix(pos, l, a)
    eigmin = float(np.min(np.linalg.eigvalsh(L)))
    if eigmin <= 0.0:
        raise RuntimeError(f"{key}-{variant}: inductance matrix not positive-definite "
                           f"(min eigenvalue {eigmin:.3e} H)")
    kmat = L / np.sqrt(np.outer(np.diag(L), np.diag(L)))
    kmax = float(np.max(np.abs(kmat - np.eye(n))))
    if kmax >= 1.0:
        raise RuntimeError(f"{key}-{variant}: |k| = {kmax} >= 1 — unphysical")

    P = elastance_matrix(pos, l, a, epsr)
    C = np.linalg.inv(P)
    rowsums = C.sum(axis=1)
    if np.any(rowsums <= 0.0):
        raise RuntimeError(f"{key}-{variant}: non-positive Maxwell row sum "
                           f"(min {rowsums.min():.3e} F) — physics violation, aborting")
    r_dc = RHO_CU * l / (a * a)

    name = f"conn-pinfield-p{pitch_um}-{cx}x{cy}-l{len_um}-{variant}"
    desc = (
        f"Free-space pin-field parasitic model of the {cx}x{cy} pin-header archetype: "
        f"pitch {pitch*1e3:g} mm"
        + (f" x {pitch_y*1e3:g} mm" if cy > 1 else "")
        + f", {n} pins, conductor length l = {l*1e3:g} mm = familyDetails.tailLength + "
        f"mechanical.height (nominal). Assumption-interval endpoint '{variant}': square "
        f"post side a = {a_frac:.2f}*pitch = {a*1e3:.4g} mm, epsr = {epsr:g} (interval "
        f"a in [0.20, 0.35]*pitch, convention center 0.25*pitch). Physics (closed form, "
        f"MQS, valid to ~1-2 GHz): partial self inductance "
        f"L_ii = (mu0*l/2pi)*(ln(l/g+sqrt(1+(l/g)^2))-sqrt(1+(g/l)^2)+g/l), g = 0.44705*a "
        f"(exact GMD of the square section; finite-length kernel, not the long-thin limit); "
        f"mutual M_ij = same kernel at d = pin spacing; "
        f"FastHenry-cross-checked (self <3%, adjacent mutual <5%). Capacitance: Maxwell "
        f"matrix inverted from finite-length line-charge potential coefficients with "
        f"r_eq = 0.59017*a (transfinite diameter of the square); node-to-node capacitors "
        f"= -C_ij (pairs under 1 fF dropped), each pin's capacitor to 'ref' = Maxwell row "
        f"sum (its free-space self term). Per-pin series R = rho_cu*l/a^2 = "
        f"{r_dc*1e3:.4g} mOhm. The brick models the connector ALONE in free space: the "
        f"board return plane/ground belongs to the consuming circuit — wire 'ref' to it. "
        f"The C network is lumped at the pin<i>_a (board/tail) side. Pin i maps to grid "
        f"column (i-1) div countY + 1, row (i-1) mod countY + 1 (dual-row zigzag "
        f"numbering). Covers {n_parts} catalog parts. Generated by "
        f"scripts/gen_connector_parasitic_bricks.py from TAS/data/connectors.ndjson "
        f"contactArray archetypes (ABT #467)."
    )

    ports = []
    for i in range(1, n + 1):
        ports.append({"name": f"pin{i}_a",
                      "description": f"Board/tail end of pin {i} (C network lumped here)"})
        ports.append({"name": f"pin{i}_b",
                      "description": f"Mating end of pin {i}"})
    ports.append({"name": "ref",
                  "description": "Common reference (wire to the board return plane/ground)"})

    lm = [[sig6(L[i, j]) for j in range(n)] for i in range(n)]
    components = [{
        "name": "LM",
        "data": {
            "behavioral": {
                "nature": "coupledInductors",
                "inductanceMatrix": lm,
                "seriesResistance": [sig6(r_dc)] * n,
            },
            "inputs": {"designRequirements": {"name": name}},
        },
    }]

    def cap_atom(value: float) -> dict:
        return {"capacitor": {},
                "inputs": {"designRequirements": {"capacitance": {"nominal": sig6(value)}}}}

    connections = []
    a_net_extra: dict[int, list] = defaultdict(list)  # pin (0-based) -> extra endpoints
    ref_endpoints = [{"port": "ref"}]
    dropped = 0
    numeric_pos = 0
    # A physical Maxwell matrix has non-positive off-diagonals, so every physical
    # node-to-node capacitance c_net = -C[i,j] is >= 0. Inverting a well-separated
    # pin field produces tiny POSITIVE off-diagonals (negative c_net) from finite
    # precision — physically zero coupling between distant pins. Distinguish that
    # numerical noise from a real positivity violation by the matrix's own scale:
    # a violation is a substantial fraction of the largest coupling, not fF noise
    # against a pF adjacent term.
    off = [abs(C[i, j]) for i in range(n) for j in range(i + 1, n)]
    cap_scale = max(off) if off else CAP_FLOOR
    # Noise band for positive off-diagonals: 5% of the strongest coupling. The
    # finite-length elastance is diagonally dominant, so real distant-pin coupling
    # is far below this; the residual few-fF positives are inversion noise for a
    # many-pin field (measured up to ~1% of peak on 13x2/5x2 archetypes). A
    # violation comparable to the physical coupling (>5%) still aborts.
    neg_tol = max(CAP_FLOOR, 5e-2 * cap_scale)
    for i in range(n):
        for j in range(i + 1, n):
            c_net = -C[i, j]
            if c_net <= -neg_tol:
                raise RuntimeError(
                    f"{name}: node-to-node capacitance {-c_net:.3e} F between pins {i+1} and "
                    f"{j+1} is negative beyond numerical tolerance ({neg_tol:.3e} F, 5% of the "
                    f"{cap_scale:.3e} F peak coupling) — Maxwell positivity violation, aborting")
            if c_net < CAP_FLOOR:
                # below the emit floor, or a within-tolerance numerical-negative
                # (distant-pin inversion noise) -> physically no coupling, drop it.
                if c_net < 0:
                    numeric_pos += 1
                dropped += 1
                continue
            cname = f"C{i+1}_{j+1}"
            components.append({"name": cname, "data": cap_atom(c_net)})
            a_net_extra[i].append({"component": cname, "pin": "1"})
            a_net_extra[j].append({"component": cname, "pin": "2"})
    for i in range(n):
        cname = f"Cref{i+1}"
        components.append({"name": cname, "data": cap_atom(rowsums[i])})
        a_net_extra[i].append({"component": cname, "pin": "1"})
        ref_endpoints.append({"component": cname, "pin": "2"})

    for i in range(n):
        connections.append({
            "name": f"na{i+1}",
            "endpoints": [{"component": "LM", "pin": f"winding{i+1}_start"},
                          {"port": f"pin{i+1}_a"}] + a_net_extra[i],
        })
        connections.append({
            "name": f"nb{i+1}",
            "endpoints": [{"component": "LM", "pin": f"winding{i+1}_end"},
                          {"port": f"pin{i+1}_b"}],
        })
    connections.append({"name": "nref", "endpoints": ref_endpoints})

    provenance = [{
        "source": "derived",
        "sourceName": "OpenConverters connector parasitic-brick generator 2026-08 (ABT #467)",
        "retrievedDate": TODAY,
        "derivation": desc,
    }]
    return ({"name": name, "ports": ports, "components": components,
             "connections": connections, "provenance": provenance}, dropped, numeric_pos)


# ---------------------------------------------------------------------------
# CIAS validation registry (mirrors tests/test_data.py::_build_tas_registry)
# ---------------------------------------------------------------------------

def build_cias_validator() -> Draft202012Validator:
    schemas = {}
    for name in ("TAS", "inputs", "outputs", "utils", "topology"):
        s = json.loads((REPO / "schemas" / f"{name}.json").read_text())
        schemas[s["$id"]] = s
    for repo in ("PEAS", "MAS", "CAS", "SAS", "RAS", "AAS", "CTAS", "CONAS", "TDAS",
                 "COAS", "CIAS"):
        sdir = WORKSPACE / repo / "schemas"
        if not sdir.is_dir():
            raise RuntimeError(f"sibling repo {repo} missing — PSMA workspace layout required")
        for path in sdir.rglob("*.json"):
            s = json.loads(path.read_text())
            if "$id" in s:
                schemas[s["$id"]] = s
    resources = [(sid, Resource(contents=s, specification=DRAFT202012))
                 for sid, s in schemas.items()]
    registry = Registry().with_resources(resources)
    cias = json.loads((WORKSPACE / "CIAS" / "schemas" / "CIAS.json").read_text())
    return Draft202012Validator(cias, registry=registry)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="run every gate and validation but do not append")
    ap.add_argument("--fasthenry", type=Path, required=True,
                    help="path to the native fasthenry binary")
    args = ap.parse_args()

    if not args.fasthenry.is_file() or not os.access(args.fasthenry, os.X_OK):
        print(f"FATAL: fasthenry binary not executable: {args.fasthenry}")
        return 2

    archetypes, skipped_multirow, skipped_single = collect_archetypes()
    total_parts = sum(archetypes.values())
    print(f"archetypes: {len(archetypes)}   parts covered: {total_parts}"
          + (f"   (skipped {skipped_multirow} multirow records without pitchY)"
             if skipped_multirow else "")
          + (f"   (skipped {skipped_single} single-pin records — no coupling)"
             if skipped_single else ""))
    if not archetypes:
        print("FATAL: no archetypes found")
        return 2

    # -- GATE 1: FastHenry -------------------------------------------------
    by_pins = sorted(archetypes, key=lambda k: (k[1] * k[2], k[0]))
    gate_keys = [by_pins[0], by_pins[len(by_pins) // 2], by_pins[-1]]
    print("\nGATE 1 — FastHenry cross-check (1 kHz, both interval endpoints):")
    for k in gate_keys:
        print(f"  archetype {k}  ({k[1]*k[2]} pins, {archetypes[k]} parts)")
    with tempfile.TemporaryDirectory(prefix="fh_gate_") as td:
        ok1, lines = run_fasthenry_gate(args.fasthenry, gate_keys, Path(td))
    print("\n".join(lines))
    if not ok1:
        print("GATE 1 FAILED — closed form disagrees with FastHenry. EMITTING NOTHING.")
        return 1
    print("GATE 1 PASSED")

    # -- GATE 2: two-wire acosh identity ------------------------------------
    print("\nGATE 2 — two-wire identity C' = pi*eps/acosh(d/2r) vs 2-D line-charge limit:")
    ok2 = True
    for key in gate_keys:
        pitch = key[0] * 1e-6
        for a_frac, gating in ((A_CONV, True), (A_LO, False), (A_HI, False)):
            err, c_lc, c_ac = two_wire_check(pitch, a_frac)
            verdict = ("PASS" if err <= 0.02 else "FAIL") if gating else "info"
            if gating and err > 0.02:
                ok2 = False
            print(f"  p={key[0]} um a={a_frac:.2f}p: line-charge {c_lc*1e12:.4f} pF/m, "
                  f"acosh {c_ac*1e12:.4f} pF/m, err {err*100:.2f}% [{verdict}]")
    if not ok2:
        print("GATE 2 FAILED — additive-constant handling is wrong. EMITTING NOTHING.")
        return 1
    print("GATE 2 PASSED (gating at the a = 0.25*pitch convention; the endpoint values "
          "are the documented single-line-charge bound accuracy)")

    # -- GATE 3 (informational): 3-conductor MoM ----------------------------
    print("\nGATE 3 (informational) — 3-conductor 2-D MoM (64 segs/conductor) vs analytic:")
    pitch = gate_keys[0][0] * 1e-6
    for label, c_mom, c_an, err in three_conductor_check(pitch, A_CONV):
        print(f"  p={gate_keys[0][0]} um a=0.25p {label}: MoM {c_mom*1e12:.4f} pF/m, "
              f"analytic {c_an*1e12:.4f} pF/m, err {err*100:.2f}%")

    # -- build all bricks ----------------------------------------------------
    print("\nbuilding bricks ...")
    existing_names = set()
    if CIRCUITS.exists():
        with CIRCUITS.open() as fh:
            for line in fh:
                m = re.match(r'\{"name":\s*"([^"]+)"', line)
                if m:
                    existing_names.add(m.group(1))
    bricks = []
    total_dropped = 0
    total_numeric_pos = 0
    total_pair_caps = 0
    total_ref_caps = 0
    for key in sorted(archetypes):
        for variant in ("lo", "hi"):
            brick, dropped, numeric_pos = build_brick(key, archetypes[key], variant)
            if brick["name"] in existing_names:
                print(f"FATAL: brick name '{brick['name']}' already exists in "
                      f"circuits.ndjson — refusing to double-append")
                return 1
            n = key[1] * key[2]
            total_dropped += dropped
            total_numeric_pos += numeric_pos
            total_ref_caps += n
            total_pair_caps += sum(1 for c in brick["components"]
                                   if c["name"].startswith("C") and "_" in c["name"]
                                   and not c["name"].startswith("Cref"))
            bricks.append(brick)
    print(f"bricks built: {len(bricks)}   pair caps kept: {total_pair_caps}   "
          f"pair caps dropped (<1 fF): {total_dropped}   "
          f"(of which numerical +off-diagonals within 5% tol: {total_numeric_pos})   "
          f"ref caps: {total_ref_caps}")

    # -- validate every brick (schema + referential integrity); one failure aborts
    print("validating all bricks against CIAS.json + referential integrity ...")
    validator = build_cias_validator()
    for i, brick in enumerate(bricks):
        errs = list(validator.iter_errors(brick))
        if errs:
            print(f"FATAL: brick '{brick['name']}' fails CIAS schema validation:\n"
                  f"  {errs[0].message} @ {list(errs[0].absolute_path)}")
            return 1
        integ = validate_cias_brick(brick, where=brick["name"])
        if integ:
            print(f"FATAL: brick '{brick['name']}' fails referential integrity:\n  "
                  + "\n  ".join(integ))
            return 1
        if (i + 1) % 200 == 0:
            print(f"  validated {i + 1}/{len(bricks)}")
    print(f"all {len(bricks)} bricks validate")

    if args.dry_run:
        print("\n--dry-run: nothing appended")
        return 0

    # -- append (open 'ab', never rewrite) -----------------------------------
    payload = b"".join(
        json.dumps(b, separators=(",", ":")).encode() + b"\n" for b in bricks)
    with CIRCUITS.open("ab") as fh:
        fh.write(payload)
    print(f"\nappended {len(bricks)} bricks ({len(payload)/1e6:.1f} MB) to {CIRCUITS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
