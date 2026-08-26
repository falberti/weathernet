from weathernet_probe.main import _flush_and_send
from weathernet_probe.spool import Spool
from weathernet_probe.transport import TransportError


class _FakeTransport:
    """Records every payload handed to send(); fails once, at a chosen
    call index, to simulate the server/network going away mid-flush.
    """

    def __init__(self, fail_at=None):
        self.sent = []
        self.fail_at = fail_at

    def send(self, payload):
        index = len(self.sent)
        if self.fail_at is not None and index == self.fail_at:
            raise TransportError("simulated failure")
        self.sent.append(payload)


def test_send_succeeds_with_empty_backlog(tmp_path):
    spool = Spool(str(tmp_path / "spool.jsonl"), max_entries=10)
    transport = _FakeTransport()

    _flush_and_send(transport, spool, {"i": 1})

    assert transport.sent == [{"i": 1}]
    assert len(spool) == 0


def test_failed_send_spools_the_current_reading(tmp_path):
    spool = Spool(str(tmp_path / "spool.jsonl"), max_entries=10)
    transport = _FakeTransport(fail_at=0)

    _flush_and_send(transport, spool, {"i": 1})

    assert transport.sent == []
    assert spool.pop_all() == [{"i": 1}]


def test_backlog_is_sent_before_current_reading_oldest_first(tmp_path):
    spool = Spool(str(tmp_path / "spool.jsonl"), max_entries=10)
    spool.append({"i": 1})
    spool.append({"i": 2})
    transport = _FakeTransport()

    _flush_and_send(transport, spool, {"i": 3})

    assert transport.sent == [{"i": 1}, {"i": 2}, {"i": 3}]
    assert len(spool) == 0


def test_partial_backlog_failure_requeues_only_what_was_not_sent(tmp_path):
    spool = Spool(str(tmp_path / "spool.jsonl"), max_entries=10)
    spool.append({"i": 1})
    spool.append({"i": 2})
    # First send (backlog item i=1) succeeds, second (i=2) fails -- the
    # current reading (i=3) never even gets attempted this cycle.
    transport = _FakeTransport(fail_at=1)

    _flush_and_send(transport, spool, {"i": 3})

    assert transport.sent == [{"i": 1}]
    assert spool.pop_all() == [{"i": 2}, {"i": 3}]
