from zhenxun.services.ai.core.models import ModelIdentity

from .handlers.mimo_handlers import (
    MiMoAudioHandler,
    MiMoTextHandler,
)
from .handlers.openai_handlers import (
    OpenAIImageHandler,
)
from .openai import OpenAICompatAdapter


class MiMoAdapter(OpenAICompatAdapter):
    """小米 MiMo 大模型专有适配器"""

    def __init__(self):
        super().__init__()
        self.text_handler = MiMoTextHandler(api_type=self.api_type)
        self.image_handler = OpenAIImageHandler()
        self.audio_handler = MiMoAudioHandler()

    @property
    def api_type(self) -> str:
        return "mimo"

    @property
    def supported_api_types(self) -> list[str]:
        return ["mimo"]

    def get_chat_endpoint(self, identity: ModelIdentity) -> str:
        return "/v1/chat/completions"
