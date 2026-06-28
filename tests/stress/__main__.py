"""ElainaBot Stress Test Framework — CLI Entry Point

Usage:
    python -m tests.stress run [--config CONFIG] [--suite SUITE]
    python -m tests.stress benchmark --suite SUITE --repeat N
    python -m tests.stress report --input RESULT_DIR --format FORMAT

Run all suites:
    python -m tests.stress run --config configs/scenarios.yaml

Run specific suite:
    python -m tests.stress run --suite webhook_flood --level heavy

Quick smoke test:
    python -m tests.stress run --config configs/ci_scenarios.yaml
"""

import argparse
import asyncio
import os
import sys

# Ensure project root is in path
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def main():
    parser = argparse.ArgumentParser(
        description="ElainaBot Stress Test Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Commands")

    # run
    run_p = sub.add_parser("run", help="Run stress test suites")
    run_p.add_argument("--config", default="tests/stress/configs/scenarios.yaml",
                       help="Path to scenario YAML config")
    run_p.add_argument("--suite", help="Run specific suite only")
    run_p.add_argument("--level", help="Suite level to run")
    run_p.add_argument("--smoke", action="store_true", help="Use CI-friendly config")

    # benchmark
    bench_p = sub.add_parser("benchmark", help="Run benchmark (repeat N times)")
    bench_p.add_argument("--suite", required=True, help="Suite to benchmark")
    bench_p.add_argument("--repeat", type=int, default=5, help="Number of repetitions")

    # report
    report_p = sub.add_parser("report", help="Generate report from results")
    report_p.add_argument("--input", required=True, help="Path to raw_metrics.json")
    report_p.add_argument("--format", default="html", choices=["console", "json", "html"])

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(_run(args))
    elif args.command == "benchmark":
        asyncio.run(_benchmark(args))
    elif args.command == "report":
        _report(args)
    else:
        # Default: run with default config
        asyncio.run(_run(argparse.Namespace(
            config="tests/stress/configs/scenarios.yaml",
            suite=None,
            level=None,
            smoke=False,
        )))


async def _run(args):
    # 初始化全局 ConfigManager（dispatch() 依赖 cfg.get_bot_setting()）
    from core.base.config import cfg
    from tests.stress.config import load_scenario_config
    from tests.stress.runner import create_runner
    if not cfg._ready:
        cfg.init(os.path.join(_project_root, "config"))

    config_path = args.config
    if args.smoke:
        config_path = "tests/stress/configs/ci_scenarios.yaml"

    # Resolve config path relative to project root
    if not os.path.isabs(config_path):
        config_path = os.path.join(_project_root, config_path)

    print(f"Loading config: {config_path}")
    global_cfg, suites = load_scenario_config(config_path)

    runner = create_runner(global_cfg.output_dir)

    # Import and register suites
    suite_classes = _get_suite_classes()

    for sname, configs in suites.items():
        cls = suite_classes.get(sname)
        if not cls:
            print(f"  WARNING: Suite '{sname}' not found, skipping")
            continue

        if args.suite and sname != args.suite:
            continue

        for suite_cfg in configs:
            # 传播全局阈到每个 suite config（用于 verdict 判定）
            suite_cfg.fail_on_error_rate = global_cfg.fail_on_error_rate
            suite_cfg.fail_on_memory_leak_mb = global_cfg.fail_on_memory_leak_mb
            suite = cls(runner.collector)
            await runner.run_suite(suite, suite_cfg)

    runner.generate_report()


async def _benchmark(args):
    from tests.stress.runner import create_runner

    runner = create_runner()
    suite_classes = _get_suite_classes()

    cls = suite_classes.get(args.suite)
    if not cls:
        print(f"Suite '{args.suite}' not found. Available: {list(suite_classes)}")
        return

    for i in range(args.repeat):
        print(f"\n--- Benchmark {args.suite} run {i + 1}/{args.repeat} ---")
        suite = cls(runner.collector)
        from tests.stress.config import StressTestConfig
        cfg = StressTestConfig(name=f"{args.suite}_bench_{i}", duration_seconds=10,
                               concurrent_users=1, rate_per_second=0)
        await runner.run_suite(suite, cfg)

    runner.generate_report()


def _report(args):
    """Generate report from existing raw metrics JSON."""
    from tests.stress.metrics import MetricsCollector
    from tests.stress.reporter import StressReporter

    collector = MetricsCollector()
    StressReporter(collector, os.path.dirname(args.input))

    if args.format == "json":
        print(f"JSON report would be generated from {args.input}")
    elif args.format == "html":
        print(f"HTML report would be generated from {args.input}")
    else:
        print(f"Console report would be generated from {args.input}")


def _get_suite_classes():
    """Lazy-import all suite classes."""
    suites = {}
    try:
        from tests.stress.suites.suite_01_webhook_flood import WebhookFloodTest
        suites["webhook_flood"] = WebhookFloodTest
    except ImportError as e:
        print(f"  Import warning: suite_01: {e}")

    try:
        from tests.stress.suites.suite_02_websocket_flood import WebSocketFloodTest
        suites["websocket_flood"] = WebSocketFloodTest
    except ImportError as e:
        print(f"  Import warning: suite_02: {e}")

    try:
        from tests.stress.suites.suite_03_plugin_dispatch import PluginDispatchTest
        suites["plugin_dispatch"] = PluginDispatchTest
    except ImportError as e:
        print(f"  Import warning: suite_03: {e}")

    try:
        from tests.stress.suites.suite_04_interceptor_pipeline import InterceptorPipelineTest
        suites["interceptor_pipeline"] = InterceptorPipelineTest
    except ImportError as e:
        print(f"  Import warning: suite_04: {e}")

    try:
        from tests.stress.suites.suite_05_message_reply import MessageReplyTest
        suites["message_reply"] = MessageReplyTest
    except ImportError as e:
        print(f"  Import warning: suite_05: {e}")

    try:
        from tests.stress.suites.suite_06_mixed_endurance import MixedEnduranceTest
        suites["mixed_endurance"] = MixedEnduranceTest
    except ImportError as e:
        print(f"  Import warning: suite_06: {e}")

    try:
        from tests.stress.suites.suite_07_dedup_pressure import DedupPressureTest
        suites["dedup_pressure"] = DedupPressureTest
    except ImportError:
        pass

    try:
        from tests.stress.suites.suite_08_log_queue_pressure import LogQueuePressureTest
        suites["log_queue_pressure"] = LogQueuePressureTest
    except ImportError:
        pass

    try:
        from tests.stress.suites.suite_09_message_receive import MessageReceiveTest
        suites["message_receive"] = MessageReceiveTest
    except ImportError:
        pass

    try:
        from tests.stress.suites.suite_10_production_scale import ProductionScaleTest
        suites["production_scale"] = ProductionScaleTest
    except ImportError as e:
        print(f"  Import warning: suite_10: {e}")

    return suites


if __name__ == "__main__":
    main()
