"""Central sampling defaults for generation.

The CLI and the Studio chat used to carry their own, conflicting numbers
(CLI: temperature 0.8 / top-k 40, Studio: 0.4 / 10). One module now owns
the interactive defaults so both entry points agree unless the user
explicitly overrides them. Evaluation (benchmark.py) stays deliberately
greedy so runs are reproducible.
"""

from __future__ import annotations

DEFAULT_TEMPERATURE = 0.8
DEFAULT_TOP_K = 40
DEFAULT_TOP_P = 1.0
DEFAULT_REPETITION_PENALTY = 1.15
