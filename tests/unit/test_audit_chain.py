from packages.shared.audit import (
    GENESIS_HASH,
    compute_chain,
    event_payload,
    verify_chain,
    verify_segment,
)
from packages.shared.schemas import AuditEvent


def make_event(event_id: str, actor: str = "system", action="state_transition", detail=None):
    return AuditEvent(event_id=event_id, actor=actor, action=action, detail=detail or {})


class TestEventPayload:
    def test_payload_is_deterministic_regardless_of_key_order(self):
        a = event_payload("evt_1", "user", "approved", {"b": 1, "a": 2})
        b = event_payload("evt_1", "user", "approved", {"a": 2, "b": 1})
        assert a == b

    def test_payload_changes_when_any_field_changes(self):
        base = event_payload("evt_1", "user", "approved", {"a": 1})
        assert base != event_payload("evt_1", "system", "approved", {"a": 1})
        assert base != event_payload("evt_1", "user", "rejected", {"a": 1})
        assert base != event_payload("evt_1", "user", "approved", {"a": 2})


class TestChain:
    def test_genesis_hash_is_all_zeros_64(self):
        assert len(GENESIS_HASH) == 64
        assert set(GENESIS_HASH) == {"0"}

    def test_first_event_chains_to_genesis(self):
        ev = make_event("evt_1")
        (chained,) = compute_chain([ev])
        assert chained.prev_hash == GENESIS_HASH
        assert chained.hash != GENESIS_HASH

    def test_each_event_links_to_previous(self):
        events = [make_event(f"evt_{i}") for i in range(3)]
        chained = compute_chain(events)
        assert chained[0].prev_hash == GENESIS_HASH
        assert chained[1].prev_hash == chained[0].hash
        assert chained[2].prev_hash == chained[1].hash

    def test_verify_chain_passes_for_untampered_chain(self):
        events = [make_event(f"evt_{i}") for i in range(4)]
        compute_chain(events)
        assert verify_chain(events) is True

    def test_tampering_middle_event_breaks_chain(self):
        events = [make_event(f"evt_{i}") for i in range(4)]
        compute_chain(events)
        events[1].detail = {"tampered": True}
        assert verify_chain(events) is False

    def test_changing_event_id_breaks_chain(self):
        events = [make_event(f"evt_{i}") for i in range(3)]
        compute_chain(events)
        events[1].event_id = "evt_tampered"
        assert verify_chain(events) is False

    def test_removing_event_breaks_chain(self):
        events = [make_event(f"evt_{i}") for i in range(3)]
        compute_chain(events)
        del events[1]
        assert verify_chain(events) is False

    def test_verify_segment_links_to_prior_hash(self):
        events = [make_event(f"evt_{i}") for i in range(3)]
        compute_chain(events)
        assert verify_segment(events[1:], expected_chain_end_hash=events[0].hash) is True
        assert verify_segment(events[1:], expected_chain_end_hash="x" * 64) is False

    def test_verify_chain_accepts_empty(self):
        assert verify_chain([]) is True
