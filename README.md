# CourtReserve Booking Bot

Automated booking scripts for CourtReserve using Selenium. Book courts and register for open play events with precise timing.

## Features

- **Court Booking**: Automatically book pickleball courts with customizable time and duration
- **Open Play Registration**: Register for open play events
- **Date Selection**: Book for today, tomorrow, or up to 5 days in advance
- **Wait Until**: Schedule scripts to run at a specific time (e.g., when booking windows open)
- **Closing Time Handling**: Automatically adjusts duration if it would exceed facility closing time (23:00)
- **Discord Notifications**: Get notified on success or failure via Discord webhook

## Prerequisites

- Python 3.11+
- Chrome browser installed

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
```

### Using pip

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Duplicate the .env.example file and rename it to `.env`:

```bash
# Required: CourtReserve credentials
EMAIL=your-email@example.com
PASSWORD=your-password

# Optional: Discord webhook for notifications
# See https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks for more details on how to create a webhook
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Optional: Discord bot token (for running the bot)
# See the "Discord Bot" section below for setup instructions
DISCORD_BOT_TOKEN=your_bot_token_here

# Optional: Run browser in headless mode (Does not open a browser tab)
HEADLESS=false

# Optional: Custom Chrome binary path
CHROME_PATH=/path/to/chrome
```

## Usage

### Court Booking

To use with `uv`, replace `python` with `uv run` in the commands below.

```bash
# List all commands
python court_booking.py --help

# Book a 2-hour slot at 9 PM, 5 days from now (latest available)
python court_booking.py --time 21:00 --duration 2

# Book for a specific date
python court_booking.py --time 21:00 --duration 2 --date 12/15

# Book for tomorrow
python court_booking.py --time 21:00 --duration 2 --date tomorrow

# Book 3 days from now
python court_booking.py --time 21:00 --duration 2 --date +3d

# Wait until 7 AM, then book
python court_booking.py --time 21:00 --duration 2 --wait-until 07:00

# Wait until tomorrow at 7 AM
python court_booking.py --time 21:00 --duration 2 --wait-until "tomorrow 07:00"
```

#### Options

| Option         | Short | Description                                                              |
| -------------- | ----- | ------------------------------------------------------------------------ |
| `--time`       | `-t`  | Reservation time in 24h format (e.g., `21:00`)                           |
| `--duration`   | `-d`  | Duration in hours: `1`, `1.5`, `2`, `2.5`, or `3`                        |
| `--date`       |       | Date to book: `today`, `tomorrow`, `+3d`, `12/15`, or `latest` (default) |
| `--wait-until` | `-w`  | Wait until time before starting: `07:00`, `tomorrow 07:00`, `+1d 07:00`  |

#### Precision Timing

The script has built-in precision timing for competitive booking scenarios. After filling out the reservation form, it waits until exactly the `--time` you specified before clicking the save button. This is useful when booking windows open at a specific time and you want to submit at the exact moment.

For example, if you run `--time 21:00` at 20:58, the script will:

1. Log in and fill out the form
2. Display a countdown timer
3. Click save at exactly 21:00:00 (with millisecond precision)

### Open Play Registration

To use with `uv`, replace `python` with `uv run` in the commands below.

```bash
# Register for default event (Pickleball Open Play - Intermediate)
python open_play.py

# Register for a specific event
python open_play.py --event "Pickleball Open Play - Advanced"

# Register for tomorrow's event
python open_play.py --date tomorrow

