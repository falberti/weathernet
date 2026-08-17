from weathernet_probe.spool import Spool


def test_append_and_pop_all_round_trip(tmp_path):
    spool = Spool(str(tmp_path / "spool.jsonl"), max_entries=10)
    spool.append({"a": 1})
    spool.append({"a": 2})

    assert len(spool) == 2
    assert spool.pop_all() == [{"a": 1}, {"a": 2}]
    assert len(spool) == 0


def test_cap_drops_oldest_entries(tmp_path):
    spool = Spool(str(tmp_path / "spool.jsonl"), max_entries=3)
    for i in range(5):
        spool.append({"i": i})

    entries = spool.pop_all()
    assert [e["i"] for e in entries] == [2, 3, 4]


def test_requeue_front_puts_entries_before_existing(tmp_path):
    spool = Spool(str(tmp_path / "spool.jsonl"), max_entries=10)
    spool.append({"i": 3})
    spool.requeue_front([{"i": 1}, {"i": 2}])

    entries = spool.pop_all()
    assert [e["i"] for e in entries] == [1, 2, 3]


def test_corrupt_line_is_skipped(tmp_path):
    path = tmp_path / "spool.jsonl"
    path.write_text('{"i": 1}\nnot-json\n{"i": 2}\n')
    spool = Spool(str(path), max_entries=10)

    entries = spool.pop_all()
    assert [e["i"] for e in entries] == [1, 2]
