"""Workflow YAML schema validator for the three workflow types: PLAIN, LA, HPO.

Pure schema validation: structural presence, types, and values against canonical
allowed sets. No semantic feasibility checks (budget vs. cost, deadline vs.
runtime, etc.). Returns a list of violation strings; empty list means valid.

Canonical sources:
  - SeisSol mesh values: scripts/speedup.py:tinyDaOverhead keys (500, 750, 1000)
  - HPO model names:     scripts/speedup_HPO_runtime.py:model_factors keys
  - License pool names:  config/licenses.yaml pools section
  - software_id values:  CLAUDE.md (1=ANSYS, 2=ABAQUS, 3=LSDYNA)
"""

from typing import Any

VALID_TYPES = ('PLAIN', 'LA', 'HPO')

SEISSOL_MESH_VALUES = {500, 750, 1000}
HPO_MODEL_NAMES = {'vgg19', 'wide_resnet101_2', 'convnext_large'}
LICENSE_POOLS = {'ANSYS', 'ABAQUS', 'LSDYNA'}
SOFTWARE_IDS = {1, 2, 3}
SOFTWARE_ID_TO_POOL = {1: 'ANSYS', 2: 'ABAQUS', 3: 'LSDYNA'}

EXPECTED_API = '4.7.0'

ALLOWED_TOP_KEYS = {'id', 'api', 'actions', 'vars', 'config', 'constraints'}
ALLOWED_CONFIG_KEYS = {
    'PLAIN': {'mesh', 'workflowConfig', 'workflowIterations'},
    'LA':    {'mesh', 'workflowConfig', 'workflowIterations', 'software_id'},
    'HPO':   {'mesh', 'workflowIterations'},
}
ALLOWED_CONSTRAINT_KEYS = {
    'PLAIN': {'budget', 'deadline', 'chains', 'tinydaIterations'},
    'LA':    {'budget', 'deadline', 'chains', 'tinydaIterations', 'license_pool'},
    'HPO':   {'budget', 'deadline', 'chains', 'tinydaIterations'},
}


def _check_field(d: dict, key: str, types: tuple, path: str, violations: list,
                 required: bool = True) -> bool:
    """Return True if field present and well-typed; append violation otherwise."""
    if key not in d:
        if required:
            violations.append(f"{path}.{key}: missing required field")
        return False
    if not isinstance(d[key], types):
        type_names = ' or '.join(t.__name__ for t in types)
        violations.append(
            f"{path}.{key}: wrong type (got {type(d[key]).__name__}, expected {type_names})"
        )
        return False
    return True


def _check_unexpected_keys(d: dict, allowed: set, path: str, violations: list) -> None:
    """Flag any key in d that isn't in the allowed set (catches typos / stale keys)."""
    if not isinstance(d, dict):
        return
    extra = set(d.keys()) - allowed
    for key in sorted(extra):
        violations.append(f"{path}: unexpected key {key!r} (allowed: {sorted(allowed)})")


def _check_positive(d: dict, key: str, path: str, violations: list,
                    allow_zero: bool = False) -> None:
    """Assumes key exists and is numeric; checks > 0 (or >= 0)."""
    val = d[key]
    if allow_zero:
        if val < 0:
            violations.append(f"{path}.{key}: must be >= 0 (got {val})")
    else:
        if val <= 0:
            violations.append(f"{path}.{key}: must be > 0 (got {val})")


def _validate_common(wf: dict, violations: list) -> None:
    """Top-level structure shared by all workflow types."""
    _check_unexpected_keys(wf, ALLOWED_TOP_KEYS, 'workflow', violations)
    _check_field(wf, 'id', (str,), 'workflow', violations)
    _check_field(wf, 'api', (str,), 'workflow', violations)
    if wf.get('api') != EXPECTED_API:
        violations.append(f"workflow.api: expected '{EXPECTED_API}', got {wf.get('api')!r}")

    if _check_field(wf, 'actions', (list,), 'workflow', violations):
        if not wf['actions']:
            violations.append("workflow.actions: must be non-empty")

    _check_field(wf, 'vars', (list,), 'workflow', violations)
    _check_field(wf, 'config', (dict,), 'workflow', violations)
    _check_field(wf, 'constraints', (dict,), 'workflow', violations)


