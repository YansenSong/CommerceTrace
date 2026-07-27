from commerce_trace.models import EventType, StreamEvent


def test_stream_event_has_stable_sse_envelope() -> None:
    event = StreamEvent(
        event=EventType.ANSWER_DELTA,
        conversation_id="conv-1",
        request_id="req-1",
        payload={"delta": "销售额"},
    )

    encoded = event.to_sse()

    assert f"id: {event.event_id}" in encoded
    assert "event: answer.delta" in encoded
    assert '"conversation_id":"conv-1"' in encoded
    assert '"request_id":"req-1"' in encoded
    assert "销售额" in encoded
    assert encoded.endswith("\n\n")
