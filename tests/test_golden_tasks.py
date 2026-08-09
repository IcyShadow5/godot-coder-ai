"""Quick sanity checks on the golden task definitions to catch drift."""

from __future__ import annotations

from godot_coder.golden_tasks import GOLDEN_TASKS, task_count, tasks_by_topic


def test_task_count_is_thirty() -> None:
    assert task_count() == 30, f"Expected 30, got {task_count()}"


def test_every_task_has_scaffold_and_reference() -> None:
    for task in GOLDEN_TASKS:
        assert isinstance(task["id"], str), f"id not string in {task}"
        assert isinstance(task["scaffold"], str), f"{task['id']} missing scaffold"
        assert task["scaffold"].strip(), f"{task['id']} scaffold is empty"
        assert isinstance(task["reference_suffix"], str), f"{task['id']} missing reference_suffix"
        assert task["reference_suffix"].strip(), f"{task['id']} reference_suffix is empty"


def test_every_scaffold_starts_with_extends() -> None:
    for task in GOLDEN_TASKS:
        assert task["scaffold"].strip().startswith("extends "), (
            f"{task['id']} scaffold does not start with 'extends': "
            f"{task['scaffold'][:60]!r}"
        )


def test_every_task_has_topic_and_difficulty() -> None:
    valid_topics = {
        "functions", "signals", "collections", "gameplay",
        "nodes", "state", "basics", "architecture",
    }
    valid_difficulties = {"easy", "medium", "hard"}
    for task in GOLDEN_TASKS:
        assert task["topic"] in valid_topics, (
            f"{task['id']}: unknown topic {task['topic']!r}"
        )
        assert task["difficulty"] in valid_difficulties, (
            f"{task['id']}: unknown difficulty {task['difficulty']!r}"
        )


def test_tasks_by_topic_covers_all_eight() -> None:
    by_topic = tasks_by_topic()
    expected = {"functions", "signals", "collections", "gameplay", "nodes", "state", "basics", "architecture"}
    assert set(by_topic.keys()) == expected, f"Got topics: {set(by_topic.keys())}"


def test_no_duplicate_ids() -> None:
    ids = [task["id"] for task in GOLDEN_TASKS]
    assert len(ids) == len(set(ids)), f"Duplicate ids: {[i for i in ids if ids.count(i) > 1]}"


def test_benchmark_imports_golden_tasks() -> None:
    """The benchmark module must import GOLDEN_TASKS so --mode=golden works."""
    from godot_coder.benchmark import GOLDEN_TASKS, run_benchmark

    # run_benchmark wiring must stay intact: it takes a checkpoint path and
    # knows about the golden task list. If the import chain breaks, --mode=golden
    # would fail at runtime instead of here.
    assert callable(run_benchmark)
    assert GOLDEN_TASKS, "golden task list must be non-empty"
