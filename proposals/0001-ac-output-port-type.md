# TAS-RFC 0001 — Grid-side AC output port type (inverters) + bidirectional port typing

- **Status:** Draft (stub — from the 2026-07 workspace review and the COAS redesign research)
- **Created:** 2026-07-02

## Problem

`topology.json`'s `portType` enum (`acLine, pulsatingDc, dcBus, hfAc, dcOutput, control`)
has no **grid-side AC output** (the `inverter` stageRole feeds `hfAc`, not a mains port), so
a grid-tied inverter/microinverter deck cannot type its output stage. There is also no
bidirectional/battery port typing, which V2G/ESS topologies need (COAS models these at the
product level via `direction: bidirectional`; TAS cannot express the circuit side).

## Direction (to be designed)

Add `acOutput` (and consider `dcBidirectional`/`battery`) to `portType`; decide whether
`outputRequirement` needs an AC-rail shape (frequency window, VA) or whether inverter specs
stay COAS-level with TAS carrying only the circuit. Keep aligned with the COAS port model
(ports[].electrical kinds dc/acSinglePhase/acThreePhase/pv).
