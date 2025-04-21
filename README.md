# Discord Ticket Bot

A comprehensive Discord ticket management system built for communities that need organized support workflows. This bot allows users to create categorized support tickets and provides staff with tools to efficiently manage, track, and resolve user issues.

## Features

### For Users
- **Categorized Tickets**: Create tickets in predefined categories (Report Player, Report Bug, Buy Business, etc.)
- **Easy Access**: Intuitive dropdown interface to start a new ticket
- **Private Communication**: Secure channels only visible to the ticket creator and staff
- **Ticket Transcripts**: Receive transcript links when tickets are closed

### For Staff
- **Ticket Management Tools**: Claim, add/remove users, and close tickets with detailed controls
- **Auto-Moderation**: Automatic closing of inactive tickets
- **Comprehensive Statistics**: Track ticket activity with detailed stats commands
- **User Tracking**: View individual user ticket history and statistics
- **Organized Categories**: Tickets are organized by type in dedicated categories
- **Ticket Archiving**: Closed tickets are archived and automatically deleted after a configurable time

### Technical Features
- **PostgreSQL Backend**: All ticket data, transcripts, and statistics are stored in a robust database
- **Auto-Recovery**: Bot automatically restores interfaces after restarts
- **Transcript System**: Generates and uploads ticket conversations to Pastebin
- **Customizable Configuration**: Easy-to-adjust settings through environment variables
- **Scalable Architecture**: Designed for high-traffic communities

## Setup Instructions

### Prerequisites
- Python 3.8+
- PostgreSQL database
- Discord Bot Token
- Pastebin API Key (optional, for transcript uploads)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/discord-ticket-bot.git
   cd discord-ticket-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables**  
   Create a `.env` file in the root directory with the following:
   ```
   DISCORD_BOT_TOKEN=your_discord_bot_token_here
   DATABASE_URL=postgresql://username:password@localhost/database_name
   PASTEBIN_API_KEY=your_pastebin_api_key_here
   ```

4. **Setup database**
   - Create a PostgreSQL database
   - The bot will automatically create the necessary tables on first run

5. **Run the bot**
   ```bash
   python bot.py
   ```

### Discord Bot Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and configure a bot
3. Enable the following intents:
   - Message Content Intent
   - Server Members Intent
4. Use the OAuth2 URL Generator to invite the bot to your server with the following permissions:
   - Manage Channels
   - Manage Roles
   - Send Messages
   - Manage Messages
   - Embed Links
   - Attach Files
   - Read Message History
   - Add Reactions
   - Use External Emojis

## Usage

### Setting Up the Ticket System

1. In your desired channel, run:
   ```
   !setup
   ```
   or use the slash command:
   ```
   /setup
   ```
   This creates the ticket creation interface with a dropdown menu.

### Staff Commands

- **View Statistics**
  ```
  /stats [day|week|month|all]
  ```
  View ticket statistics over different timeframes.

- **View User Statistics**
  ```
  /userstats @user
  ```
  View statistics for a specific user.

### Configuration

You can modify the following settings in the code:

- `STAFF_ROLES`: List of role names that have staff access
- `TICKET_CATEGORIES`: Configure ticket types, names, and emojis
- `INACTIVE_CLOSE_DAYS`: Days of inactivity before auto-closing tickets
- `ARCHIVE_DELETE_DAYS`: Days before archived tickets are permanently deleted

## Customization

### Adding New Ticket Categories

Edit the `TICKET_CATEGORIES` dictionary in the code:

```python
TICKET_CATEGORIES = {
    "categoryId": {"name": "Category Display Name", "emoji": "🔧"},
    # Add more categories here
}
```

### Modifying Staff Roles

Edit the `STAFF_ROLES` list in the code:

```python
STAFF_ROLES = ["Role Name 1", "Role Name 2"]
```

## License

[MIT License](LICENSE)

## Support

If you encounter any issues or have questions, please open an issue on this repository.

---

Created with ❤️ for Discord communities.