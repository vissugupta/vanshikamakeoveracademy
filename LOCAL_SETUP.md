# Vanshika Makeover Academy — Local Project Copy

This archive contains the Vanshika Makeover Academy salon ERP source, its
SQLite database, branding assets, the Expo mobile app, and the shared
workspace packages used by the mobile app.

## Main web/ERP application

Requirements:

- Python 3.10+
- pip

Run the Flask salon application:

```bash
cd artifacts/salon-app
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
export SESSION_SECRET="replace-with-a-long-random-value"
python app.py
```

Open:

- Customer site: http://localhost:5000/
- Admin panel: http://localhost:5000/admin
- Customer portal: http://localhost:5000/my

The included `salon.db` is the current database copy. If it is removed, the
application creates a fresh database with the service catalog on startup.

## Email and messaging configuration

The app runs without external messaging credentials, but booking OTP,
customer-login OTP, confirmation email, and salon notification delivery need
Gmail SMTP settings:

```bash
export GMAIL_USER="info.vanshikamakeoveracademy@gmail.com"
export GMAIL_APP_PASSWORD="your-16-character-google-app-password"
```

Optional integrations:

```bash
export FAST2SMS_API_KEY="..."
export TWILIO_ACCOUNT_SID="..."
export TWILIO_AUTH_TOKEN="..."
export TWILIO_FROM_NUMBER="..."
export WHATSAPP_ACCESS_TOKEN="..."
export WHATSAPP_PHONE_NUMBER_ID="..."
```

Never commit these values to the project or share them in the archive.

## Demo data

To intentionally replace operational data with the demo dataset:

```bash
cd artifacts/salon-app
python3 seed_demo_data.py --reset
```

This is destructive to operational records. Make a backup of `salon.db`
before using the reset command.

## Expo mobile app

Requirements:

- Node.js 18+ or newer
- pnpm
- Expo Go for device testing, if desired

From the archive root:

```bash
pnpm install
pnpm --filter @workspace/mobile run dev
```

For a local Expo development server, use the normal Expo CLI prompts and scan
the displayed QR code with Expo Go. The Replit-only proxy environment
variables in the original development command are not required locally.

The mobile app source is in `artifacts/mobile`. Shared workspace packages are
under `lib`.

## Included source

- `artifacts/salon-app` — Flask ERP, templates, static assets, uploads, and SQLite database
- `artifacts/mobile` — Expo mobile application
- `artifacts/api-server` — workspace API artifact source
- `artifacts/mockup-sandbox` — component preview source
- `lib` — shared API client/spec/schema packages
- `scripts` — workspace scripts
- Root pnpm and TypeScript configuration

The archive intentionally omits secrets, `node_modules`, Python caches,
`.expo`, generated build output, Replit-only artifact metadata, and workspace
runtime caches.