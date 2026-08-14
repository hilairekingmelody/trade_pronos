"""A small, reusable Telegram bot starter built with pyTelegramBotAPI."""

from __future__ import annotations

import html
import logging
import os
import sys

import telebot
from dotenv import load_dotenv


LOGGER = logging.getLogger("telegram_bot")


def configure_logging() -> None:
    """Configure readable logs for local development and Replit."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def load_bot() -> telebot.TeleBot:
    """Load the bot token and create the TeleBot instance."""
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Add it in Replit Secrets "
            "or create a local .env file from .env.example."
        )

    return telebot.TeleBot(token, parse_mode="HTML")


def register_handlers(bot: telebot.TeleBot) -> None:
    """Register the starter command and message handlers."""

    @bot.message_handler(commands=["start"])
    def handle_start(message: telebot.types.Message) -> None:
        first_name = html.escape(message.from_user.first_name or "there")
        bot.reply_to(
            message,
            (
                f"Hi, <b>{first_name}</b>! Welcome to your new Telegram bot.\n\n"
                "Send me a message and I’ll echo it back.\n"
                "Use /help to see what I can do."
            ),
        )

    @bot.message_handler(commands=["help"])
    def handle_help(message: telebot.types.Message) -> None:
        bot.reply_to(
            message,
            (
                "<b>Available commands</b>\n"
                "/start — welcome message\n"
                "/help — show this help message\n"
                "/ping — check that the bot is online"
            ),
        )

    @bot.message_handler(commands=["ping"])
    def handle_ping(message: telebot.types.Message) -> None:
        bot.reply_to(message, "Pong! The bot is online.")

    @bot.message_handler(content_types=["text"])
    def handle_text(message: telebot.types.Message) -> None:
        bot.reply_to(message, f"You said: {html.escape(message.text or '')}")


def main() -> int:
    configure_logging()

    try:
        bot = load_bot()
    except RuntimeError as error:
        LOGGER.error(error)
        return 1

    register_handlers(bot)
    LOGGER.info("Telegram bot started in polling mode.")

    try:
        bot.infinity_polling(skip_pending=True)
    except KeyboardInterrupt:
        LOGGER.info("Telegram bot stopped.")
    except Exception:
        LOGGER.exception("Telegram bot stopped unexpectedly.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())