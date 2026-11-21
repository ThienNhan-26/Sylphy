from sylphy.avatar import PresentationState, normalize_presentation_state


def test_normalize_accepts_supported_activity_states():
    for state in PresentationState:
        if state is PresentationState.UNAVAILABLE:
            continue
        assert normalize_presentation_state(state) is state
        assert normalize_presentation_state(state.value) is state


def test_normalize_rejects_unavailable_and_unbounded_emotion_labels():
    assert normalize_presentation_state(PresentationState.UNAVAILABLE) is None
    assert normalize_presentation_state("HAPPY") is None
    assert normalize_presentation_state("ANGRY") is None
    assert normalize_presentation_state("unknown") is None
    assert normalize_presentation_state(None) is None
