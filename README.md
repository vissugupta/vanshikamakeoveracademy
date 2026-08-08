# Vanshika Makeover Academy — Project Source Bundle

This bundle contains the current Vanshika Makeover Academy salon ERP and its Expo mobile wrapper.

## Included

- `salon-app/` — Flask ERP, customer website, admin/staff portals, SQLite schema initialization, templates, static branding, and notification helpers.
- `mobile/` — Expo mobile app source with native WebView and browser preview support.
- `BUILD_ANDROID.md` — local Android build instructions.
- `LOCAL_SETUP.md` — local development and configuration instructions.
- Root pnpm manifests — workspace package metadata and lockfile.

## Important

The development SQLite database, secrets, dependency folders, Expo caches, generated Android project, signing files, and build output are intentionally excluded. The application creates a fresh database on first start.

Configure production secrets through your environment or secret manager. Never commit credentials.

## Run the Flask ERP

```bash
cd salon-app
python3 -m pip install -r requirements.txt
export SESSION_SECRET="replace-with-a-long-random-value"
python3 app.py
```

Open `http://localhost:5000/`.

## Run the Expo mobile app

```bash
pnpm install
pnpm --filter @workspace/mobile run dev
```

For an Android build, follow `BUILD_ANDROID.md`.
