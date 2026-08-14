# Telegram Bot Starter

A reusable Python Telegram bot template built with pyTelegramBotAPI.

## Run & Operate

- `python main.py` — start the Telegram bot
- Required secret: `TELEGRAM_BOT_TOKEN`
- Optional environment variable: `LOG_LEVEL` (defaults to `INFO`)

## Stack

- Python
- pyTelegramBotAPI (`telebot`)
- python-dotenv

## Where things live

- `main.py` — bot setup, handlers, and polling loop
- `.env.example` — local environment variable template
- `requirements.txt` — Python dependencies
- `README.md` — setup and customization guide

## Architecture decisions

- Long polling is used because it runs without a public webhook URL.
- The bot token is loaded from the environment and is never hardcoded.
- Startup exits with a clear error when the token is missing.

## Product

The starter responds to `/start`, `/help`, and `/ping`, and echoes text messages.
It is intentionally small so new handlers and business logic can be added easily.

## User preferences

Keep the bot simple, readable, and easy to customize.

## Gotchas

- Add `TELEGRAM_BOT_TOKEN` as a secret before running the bot.
- Do not commit `.env` or paste the token into source files.

## Pointers
- See `README.md` for the full setup guide.
