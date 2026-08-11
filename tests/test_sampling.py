"""The sampling defaults are a product decision, not an implementation detail.

CLI, Studio chat and the generation service all link to these constants, so
other tests only prove the linkage. This file pins the actual numbers — a
silent drift (say temperature 5.0) must fail loudly instead of passing the
whole suite because every consumer reads the same wrong value.
"""

from godot_coder.sampling import (
    DEFAULT_REPETITION_PENALTY,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
)


def test_interactive_sampling_defaults() -> None:
    assert DEFAULT_TEMPERATURE == 0.8
    assert DEFAULT_TOP_K == 40
    assert DEFAULT_TOP_P == 1.0
    assert DEFAULT_REPETITION_PENALTY == 1.15
