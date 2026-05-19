"""MetricsCollector — lightweight Prometheus-style metrics registry.

Thread-safe. Supports Counter, Gauge, Histogram.
No external dependencies.
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum


class MetricType(Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


@dataclass
class MetricValue:
    """Single metric with optional labels."""
    name: str
    type: MetricType
    labels: dict = field(default_factory=dict)
    _value: float = 0.0
    _buckets: list = field(default_factory=list)
    _observations: list = field(default_factory=list)
    _sum: float = 0.0
    _count: int = 0

    def inc(self, delta=1.0):
        if self.type == MetricType.COUNTER:
            self._value += delta
        elif self.type == MetricType.GAUGE:
            self._value += delta

    def dec(self, delta=1.0):
        self._value -= delta

    def set(self, value):
        if self.type == MetricType.GAUGE:
            self._value = value

    def observe(self, value):
        if self.type == MetricType.HISTOGRAM:
            self._observations.append(value)
            self._sum += value
            self._count += 1

    def snapshot(self):
        base = {"name": self.name, "type": self.type.value, "labels": dict(self.labels)}
        if self.type == MetricType.HISTOGRAM:
            obs = sorted(self._observations)
            base["count"] = self._count
            base["sum"] = round(self._sum, 6)
            base["min"] = round(obs[0], 6) if obs else 0.0
            base["max"] = round(obs[-1], 6) if obs else 0.0
            base["avg"] = round(self._sum / self._count, 6) if self._count else 0.0
            if obs:
                base["p50"] = round(_percentile(obs, 50.0), 6)
                base["p90"] = round(_percentile(obs, 90.0), 6)
                base["p95"] = round(_percentile(obs, 95.0), 6)
                base["p99"] = round(_percentile(obs, 99.0), 6)
            else:
                base["p50"] = base["p90"] = base["p95"] = base["p99"] = 0.0
            if self._buckets:
                base["buckets"] = _histogram_buckets(obs, self._buckets)
        else:
            base["value"] = self._value
        return base


def _percentile(sorted_data, pct):
    """Compute percentile from sorted list without numpy."""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return d0 + d1


def _histogram_buckets(sorted_data, buckets):
    """Count observations in each bucket."""
    result = {}
    idx = 0
    for b in sorted(buckets):
        while idx < len(sorted_data) and sorted_data[idx] <= b:
            idx += 1
        result[f"le_{b}"] = idx
    result["le_+Inf"] = len(sorted_data)
    return result


class MetricsCollector:
    """Thread-safe metrics registry."""

    def __init__(self):
        self._metrics = {}
        self._lock = threading.Lock()
        self._created_at = time.time()

    def _make_key(self, name, labels=None):
        if labels:
            label_parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
            return f"{name}{{{label_parts}}}"
        return name

    def counter(self, name, labels=None):
        return self._register(name, MetricType.COUNTER, labels)

    def gauge(self, name, labels=None):
        return self._register(name, MetricType.GAUGE, labels)

    def histogram(self, name, buckets=None, labels=None):
        mv = self._register(name, MetricType.HISTOGRAM, labels)
        if buckets:
            mv._buckets = list(buckets)
        return mv

    def _register(self, name, mtype, labels=None):
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._metrics:
                self._metrics[key] = MetricValue(name=name, type=mtype, labels=dict(labels or {}))
            return self._metrics[key]

    def record_latency(self, name, seconds, labels=None):
        """Convenience: record a duration in a histogram."""
        h = self.histogram(name, labels=labels)
        h.observe(seconds)

    def get(self, name, labels=None):
        key = self._make_key(name, labels)
        with self._lock:
            return self._metrics.get(key)

    def get_all(self, name):
        """Return all MetricValue instances whose name matches (across all label variants)."""
        with self._lock:
            return [v for k, v in self._metrics.items() if v.name == name]

    def snapshot(self):
        with self._lock:
            return {k: v.snapshot() for k, v in self._metrics.items()}

    def reset(self):
        with self._lock:
            self._metrics.clear()

    @property
    def metric_names(self):
        with self._lock:
            return sorted(self._metrics.keys())

    @property
    def uptime(self):
        return time.time() - self._created_at