# Wait until 7 AM, then register
python open_play.py --wait-until 07:00
```

#### Options

| Option         | Short | Description                                                               |
| -------------- | ----- | ------------------------------------------------------------------------- |
| `--event`      | `-e`  | Event name to register for (default: Pickleball Open Play - Intermediate) |
| `--date`       |       | Date to book: `today`, `tomorrow`, `+3d`, `12/15`, or `latest` (default)  |
| `--wait-until` | `-w`  | Wait until time before starting                                           |

### Date Formats

The `--date` option supports:

| Format     | Example           | Description                                    |
| ---------- | ----------------- | ---------------------------------------------- |
| `latest`   | `--date latest`   | 5 days from now (default, max advance booking) |
| `today`    | `--date today`    | Today's date                                   |
| `tomorrow` | `--date tomorrow` | Tomorrow's date                                |
| `+Nd`      | `--date +3d`      | N days from now                                |
| `MM/DD`    | `--date 12/15`    | Specific date (current year)                   |

### Wait-Until Formats

The `--wait-until` option supports:

| Format           | Example               | Description                   |
| ---------------- | --------------------- | ----------------------------- |
| `HH:MM`          | `-w 07:00`            | Today (or tomorrow if passed) |
| `HH:MM:SS`       | `-w 06:59:55`         | With seconds precision        |
| `tomorrow HH:MM` | `-w "tomorrow 07:00"` | Tomorrow at time              |
| `+Nd HH:MM`      | `-w "+1d 07:00"`      | N days from now at time       |

## Example: Scheduled Booking

Book a court 5 days in advance when the booking window opens at 7 AM:

```bash
uv run court_booking.py \
  --time 21:00 \
  --duration 2 \
  --date latest \
  --wait-until 07:00
```

> ⚠️ **Warning:** The script cannot run if your computer is asleep. Make sure to disable sleep/hibernation or use a machine that stays awake (e.g., a server or cloud VM) when using `--wait-until` for scheduled bookings.

## Scheduling with Cron

Instead of using `--wait-until`, you can use cron to run the script at a specific time. This is useful for servers or machines that are always on.

### Setup

1. Open your crontab for editing:

```bash
crontab -e
```

2. Add a cron job. The format is:

```
MIN HOUR DAY MONTH WEEKDAY command
```

### Examples

```bash
# Book a court every day at 7:00 AM
0 7 * * * cd /path/to/project && /path/to/.venv/bin/python court_booking.py --time 21:00 --duration 2

# Book a court on weekdays only at 6:59:55 AM (using --wait-until for precision)
59 6 * * 1-5 cd /path/to/project && /path/to/.venv/bin/python court_booking.py --time 21:00 --duration 2 --wait-until 07:00

# Register for open play every Saturday at 7:00 AM
0 7 * * 6 cd /path/to/project && /path/to/.venv/bin/python open_play.py
```

See https://crontab.guru/ if you need assistance generating cron expressions.

### Finding Your Paths

```bash
# Get the full path to your project
pwd

# Get the full path to your Python interpreter
which python
# Or if using a virtual environment (ie uv after running `uv venv`)
echo $VIRTUAL_ENV/bin/python
```

### Logging Cron Output

Redirect output to a log file to debug issues:

```bash
0 7 * * * cd /path/to/project && /path/to/.venv/bin/python court_booking.py --time 21:00 --duration 2 >> /path/to/project/logs/cron.log 2>&1
```

### Verify Cron is Running

```bash
# List your current cron jobs
crontab -l

# Check cron logs (macOS)
log show --predicate 'process == "cron"' --last 1h

