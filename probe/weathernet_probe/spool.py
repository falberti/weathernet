import json
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


class Spool:
    """On-disk retry queue for readings that failed to send.

    A simple append-only JSON-lines file -- no database needed on the
    probe side. These devices run outdoors, unattended, for months: a
    flaky connection should degrade to "data delayed", not "data lost".

    Capped at `max_entries` so a long outage can't fill the SD card;
    the oldest entries are dropped first once the cap is hit, and each
    drop is logged.
    """

    def __init__(self, path: str, max_entries: int):
        self.path = Path(path)
        self.max_entries = max_entries
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, payload: dict) -> None:
        self._store(self._read_all() + [payload])

    def requeue_front(self, payloads: List[dict]) -> None:
        """Put payloads back at the front of the spool (oldest first)."""
        if payloads:
            self._store(payloads + self._read_all())

    def pop_all(self) -> List[dict]:
        entries = self._read_all()
        self._store([])
        return entries

    def __len__(self) -> int:
        return len(self._read_all())

    def _store(self, entries: List[dict]) -> None:
        if len(entries) > self.max_entries:
            dropped = len(entries) - self.max_entries
            entries = entries[dropped:]
            logger.warning(
                "Spool exceeded %d entries; dropped %d oldest reading(s)",
                self.max_entries,
                dropped,
            )
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w") as f:
            for entry in entries:
                f.write(json.dumps(entry))
                f.write("\n")
        tmp_path.replace(self.path)

    def _read_all(self) -> List[dict]:
        if not self.path.exists():
            return []
        entries = []
        with self.path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping corrupt spool line")
        return entries
