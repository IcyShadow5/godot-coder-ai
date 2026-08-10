"""Tests for prepare_data — arg parsing, tokenizer create/load paths, summary output."""

from pathlib import Path

from godot_coder import prepare_data


def test_parse_args_defaults(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["prepare_data"])
    args = prepare_data.parse_args()
    assert args.input == "data/raw"
    assert args.output == "data/processed"
    assert args.tokenizer == "artifacts/tokenizer.json"
    assert args.val_ratio == 0.15
    assert args.extensions == [".gd"]
    assert args.sampling_policy == "packed_with_file_sep"


def test_parse_args_overrides(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_data",
            "--input", "in",
            "--output", "out",
            "--val-ratio", "0.2",
            "--shard-tokens", "1000",
            "--create-byte-tokenizer",
        ],
    )
    args = prepare_data.parse_args()
    assert args.input == "in"
    assert args.output == "out"
    assert args.val_ratio == 0.2
    assert args.shard_tokens == 1000
    assert args.create_byte_tokenizer is True


class _FakeTokenizer:
    vocab_size = 4096

    def save(self, path: Path) -> None:
        path.write_text("fake", encoding="utf-8")


def test_main_creates_tokenizer_and_prints_summary(tmp_path, monkeypatch, capsys) -> None:
    input_dir = tmp_path / "raw"
    input_dir.mkdir()
    output_dir = tmp_path / "processed"
    tokenizer_path = tmp_path / "tokenizer.json"
    manifest = {
        "dataset_fingerprint": "abc123",
        "splits": {"train": {"tokens": 1200}, "val": {"tokens": 300}},
    }
    calls: dict = {}

    def fake_prepare(input_dir_, output_dir_, tokenizer, **kwargs):
        calls["input"] = str(input_dir_)
        calls["output"] = str(output_dir_)
        calls["kwargs"] = kwargs
        return manifest

    monkeypatch.setattr(prepare_data, "prepare_dataset", fake_prepare)
    monkeypatch.setattr(prepare_data, "ByteTokenizer", _FakeTokenizer)
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_data",
            "--input", str(input_dir),
            "--output", str(output_dir),
            "--tokenizer", str(tokenizer_path),
            "--val-ratio", "0.2",
        ],
    )
    prepare_data.main()
    assert calls["input"] == str(input_dir)
    assert calls["kwargs"]["val_ratio"] == 0.2
    assert tokenizer_path.exists()
    out = capsys.readouterr().out
    assert "1,500 tokens" in out
    assert "abc123" in out


def test_main_loads_existing_tokenizer(tmp_path, monkeypatch) -> None:
    input_dir = tmp_path / "raw"
    input_dir.mkdir()
    tokenizer_path = tmp_path / "tokenizer.json"
    tokenizer_path.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(
        prepare_data,
        "prepare_dataset",
        lambda *a, **k: {"dataset_fingerprint": "x", "splits": {"train": {"tokens": 10}}},
    )
    monkeypatch.setattr(prepare_data, "ByteTokenizer", _FakeTokenizer)
    loaded: list = []
    monkeypatch.setattr(prepare_data, "load_tokenizer", lambda p: (loaded.append(p), _FakeTokenizer())[1])
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_data",
            "--input", str(input_dir),
            "--output", str(tmp_path / "out"),
            "--tokenizer", str(tokenizer_path),
        ],
    )
    prepare_data.main()
    assert loaded == [tokenizer_path]