# Check cron logs (Linux)
grep CRON /var/log/syslog
```

> 💡 **Tip:** Schedule the court booking script to run a few minutes before the time you would like to book to ensure the reservation is set up in time.

## Discord Bot

Run bookings via Discord commands instead of the command line. Perfect for non-technical users.

### Setup

1. **Create a Discord Application**

   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Click "New Application" and give it a name
   - Go to "Bot" in the sidebar and click "Add Bot"
   - Under "Privileged Gateway Intents", enable nothing (defaults are fine)
   - Click "Reset Token" and copy the token

2. **Add the bot token to your `.env`**

   ```bash
   DISCORD_BOT_TOKEN=your_bot_token_here
   ```

3. **Invite the bot to your server**

   - Go to "OAuth2" > "URL Generator" in the Developer Portal
   - Select scopes: `bot`, `applications.commands`
   - Select permissions: `Send Messages`, `Use Slash Commands`
   - Copy the generated URL and open it to invite the bot

4. **Run the bot**
   ```bash
   python bot.py
   ```

### Commands

| Command                                        | Description                         |
| ---------------------------------------------- | ----------------------------------- |
| `/help`                                        | Show all commands and usage         |
| `/register email:... password:...`             | Save your CourtReserve credentials  |
| `/unregister`                                  | Delete your saved credentials       |
| `/book time:21:00 duration:2`                  | Book a court at 9 PM for 2 hours    |
| `/book time:21:00 duration:2 date:tomorrow`    | Book for tomorrow                   |
| `/book time:21:00 duration:2 wait_until:07:00` | Wait until 7 AM, then book          |
| `/openplay`                                    | Register for Intermediate open play |
| `/openplay event:Advanced date:tomorrow`       | Register for Advanced, tomorrow     |
| `/cancel`                                      | Cancel a running booking task       |
| `/ping`                                        | Check if bot is online              |

### Multi-User Support

Each user registers their own CourtReserve credentials:

1. User sends `/register email:user@example.com password:secret123`
2. Credentials are encrypted and stored in the database
3. When the user runs `/book` or `/openplay`, their credentials are used
4. Users can delete their credentials with `/unregister`

Credentials are encrypted using Fernet symmetric encryption. The encryption key is derived from the bot token.

### Running the Bot 24/7

For the bot to always be available, run it on a server or use a process manager:

```bash
# Using nohup (simple)
nohup python bot.py > bot.log 2>&1 &

# Using screen
screen -S courtbot
python bot.py
# Press Ctrl+A, then D to detach
```

### Deploying to Fly.io

Deploy the bot to the cloud so it runs 24/7 without keeping your computer on.

1. **Install the Fly CLI**

   ```bash
   # macOS
   brew install flyctl

   # Linux
   curl -L https://fly.io/install.sh | sh

   # Windows
   powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"
   ```

2. **Sign up and log in**

   ```bash
   fly auth signup   # or fly auth login if you have an account
   ```

3. **Launch the app** (first time only)

   ```bash
   fly launch --no-deploy
   ```

   - When prompted, accept the generated name or choose your own
   - Select a region close to you
   - Say **No** to PostgreSQL and Redis

4. **Set your secrets**

   ```bash
   fly secrets set DISCORD_BOT_TOKEN=your-bot-token
   fly secrets set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ```

   > Note: `EMAIL` and `PASSWORD` are no longer needed as secrets since each user registers their own credentials via `/register`.

5. **Create a volume for persistent storage** (stores user credentials)

   ```bash
   fly volumes create courtbot_data --size 1 --region iad
   ```

6. **Deploy**

   ```bash
   fly deploy
   ```

7. **Check logs**

   ```bash
   fly logs
   ```

8. **Manage the bot**

   ```bash
   fly status          # Check if running
   fly apps restart    # Restart the bot
   fly apps destroy    # Delete the app
   ```

> 💡 **Cost:** Fly.io's free tier includes enough resources for this bot. You may need to add a credit card but won't be charged for light usage.

## Project Structure

```
.
├── bot.py                    # Discord bot for slash commands
├── constants.py              # Driver setup with lazy initialization
├── court_booking.py          # Court booking script
├── open_play.py              # Open play registration script
├── requirements.txt          # Python dependencies
├── utils/
│   ├── __init__.py
│   ├── booking_date.py       # Date selection and parsing
│   ├── discord.py            # Discord webhook notifications
│   ├── exceptions.py         # Custom exceptions
│   ├── find.py               # Element finder with retry logic
│   ├── login.py              # Login utility
│   └── wait.py               # Wait-until functionality
└── .env                      # Your credentials (not committed)
```

## Troubleshooting

### Browser doesn't open

- Make sure Chrome is installed
- Try setting `CHROME_PATH` in `.env` if Chrome is in a non-standard location

### Login fails

- Verify your `EMAIL` and `PASSWORD` in `.env`
- Check if your account is locked or requires 2FA

### Date not available

- Bookings open 5 days in advance at a specific time (usually 7 AM)
- Use `--wait-until` to wait for the booking window to open

### Court unavailable

- The script tries multiple courts in priority order
- All courts may already be booked at your requested time
