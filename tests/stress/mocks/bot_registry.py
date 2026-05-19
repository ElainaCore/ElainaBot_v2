"""MockBotRegistry — simulated BotRegistry + BotInstance for stress testing.

Creates light-weight BotInstance objects with MockMessageSender and MockLogService.
Supports configurable bot counts and settings.
"""

from dataclasses import dataclass, field

from tests.stress.config import MockConfig
from tests.stress.mocks.log_service import MockLogService
from tests.stress.mocks.message_sender import MockMessageSender


@dataclass
class MockBot:
    """Light-weight BotInstance stand-in for stress testing."""
    appid: str
    name: str = ""
    secret: str = "test_secret"
    owner_ids: list = field(default_factory=list)
    robot_qq: str = ""

    def __post_init__(self):
        self.sender = None
        self.log_service = None
        self.token_manager = None
        self.bot_id = ""
        self.avatar_url = ""


class MockBotRegistry:
    """Simulated BotRegistry for stress testing.

    Creates n virtual bots with MockMessageSender and MockLogService.
    Provides `get(appid)` and `_bots` dict matching production interface.
    """

    def __init__(self, bot_count=1, config=None, message_sender_cls=None):
        self._config = config or MockConfig()
        self._sender_cls = message_sender_cls or MockMessageSender
        self._bots = {}
        self._appids = []

        for i in range(bot_count):
            appid = f"102000{str(i + 1).zfill(3)}"
            self._add_bot(appid)

    def _add_bot(self, appid):
        bot = MockBot(appid=appid, name=f"Bot_{appid}", robot_qq=appid)
        bot.sender = self._sender_cls(appid, self._config)
        bot.log_service = MockLogService(appid, self._config)
        bot.sender.bind_instance(log_service=bot.log_service, bot_name=bot.name, bot_qq=bot.robot_qq)
        self._bots[appid] = bot
        self._appids.append(appid)
        return bot

    def get(self, appid):
        return self._bots.get(str(appid))

    @property
    def bots(self):
        return dict(self._bots)

    @property
    def appids(self):
        return list(self._appids)

    def get_sender(self, appid=None):
        """Get MockMessageSender for a specific bot (default: first)."""
        aid = appid or self._appids[0]
        bot = self._bots.get(str(aid))
        return bot.sender if bot else None

    def get_log_service(self, appid=None):
        """Get MockLogService for a specific bot (default: first)."""
        aid = appid or self._appids[0]
        bot = self._bots.get(str(aid))
        return bot.log_service if bot else None

    def stats(self):
        return {
            "bot_count": len(self._bots),
            "senders": {aid: bot.sender.stats() for aid, bot in self._bots.items()},
            "logs": {aid: bot.log_service.stats() for aid, bot in self._bots.items()},
        }
