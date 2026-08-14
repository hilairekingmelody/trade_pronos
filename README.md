# Telegram Bot Starter

A small Python Telegram bot template using `pyTelegramBotAPI` (the `telebot`
package). It includes a welcome flow, help text, a health-check command, and
an echo handler that you can replace with your own bot logic.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Add the token as a Replit Secret named `TELEGRAM_BOT_TOKEN`.
3. Run the bot with:

   ```bash
   python main.py
   ```

For local development, copy `.env.example` to `.env` and add the token there.
`.env` is ignored by Git, so the token will not be committed.

## Included commands

- `/start` — welcome message
- `/help` — list available commands
- `/ping` — confirm the bot is online
- Any text — echoes the message back

## Customize it

Add or change handlers in `register_handlers()` inside `main.py`. The bot uses
long polling, which is simple to run on Replit and does not require a public
webhook URL.