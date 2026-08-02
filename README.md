# CourtReserve Booking Bot

Automated booking scripts for CourtReserve using Playwright. Book courts and register for open play events with precise timing.

## Features

- **Court Booking**: Automatically book pickleball courts with customizable time and duration
- **Direct API Mode**: Fires HTTP POST requests directly at click time — no browser UI interaction needed during the booking race
- **Open Play Registration**: Register for open play events
- **Check Availability**: View available time slots for any date
- **Date Selection**: Book for today, tomorrow, or up to 6 days in advance
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
# Book a 2-hour slot at 9 PM using direct API (recommended)
# Waits until 2 minutes before 9 PM, then fires HTTP requests at 9 PM
python court_booking.py --time 21:00 --duration 2 --direct

# Dry run — verify the payload without actually booking
python court_booking.py --time 21:00 --duration 2 --direct --no-wait --dry-run

# Execute immediately (skip waiting)
python court_booking.py --time 21:00 --duration 2 --direct --no-wait

# Book for a specific date
python court_booking.py --time 21:00 --duration 2 --direct --date 12/15 --no-wait

# Book for tomorrow
python court_booking.py --time 21:00 --duration 2 --direct --date tomorrow --no-wait

# Wait until a specific time (e.g., when booking window opens at 7 AM)
python court_booking.py --time 21:00 --duration 2 --direct --wait-until 07:00

# Try more courts (default: top 3 available, 0 = no limit)
python court_booking.py --time 21:00 --duration 2 --direct --max-courts 5

# Tune direct API timing offsets in milliseconds (default shown)
python court_booking.py --time 21:00 --duration 2 --direct --direct-offsets="-1000,-750,-500,-250,0"

# Disable pre-fire latency probes if you want the smallest possible request footprint
python court_booking.py --time 21:00 --duration 2 --direct --no-direct-latency-probes

# Specify preferred courts in priority order
python court_booking.py --time 21:00 --duration 2 --direct --courts 5C,5D,6D

# Parallel browser mode (legacy — uses multiple Chrome instances)
python court_booking.py --time 21:00 --duration 2 --parallel --attempts 5
```

> **Booking Modes:**
> - `--direct` (recommended): Uses one browser for setup, logs a few low-volume latency probes, then fires lightweight HTTP POST requests at configured timing offsets around the target time. Fastest and lowest resource usage.
> - `--parallel`: Spawns multiple Chrome instances that each fill out and submit the form. Uses more resources but doesn't rely on hardcoded form data.
> - Neither flag: Sequential mode — tries courts one at a time in a single browser.
>
> Both the CLI and Discord bot default to waiting until 2 minutes before the reservation time. Use `--no-wait` for immediate execution.

### Open Play Registration

```bash
# Register for open play (clicks immediately)
python open_play.py

# Register for tomorrow's event
python open_play.py --date tomorrow

# Wait until 7 AM to click Details (for when registration opens)
python open_play.py --date tomorrow --click-at 07:00

# Start script early, wait until 7 AM to click
python open_play.py --date +5d --wait-until 06:55 --click-at 07:00
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
| `/check-availability`         | View available slots (with quick-book) |
| `/book time:21:00 duration:2` | Book a court (court can be `5C,5D,6D`) |
| `/openplay`                   | Register for open play             |
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

## Remote control over SSH

The bot runs on a separate Mac on the LAN, started by hand in a tmux session.
`scripts/remote.sh` wraps the common operations so they can be driven from this
machine (or by an agent) without an interactive shell.

### One-time bootstrap

These steps need a human — they authorize a key on the remote machine.

**1. Enable Remote Login on the bot Mac.** System Settings → General → Sharing →
Remote Login. Or, in a terminal on that machine:

```bash
sudo systemsetup -setremotelogin on
```

**2. Note its address and username.** On the bot Mac:

```bash
scutil --get LocalHostName   # e.g. mac-mini -> reachable as mac-mini.local
whoami
ipconfig getifaddr en0       # fallback if mDNS is flaky
```

Give it a static DHCP reservation in your router if you use the IP — a lease
change otherwise breaks the alias silently.

**3. Authorize your key.** From this machine, substituting the values above:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub USER@HOST
ssh USER@HOST 'echo ok'     # must succeed without a password prompt
```

**4. Add the `courtbot` alias** to `~/.ssh/config`:

```sshconfig
Host courtbot
  HostName mac-mini.local
  User USER
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  ServerAliveInterval 30
```

The scripts and the agent permission rules both key off the alias `courtbot`,
so the address can change later without touching anything else.

**5. Verify the remote side:**

```bash
./scripts/remote.sh doctor
```

This checks ssh, `tmux`, `git`, the venv interpreter and `.env`. Install
anything it reports as MISSING (`brew install tmux`).

### Daily use

```bash
./scripts/remote.sh status          # tmux session, bot process, git revision
./scripts/remote.sh pull            # git pull --ff-only on the remote
./scripts/remote.sh restart         # restart the bot's tmux session
./scripts/remote.sh logs 100        # last 100 log lines
./scripts/remote.sh follow          # stream the log
./scripts/remote.sh book --time 21:00 --duration 2   # one-off booking run
```

`bot.py` only logs to stdout, so `start` pipes it through `tee` into
`logs/bot.out`. That file is what `logs`/`follow` read — a bot started by hand
outside this script leaves nothing durable to tail.

If the remote paths differ from the defaults, drop a `scripts/remote.env`
(gitignored) next to the script:

```bash
COURTBOT_REPO=~/code/court-reserve-bot
COURTBOT_TMUX=bot
```

### Letting the agent drive it

Add these to the `permissions.allow` list in `.claude/settings.local.json` so
routine remote commands don't prompt each time:

```json
"Bash(./scripts/remote.sh:*)",
"Bash(scripts/remote.sh:*)",
"Bash(ssh courtbot:*)"
```

Scoping to the `courtbot` alias rather than `ssh:*` keeps the grant to this one
host.

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
├── scripts/
│   └── remote.sh             # Drive the remote Mac deployment over SSH
└── utils/
    ├── booking_date.py       # Date selection
    ├── direct_api.py         # Direct HTTP API booking (no browser UI)
    ├── discord.py            # Discord notifications
    ├── exceptions.py         # Custom exceptions
    ├── login.py              # Login utility
    ├── user_store.py         # User credential storage
    └── wait.py               # Wait-until functionality
```

## Debugging

When running in headless mode (`HEADLESS=true`), the scripts automatically save debug snapshots (screenshot + HTML) to `data/debug/`. This helps diagnose issues when bookings fail.

To enable debug snapshots in headed mode:

```bash
DEBUG_SNAPSHOTS=true python court_booking.py --time 21:00 --duration 2 --no-wait
```

Debug files are saved at key moments:
- After clicking save button
- When errors occur
- On successful bookings

Only the last 20 snapshots are kept to save disk space.

## Troubleshooting

### Login fails

- Verify your `EMAIL` and `PASSWORD` in `.env`
- Check if your account requires 2FA

### Date not available

- Bookings open 6 days in advance at a specific time (usually 7 AM)
- Use `--wait-until` to wait for the booking window to open

### Court unavailable

- The script tries multiple courts in priority order
- All courts may already be booked at your requested time

### Booking says success but didn't work

- Check the debug snapshots in `data/debug/`
- Look for error modals or form validation issues in the HTML
