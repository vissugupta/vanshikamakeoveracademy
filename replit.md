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
- Super-admin: `/superadmin/`

On first start the app creates a fresh SQLite database (`salon-app/salon.db`) and prints a one-time bootstrap admin password to the logs.

### Desktop app (Electron)
The **Desktop App** workflow launches an Electron window that spawns Flask internally on port 5050 and opens it as a native desktop window.

```bash
cd artifacts/desktop && pnpm exec electron .
```

Set `FLASK_DESKTOP_MODE=1` (done automatically by the Electron main process) to disable Secure-cookie requirements when running without an HTTPS proxy.

## Stack

- **Backend:** Python / Flask, SQLite
- **Templates:** Jinja2 HTML
- **Mobile:** Expo (React Native WebView wrapper) — see `mobile/`

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

## User preferences

- App should support multiple salons (multi-tenant/white-label SaaS)
- Salon name, theme, and colors should be configurable per client
- All credentials (email, SMS, WhatsApp) should be configurable per salon in the admin panel
- Target: desktop app (Electron wrapper or similar)
