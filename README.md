# Telegram Bot Starter

A small Python Telegram bot template using `pyTelegramBotAPI` (the `telebot`
package). It includes a welcome flow, help text, a health-check command, and
an echo handler that you can replace with your own bot logic.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and add its token as
   the Replit Secret `TELEGRAM_BOT_TOKEN`.
2. Add your API-Sports key as the Replit Secret `FOOTBALL_API_KEY` if you want
   to use `/match` and `/coupon`.
3. Run the bot with:

   ```bash
   python main.py
   ```

The bot also starts a small Flask keep-alive endpoint at `/`. It uses port
5000 by default; set `KEEP_ALIVE_PORT` if you need another available port.

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