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

### Desktop app (Electron)
The **Desktop App** workflow launches an Electron window that runs Flask internally on port 5001 and opens it as a native desktop window. Development falls back to the local Python installation; distributable installers use a bundled server executable.

```bash
cd desktop && node_modules/.bin/electron . --no-sandbox
```

`FLASK_DESKTOP_MODE=1` is set automatically by the Electron main process so Flask uses plain HTTP session cookies (no `Secure`/`SameSite=None`) over the local loopback interface.

Build distributable installers from the `desktop/` directory:

```bash
npm run dist:linux   # → dist/*.AppImage + *.deb  (run on Linux)
npm run dist:win     # → dist/*.exe NSIS installer (requires Wine on Linux)
npm run dist:mac     # → dist/*.dmg               (run on macOS; native host architecture)
```

## Build a self-contained installer

Python is required only on the build machine. The customer's installed app
contains a standalone PyInstaller server and does not require Python or Flask.

```bash
cd desktop
python -m pip install -r ../salon-app/requirements.txt pyinstaller
npm run build:server
npm run dist:win    # or dist:mac / dist:linux
```

`build-resources/` is platform-specific build output and is intentionally
gitignored. Build each installer on its target platform. The macOS command
creates one DMG matching the build Mac's architecture; run it on both Intel
and Apple Silicon Macs if both variants are required.

## Stack

- **Backend:** Python / Flask, SQLite
- **Templates:** Jinja2 HTML
- **Mobile:** Expo (React Native WebView wrapper) — see `mobile/`
- **Desktop:** Electron (wraps the Flask server) — see `desktop/`

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
- The desktop shell is Electron-based and runs the local Flask server privately on the computer.
