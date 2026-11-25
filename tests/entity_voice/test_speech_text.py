from sylphy_entity.voice.text import prepare_speech_text


def test_speech_text_removes_markdown_emojis_and_icon_shortcodes():
    assert prepare_speech_text(
        "Your favorite color is **violet** 🌸 — such a *lovely choice!* :sparkles:"
    ) == "Your favorite color is violet — such a lovely choice!"


def test_speech_text_keeps_link_label_and_inline_code_content():
    assert prepare_speech_text("See [the guide](https://example.test) and `kokoro.onnx`.") == (
        "See the guide and kokoro.onnx."
    )


def test_speech_text_removes_heading_and_list_decoration():
    assert prepare_speech_text("## Remember\n- **violet**\n- blue") == "Remember\nviolet\nblue"


def test_speech_text_does_not_change_plain_text():
    text = "I don't know that yet, and I won't pretend I do."
    assert prepare_speech_text(text) == text


def test_speech_text_removes_unicode_icons_and_preserves_sentence_punctuation():
    assert prepare_speech_text("Hello ✨ Sylphy 🌸! I am here ♡.") == (
        "Hello Sylphy! I am here."
    )


def test_speech_text_removes_keycaps_and_fenced_code():
    assert prepare_speech_text("Step 1️⃣: say hello.\n```python\nprint('hi')\n```Done.") == (
        "Step: say hello. Done."
    )
