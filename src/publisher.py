from telethon import TelegramClient
from telethon.tl.types import Message
from .logger import setup_logger

logger = setup_logger('publisher')

class TelegramPublisher:
    def __init__(self, client: TelegramClient, target_channel: str):
        self.client = client
        self.target_channel = target_channel

    async def publish_post(self, message: Message) -> bool:
        try:
            if message.media:
                await self.client.send_file(
                    self.target_channel,
                    message.media,
                    caption=message.text or ''
                )
            elif message.text:
                await self.client.send_message(
                    self.target_channel,
                    message.text
                )
            else:
                logger.warning(f"Пост {message.id} не содержит текста или медиа")
                return False

            logger.info(f"Пост {message.id} успешно опубликован")
            return True
        except Exception as e:
            logger.error(f"Ошибка при публикации поста {message.id}: {e}")
            return False

    async def format_and_publish(self, message: Message, custom_text: str = None) -> bool:
        try:
            text = custom_text or message.text or ''

            if message.media:
                await self.client.send_file(
                    self.target_channel,
                    message.media,
                    caption=text
                )
            else:
                await self.client.send_message(
                    self.target_channel,
                    text
                )

            logger.info(f"Пост {message.id} опубликован с кастомным текстом")
            return True
        except Exception as e:
            logger.error(f"Ошибка при публикации поста: {e}")
            return False

    async def publish_alert_template(
        self,
        template_text: str,
        source_message_id: int = None
    ) -> bool:
        """
        Публикует статичный текст-шаблон алерта (без медиа).

        Args:
            template_text: Текст шаблона для публикации
            source_message_id: ID исходного сообщения (для логирования)

        Returns:
            True если публикация успешна
        """
        try:
            if not template_text or template_text.strip() == '':
                logger.error("Попытка опубликовать пустой шаблон")
                return False

            # Публикуем только текст, медиа игнорируем
            await self.client.send_message(
                self.target_channel,
                template_text
            )

            log_msg = "Шаблон алерта успешно опубликован"
            if source_message_id:
                log_msg += f" (исходный пост: {source_message_id})"
            logger.info(log_msg)

            return True

        except Exception as e:
            logger.error(f"Ошибка при публикации шаблона алерта: {e}")
            return False
