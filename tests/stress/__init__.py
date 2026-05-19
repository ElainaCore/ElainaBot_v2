"""ElainaBot Stress Test Framework v1.0

Test high-concurrency message performance across all modules:
- Webhook / WebSocket entry points
- Event pipeline (_on_event)
- Plugin dispatch (handler matching, interceptor pipeline)
- MessageSender.reply() throughput
- LogService queue pressure
- Memory / task leak detection
"""

__version__ = "1.0.0"
