# Sumida inductors

## Sources
- Sumida inductor parametric search: https://products.sumida.com/ProductsInfo/ProductSearch/InductorParamSearch3.php (retrieved 2026-11-10); one row per orderable part, values as printed in the manufacturer's table.

## Counts
- magnetics: input 4925
  - already in the TAS quarantine: 28
  - already live in TAS (same manufacturer): 419
  - suspect after the hand-check (suspects.jsonl, not submitted): 1
  - candidates (schema-valid, submitted): 4477
  - fixes: live inductance off by an exact power of ten (fixes.magnetics.jsonl, op set): 22

## Field coverage
Accepted candidates: 4477.
- electrical.dcResistance.nominal: 4295
- electrical.inductance.nominal: 4477
- electrical.ratedCurrents: 4023
- electrical.saturationCurrentPeak: 4067
- mechanical.height.maximum: 4477
- mechanical.length.maximum: 4477
- mechanical.mounting: 4477
- mechanical.width.maximum: 4477
- part.automotive: 1016
- part.material: 4284
- part.shielded: 4477
- thermal.operatingTemperature.maximum: 1333

## Hand-check
Column meaning checked against the parts this manufacturer already has in TAS (same part number): share of shared parts whose value agrees within 2 %, and median ratio ours / TAS.
- L: 392/419 agree, median ratio 1
- DCRtyp: 10/165 agree, median ratio 0.7983
- Ir: 6/68 agree, median ratio 1.13
- Isat: 56/294 agree, median ratio 1.18
- Tmax: 0/5 agree, median ratio 0.84

Hand-check on 10 random candidates against the Sumida series datasheets on products.sumida.com (2026-11-10): C2DEPIH80D90NP-100MC, CY0630AT125DS-8R2MC, RCH110NP-561K, CDRH5D28NP-560NC, CYRS5045T150DS-101MC, WCDMT0520DS-1R0MC, CDMC10D48L150NP-R68MC, CDRH104NP-271MC, CYRS8040T150DS-101MC, 0512CDMCCDS-R22MC.
- Inductance, typical DCR, typical Isat, typical Irms, maximum dimensions and Tmax match the datasheets ("Max.(Typ.)" tables) for 9 of 10 parts.
- RCH110NP-561K: the table's 0.708 Ohm is not printed in the RCH110 datasheet (1.000 Ohm max for 560 uH; 0.710 Ohm is printed for 470 uH): listed in suspects.jsonl, not submitted.

## Controls
Not run: the existence controls (fabricated code, real obsolete part) and the candidates gate need the TAS checkout and Blade Runner (Linux). Every part number comes from the manufacturer's own table.

## Doubts
- the table prints typical values only (Isat Typ at 20 degC, Irms Typ, DCR Typ): DCR stored as dcResistance.nominal
- dimensions are the table's maximum dimensions (Width / Length / Height max)
- rows whose name carries a winding note ('(single winding)', '(1-2)') are left out
