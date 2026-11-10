# Coilcraft inductors

## Sources
- Coilcraft parametric search: https://www.coilcraft.com/en-us/tools/power-inductor-finder (retrieved 2026-11-09); one row per orderable part, values as printed in the manufacturer's table.

## Counts
- magnetics: input 4931
  - possible ordering-code variant of a live part (not submitted): 2800
  - already live in TAS (same manufacturer): 1442
  - already in the TAS quarantine: 203
  - candidates (schema-valid, submitted): 486
  - fixes: live inductance off by an exact power of ten (fixes.magnetics.jsonl, op set): 0

## Field coverage
Accepted candidates: 486.
- electrical.dcResistance.maximum: 486
- electrical.inductance.nominal: 486
- electrical.ratedCurrents: 486
- electrical.selfResonantFrequency: 486
- mechanical.height.maximum: 486
- mechanical.length.maximum: 486
- mechanical.width.maximum: 486
- part.automotive: 234
- part.caseCode: 393
- part.family: 418
- thermal.operatingTemperature.maximum: 486

## Hand-check
Column meaning checked against the parts this manufacturer already has in TAS (same part number): share of shared parts whose value agrees within 2 %, and median ratio ours / TAS.
- L: 1442/1442 agree, median ratio 1
- DCRmax: 1442/1442 agree, median ratio 1
- Ir: 1416/1416 agree, median ratio 1
- Isat: 1414/1414 agree, median ratio 1
- Tmax: 1440/1440 agree, median ratio 1
- longueur: 1442/1442 agree, median ratio 1
- largeur: 1442/1442 agree, median ratio 1
- hauteur: 1442/1442 agree, median ratio 1

Hand-check on 10 random candidates against the Coilcraft series pages (2026-11-10): 0603DC-56N, B06T, 0805HP-47N, 0402DC-24N, 0402DC-51N, 0402DC-22N, 0402CS-19N, 0402CS-1N9, 026011C-33N, 0603CT-51N.
- Inductance, DCR max, Irms and dimensions match; the series pages print dimensions as "A max, B max, C max", so they are stored as maximum (B06T dimensions not checked: drawing only).
- SRF: the parametric table has one more decimal than the series page (0402DC-24N 4.82 GHz vs 4.8 GHz printed); B06T prints SRF as a minimum.
- 026011C-33N: the parametric "Max Temp" is 125 degC while the series page prints a 140 degC part temperature.

## Controls
Not run: the existence controls (fabricated code, real obsolete part) and the candidates gate need the TAS checkout and Blade Runner (Linux). Every part number comes from the manufacturer's own table.

## Doubts
- Length / Width / Height of the parametric table stored as maximum (the series pages print 'A max, B max, C max'); live TAS Coilcraft records store the same numbers as nominal
- Irms and Isat are given without their condition (temperature rise, inductance drop) in the parametric table
