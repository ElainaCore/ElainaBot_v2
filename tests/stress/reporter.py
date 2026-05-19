"""StressReporter — generates console, JSON, and HTML reports.

Reads StressTestResult lists and produces formatted output.
"""

import json
import os
import time
from datetime import datetime


class StressReporter:
    """Generates formatted reports from stress test results."""

    def __init__(self, metrics, output_dir):
        self._metrics = metrics
        self._output_dir = output_dir

    def generate(self, results, format="all"):
        """Generate reports for all results."""
        os.makedirs(self._output_dir, exist_ok=True)

        fmt = format.lower()
        paths = {}
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        if fmt in ("all", "json"):
            p = self._generate_json(results, ts)
            paths["json"] = p

        if fmt in ("all", "html"):
            p = self._generate_html(results, ts)
            paths["html"] = p

        return self._output_dir

    def _generate_json(self, results, ts):
        """Generate JSON report."""
        import platform

        output = {
            "framework_version": "1.0",
            "timestamp": datetime.now().isoformat(),
            "environment": {
                "python_version": platform.python_version(),
                "platform": platform.system(),
                "cpu_count": os.cpu_count() or 0,
            },
            "summary": self._build_summary(results),
            "suites": [self._result_to_dict(r) for r in results],
        }
        path = os.path.join(self._output_dir, f"summary_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        return path

    def _generate_html(self, results, ts):
        """Generate simple HTML report."""
        summary = self._build_summary(results)

        rows = ""
        for r in results:
            verdict_color = {"PASS": "green", "WARN": "orange", "FAIL": "red"}.get(r.verdict, "gray")
            rows += f"""<tr>
                <td>{r.suite_name}</td>
                <td style="text-align:right">{r.total_events:,}</td>
                <td style="text-align:right">{r.throughput_avg:.0f}/s</td>
                <td style="text-align:right">{r.latency_p50 * 1000:.1f}ms</td>
                <td style="text-align:right">{r.latency_p99 * 1000:.1f}ms</td>
                <td style="text-align:right">{r.error_rate:.2%}</td>
                <td style="text-align:right">{r.memory_delta_mb:+.0f}MB</td>
                <td style="color:{verdict_color};font-weight:bold">{r.verdict}</td>
            </tr>\n"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>ElainaBot Stress Test Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               max-width: 1100px; margin: 40px auto; background: #f5f5f5; color: #333; }}
        .card {{ background: white; border-radius: 8px; padding: 24px; margin: 16px 0;
                 box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        h1 {{ margin-top: 0; font-size: 24px; }}
        .summary {{ display: flex; gap: 16px; margin: 16px 0; }}
        .stat {{ flex: 1; text-align: center; padding: 12px; background: #f0f4ff; border-radius: 6px; }}
        .stat .value {{ font-size: 28px; font-weight: bold; color: #1a56db; }}
        .stat .label {{ font-size: 12px; color: #666; margin-top: 4px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
        th, td {{ padding: 10px 14px; border-bottom: 1px solid #eee; text-align: left; }}
        th {{ background: #f8f9fa; font-weight: 600; font-size: 13px; color: #555; }}
        tr:hover {{ background: #f8f9ff; }}
        .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 32px; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>ElainaBot Stress Test Report</h1>
        <p style="color:#666">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <div class="summary">
            <div class="stat"><div class="value">{summary['total_suites']}</div><div class="label">Suites</div></div>
            <div class="stat"><div class="value">{summary['passed']}</div><div class="label">Passed</div></div>
            <div class="stat"><div class="value">{summary['warned']}</div><div class="label">Warned</div></div>
            <div class="stat"><div class="value">{summary['failed']}</div><div class="label">Failed</div></div>
            <div class="stat"><div class="value">{summary['total_events']:,}</div><div class="label">Total Events</div></div>
            <div class="stat"><div class="value">{summary['overall_error_rate']:.2%}</div><div class="label">Error Rate</div></div>
        </div>
    </div>
    <div class="card">
        <h2>Suite Results</h2>
        <table>
            <thead>
                <tr>
                    <th>Suite</th><th>Events</th><th>Throughput</th>
                    <th>p50 Lat</th><th>p99 Lat</th>
                    <th>Errors</th><th>Mem Δ</th><th>Verdict</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </div>
    <div class="footer">ElainaBot Stress Test Framework v1.0</div>
</body>
</html>"""

        path = os.path.join(self._output_dir, f"report_{ts}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path

    def generate_empty(self):
        """Generate an empty report placeholder."""
        return self._output_dir

    def _build_summary(self, results):
        total = len(results)
        passed = sum(1 for r in results if r.verdict == "PASS")
        warned = sum(1 for r in results if r.verdict == "WARN")
        failed = sum(1 for r in results if r.verdict == "FAIL")
        total_events = sum(r.total_events for r in results)
        total_failed = sum(r.failed for r in results)
        return {
            "total_suites": total,
            "passed": passed,
            "warned": warned,
            "failed": failed,
            "total_events": total_events,
            "total_duration_seconds": round(sum(r.duration for r in results), 1),
            "overall_error_rate": round(total_failed / max(total_events, 1), 4),
        }

    def _result_to_dict(self, r):
        return {
            "name": r.suite_name,
            "verdict": r.verdict,
            "duration": round(r.duration, 3),
            "total_events": r.total_events,
            "successful": r.successful,
            "failed": r.failed,
            "error_rate": round(r.error_rate, 4),
            "throughput_avg": round(r.throughput_avg, 1),
            "latency_p50_ms": round(r.latency_p50 * 1000, 2),
            "latency_p90_ms": round(r.latency_p90 * 1000, 2),
            "latency_p95_ms": round(r.latency_p95 * 1000, 2),
            "latency_p99_ms": round(r.latency_p99 * 1000, 2),
            "latency_max_ms": round(r.latency_max * 1000, 2),
            "memory_start_mb": r.memory_start_mb,
            "memory_end_mb": r.memory_end_mb,
            "memory_delta_mb": r.memory_delta_mb,
            "task_count_peak": r.task_count_peak,
            "task_count_end": r.task_count_end,
            "error_samples": r.error_samples[:10],
        }
