import subprocess
from unittest.mock import patch

from weathernet_probe import health


def _fake_vcgencmd(stdout):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    return run


def test_undervoltage_now_and_occurred_bits_are_decoded():
    # 0x50005 = bits 0, 2, 16, 18 -- under-voltage now (0) and
    # historically (16), plus throttled now/historically (2, 18, not
    # collected here). This is the exact value that surfaced the real
    # SPS30-fan-inrush brownout this feature exists to catch.
    with patch("subprocess.run", _fake_vcgencmd("throttled=0x50005\n")):
        assert health._read_undervoltage_status() == (True, True)


def test_no_undervoltage_decodes_to_false_not_none():
    with patch("subprocess.run", _fake_vcgencmd("throttled=0x0\n")):
        assert health._read_undervoltage_status() == (False, False)


def test_missing_vcgencmd_reports_none_not_a_crash():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert health._read_undervoltage_status() == (None, None)


def test_unparseable_output_reports_none():
    with patch("subprocess.run", _fake_vcgencmd("not what we expected\n")):
        assert health._read_undervoltage_status() == (None, None)


def test_collect_includes_undervoltage_fields():
    with patch("subprocess.run", _fake_vcgencmd("throttled=0x1\n")):
        snapshot = health.collect()
    assert snapshot.undervoltage_now is True
    assert snapshot.undervoltage_occurred is False
