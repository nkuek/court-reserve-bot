# CourtReserve Selenium Automation

Selenium automation scripts for booking courts and registering for open play on CourtReserve.

## Prerequisites

- Python 3.10+
- Chrome browser installed
- ChromeDriver (will be auto-managed by Selenium 4.6+)

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the project root with your CourtReserve credentials:

```
EMAIL=your-email@example.com
PASSWORD=your-password
```

## Usage

### Court Booking

Book a court at a specific time:

```bash
python court_booking.py --time=21:00 --duration=2
```

Options:

- `--time`: Reservation time in 24-hour format (e.g., `21:00` for 9:00 PM)
- `--duration`: Duration in hours (1, 1.5, 2, 2.5, or 3)

### Open Play Registration

Register for Pickleball Open Play - Intermediate:

```bash
python open_play.py
```

## Project Structure

```
.
├── constants.py                    # Shared constants and driver setup
├── court_booking.py                # Court booking script
├── open_play.py                    # Open play registration script
├── requirements.txt                # Python dependencies
├── utils/
│   ├── __init__.py
│   ├── click_latest_available_date.py
│   ├── find.py                     # Element finder with retry logic
│   └── login.py                    # Login utility
└── .env                            # Your credentials (not committed)
```
