# N=5 EDF — Static vs Moldable (Modeled)
*Modeled 2026-05-08 from R3/R5 empirical τ values + on-prem-first allocation rules.*

## Setup

Dispatch [8, 9, 5, 7, 3], Poisson seed=42 (submits at 0, 271, 390, 472, 487).

## Allocations

### Static
| wf | container | lanes | reason |
|---|---|---:|---|
| data8 | slurm | 4 (full, no scale-down) | on-prem-first |
| data9 | cloud-g4 | 3 (2 res + 1 OD) | slurm full |
| data5 | cloud-g5 | 3 (2 res + 1 OD) | slurm full + on-prem-first checks optimal_type=g5 vs on-prem=g4 (no match) |
| data7 | cloud-g4 | 1 partial (1 OD-g4 left) | slurm full, optimal_type=g4 matches but slurm full |
| data3 | cloud-g5 | 1 partial (1 OD-g5 left) | other clusters full |

### Moldable
| wf | container | lanes (initial → final) | reason |
|---|---|---:|---|
| data8 | slurm | 4 → 2 (scale-down) | on-prem-first |
| data9 | cloud-g4 | 3 (no scale event) | slurm full at submit |
| data5 | slurm | 2 → 4 (scaled up at iter-2 boundary when data8 done) | data8 just scaled down → slurm 2 free → on-prem-first hijacks |
| data7 | cloud-g4 | 1 partial | slurm full (data8+data5=4) |
| data3 | **cluster-g5** | **4 (full, 2 res + 2 OD)** | **cluster-g5 EMPTY because data5 didn't go there** |

## Results

| wf | Static | Moldable | Δ |
|---|---:|---:|---|
| data8 | 1,110 s | 1,598 s | +44% (mold scale overhead) |
| data9 | 7,620 s | 7,620 s | tied |
| data5 | **6,774 s (HIT)** | **8,277 s (MISS by 987)** | **+22% mold worse** |
| data7 | 9,228 s (MISS) | 9,228 s (MISS) | tied |
| **data3** | **5,448 s (MISS by 252)** | **2,032 s (HIT)** | **−63% mold wins** |

| Aggregate | Static | Moldable | Δ |
|---|---:|---:|---|
| Misses | **2/5** (data7, data3) | **2/5** (data5, data7) | **tied count, different wfs** |
| Sum-flowtimes | 30,180 s | 28,754 s | mold −5% |
| **OD cost** | **$5.88** | **$3.60** | **mold −39%** ✅ |
| OD instances | 4 | 4 (same count!) | tied |

## Mechanism shift at N=5

At N=3: moldable saves cost by spawning 1 fewer OD instance (data5 routes to slurm).
**At N=5: moldable saves cost via SHORTER OD instance lifetimes.** Same 4 instances spawned in both modes, but moldable's data3 finishes in 2,032s on cluster-g5 lanes=4; static's data3 trapped in lanes=1 partial finishes in 5,448s. The 2 OD-g5 instances are held 2.7× longer under static.

## Mixed deadline outcome

- Moldable saves **data3** (2,032 vs 5,448; lanes=4 vs lanes=1).
- Moldable loses **data5** (8,277 vs 6,774; slurm partial 2 vs cloud-g5 lanes=3).
- Net: same miss count, different wfs miss.

## What this proves

1. **Moldable cost advantage scales** — 39% at N=5 (similar to 43% at N=3).
2. **Mechanism shifts with N** — from "fewer OD instances" (low N) to "shorter OD lifetimes" (mid N).
3. **Per-wf time effect is workload-dependent** — moldable wins on the wf that gets cluster-g5 fully (data3); loses on the wf trapped in slurm partial (data5).
4. **Same deadline-miss count** but different victims.

## Files

- `N5_MODELED.md` — this writeup
- `sim_n5.py` — reproducible simulation
