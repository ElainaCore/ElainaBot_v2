"""Mock components for stress testing.

Provides MockMessageSender, MockLogService, MockBotRegistry, and EventFactory.
All mocks record metrics and support SIMULATE / RECORD / PASSTHROUGH modes.
"""

from tests.stress.config import MockConfig, MockMode

__all__ = ["MockConfig", "MockMode"]
