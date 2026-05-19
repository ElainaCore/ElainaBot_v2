"""StressTestRunner — main orchestrator for stress test execution.

Manages test suites, coordinates execution, collects results.
"""

import asyncio
import os
import time
from datetime import datetime

from tests.stress.config import StressTestConfig
from tests.stress.metrics import MetricsCollector
from tests.stress.reporter import StressReporter


class VirtualUser:
    """Simulates one concurrent user sending events at a configurable rate."""

    def __init__(self, user_id, rate, event_generator, metrics, suite_name):
        self.user_id = user_id
        self.rate = max(rate, 0)
        self._interval = 1.0 / rate if rate > 0 else 0
        self._generator = event_generator
        self._metrics = metrics
        self._suite_name = suite_name
        self._event_count = 0

    @property
    def event_count(self):
        return self._event_count

    async def run(self, on_event_coro, stop_event):
        """Main loop: generate and send events at configured rate."""
        interval = self._interval
        suite_label = {"suite": self._suite_name}

        while not stop_event.is_set():
            event = self._generator()
            if event is None:
                break

            t0 = time.perf_counter()
            try:
                await on_event_coro(event)
                self._metrics.counter("events_success", suite_label).inc()
            except Exception:
                self._metrics.counter("events_failed", suite_label).inc()
            finally:
                dt = time.perf_counter() - t0
                self._metrics.counter("events_total", suite_label).inc()
                self._metrics.record_latency("dispatch_latency_seconds", dt, suite_label)
                self._event_count += 1

            if interval > 0:
                await asyncio.sleep(interval)

    async def burst(self, on_event_coro, count):
        """Send N events as fast as possible."""
        suite_label = {"suite": self._suite_name}
        for _ in range(count):
            event = self._generator()
            if event is None:
                break
            t0 = time.perf_counter()
            try:
                await on_event_coro(event)
                self._metrics.counter("events_success", suite_label).inc()
            except Exception:
                self._metrics.counter("events_failed", suite_label).inc()
            finally:
                dt = time.perf_counter() - t0
                self._metrics.counter("events_total", suite_label).inc()
                self._metrics.record_latency("dispatch_latency_seconds", dt, suite_label)
                self._event_count += 1


class RampUpController:
    """Gradually creates VirtualUsers during ramp-up phase."""

    def __init__(self, target_users, duration, rate_per_user, generator_fn, metrics, suite_name):
        self._target = target_users
        self._duration = duration
        self._rate_per_user = rate_per_user
        self._generator_fn = generator_fn
        self._metrics = metrics
        self._suite_name = suite_name
        self._active_users = []
        self._tasks = []

    async def ramp(self, on_event_coro, stop_event):
        """Create users incrementally over the ramp duration."""
        if self._target <= 0:
            return

        interval = self._duration / self._target if self._duration > 0 else 0
        for i in range(self._target):
            user = VirtualUser(
                f"vu_{i:04d}",
                self._rate_per_user,
                self._generator_fn(),
                self._metrics,
                self._suite_name,
            )
            t = asyncio.create_task(user.run(on_event_coro, stop_event))
            self._active_users.append(user)
            self._tasks.append(t)
            if interval > 0:
                await asyncio.sleep(interval)

    @property
    def total_events(self):
        return sum(u.event_count for u in self._active_users)


class StressTestRunner:
    """Main test orchestrator — loads config, runs suites, generates reports."""

    def __init__(self, output_dir="tests/stress/results"):
        self._output_dir = output_dir
        self._collector = MetricsCollector()
        self._reporter = StressReporter(self._collector, output_dir)
        self._suites = {}
        self._results = []

    @property
    def collector(self):
        return self._collector

    @property
    def reporter(self):
        return self._reporter

    def register_suite(self, suite):
        """Register a BaseStressTest suite."""
        self._suites[suite.suite_name] = suite

    async def run_all(self, config_path=None, suite_names=None):
        """Run all registered suites (or filtered by suite_names)."""
        suites_to_run = self._suites
        if suite_names:
            suites_to_run = {k: v for k, v in self._suites.items() if k in suite_names}

        for name, suite in suites_to_run.items():
            config = StressTestConfig(name=name, duration_seconds=30, concurrent_users=10, rate_per_second=10)
            result = await self.run_suite(suite, config)
            self._results.append(result)

        self.generate_report()
        return self._results

    async def run_suite(self, suite, config):
        """Run a single suite with given config."""
        print(f"\n{'=' * 60}")
        print(f"  Suite: {config.name}")
        print(f"  Description: {config.description}")
        print(f"  Users: {config.concurrent_users} | Rate: {config.rate_per_second}/s")
        print(f"  Duration: {config.duration_seconds}s | Mode: {config.mock_mode.value}")
        print(f"{'=' * 60}")

        result = await suite.execute(config)
        self._results.append(result)

        # Print result
        self._print_suite_result(result)
        return result

    def _print_suite_result(self, result):
        verdict_icon = {"PASS": "[OK]", "WARN": "[WARN]", "FAIL": "[FAIL]"}.get(result.verdict, "[?]")
        print(f"\n  {verdict_icon} {result.suite_name} ({result.duration:.1f}s)")
        print(f"      Events: {result.total_events} | Success: {result.successful} | Failed: {result.failed}")
        print(f"      Throughput: {result.throughput_avg:.0f}/s | p50={result.latency_p50 * 1000:.1f}ms p99={result.latency_p99 * 1000:.1f}ms")
        print(f"      Memory: {result.memory_start_mb:.0f}MB → {result.memory_end_mb:.0f}MB (Δ{result.memory_delta_mb:+.0f}MB)")
        print(f"      Tasks: peak={result.task_count_peak} end={result.task_count_end}")

    def generate_report(self, format="all"):
        """Generate reports for all completed runs."""
        if not self._results:
            print("No results to report.")
            return self._reporter.generate_empty()

        report_dir = self._reporter.generate(self._results, format)
        self._print_summary()
        return report_dir

    def _print_summary(self):
        """Print summary table of all suite results."""
        if not self._results:
            return
        print(f"\n{'=' * 80}")
        print(f"{'SUMMARY REPORT':^80}")
        print(f"{'=' * 80}")
        header = f" {'Suite':<25s} | {'Events':>10s} | {'Thru/s':>8s} | {'p99':>8s} | {'Errors':>7s} | {'Verdict':<6s}"
        print(header)
        print("-" * len(header))
        for r in self._results:
            print(f" {r.suite_name:<25s} | {r.total_events:>10d} | {r.throughput_avg:>8.0f} | "
                  f"{r.latency_p99 * 1000:>7.1f}ms | {r.error_rate:>6.1%} | {r.verdict:<6s}")
        print("-" * len(header))

        passed = sum(1 for r in self._results if r.verdict == "PASS")
        warned = sum(1 for r in self._results if r.verdict == "WARN")
        failed = sum(1 for r in self._results if r.verdict == "FAIL")
        print(f" TOTAL: {len(self._results)} suites | {passed}P/{warned}W/{failed}F")
        print(f"{'=' * 80}\n")

    @property
    def results(self):
        return list(self._results)


# ---- Convenience runner ----

def create_runner(output_dir=None):
    """Create a StressTestRunner with default output directory."""
    if output_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join("tests", "stress", "results", ts)
    return StressTestRunner(output_dir)