def _validate_constraints_common(c: dict, violations: list) -> None:
    """Constraint fields read by getConstraintsFromWorkflow (all types)."""
    if _check_field(c, 'budget', (int, float), 'constraints', violations):
        _check_positive(c, 'budget', 'constraints', violations)
    if _check_field(c, 'deadline', (int, float), 'constraints', violations):
        _check_positive(c, 'deadline', 'constraints', violations)
    if _check_field(c, 'chains', (int,), 'constraints', violations):
        _check_positive(c, 'chains', 'constraints', violations)
    if _check_field(c, 'tinydaIterations', (int,), 'constraints', violations):
        _check_positive(c, 'tinydaIterations', 'constraints', violations, allow_zero=True)


def _validate_workflow_config_array(cfg: dict, violations: list) -> None:
    """workflowConfig array used by PLAIN and LA."""
    if not _check_field(cfg, 'workflowConfig', (list,), 'config', violations):
        return
    arr = cfg['workflowConfig']
    if not arr:
        violations.append("config.workflowConfig: must be non-empty")
        return
    for i, entry in enumerate(arr):
        path = f"config.workflowConfig[{i}]"
        if not isinstance(entry, dict):
            violations.append(f"{path}: must be a dict")
            continue
        if _check_field(entry, 'chains', (int,), path, violations):
            _check_positive(entry, 'chains', path, violations)
        if _check_field(entry, 'tinydaIterations', (int,), path, violations):
            _check_positive(entry, 'tinydaIterations', path, violations, allow_zero=True)

    if _check_field(cfg, 'workflowIterations', (int,), 'config', violations):
        if cfg['workflowIterations'] != len(arr):
            violations.append(
                f"config.workflowIterations ({cfg['workflowIterations']}) "
                f"!= len(workflowConfig) ({len(arr)})"
            )


def _validate_plain(wf: dict, violations: list) -> None:
    cfg = wf.get('config')
    if not isinstance(cfg, dict):
        return
    _check_unexpected_keys(cfg, ALLOWED_CONFIG_KEYS['PLAIN'], 'config', violations)
    _check_unexpected_keys(wf.get('constraints', {}),
                           ALLOWED_CONSTRAINT_KEYS['PLAIN'], 'constraints', violations)
    if _check_field(cfg, 'mesh', (int,), 'config', violations):
        if isinstance(cfg['mesh'], bool) or cfg['mesh'] not in SEISSOL_MESH_VALUES:
            violations.append(
                f"config.mesh: expected one of {sorted(SEISSOL_MESH_VALUES)}, got {cfg['mesh']!r}"
            )
    _validate_workflow_config_array(cfg, violations)

    constraints = wf.get('constraints', {})
    if isinstance(constraints, dict):
        _validate_constraints_common(constraints, violations)
        if 'license_pool' in constraints:
            violations.append("constraints.license_pool: must NOT be present in PLAIN workflow")
    if 'software_id' in cfg:
        violations.append("config.software_id: must NOT be present in PLAIN workflow")


def _validate_la(wf: dict, violations: list) -> None:
    cfg = wf.get('config')
    if not isinstance(cfg, dict):
        return
    _check_unexpected_keys(cfg, ALLOWED_CONFIG_KEYS['LA'], 'config', violations)
    _check_unexpected_keys(wf.get('constraints', {}),
                           ALLOWED_CONSTRAINT_KEYS['LA'], 'constraints', violations)
    if _check_field(cfg, 'mesh', (int,), 'config', violations):
        if isinstance(cfg['mesh'], bool) or cfg['mesh'] not in SEISSOL_MESH_VALUES:
            violations.append(
                f"config.mesh: expected one of {sorted(SEISSOL_MESH_VALUES)}, got {cfg['mesh']!r}"
            )
    if _check_field(cfg, 'software_id', (int,), 'config', violations):
        if isinstance(cfg['software_id'], bool) or cfg['software_id'] not in SOFTWARE_IDS:
            violations.append(
                f"config.software_id: expected one of {sorted(SOFTWARE_IDS)}, got {cfg['software_id']!r}"
            )
    _validate_workflow_config_array(cfg, violations)

    constraints = wf.get('constraints', {})
    if isinstance(constraints, dict):
        _validate_constraints_common(constraints, violations)
        pool_ok = False
        if _check_field(constraints, 'license_pool', (str,), 'constraints', violations):
            if constraints['license_pool'] not in LICENSE_POOLS:
                violations.append(
                    f"constraints.license_pool: expected one of {sorted(LICENSE_POOLS)}, "
                    f"got {constraints['license_pool']!r}"
                )
            else:
                pool_ok = True
        # software_id ↔ license_pool consistency: 1=ANSYS, 2=ABAQUS, 3=LSDYNA
        sid = cfg.get('software_id')
        if pool_ok and isinstance(sid, int) and sid in SOFTWARE_ID_TO_POOL:
            expected_pool = SOFTWARE_ID_TO_POOL[sid]
            if constraints['license_pool'] != expected_pool:
                violations.append(
                    f"config.software_id ({sid}) is paired with constraints.license_pool "
                    f"{constraints['license_pool']!r}; expected {expected_pool!r} "
                    f"(canonical mapping: {SOFTWARE_ID_TO_POOL})"
                )


