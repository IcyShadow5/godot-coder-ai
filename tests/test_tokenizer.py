from godot_coder.tokenizer import ByteTokenizer


def test_round_trip_unicode_and_gdscript() -> None:
    tokenizer = ByteTokenizer()
    text = 'func _ready() -> void:\n\tprint("Grüße 🌑")\n'
    ids = tokenizer.encode(text, add_bos=True, add_eos=True)
    assert tokenizer.decode(ids, skip_special_tokens=True) == text


def test_structural_token_is_single_id() -> None:
    tokenizer = ByteTokenizer()
    ids = tokenizer.encode("<task>move player</task>")
    assert ids[0] == tokenizer.token_to_id("<task>")
    assert ids[-1] == tokenizer.token_to_id("</task>")
