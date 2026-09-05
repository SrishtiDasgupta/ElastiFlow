# LA scheduler — finalized thesis prose (2026-06-14)

Verified-correct paragraphs from today's session. LaTeX markup as drafted.
Naming: pick "Malleable" OR "Moldable" and use consistently (code says Moldable).

## Policy descriptions (static + elastic families)

> \textbf{First-Come-First-Served Static License-Aware (FCFS-ST-LA)} and
> \textbf{Earliest-Deadline-First Static License-Aware (EDF-ST-LA)} apply static
> allocation, where resources and pool units are committed once at admission under
> the two-phase commit of \S\ref{sec:two-phase} and held for the workflow's
> lifetime. No renegotiation occurs at iteration boundaries. The two variants
> differ only in admission order: \textsc{FCFS-ST-LA} serves contending workflows
> in arrival order, whereas \textsc{EDF-ST-LA} serves them in earliest-deadline-first
> order. They serve as the baseline against which the elastic strategies are evaluated.

> \textbf{First-Come-First-Served License-Aware Malleable-First (FCFS-LAMF)},
> \textbf{Earliest-Deadline-First License-Aware Malleable-First (EDF-LAMF)} and
> \textbf{Hybrid Static-Malleable (HSM)} renegotiate at every iteration boundary at
> which elastic logic is active. They differ in scale-down behaviour. \textsc{HSM}
> suppresses scale-down via a per-workflow phase gate until deadline slack and pool
> pressure jointly permit the transition to full elastic mode, whereas
> \textsc{EDF-LAMF} and \textsc{FCFS-LAMF} permit scale-down from the first iteration
> boundary. As in the static family, the \textsc{FCFS} and \textsc{EDF} variants
> differ only in queue ordering—arrival order versus earliest-deadline-first—which
> determines the priority with which contending workflows are served. All three
> strategies share the urgency-weight instantiation described below and delegate
> allocation resolution to Algorithm~\ref{alg:three-stage-la}.

## HSM gate definition

> The Hybrid Static-Malleable strategy (HSM) modifies EDF-LAMF by adding a
> per-workflow \emph{phase}. Each workflow begins in the \textbf{STATIC} phase and
> makes a one-way transition to the \textbf{MALLEABLE} phase once two conditions hold
> simultaneously: projected deadline slack at the current allocation exceeds a
> threshold fraction $\tau$ of the workflow's **total deadline window** (\emph{safe}),
> and the pool's token occupancy (allocated\,/\,total) falls below a per-pool
> threshold $\rho$ (\emph{cheap}). In the \textsc{Static} phase, scale-down is
> suppressed while urgency-driven scale-up remains active. In the \textsc{Malleable}
> phase, full EDF-LAMF logic, with scale-up and scale-down, applies unchanged.

NOTE: "total deadline window" (deadline − start), NOT "remaining" — code uses the
fixed total window as the denominator.

## HSM rationale (co-location narrative REMOVED — unproven; tightened)

> \textsc{HSM}'s static phase defers scale-down until the pool is uncontended.
> Releasing to the malleable phase permits scale-down, and a workflow that releases
> capacity while its pool is saturated may be unable to re-acquire it when later
> required, jeopardising its deadline; the occupancy threshold $\rho$ therefore
> vetoes the transition until the pool is sufficiently underloaded that
> re-acquisition is low-risk. Empirically the slack (\emph{safe}) condition is
> satisfied for nearly all workflows, so the occupancy (\emph{cheap}) condition is
> the binding determinant of the transition. Thresholds reflect each solver's token
> economics—$\rho = 0.60$ for \textsc{Ansys} and \textsc{Abaqus}, whose workgroup
> and power-law token laws render scale-down cost-adverse, and $\rho = 0.95$ for
> \textsc{Lsdyna}, whose linear law makes scale-down cost-neutral and thus freely
> released—and the phase accepts a small cost premium in exchange for improved
> deadline reliability.

## CAVEATS on the prose above (decide tomorrow)
- The rationale's **per-pool ρ justification (0.60/0.95)** is UNDERCUT by the
  P_thresh sweep (cost insensitive; lower ρ → fewer misses for every pool). If we
  switch to UNIFORM ρ=0.70 (current recommendation), rewrite the last sentence of
  the rationale to drop per-pool token-economics and justify a single ρ as the
  most-elastic threshold that still beats EDF-LAMF on deadline.
- Removed earlier (do not reintroduce without proof): "contention-blind slack
  estimator / linear co-location penalty" narrative, and "panic scale-up
  oscillation" and "unnecessary re-acquisition COST" (HSM is not cheaper than
  EDF-LAMF — the win is deadline-side; cost is a deliberate trade/premium).
