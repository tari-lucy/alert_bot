"""
Публикация через Bot API (классический бот)
Используется для гибридного режима - максимальная безопасность
"""
from telegram import Bot
from telegram.error import TelegramError
from .logger import setup_logger

logger = setup_logger('bot_publisher')


class BotPublisher:
    """
    Публикация через официальный Bot API.
    Безопаснее чем userbot для публикации.
    """

    def __init__(self, bot_token: str, target_channel: str):
        """
        Args:
            bot_token: Токен бота от @BotFather
            target_channel: Username или ID целевого канала
        """
        self.bot = Bot(token=bot_token)
        self.target_channel = target_channel

    async def publish_alert_template(
        self,
        template_text: str,
        source_message_id: int = None
    ) -> bool:
        """
        Публикует статичный текст через Bot API.

        Args:
            template_text: Текст шаблона
            source_message_id: ID исходного сообщения (для логирования)

        Returns:
            True если публикация успешна
        """
        try:
            if not template_text or template_text.strip() == '':
                logger.error("Попытка опубликовать пустой шаблон")
                return False

            # Публикация через Bot API
            await self.bot.send_message(
                chat_id=self.target_channel,
                text=template_text
            )

            log_msg = "✅ Шаблон алерта опубликован через Bot API"
            if source_message_id:
                log_msg += f" (исходный пост: {source_message_id})"
            logger.info(log_msg)

            return True

        except TelegramError as e:
            logger.error(f"Ошибка Bot API при публикации: {e}")
            return False
        except Exception as e:
            logger.error(f"Неожиданная ошибка при публикации: {e}")
            return False
