# 🤖 Telegram Auto Delete Bot

A powerful Telegram bot that automatically deletes all messages except those from specified admins in channels.

## 🚀 Features

- **Auto-deletion**: Deletes all messages from non-approved users
- **Admin Management**: Add/remove allowed admins via inline keyboards
- **Configurable Intervals**: Set deletion delay from 1-30 minutes
- **Channel Protection**: Only deletes messages sent after bot was added
- **Keep-Alive System**: 24/7 operation on free hosting
- **Health Monitoring**: Built-in health check endpoints
- **SQLite Database**: Persistent storage for settings

## 🛠️ Setup

### Prerequisites

1. Python 3.8+
2. Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### Environment Variables

Create a `.env` file:

```env
BOT_TOKEN=your_telegram_bot_token_here