def _validate_hpo(wf: dict, violations: list) -> None:
    cfg = wf.get('config')
    if not isinstance(cfg, dict):
        return
    _check_unexpected_keys(cfg, ALLOWED_CONFIG_KEYS['HPO'], 'config', violations)
    _check_unexpected_keys(wf.get('constraints', {}),
                           ALLOWED_CONSTRAINT_KEYS['HPO'], 'constraints', violations)
    if _check_field(cfg, 'mesh', (str,), 'config', violations):
        if cfg['mesh'] not in HPO_MODEL_NAMES:
            violations.append(
                f"config.mesh: expected one of {sorted(HPO_MODEL_NAMES)}, got {cfg['mesh']!r}"
            )
    if 'workflowConfig' in cfg:
        violations.append("config.workflowConfig: must NOT be present in HPO workflow")
    if 'software_id' in cfg:
        violations.append("config.software_id: must NOT be present in HPO workflow")
    _check_field(cfg, 'workflowIterations', (int,), 'config', violations, required=True)
    if cfg.get('workflowIterations', 1) <= 0:
        violations.append(f"config.workflowIterations: must be > 0 (got {cfg.get('workflowIterations')})")

    constraints = wf.get('constraints', {})
    if isinstance(constraints, dict):
        _validate_constraints_common(constraints, violations)
        if 'license_pool' in constraints:
            violations.append("constraints.license_pool: must NOT be present in HPO workflow")

    # vars[0].value should be a dict with hyperparameters; cross-check with config + constraints
    vars_list = wf.get('vars')
    if isinstance(vars_list, list) and vars_list:
        v0 = vars_list[0]
        if not isinstance(v0, dict):
            violations.append("vars[0]: must be a dict")
            return
        val = v0.get('value')
        if not isinstance(val, dict):
            violations.append("vars[0].value: must be a dict for HPO (got "
                              f"{type(val).__name__})")
            return
        for key, types in (('model', (str,)), ('epochs', (int,)),
                           ('learning_rate', (int, float)), ('momentum', (int, float)),
                           ('batch_size', (int,)), ('next_trials', (int,))):
            _check_field(val, key, types, 'vars[0].value', violations)
        # Cross-field consistency
        if 'model' in val and 'mesh' in cfg and val['model'] != cfg['mesh']:
            violations.append(
                f"vars[0].value.model ({val['model']!r}) != config.mesh ({cfg['mesh']!r})"
            )
        if 'epochs' in val and 'tinydaIterations' in constraints \
                and val['epochs'] != constraints['tinydaIterations']:
            violations.append(
                f"vars[0].value.epochs ({val['epochs']}) "
                f"!= constraints.tinydaIterations ({constraints['tinydaIterations']})"
            )
        if 'next_trials' in val and 'chains' in constraints \
                and val['next_trials'] != constraints['chains']:
            violations.append(
                f"vars[0].value.next_trials ({val['next_trials']}) "
                f"!= constraints.chains ({constraints['chains']})"
            )


def validate_workflow(wf: Any, expected_type: str) -> list:
    """Validate a workflow dict against the schema for the expected type.

    Args:
        wf: Parsed YAML (expected to be a dict).
        expected_type: One of 'PLAIN', 'LA', 'HPO'.

    Returns:
        List of violation strings. Empty list = valid.
    """
    if expected_type not in VALID_TYPES:
        raise ValueError(f"expected_type must be one of {VALID_TYPES}, got {expected_type!r}")

    violations = []
    if not isinstance(wf, dict):
        return [f"workflow: top-level must be a dict (got {type(wf).__name__})"]

    _validate_common(wf, violations)

    if expected_type == 'PLAIN':
        _validate_plain(wf, violations)
    elif expected_type == 'LA':
        _validate_la(wf, violations)
    elif expected_type == 'HPO':
        _validate_hpo(wf, violations)

    return violations
