"""Configuration loader for stress test scenarios.

Reads YAML config files and produces StressTestConfig dataclass instances.
"""

from dataclasses import dataclass, field
from enum import Enum


class MockMode(Enum):
    PASSTHROUGH = "passthrough"
    RECORD = "record"
    SIMULATE = "simulate"


@dataclass
class MockConfig:
    mode: MockMode = MockMode.SIMULATE
    inject_latency: float = 0.0
    failure_rate: float = 0.0
    record_metrics: bool = True


@dataclass
class StressTestConfig:
    name: str
    description: str = ""
    duration_seconds: float = 60.0
    ramp_up_seconds: float = 0.0
    concurrent_users: int = 1
    rate_per_second: int = 1
    total_events: int = 0
    cooldown_seconds: float = 5.0
    mock_mode: MockMode = MockMode.SIMULATE
    mock_latency_ms: float = 0.0
    mock_failure_rate: float = 0.0
    sample_interval_seconds: float = 1.0
    metrics_export_path: str = ""
    overrides: dict = field(default_factory=dict)
    fail_on_error_rate: float = 0.10
    fail_on_memory_leak_mb: float = 200.0

    @classmethod
    def from_dict(cls, d, base_name=""):
        """Create config from dictionary."""
        return cls(
            name=d.get("name", base_name),
            description=d.get("description", ""),
            duration_seconds=d.get("duration_seconds", 60.0),
            ramp_up_seconds=d.get("ramp_up_seconds", 0.0),
            concurrent_users=d.get("concurrent_users", 1),
            rate_per_second=d.get("rate_per_second", 1),
            total_events=d.get("total_events", 0),
            cooldown_seconds=d.get("cooldown_seconds", 5.0),
            mock_mode=_parse_mock_mode(d.get("mock_mode", "simulate")),
            mock_latency_ms=d.get("mock_latency_ms", 0.0),
            mock_failure_rate=d.get("mock_failure_rate", 0.0),
            sample_interval_seconds=d.get("sample_interval_seconds", 1.0),
            metrics_export_path=d.get("metrics_export_path", ""),
            overrides=d.get("overrides", {}),
        )


@dataclass
class GlobalConfig:
    output_dir: str = "tests/stress/results"
    mock_mode: MockMode = MockMode.SIMULATE
    report_formats: list = field(default_factory=lambda: ["console", "json"])
    fail_on_error_rate: float = 0.05
    fail_on_memory_leak_mb: float = 100.0

    @classmethod
    def from_dict(cls, d):
        if not d:
            return cls()
        return cls(
            output_dir=d.get("output_dir", "tests/stress/results"),
            mock_mode=_parse_mock_mode(d.get("mock_mode", "simulate")),
            report_formats=d.get("report_formats", ["console", "json"]),
            fail_on_error_rate=d.get("fail_on_error_rate", 0.05),
            fail_on_memory_leak_mb=d.get("fail_on_memory_leak_mb", 100.0),
        )


def _parse_mock_mode(s):
    if isinstance(s, MockMode):
        return s
    try:
        return MockMode(s.lower())
    except ValueError:
        return MockMode.SIMULATE


def load_scenario_config(path):
    """Load a YAML scenario configuration file.

    Returns (GlobalConfig, dict[str, list[StressTestConfig]])
    """
    import yaml

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    global_cfg = GlobalConfig.from_dict(data.get("global", {}))

    suites = {}
    raw_suites = data.get("suites", {})
    for suite_name, suite_data in raw_suites.items():
        if isinstance(suite_data, dict):
            configs = _parse_suite(suite_name, suite_data, global_cfg)
            if configs:
                suites[suite_name] = configs

    return global_cfg, suites


def _parse_suite(name, data, global_cfg):
    """Parse a suite definition into a list of StressTestConfig instances."""
    configs = []
    mock_mode = _parse_mock_mode(data.get("mock_mode", global_cfg.mock_mode))

    # If suite has "levels", create one config per level
    levels = data.get("levels")
    if levels:
        for level_name, level_data in levels.items():
            cfg = StressTestConfig.from_dict({
                "name": f"{name}_{level_name}",
                "description": data.get("description", ""),
                **level_data,
                "mock_mode": mock_mode,
            })
            configs.append(cfg)
        return configs

    # Treat entire suite data as one config with overrides
    base = {
        "name": name,
        "description": data.get("description", ""),
        "duration_seconds": data.get("duration_seconds", 60),
        "concurrent_users": data.get("concurrent_users", 1),
        "rate_per_second": data.get("rate_per_second", 1),
        "mock_mode": mock_mode,
    }
    cfg = StressTestConfig.from_dict(base)
    # Flatten: if YAML has nested "overrides:" key, unpack its contents
    inner_overrides = data.get("overrides", {})
    extra = {k: v for k, v in data.items() if k not in base}
    # Merge: inner overrides take priority, rest fill in
    cfg.overrides = {**extra, **inner_overrides}
    configs.append(cfg)
    return configs
