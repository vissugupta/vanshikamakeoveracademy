# Vanshika Makeover Academy — Salon ERP

A Flask-based salon management platform with customer-facing booking, admin panel, staff portal, and an Expo mobile app wrapper.

## How to run

### Web preview
The **Start application** workflow runs the Flask web server on port 5000.

```bash
cd salon-app && python app.py
```

- Customer site: `/`
- Admin panel: `/admin`
- Customer portal: `/my`
- Staff portal: `/staff`
- Admin panel: `/admin`

On first start the app creates a fresh SQLite database (`salon-app/salon.db`) and prints a one-time bootstrap admin password to the logs.

### Desktop app (Python + pywebview)
The **Desktop App** workflow uses the same Flask screens inside a native
pywebview window. The distributable Windows executable is built with PyInstaller
and does not require Python, Flask, Node, or Electron on the customer's machine.

```bash
python desktop/launcher.py
```

`FLASK_DESKTOP_MODE=1` is set automatically by the Python launcher so Flask
uses plain HTTP session cookies over the local loopback interface.

Build distributable installers from the `desktop/` directory:

```bash
python -m PyInstaller --noconfirm --clean desktop/pywebview.spec
```

## Build a self-contained desktop executable

Python is required only on the build machine. The customer's executable
contains the launcher, Flask application, templates, static assets, and
pywebview dependencies.

```bash
python -m pip install -r salon-app/requirements.txt -r desktop/requirements.txt
python -m PyInstaller --noconfirm --clean desktop/pywebview.spec
```

`desktop/dist/` is platform-specific build output and is intentionally
gitignored. Build each executable on its target platform.

## Stack

- **Backend:** Python / Flask, SQLite
- **Templates:** Jinja2 HTML
- **Mobile:** Expo (React Native WebView wrapper) — see `mobile/`
- **Desktop:** Python + pywebview (wraps the Flask server) — see `desktop/`

## Secrets & credentials

All credentials are read from environment variables — never hardcoded.

| Variable | Purpose |
|---|---|
| `SESSION_SECRET` | Flask session signing key ✅ already set |
| `GMAIL_USER` | Sender address for email notifications |
| `GMAIL_APP_PASSWORD` | Gmail app password (16-char) |
| `FAST2SMS_API_KEY` | SMS via Fast2SMS |
| `TWILIO_ACCOUNT_SID` | Twilio SMS |
| `TWILIO_AUTH_TOKEN` | Twilio auth |
| `TWILIO_FROM_NUMBER` | Twilio sender number |
| `WHATSAPP_ACCESS_TOKEN` | WhatsApp Business API |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp phone number ID |

The app works without messaging credentials — OTP and notifications simply won't be delivered.

## Product model

- This is a standalone desktop product sold and installed separately for each salon customer.
- Each installation has one local salon profile and one local SQLite database.
- The salon owner can change the name, logo, tagline, contact details, and colors from **Admin → Branding**.
- There is no tenant switching, public salon routing, SaaS signup, or platform super-admin.
- The desktop shell is Python + pywebview and runs the local Flask server privately on the computer.
