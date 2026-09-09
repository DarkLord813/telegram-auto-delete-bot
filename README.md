# Admin Auto-Delete Bot

> A powerful Telegram bot that automatically deletes messages from non-approved admins in groups, supergroups, and channels.

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Telegram](https://img.shields.io/badge/telegram-bot-blue.svg)](https://t.me/NCK_Dev)

## 📋 Table of Contents

- [Features](#-features)
- [Installation](#-installation)
- [Deployment](#-deployment)
- [Usage Guide](#-usage-guide)
- [Configuration](#-configuration)
- [Database Schema](#-database-schema)
- [Environment Variables](#-environment-variables)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [License](#-license)

## ✨ Features

### 🎯 Core Functionality

- **Admin Approval System**: Approve specific admins whose messages won't be deleted
- **Scheduled Deletion**: Set custom deletion timers (Instant, 30s, 1m, 5m, 15m, 1h, or custom)
- **Blacklist Keywords**: Auto-delete messages containing banned words/phrases
- **Whitelist Keywords**: Messages matching whitelisted keywords are always protected
- **Force-Join Gate**: Require users to join a specific channel before using the bot
- **DM-Only Management**: All settings are configured through private DMs

### 🤖 Admin Management

- **Full Admin List**: View all admins including bots and the bot itself
- **Bulk Approval**: Approve admins directly from the list
- **Username Approval**: Approve admins by username (useful for bots not showing in list)
- **Refresh Admins**: Manually update the admin list
- **Bot Self-Protection**: The bot cannot approve/unapprove itself

### 📋 Content Moderation

- **Blacklist System**: Delete messages containing flagged keywords
- **Whitelist System**: Protect messages that match whitelisted keywords (overrides blacklist)
- **Admin-Only Filtering**: Blacklist keywords only apply to non-approved admins
- **Message Scheduling**: Set deletion delays per chat

### 🗑️ Chat Management

- **Remove Chats**: Remove chats from bot management
- **Restore Chats**: Re-add previously removed chats
- **Chat Status**: Visual indicators (✅ Active / ⏳ Setup Pending)

### 🔧 Technical Features

- **Persistent Storage**: SQLite database for all settings
- **Keep-Alive Server**: Built-in health check endpoint
- **Error Handling**: Graceful error recovery
- **Async Operations**: Fast and responsive

## 📦 Installation

### Prerequisites

- Python 3.8 or higher
- Telegram Bot Token (get from [@BotFather](https://t.me/BotFather))
- Git (optional)

### Local Setup

1. **Clone the repository**

```bash
git clone https://github.com/DarkLord813/telegram-auto-delete-bot.git
cd telegram-auto-delete-bot