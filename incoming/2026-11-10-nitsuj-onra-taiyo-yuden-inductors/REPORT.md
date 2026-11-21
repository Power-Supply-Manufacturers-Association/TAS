# Taiyo Yuden inductors

## Sources
- Taiyo Yuden TY-COMPAS inductor search: https://ds.yuden.co.jp/TYCOMPAS/ut/search?cid=L&u=M (retrieved 2026-11-10); one row per orderable part, values as printed in the manufacturer's table.

## Counts
- magnetics: input 8036
  - already live in TAS (same manufacturer): 2528
  - possible ordering-code variant of a live part (not submitted): 41
  - already in the TAS quarantine: 80
  - candidates (schema-valid, submitted): 5387
  - fixes: live inductance off by an exact power of ten (fixes.magnetics.jsonl, op set): 0

## Field coverage
Accepted candidates: 5387.
- electrical.dcResistance.maximum: 5387
- electrical.dcResistance.nominal: 3700
- electrical.inductance.nominal: 5387
- electrical.ratedCurrents: 2734
- electrical.saturationCurrentPeak: 2734
- electrical.selfResonantFrequency: 4716
- mechanical.height.maximum: 1857
- mechanical.height.nominal: 2985
- mechanical.length.nominal: 5387
- mechanical.mounting: 5387
- mechanical.width.nominal: 5387
- part.automotive: 159
- part.caseCode: 3121
- part.description: 5387
- part.material: 2677
- thermal.operatingTemperature.maximum: 5387
- thermal.operatingTemperature.minimum: 5387

## Hand-check
Column meaning checked against the parts this manufacturer already has in TAS (same part number): share of shared parts whose value agrees within 2 %, and median ratio ours / TAS.
- L: 2528/2528 agree, median ratio 1
- DCRmax: 2504/2509 agree, median ratio 1
- DCRtyp: 2501/2519 agree, median ratio 1
- Ir: 1622/2240 agree, median ratio 1
- Isat: 2268/2268 agree, median ratio 1
- Tmax: 2500/2500 agree, median ratio 1
- longueur: 2500/2500 agree, median ratio 1
- largeur: 2500/2500 agree, median ratio 1
- hauteur: 1685/2500 agree, median ratio 1
- SRF: 14/14 agree, median ratio 1

Ten random candidates to compare with the vendor document (not yet done by hand):
- HKQ04022N9S-T
- HKQ0603W2N2S-T
- LLBHB2012KKTR24N
- LLXBD3030MKT2R2M
- LMQCA201616T220M
- LBQBA251818T331K
- HKQ04023N1C-E
- LLQNA201212T220K
- LBQEA201212T1R0M
- HKQ0603U0N9B-T

## Controls
Not run: the existence controls (fabricated code, real obsolete part) and the candidates gate need the TAS checkout and Blade Runner (Linux). Every part number comes from the manufacturer's own table.

## Doubts
- I Temp.(max) and I Sat.(max) are given without their condition (temperature rise, inductance drop)
- selfResonantFrequency holds the table's SRF (min) value
- ratedCurrents holds I Temp.(max); some live TAS records hold the table's I(max) column (the lower of I Sat. and I Temp.) instead: 618 of 2240 shared parts differ this way
- status 'production' only for 'Mass Production (Preferred)' and 'Mass Production'; other lifecycle states (previous category, non-preferred, phase-out, discontinued) carry no status
