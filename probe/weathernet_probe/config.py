from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass(frozen=True)
class ProbeConfig:
    probe_id: str
    hardware_type: str
    server_url: str
    client_cert_path: str
    client_key_path: str
    ca_cert_path: str
    report_interval_seconds: int
    sensors: List[str]
    spool_path: str
    spool_max_days: int
    log_path: Optional[str]

    @property
    def spool_max_entries(self) -> int:
        return max(1, (self.spool_max_days * 86400) // self.report_interval_seconds)

    @classmethod
    def load(cls, path: str) -> "ProbeConfig":
        raw = yaml.safe_load(Path(path).read_text())

        missing = [
            key
            for key in (
                "probe_id",
                "hardware_type",
                "server_url",
                "client_cert_path",
                "client_key_path",
                "ca_cert_path",
            )
            if not raw.get(key)
        ]
        if missing:
            raise ValueError(f"probe config is missing required field(s): {', '.join(missing)}")

        return cls(
            probe_id=str(raw["probe_id"]),
            hardware_type=str(raw["hardware_type"]),
            server_url=str(raw["server_url"]),
            client_cert_path=str(raw["client_cert_path"]),
            client_key_path=str(raw["client_key_path"]),
            ca_cert_path=str(raw["ca_cert_path"]),
            report_interval_seconds=int(raw.get("report_interval_seconds", 300)),
            sensors=list(raw.get("sensors", [])),
            spool_path=str(raw.get("spool_path", "/var/lib/weathernet-probe/spool.jsonl")),
            spool_max_days=int(raw.get("spool_max_days", 14)),
            log_path=raw.get("log_path"),
        )
