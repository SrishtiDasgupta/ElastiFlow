"""Canonical display names for the LA scheduling policies.

Maps the internal scheduler identifiers (as used in --scheduler and in the
*_results.csv / canonical_results.json keys) to the thesis-facing names.
Naming: the two static baselines keep their ST-LA tags; the elastic policies are
named by ordering class (FCFS-LAMF, EDF-LAMF) plus HSM. Hatching still marks the
static (ST-LA) class. All LA figure scripts import DISPLAY/ORDER from here so
naming stays consistent across the chapter.
"""

DISPLAY = {
    'FCFS-ST-LA': 'FCFS-ST-LA',
    'EDF-ST-LA':  'EDF-ST-LA',
    'LAMF':       'FCFS-LAMF',
    'EDF-LAMF':   'EDF-LAMF',
    'EDF-HSM':    'HSM',
}

# Canonical plotting order: statics first, then elastics; FCFS-class before EDF-class.
INTERNAL_ORDER = ['FCFS-ST-LA', 'EDF-ST-LA', 'LAMF', 'EDF-LAMF', 'EDF-HSM']
ORDER = [DISPLAY[p] for p in INTERNAL_ORDER]

STATIC = ['FCFS-ST-LA', 'EDF-ST-LA']
ELASTIC = ['LAMF', 'EDF-LAMF', 'EDF-HSM']

# Static/elastic pairs by ordering class, for pairwise % improvement.
PAIRS = {
    'FCFS': ('FCFS-ST-LA', 'LAMF'),       # STATIC-FCFS vs ELASTIC-FCFS
    'EDF':  ('EDF-ST-LA', 'EDF-LAMF'),    # STATIC-EDF  vs ELASTIC-EDF
    'EDF-HSM': ('EDF-ST-LA', 'EDF-HSM'),  # STATIC-EDF  vs ELASTIC-HSM
}
