# Admin Auto-Delete Bot

Single-file Telegram bot (`auto_delete.py`). Deletes messages/posts from **admins**
who aren't on a chat's approved list — in groups, supergroups, *and*
channels. Regular members are untouched. The only slash command is
`/start`; everything else is inline buttons. **All settings live only in
a private DM with the bot** — groups/supergroups/channels show no menus,
replies, or prompts at all; the bot's only in-chat behavior there is
auto-deleting messages. Every interaction is gated behind joining your
force-join channel first — no exceptions.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather), copy the token.
2. Make the bot an **admin** of `https://t.me/NCK_Dev` (or your own force-join
   channel) — it needs this to verify users joined.
3. `cp .env.example .env` and fill in `BOT_TOKEN` (and `FORCE_JOIN_CHANNEL` /
   `FORCE_JOIN_CHANNEL_LINK` if you're using a different channel).
4. `pip install -r requirements.txt`
5. `python bot.py`

## Using it

- Add the bot to your group/channel, promote it to **admin** with
  *Delete Messages* permission.
- DM the bot `/start` in private. It shows a picker of every group/channel
  it's admin in where you're also an admin; tap one to open the settings
  menu for that chat, right there in the DM.
  - _If you added the bot as admin before this feature, promote/demote it
    once (any status change) so it registers the chat — this uses
    Telegram's `my_chat_member` update to build the list._
- `/start` typed inside a group or channel does nothing but delete itself —
  by design, there is no in-chat interaction of any kind. The bot only
  acts in the chat itself by auto-deleting flagged messages.
- **🔒 Force-join gate**: nothing works — not `/start`, not a single button —
  until the user has joined the configured channel. Verified on every tap.
- **Approved Admins**: tap admins to toggle ✅/⬜. Un-approved admins' posts
  get auto-deleted; approved ones never do. Members are never touched.
- **Deletion Timer**: presets or a custom number of minutes (default 5 min).
- **Banned Keywords**: any message from anyone containing one is deleted
  instantly.
- Toggles for turning auto-delete or the force-join gate on/off per chat.

### Anonymous ("sign messages") admins & channels

Group anonymous-admin posts and *all* channel posts arrive the same way:
as the chat itself, with an optional `author_signature` — the admin's
*custom title* if one is set, otherwise the chat's own name (which looks
identical for every admin). Give each admin a distinct custom title and
turn on **Sign messages** in the chat's admin settings if you want
per-admin filtering there. If a post has no signature at all, the bot
can't tell who sent it and deliberately leaves it alone rather than risk
deleting everything.

## Deploying with a keep-alive ping

`bot.py` runs a tiny web server (`/` and `/health`, port from `PORT`) so a
free-tier host doesn't idle it out — point UptimeRobot / cron-job.org / your
host's health check at it. Works out of the box on Render, Railway, Replit,
Fly.io, etc. — just set `BOT_TOKEN` and friends as environment variables in
the host's dashboard rather than a `.env` file.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `BOT_TOKEN` | *(required)* | From @BotFather |
| `FORCE_JOIN_CHANNEL` | `NCK_Dev` | Channel username (no `@`) users must join |
| `FORCE_JOIN_CHANNEL_LINK` | `https://t.me/NCK_Dev` | Link shown on the join button |
| `DEFAULT_DELETE_DELAY` | `300` | Seconds, default timer for new chats |
| `DATABASE_PATH` | `bot.db` | SQLite file |
| `KEEP_ALIVE_ENABLED` | `true` | Toggle the web server |
| `PORT` | `8080` | Keep-alive server port |
