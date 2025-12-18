# CourtReserve Booking Bot

Automated booking scripts for CourtReserve using Playwright. Book courts and register for open play events with precise timing.

## Features

- **Court Booking**: Automatically book pickleball courts with customizable time and duration
- **Open Play Registration**: Register for open play events
- **Check Availability**: View available time slots for any date
- **Date Selection**: Book for today, tomorrow, or up to 5 days in advance
- **Wait Until**: Schedule scripts to run at a specific time (e.g., when booking windows open)
- **Closing Time Handling**: Automatically adjusts duration if it would exceed facility closing time (23:00)
- **Discord Bot**: Control everything via Discord slash commands
- **Multi-User Support**: Each user can register their own credentials
- **Scheduling**: Set up recurring bookings via Discord

## Prerequisites

- Python 3.11+
- Playwright (installed automatically)

## Installation

### Using uv (Recommended)

[uv](https://docs.astral.sh/uv/) is a fast Python package installer and resolver.

```bash
# Install uv (macOS/Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with Homebrew
brew install uv

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### Using pip

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Configuration

Create a `.env` file:

```bash
# Required: CourtReserve credentials
EMAIL=your-email@example.com
PASSWORD=your-password

# Optional: Discord webhook for notifications
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Optional: Discord bot token (for running the bot)
DISCORD_BOT_TOKEN=your_bot_token_here

# Optional: Run browser in headless mode
HEADLESS=false
```

## Usage

### Court Booking

```bash
# Book a 2-hour slot at 9 PM, 5 days from now (latest available)
python court_booking.py --time 21:00 --duration 2

# Book for a specific date
python court_booking.py --time 21:00 --duration 2 --date 12/15

# Book for tomorrow
python court_booking.py --time 21:00 --duration 2 --date tomorrow

# Wait until 7 AM, then book
python court_booking.py --time 21:00 --duration 2 --wait-until 07:00
```

### Open Play Registration

```bash
# Register for open play
python open_play.py

# Register for tomorrow's event
python open_play.py --date tomorrow
```

### Check Availability

```bash
# Check available slots for the latest date
python check_slots.py

# Check for a specific date
python check_slots.py --date tomorrow
```

## Discord Bot

Run bookings via Discord commands instead of the command line.

### Setup

1. **Create a Discord Application** at [Discord Developer Portal](https://discord.com/developers/applications)
2. Add the bot token to your `.env`:
   ```bash
   DISCORD_BOT_TOKEN=your_bot_token_here
   ```
3. **Run the bot**:
   ```bash
   python bot.py
   ```

### Commands

| Command                       | Description                        |
| ----------------------------- | ---------------------------------- |
| `/help`                       | Show all commands                  |
| `/register`                   | Save your CourtReserve credentials |
| `/unregister`                 | Delete your saved credentials      |
| `/account`                    | View your registered email         |
| `/book time:21:00 duration:2` | Book a court                       |
| `/openplay`                   | Register for open play             |
| `/check-availability`         | View available slots               |
| `/cancel`                     | Cancel a running task              |
| `/schedule list`              | View your scheduled tasks          |
| `/schedule openplay`          | Schedule recurring open play       |
| `/schedule book`              | Schedule recurring court booking   |
| `/ping`                       | Check if bot is online             |

## Deploying to Fly.io

Deploy the bot to run 24/7 in the cloud.

```bash
# Install Fly CLI
brew install flyctl  # or: curl -L https://fly.io/install.sh | sh

# Login
fly auth login

# Launch (first time)
fly launch --no-deploy

# Create volume for data persistence
fly volumes create courtbot_data --size 1 --region iad

# Set secrets
fly secrets set DISCORD_BOT_TOKEN=your_token
fly secrets set ENCRYPTION_KEY=your_key

# Deploy
fly deploy

# View logs
fly logs
```

## Project Structure

```
.
├── bot.py                    # Discord bot
├── constants.py              # Playwright browser setup
├── court_booking.py          # Court booking script
├── open_play.py              # Open play registration
├── check_slots.py            # Check availability
├── requirements.txt          # Python dependencies
├── Dockerfile                # Container definition
├── fly.toml                  # Fly.io configuration
└── utils/
    ├── booking_date.py       # Date selection
    ├── discord.py            # Discord notifications
    ├── exceptions.py         # Custom exceptions
    ├── login.py              # Login utility
    ├── user_store.py         # User credential storage
    └── wait.py               # Wait-until functionality
```

## Troubleshooting

### Login fails

- Verify your `EMAIL` and `PASSWORD` in `.env`
- Check if your account requires 2FA

### Date not available

- Bookings open 5 days in advance at a specific time (usually 7 AM)
- Use `--wait-until` to wait for the booking window to open

### Court unavailable

- The script tries multiple courts in priority order
- All courts may already be booked at your requested time
