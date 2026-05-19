"""Base stress test suite — abstract class with template method.

Each suite follows: setup → ramp_up → run_phase → cooldown → teardown → collect.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from tests.stress.config import StressTestConfig


@dataclass
class StressTestResult:
    """Aggregated results from a single stress test run."""
    suite_name: str = ""
    config: StressTestConfig = None
    start_time: float = 0.0
    end_time: float = 0.0
    duration: float = 0.0
    total_events: int = 0
    successful: int = 0
    failed: int = 0
    throughput_avg: float = 0.0
    throughput_peak: float = 0.0
    latency_p50: float = 0.0
    latency_p90: float = 0.0
    latency_p95: float = 0.0
    latency_p99: float = 0.0
    latency_max: float = 0.0
    memory_start_mb: float = 0.0
    memory_end_mb: float = 0.0
    memory_delta_mb: float = 0.0
    task_count_peak: int = 0
    task_count_end: int = 0
    error_samples: list = field(default_factory=list)
    metrics_snapshots: list = field(default_factory=list)
    custom_metrics: dict = field(default_factory=dict)
    verdict: str = "PENDING"  # PASS, WARN, FAIL

    @property
    def error_rate(self):
        if self.total_events == 0:
            return 0.0
        return self.failed / self.total_events

    @property
    def task_retained_pct(self):
        if self.task_count_peak == 0:
            return 0.0
        return self.task_count_end / self.task_count_peak * 100


class BaseStressTest(ABC):
    """Abstract base for all stress test suites.

    Template method: execute() calls setup → ramp_up → run_phase → cooldown → teardown.
    """

    def __init__(self, metrics):
        self._metrics = metrics
        self._stop_event = asyncio.Event()
        self._config = None
        self._result = None
        self._start_ts = 0.0
        self._monitors = []

    @property
    @abstractmethod
    def suite_name(self) -> str:
        """Unique suite identifier."""
        ...

    @abstractmethod
    async def setup(self, config: StressTestConfig) -> None:
        """Create mock components, register handlers, prepare state."""
        ...

    @abstractmethod
    async def run_phase(self, config: StressTestConfig) -> None:
        """Main event generation loop. Sets self._stop_event when done."""
        ...

    @abstractmethod
    async def teardown(self) -> None:
        """Clean up resources, flush metrics, close connections."""
        ...

    async def execute(self, config: StressTestConfig) -> StressTestResult:
        """Template method orchestrating the full test lifecycle."""
        self._config = config
        self._result = StressTestResult(
            suite_name=config.name,
            config=config,
        )
        self._stop_event.clear()

        # Setup
        await self.setup(config)

        # Start monitors
        self._start_monitors()

        # Record start
        self._result.start_time = time.time()
        self._start_ts = self._result.start_time
        mem0 = _memory_sample()
        self._result.memory_start_mb = mem0.get("rss_mb", 0)

        # Ramp-up
        if config.ramp_up_seconds > 0:
            # Ramp-up is handled by suite if needed; base just waits
            pass

        # Run phase
        try:
            if config.duration_seconds > 0:
                # Time-limited
                await asyncio.wait_for(
                    self.run_phase(config),
                    timeout=config.duration_seconds,
                )
            else:
                await self.run_phase(config)
        except asyncio.TimeoutError:
            pass  # Duration expired — expected

        # Cooldown: let pending tasks drain
        if config.cooldown_seconds > 0:
            await asyncio.sleep(config.cooldown_seconds)

        # Record end
        self._result.end_time = time.time()
        self._result.duration = self._result.end_time - self._result.start_time

        mem1 = _memory_sample()
        self._result.memory_end_mb = mem1.get("rss_mb", 0)
        self._result.memory_delta_mb = self._result.memory_end_mb - self._result.memory_start_mb
        self._result.task_count_end = mem1.get("task_count", 0)

        # Stop monitors
        self._stop_monitors()

        # Teardown
        await self.teardown()

        # Collect snapshot
        self._result.metrics_snapshots = self._collect_snapshots()

        # Compute derived metrics
        self._compute_result()

        return self._result

    def _start_monitors(self):
        """Start background monitors (loop lag, memory sampler)."""
        loop = asyncio.get_running_loop()

        # Event loop lag monitor
        async def lag_monitor():
            interval = 0.1
            lag_gauge = self._metrics.gauge("event_loop_lag_seconds",
                                            {"suite": self.suite_name})
            while not self._stop_event.is_set():
                t0 = time.perf_counter()
                await asyncio.sleep(interval)
                lag = max(0, (time.perf_counter() - t0) - interval)
                lag_gauge.set(round(lag, 6))

        # Memory sampler
        async def mem_sampler():
            interval = self._config.sample_interval_seconds if self._config else 1.0
            mem_gauge = self._metrics.gauge("memory_mb",
                                            {"suite": self.suite_name})
            task_gauge = self._metrics.gauge("task_count",
                                             {"suite": self.suite_name})
            while not self._stop_event.is_set():
                s = _memory_sample()
                mem_gauge.set(round(s.get("rss_mb", 0), 1))
                task_gauge.set(s.get("task_count", 0))

                # Track peaks
                cur_tasks = s.get("task_count", 0)
                if cur_tasks > self._result.task_count_peak:
                    self._result.task_count_peak = cur_tasks

                await asyncio.sleep(interval)

        self._monitors = [
            asyncio.create_task(lag_monitor()),
            asyncio.create_task(mem_sampler()),
        ]

    def _stop_monitors(self):
        for t in self._monitors:
            t.cancel()
        self._monitors.clear()

    def _compute_result(self):
        """Derive throughput and latency from metrics."""
        dur = max(self._result.duration, 0.001)

        # Total events — populate first (used by throughput + verdict)
        evt_total = self._metrics.get("events_total", {"suite": self.suite_name})
        evt_succ = self._metrics.get("events_success", {"suite": self.suite_name})
        evt_fail = self._metrics.get("events_failed", {"suite": self.suite_name})
        if evt_total:
            self._result.total_events = int(evt_total._value)
        if evt_succ:
            self._result.successful = int(evt_succ._value)
        if evt_fail:
            self._result.failed = int(evt_fail._value)

        # Throughput — use already-populated successful count
        self._result.throughput_avg = self._result.successful / dur

        # Latency — aggregate ALL label variants (suites use different extra labels)
        all_histograms = self._metrics.get_all("dispatch_latency_seconds")
        if all_histograms:
            merged = _merge_histograms(all_histograms)
            if merged["count"] > 0:
                self._result.latency_p50 = merged["p50"]
                self._result.latency_p90 = merged["p90"]
                self._result.latency_p95 = merged["p95"]
                self._result.latency_p99 = merged["p99"]
                self._result.latency_max = merged["max"]

        # Verdict computation
        self._compute_verdict()

    def _collect_snapshots(self):
        return [self._metrics.snapshot()]

    def record_event(self, success=True):
        """Record a single event result."""
        suite_label = {"suite": self.suite_name}
        self._metrics.counter("events_total", suite_label).inc()
        if success:
            self._metrics.counter("events_success", suite_label).inc()
        else:
            self._metrics.counter("events_failed", suite_label).inc()

    def record_latency(self, seconds):
        """Record dispatch latency."""
        self._metrics.record_latency(
            "dispatch_latency_seconds",
            seconds,
            {"suite": self.suite_name},
        )

    def _compute_verdict(self):
        """Compute PASS/WARN/FAIL based on thresholds from the stress config."""
        r = self._result
        cfg = self._config
        if cfg is None:
            r.verdict = "PASS"
            return

        error_rate = r.error_rate
        mem_leak = r.memory_delta_mb

        fail_er = getattr(cfg, "fail_on_error_rate", 0.10) or 0.10
        fail_mem = getattr(cfg, "fail_on_memory_leak_mb", 200) or 200
        warn_er = 0.01
        warn_mem = 50

        if error_rate >= fail_er or mem_leak >= fail_mem:
            r.verdict = "FAIL"
        elif error_rate >= warn_er or mem_leak >= warn_mem:
            r.verdict = "WARN"
        else:
            r.verdict = "PASS"


def _memory_sample():
    """Sample process memory and asyncio task count."""
    try:
        import os
        import psutil
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        return {
            "rss_mb": round(mem.rss / 1024 / 1024, 1),
            "vms_mb": round(mem.vms / 1024 / 1024, 1),
            "task_count": len(asyncio.all_tasks()),
        }
    except ImportError:
        return {"rss_mb": 0, "vms_mb": 0, "task_count": 0}


def _merge_histograms(metric_values):
    """Merge multiple MetricValue histograms (different label sets) into one snapshot dict."""
    obs = []
    for mv in metric_values:
        obs.extend(mv._observations)
    if not obs:
        return {"count": 0, "p50": 0, "p90": 0, "p95": 0, "p99": 0, "max": 0}
    obs.sort()
    from tests.stress.metrics import _percentile
    return {
        "count": len(obs),
        "min": obs[0],
        "max": obs[-1],
        "avg": sum(obs) / len(obs),
        "p50": _percentile(obs, 50.0),
        "p90": _percentile(obs, 90.0),
        "p95": _percentile(obs, 95.0),
        "p99": _percentile(obs, 99.0),
    }
