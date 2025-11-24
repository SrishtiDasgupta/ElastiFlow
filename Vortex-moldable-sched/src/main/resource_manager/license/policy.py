from math import ceil, floor, log2
from typing import Dict, Any, Optional

def _round(x: float, mode: str) -> int:
    """
    Helper function to round a float to an integer based on the mode.

    Args:
        x: The float to round.
        mode: The rounding mode.

    Returns:
        The rounded integer.

    Raises:
        ValueError: If the rounding mode is invalid.
    """
    m = (mode or "ceil").lower()
    if mode == "ceil":
        return int(ceil(x))
    elif mode == "floor":
        return int(floor(x))
    elif mode == "nearest":
        return int(round(x))
    else:
        raise ValueError(f"Invalid rounding mode: {mode}")
    

class PerCoreSoftwareCalculations:
    """
    Software-specific calculators for a *per-cpre* licensing policy

    YAML structure (under policy.per_Cre)

    policy:
        mode: per_core
            rounding: ceil
            per_core:
            default_strategy: linear
            strategies:
                lsdyna:
                type: linear                     # tokens = cores
                abaqus:
                type: powerlaw                   # tokens = ceil(max(min_tokens, a * cores^b))
                a: 5.0
                b: 0.422
                min_tokens: 5
                ansys:
                type: ansys_workgroup            # OR: ansys_packs

    Supported strategy types:
      - linear          : tokens = cores
      - powerlaw        : tokens = ceil(max(min_tokens, a * cores^b))
      - ansys_workgroup : tokens = 1 (MEBA) + max(0, cores - 4)
      - ansys_packs     : tokens = 1 (MEBA) + ceil(log2(cores/4)) for cores > 4, else 1
    """

    def __init__(self, cfg: Dict[str, Any], rounding: str):
        pc = cfg or {}
        self.rounding = (rounding or "ceil").lower()
        self.default = (pc.get("default_strategy") or "linear").lower()

        # Normalize strategy dictionary keys to lowercase
        self.strategies: Dict[str, Dict[str, Any]] = {
            (k or "").strip().lower(): (v or {}) for k, v in (pc.get("strategies") or {}).items()
        }

    # ---------- strategy implementations ---------- #
    def _linear(self, cores: int, _: Dict[str, Any]) -> int:
        return max(0, int(cores))

    def _powerlaw(self, cores: int, params: Dict[str, Any]) -> int:
        if cores <= 0:
            return 0
        a = float(params.get("a", 5.0))
        b = float(params.get("b", 0.422))
        mn = int(params.get("min_tokens", 5))
        val = max(mn, a * (float(cores) ** b))
        return _round(val, self.rounding)

    def _ansys_workgroup(self, cores: int, _: Dict[str, Any]) -> int:
        if cores <= 0:
            return 0
        # 1 MEBA + per-core beyond 4
        return 1 + max(0, int(cores) - 4)

    def _ansys_packs(self, cores: int, _: Dict[str, Any]) -> int:
        if cores <= 0:
            return 0
        # 1 MEBA + number of doubling packs beyond 4 cores
        packs = 0 if cores <= 4 else _round(log2(float(cores) / 4.0), "ceil")
        return 1 + packs

    # ---------- dispatcher ---------- #
    def tokens_for(self, software: Optional[str], cores: int) -> int:
        """
        Compute tokens for the given software label and total cores.
        Unknown software -> falls back to default strategy ('linear' by default).
        """
        sw = (software or "").strip().lower()
        params = self.strategies.get(sw)
        typ = (params.get("type") if params else self.default).lower()

        if   typ == "linear":
            return self._linear(cores, params or {})
        elif typ == "powerlaw":
            return self._powerlaw(cores, params or {})
        elif typ == "ansys_workgroup":
            return self._ansys_workgroup(cores, params or {})
        elif typ == "ansys_packs":
            return self._ansys_packs(cores, params or {})
        else:
            # Safe fallback
            return self._linear(cores, {})
        
class Policy:
    """
    License token policy.

    Modes:
      - 'per_core' (default): token calculation is per-core but *software-specific*.
      - 'per_chain'        : flat tokens per chain (legacy / simple mode).

    Typical usage:
        policy = Policy.from_cfg(cfg.get("policy", {}))
        tokens = policy.tokens_required(chain_cores=96, software="abaqus")
    """

    def __init__(
        self,
        mode: str = "per_core",
        tokens_per_chain: int = 1,
        rounding: str = "ceil",
        per_core_cfg: Optional[Dict[str, Any]] = None,
    ):
        self.mode = (mode or "per_core").lower()
        self.tokens_per_chain = int(tokens_per_chain)
        self.rounding = (rounding or "ceil").lower()

        if self.mode not in ("per_core", "per_chain"):
            raise ValueError(f"Unknown policy mode: {self.mode}")

        # Per-core calculators (software-specific)
        self.per_core = PerCoreSoftwareCalculations(per_core_cfg or {}, self.rounding)

    def tokens_required(self, chain_cores: int, software: Optional[str] = None) -> int:
        """
        Compute tokens required by a chain:
          - per_chain: return fixed tokens_per_chain
          - per_core : use software-specific per-core calculator (requires software label)
        """
        if self.mode == "per_chain":
            return max(0, int(self.tokens_per_chain))

        # per_core with software-specific calculator
        return self.per_core.tokens_for(software, chain_cores)

    # Factory from YAML dict
    @classmethod
    def from_cfg(cls, cfg: dict):
        p = cfg or {}
        return cls(
            mode=p.get("mode", "per_core"),
            tokens_per_chain=p.get("tokens_per_chain", 1),
            rounding=p.get("rounding", "ceil"),
            per_core_cfg=p.get("per_core", {}),
        )    
