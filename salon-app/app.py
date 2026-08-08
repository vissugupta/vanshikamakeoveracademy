import os
import json
import csv
import random
import time
import sqlite3
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, g, send_from_directory, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET', 'vanshika-makeover-secret-2024')
_desktop_mode = os.environ.get('FLASK_DESKTOP_MODE') == '1'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# The Replit preview can reach Flask through both its HTTPS proxy and the
# internal HTTP preview URL. A Secure-only cookie is dropped on the latter,
# which makes a successful admin POST immediately appear logged out.
app.config['SESSION_COOKIE_SECURE'] = False

# Trust the Replit HTTPS proxy for URL generation and request metadata.
if not _desktop_mode:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Replit gives Expo Go a dedicated *.expo.pike.replit.dev host. In this
# multi-artifact workspace that host can fall through to the root Flask
# service instead of reaching Metro, which makes Expo Go receive salon HTML
# where it expects an Expo manifest. Keep this bridge limited to the Expo host
# and only proxy Metro's manifest, bundles, and assets.
MOBILE_EXPO_PORT = int(os.environ.get('MOBILE_EXPO_PORT', '18115'))


@app.before_request
def proxy_expo_domain_to_metro():
    host = request.host.split(':', 1)[0].lower()
    if '.expo.' not in host:
        return None

    path = request.path
    if path == '/' or path == '/_expo/manifest':
        upstream_path = '/manifest'
    elif (
        path == '/manifest'
        or path == '/status'
        or path.startswith('/node_modules/')
        or path.startswith('/assets/')
        or path.startswith('/_expo/')
    ):
        upstream_path = path
    else:
        return None

    query = request.query_string.decode('utf-8')
    upstream_url = f'http://127.0.0.1:{MOBILE_EXPO_PORT}{upstream_path}'
    if query:
        upstream_url = f'{upstream_url}?{query}'

    forwarded_headers = {}
    for header_name in (
        'Accept',
        'Expo-Platform',
        'Expo-Protocol-Version',
        'User-Agent',
    ):
        value = request.headers.get(header_name)
        if value:
            forwarded_headers[header_name] = value

    try:
        upstream_request = urllib.request.Request(
            upstream_url,
            headers=forwarded_headers,
            method=request.method,
        )
        with urllib.request.urlopen(upstream_request, timeout=30) as upstream:
            response_headers = {}
            for header_name in (
                'Content-Type',
                'Cache-Control',
                'Expo-Protocol-Version',
                'Expo-SFV-Version',
            ):
                value = upstream.headers.get(header_name)
                if value:
                    response_headers[header_name] = value
            return Response(
                upstream.read(),
                status=upstream.status,
                headers=response_headers,
            )
    except urllib.error.HTTPError as error:
        return Response(
            error.read(),
            status=error.code,
            content_type=error.headers.get_content_type(),
        )
    except urllib.error.URLError:
        return Response('Expo bundler is unavailable', status=503)

# When running inside the packaged Electron desktop app, SALON_DATA_DIR is set
# to the platform user-data directory (writable). In development / web it is
# unset and everything defaults to the directory next to app.py as before.
_DATA_DIR = os.environ.get('SALON_DATA_DIR', '')
_APP_DIR  = os.path.dirname(__file__)

DATABASE           = os.path.join(_DATA_DIR or _APP_DIR, 'salon.db')
FEEDBACK_UPLOAD_DIR = os.path.join(_DATA_DIR or _APP_DIR, 'uploads', 'feedback')
FEEDBACK_ALLOWED_EXTENSIONS = {
    'jpg': 'image', 'jpeg': 'image', 'png': 'image',
    'webp': 'image', 'gif': 'image',
    'mp4': 'video', 'mov': 'video', 'webm': 'video',
}
# In dev/web mode logos live in static/logos/ so Flask's built-in static handler
# also works (backward compatible with any existing uploads).
# In packaged Electron mode they move to the writable user-data directory so the
# read-only resources bundle is never mutated.
LOGO_UPLOAD_DIR = (
    os.path.join(_DATA_DIR, 'logos')
    if _DATA_DIR
    else os.path.join(_APP_DIR, 'static', 'logos')
)
LOGO_ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024

# Role hierarchy: owner > manager > receptionist
ROLE_HIERARCHY = {'owner': 3, 'manager': 2, 'receptionist': 1}

OTP_EXPIRY_SECONDS = 600
OTP_MAX_ATTEMPTS   = 3
OTP_RESEND_COOLDOWN = 30

# Loyalty policy: each completed ₹1,000 earns 50 points. The customer's
# available balance expires 60 days after the latest earning event.
LOYALTY_SPEND_BLOCK = 1000
LOYALTY_POINTS_PER_BLOCK = 50
LOYALTY_EXPIRY_DAYS = 60

TIME_SLOTS = [
    '09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM',
    '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM',
    '01:00 PM', '01:30 PM', '02:00 PM', '02:30 PM',
    '03:00 PM', '03:30 PM', '04:00 PM', '04:30 PM',
    '05:00 PM', '05:30 PM', '06:00 PM', '06:30 PM',
    '07:00 PM', '07:30 PM', '08:00 PM',
]

SERVICES_SEED = {
    'Bridal & Professional Makeup': [
        'Bridal Package', 'Party Makeup', 'Airbrush Makeup', 'Saree Draping'
    ],
    'Skincare & Advanced Facials': [
        'Hydra Facial', 'Cleanup', 'Gold Facial', 'Diamond Facial', 'D-Tan'
    ],
    'Hair Styling & Treatments': [
        'Haircut', 'Keratin Treatment', 'Smoothening', 'Hair Spa', 'Global Color'
    ],
    'Nail Studio': [
        'Nail Art', 'Gel Extensions', 'Manicure', 'Pedicure'
    ],
    'Hair Removal & Grooming': [
        'Threading', 'Rica Waxing', 'Honey Waxing'
    ],
}

# Authoritative price list — single source of truth shared by _seed_services()
# and the migration that back-fills any existing price=0 rows.
SERVICE_PRICE_MAP = {
    'Bridal Package':    8500,
    'Party Makeup':      2500,
    'Airbrush Makeup':   3500,
    'Saree Draping':     1200,
    'Hydra Facial':      3200,
    'Cleanup':            800,
    'Gold Facial':       1800,
    'Diamond Facial':    2200,
    'D-Tan':              700,
    'Haircut':            600,
    'Keratin Treatment': 4500,
    'Smoothening':       5500,
    'Hair Spa':          1500,
    'Global Color':      3800,
    'Nail Art':           900,
    'Gel Extensions':    2200,
    'Manicure':           600,
    'Pedicure':           700,
    'Threading':          150,
    'Rica Waxing':       1200,
    'Honey Waxing':       900,
}


# ─── Database helpers ────────────────────────────────────────────────────────

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


@app.before_request
def load_salon():
    """Attach this installation's single salon profile to every request."""
    if request.endpoint == 'static':
        return
    # Expo proxy already handled these and returned early
    if '.expo.' in request.host.split(':', 1)[0].lower():
        return
    g.salon = get_db().execute(
        'SELECT * FROM salon_settings WHERE id=1'
    ).fetchone()


@app.context_processor
def inject_salon():
    """Make the local salon profile + logo URL available to all templates.

    ``tenant`` remains as a template compatibility alias for installations
    upgraded from the earlier white-label build; it is always the one local
    salon and can never be selected or switched.
    """
    salon = getattr(g, 'salon', None)
    if salon and salon['logo_filename'] and salon['logo_filename'] != 'brand-logo.jpeg':
        if _DATA_DIR:
            logo_url = url_for('salon_logo', filename=salon['logo_filename'])
        else:
            logo_url = url_for('static', filename=f"logos/{salon['logo_filename']}")
    else:
        logo_url = url_for('static', filename='brand-logo.jpeg')
    return {
        'salon': salon,
        'tenant': salon,
        'salon_logo_url': logo_url,
        'tenant_logo_url': logo_url,
    }


@app.route('/uploads/logo/<path:filename>')
def salon_logo(filename):
    """Serve a salon's uploaded logo from its writable installation data."""
    return send_from_directory(LOGO_UPLOAD_DIR, filename)


@app.errorhandler(RequestEntityTooLarge)
def handle_feedback_upload_too_large(error):
    """Keep oversized feedback uploads on the form with a useful message."""
    all_services = get_all_services_flat()
    return render_template(
        'feedback.html',
        all_services=all_services,
        errors=['That file is too large. Please choose a photo or video up to 25 MB.'],
        form=request.form,
    ), 413


def init_db():
    with app.app_context():
        db = get_db()

        # ── One local salon profile (one database = one customer) ────────────
        db.execute('''CREATE TABLE IF NOT EXISTS salon_settings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            tagline       TEXT    DEFAULT '',
            logo_filename TEXT    DEFAULT 'brand-logo.jpeg',
            primary_color TEXT    DEFAULT '#c9a96e',
            accent_color  TEXT    DEFAULT '#d4af37',
            bg_color      TEXT    DEFAULT '#0a0a0a',
            phone         TEXT,
            email         TEXT,
            address       TEXT,
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        # ── Core tables ──────────────────────────────────────────────────────
        db.execute('''CREATE TABLE IF NOT EXISTS appointments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            phone       TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            service     TEXT    NOT NULL,
            date        TEXT    NOT NULL,
            time        TEXT    NOT NULL,
            email       TEXT,
            customer_id INTEGER,
            branch_id   INTEGER DEFAULT 1,
            status      TEXT    DEFAULT 'confirmed',
            booking_token TEXT,
            created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS feedback (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT    NOT NULL,
            service       TEXT    NOT NULL,
            rating        INTEGER NOT NULL,
            comment       TEXT,
            date          TEXT    NOT NULL,
            media_path    TEXT,
            media_type    TEXT,
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')
        # Add upload columns to databases created before feedback media existed.
        feedback_columns = {
            row['name'] for row in db.execute('PRAGMA table_info(feedback)').fetchall()
        }
        if 'media_path' not in feedback_columns:
            db.execute('ALTER TABLE feedback ADD COLUMN media_path TEXT')
        if 'media_type' not in feedback_columns:
            db.execute('ALTER TABLE feedback ADD COLUMN media_type TEXT')

        # ── ERP tables ───────────────────────────────────────────────────────
        db.execute('''CREATE TABLE IF NOT EXISTS branches (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            address      TEXT,
            phone        TEXT,
            manager_name TEXT,
            is_active    INTEGER DEFAULT 1,
            created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS customers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            phone           TEXT    UNIQUE NOT NULL,
            email           TEXT,
            birthday        TEXT,
            notes           TEXT,
             loyalty_points  INTEGER DEFAULT 0,
             loyalty_points_expiry_date TEXT,
            visit_count     INTEGER DEFAULT 0,
            total_spend     REAL    DEFAULT 0,
            last_visit_date TEXT,
            branch_id       INTEGER DEFAULT 1,
            created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS service_categories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            icon       TEXT    DEFAULT 'fa-spa',
            sort_order INTEGER DEFAULT 0,
            is_active  INTEGER DEFAULT 1
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS services (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id      INTEGER NOT NULL,
            name             TEXT    NOT NULL,
            price            REAL    DEFAULT 0,
            duration_minutes INTEGER DEFAULT 60,
            description      TEXT,
            is_active        INTEGER DEFAULT 1,
            sort_order       INTEGER DEFAULT 0,
            FOREIGN KEY (category_id) REFERENCES service_categories(id)
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS staff (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id      INTEGER DEFAULT 1,
            name           TEXT    NOT NULL,
            phone          TEXT,
            role           TEXT    DEFAULT 'Stylist',
            commission_pct REAL    DEFAULT 0,
            is_active      INTEGER DEFAULT 1,
            created_at     TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS products (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id       INTEGER DEFAULT 1,
            name            TEXT    NOT NULL,
            category        TEXT,
            unit            TEXT    DEFAULT 'piece',
            cost_price      REAL    DEFAULT 0,
            sale_price      REAL    DEFAULT 0,
            stock_qty       INTEGER DEFAULT 0,
            alert_threshold INTEGER DEFAULT 5,
            is_active       INTEGER DEFAULT 1,
            created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS invoices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id     INTEGER,
            branch_id       INTEGER DEFAULT 1,
            staff_id        INTEGER,
            customer_name   TEXT,
            customer_phone  TEXT,
            subtotal        REAL    DEFAULT 0,
            discount_type   TEXT    DEFAULT 'flat',
            discount_value  REAL    DEFAULT 0,
            discount_amount REAL    DEFAULT 0,
            gst_pct         REAL    DEFAULT 0,
            gst_amount      REAL    DEFAULT 0,
            total           REAL    DEFAULT 0,
            payment_method  TEXT    DEFAULT 'cash',
            status          TEXT    DEFAULT 'paid',
            notes           TEXT,
            created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS invoice_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id  INTEGER NOT NULL,
            item_type   TEXT    NOT NULL,
            item_name   TEXT    NOT NULL,
            unit_price  REAL    DEFAULT 0,
            qty         INTEGER DEFAULT 1,
            line_total  REAL    DEFAULT 0,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id)
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS staff_schedules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id    INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            is_working  INTEGER DEFAULT 1,
            FOREIGN KEY (staff_id) REFERENCES staff(id),
            UNIQUE(staff_id, day_of_week)
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS membership_plans (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            price         REAL    DEFAULT 0,
            validity_days INTEGER DEFAULT 30,
            discount_pct  REAL    DEFAULT 0,
            description   TEXT,
            is_active     INTEGER DEFAULT 1,
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS customer_memberships (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            plan_id     INTEGER NOT NULL,
            start_date  TEXT    NOT NULL,
            expiry_date TEXT    NOT NULL,
            status      TEXT    DEFAULT 'active',
            created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (plan_id) REFERENCES membership_plans(id)
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS campaigns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id       INTEGER DEFAULT 1,
            name            TEXT    NOT NULL,
            message_body    TEXT    NOT NULL,
            segment_type    TEXT    NOT NULL DEFAULT 'all',
            segment_params  TEXT    DEFAULT '{}',
            status          TEXT    DEFAULT 'sent',
            sent_count      INTEGER DEFAULT 0,
            created_at      TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS campaign_recipients (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            status      TEXT    DEFAULT 'sent',
            sent_at     TEXT    DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS follow_up_rules (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id        INTEGER DEFAULT 1,
            name             TEXT    NOT NULL,
            trigger_type     TEXT    NOT NULL,
            trigger_value    INTEGER DEFAULT 30,
            message_template TEXT    NOT NULL,
            is_active        INTEGER DEFAULT 1,
            last_run_date    TEXT,
            created_at       TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS automation_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date      TEXT    NOT NULL UNIQUE,
            rules_fired   INTEGER DEFAULT 0,
            messages_sent INTEGER DEFAULT 0,
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.execute('''CREATE TABLE IF NOT EXISTS admin_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            full_name     TEXT    NOT NULL DEFAULT '',
            role          TEXT    NOT NULL DEFAULT 'receptionist',
            is_active     INTEGER DEFAULT 1,
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP,
            last_login    TEXT
        )''')

        # Tracks one-time installation migrations that must not be repeated.
        db.execute('''CREATE TABLE IF NOT EXISTS app_metadata (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''')

        # ── Per-tenant messaging credentials (encrypted at rest) ─────────────
        db.execute('''CREATE TABLE IF NOT EXISTS messaging_credentials (
            id                    INTEGER PRIMARY KEY DEFAULT 1,
            gmail_user            TEXT    DEFAULT '',
            gmail_password        TEXT    DEFAULT '',
            twilio_sid            TEXT    DEFAULT '',
            twilio_token          TEXT    DEFAULT '',
            twilio_number         TEXT    DEFAULT '',
            whatsapp_token        TEXT    DEFAULT '',
            whatsapp_phone_id     TEXT    DEFAULT '',
            whatsapp_otp_template TEXT    DEFAULT 'vanshika_otp',
            fast2sms_key          TEXT    DEFAULT '',
            updated_at            TEXT    DEFAULT CURRENT_TIMESTAMP
        )''')

        db.commit()
        _migrate_db(db)
        _seed_salon_settings(db)
        _seed_default_branch(db)
        _seed_services(db)
        _backfill_customers(db)
        _seed_admin_users(db)
        _apply_configured_bootstrap_password(db)


def _migrate_db(db):
    """Idempotently add new columns to existing tables."""
    migrations = [
        ('appointments',   'email',            'TEXT'),
        ('appointments',   'customer_id',      'INTEGER'),
        ('appointments',   'branch_id',        'INTEGER DEFAULT 1'),
        ('appointments',   'status',           "TEXT DEFAULT 'confirmed'"),
        ('appointments',   'staff_id',         'INTEGER'),
        ('invoice_items',  'service_id',       'INTEGER'),
        ('invoice_items',  'product_id',       'INTEGER'),
        ('staff',          'portal_pin',       'TEXT'),
        ('invoices',       'points_redeemed',  'REAL DEFAULT 0'),
         ('customers',      'points_reserved',  'INTEGER DEFAULT 0'),
         ('customers',      'loyalty_points_expiry_date', 'TEXT'),
        ('appointments',   'booking_token',     'TEXT'),
    ]
    for table, col, defn in migrations:
        try:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {defn}')
            db.commit()
        except Exception:
            pass

    db.execute(
        '''CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_booking_token
           ON appointments(booking_token)
           WHERE booking_token IS NOT NULL'''
    )

    # Give existing balances a transition window under the new policy.
    # New earning events replace this with their own 60-day expiry date.
    db.execute(
        '''UPDATE customers
           SET loyalty_points_expiry_date = DATE('now', '+60 days')
           WHERE COALESCE(loyalty_points, 0) > 0
             AND loyalty_points_expiry_date IS NULL'''
    )

    # Back-fill services that were seeded with price=0 using the authoritative
    # SERVICE_PRICE_MAP.  This runs on every startup but is a no-op once prices
    # are already set, so it is safe to repeat.
    for svc_name, price in SERVICE_PRICE_MAP.items():
        db.execute(
            'UPDATE services SET price=? WHERE name=? AND price=0',
            (price, svc_name)
        )
    db.commit()


def _seed_salon_settings(db):
    """Create the one local salon profile, migrating an older white-label DB."""
    columns = {row['name'] for row in db.execute('PRAGMA table_info(salon_settings)').fetchall()}
    if not columns:
        return
    existing = db.execute('SELECT id FROM salon_settings WHERE id=1').fetchone()
    if not existing:
        legacy = None
        try:
            legacy = db.execute('SELECT * FROM tenants ORDER BY id LIMIT 1').fetchone()
        except sqlite3.OperationalError:
            pass
        values = (
            legacy['name'] if legacy else 'Vanshika Makeover Academy',
            legacy['tagline'] if legacy else 'Makeover Academy',
            legacy['logo_filename'] if legacy else 'brand-logo.jpeg',
            legacy['primary_color'] if legacy else '#c9a96e',
            legacy['accent_color'] if legacy else '#d4af37',
            legacy['bg_color'] if legacy else '#0a0a0a',
            legacy['phone'] if legacy else '+91 98992 23426',
            legacy['email'] if legacy else 'owner@vanshika.com',
            legacy['address'] if legacy else 'Main Branch',
        )
        db.execute('''INSERT INTO salon_settings
                      (id, name, tagline, logo_filename, primary_color,
                       accent_color, bg_color, phone, email, address)
                      VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', values)
    # A legacy white-label database may have stored the primary branch and
    # owner under tenant_id=1. Those columns are harmless legacy data; the
    # single-salon app no longer reads or writes them.
    db.commit()
    db.commit()


def _seed_default_branch(db):
    cnt = db.execute('SELECT COUNT(*) as cnt FROM branches').fetchone()['cnt']
    if cnt == 0:
        db.execute('''INSERT INTO branches (name, address, phone, manager_name)
                      VALUES (?, ?, ?, ?)''',
                   ('Vanshika Makeover Academy', 'Main Branch',
                    '+91 98992 23426', 'Vanshika'))
        db.commit()


def _seed_services(db):
    cnt = db.execute('SELECT COUNT(*) as cnt FROM service_categories').fetchone()['cnt']
    if cnt > 0:
        return
    icons = {
        'Bridal & Professional Makeup': 'fa-ring',
        'Skincare & Advanced Facials':  'fa-face-smile-beam',
        'Hair Styling & Treatments':    'fa-scissors',
        'Nail Studio':                  'fa-hand-sparkles',
        'Hair Removal & Grooming':      'fa-wand-magic-sparkles',
    }
    for i, (cat, svcs) in enumerate(SERVICES_SEED.items()):
        icon = icons.get(cat, 'fa-spa')
        cur = db.execute(
            'INSERT INTO service_categories (name, icon, sort_order) VALUES (?,?,?)',
            (cat, icon, i)
        )
        cat_id = cur.lastrowid
        for j, svc in enumerate(svcs):
            price = SERVICE_PRICE_MAP.get(svc, 0)
            db.execute(
                'INSERT INTO services (category_id, name, price, sort_order) VALUES (?,?,?,?)',
                (cat_id, svc, price, j)
            )
    db.commit()


def _seed_admin_users(db):
    """Create a default owner account if no admin users exist yet.

    Password priority:
      1. ADMIN_BOOTSTRAP_PASSWORD env var — set this before first launch for a known credential.
      2. Randomly generated — printed once to the server log so the operator can retrieve it.

    Also creates the platform super-admin account if SUPERADMIN_EMAIL is set.
    """
    cnt = db.execute('SELECT COUNT(*) as cnt FROM admin_users').fetchone()['cnt']
    if cnt == 0:
        bootstrap_pwd = os.environ.get('ADMIN_BOOTSTRAP_PASSWORD', '').strip()
        if not bootstrap_pwd:
            import secrets, string
            alphabet = string.ascii_letters + string.digits + '!@#$%^&*'
            bootstrap_pwd = ''.join(secrets.choice(alphabet) for _ in range(16))
            app.logger.warning(
                '\n' + '='*60 +
                f'\n  ADMIN BOOTSTRAP PASSWORD (first-run only)\n'
                f'  Email:    owner@vanshika.com\n'
                f'  Password: {bootstrap_pwd}\n'
                f'  Set ADMIN_BOOTSTRAP_PASSWORD env var to choose your own.\n' +
                '='*60
            )
        db.execute('''INSERT INTO admin_users (email, password_hash, full_name, role)
                      VALUES (?, ?, ?, ?)''',
                   ('owner@vanshika.com',
                    generate_password_hash(bootstrap_pwd),
                    'Vanshika (Owner)',
                    'owner'))
        db.commit()


def _apply_configured_bootstrap_password(db):
    """Apply a configured bootstrap password once to the existing owner.

    Fresh databases already use ADMIN_BOOTSTRAP_PASSWORD in
    ``_seed_admin_users``. This second step supports an installation that was
    created before the secret was configured, without resetting the owner
    password on every subsequent restart.
    """
    bootstrap_pwd = os.environ.get('ADMIN_BOOTSTRAP_PASSWORD', '').strip()
    if not bootstrap_pwd:
        return

    applied = db.execute(
        "SELECT value FROM app_metadata WHERE key=?",
        ('bootstrap_password_applied',),
    ).fetchone()
    if applied:
        return

    owner = db.execute(
        "SELECT id FROM admin_users WHERE role='owner' ORDER BY id LIMIT 1"
    ).fetchone()
    if not owner:
        return

    db.execute(
        "UPDATE admin_users SET password_hash=? WHERE id=?",
        (generate_password_hash(bootstrap_pwd), owner['id']),
    )
    db.execute(
        "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
        ('bootstrap_password_applied', '1'),
    )
    db.commit()
    app.logger.info('Configured bootstrap password applied to the local owner account.')


def _backfill_customers(db):
    """Create customer records from existing appointments where missing."""
    rows = db.execute('''
        SELECT phone, name, email,
               MAX(date) as last_visit,
               COUNT(*) as visit_count
        FROM appointments
        WHERE customer_id IS NULL
        GROUP BY phone
    ''').fetchall()
    for row in rows:
        existing = db.execute(
            'SELECT id FROM customers WHERE phone=?', (row['phone'],)
        ).fetchone()
        if existing:
            cust_id = existing['id']
        else:
            cur = db.execute('''
                INSERT INTO customers
                    (name, phone, email, visit_count, last_visit_date, branch_id)
                VALUES (?,?,?,?,?,1)
            ''', (row['name'], row['phone'], row['email'],
                  row['visit_count'], row['last_visit']))
            cust_id = cur.lastrowid
        db.execute(
            '''UPDATE appointments SET customer_id=?, branch_id=COALESCE(branch_id,1)
               WHERE phone=? AND customer_id IS NULL''',
            (cust_id, row['phone'])
        )
    db.commit()


# ─── Service helpers ─────────────────────────────────────────────────────────

def get_services_dict():
    """Returns {category_name: [service_names]} for booking form."""
    db = get_db()
    cats = db.execute(
        'SELECT id, name FROM service_categories WHERE is_active=1 ORDER BY sort_order'
    ).fetchall()
    result = {}
    for cat in cats:
        svcs = db.execute(
            'SELECT name FROM services WHERE category_id=? AND is_active=1 ORDER BY sort_order',
            (cat['id'],)
        ).fetchall()
        result[cat['name']] = [s['name'] for s in svcs]
    return result


def get_all_services_flat():
    db = get_db()
    return [r['name'] for r in db.execute(
        'SELECT name FROM services WHERE is_active=1 ORDER BY sort_order'
    ).fetchall()]


def award_invoice_loyalty_points(db, customer_id, total):
    """Credit 50 points per completed ₹1,000, within the invoice transaction."""
    if not customer_id:
        return 0
    points = max(
        0,
        int(float(total) // LOYALTY_SPEND_BLOCK) * LOYALTY_POINTS_PER_BLOCK
    )
    if points:
        expiry_date = (
            datetime.now() + timedelta(days=LOYALTY_EXPIRY_DAYS)
        ).strftime('%Y-%m-%d')
        db.execute(
            '''UPDATE customers
               SET loyalty_points = COALESCE(loyalty_points, 0) + ?,
                   loyalty_points_expiry_date = ?
               WHERE id=?''',
            (points, expiry_date, customer_id)
        )
    return points


def expire_customer_loyalty(db, customer_id):
    """Clear an expired balance before it is displayed, reserved, or redeemed."""
    if not customer_id:
        return False
    row = db.execute(
        '''SELECT loyalty_points, points_reserved, loyalty_points_expiry_date
           FROM customers WHERE id=?''',
        (customer_id,)
    ).fetchone()
    if not row or not row['loyalty_points_expiry_date']:
        return False
    if row['loyalty_points_expiry_date'] < datetime.now().strftime('%Y-%m-%d'):
        if (row['loyalty_points'] or 0) or (row['points_reserved'] or 0):
            db.execute(
                '''UPDATE customers
                   SET loyalty_points=0, points_reserved=0,
                       loyalty_points_expiry_date=NULL
                   WHERE id=?''',
                (customer_id,)
            )
            return True
    return False


def expire_all_customer_loyalty(db):
    """Expire every stale balance before staff/admin views customer balances."""
    db.execute(
        '''UPDATE customers
           SET loyalty_points=0, points_reserved=0,
               loyalty_points_expiry_date=NULL
           WHERE loyalty_points_expiry_date IS NOT NULL
             AND loyalty_points_expiry_date < DATE('now')
             AND (COALESCE(loyalty_points, 0) > 0
                  OR COALESCE(points_reserved, 0) > 0)'''
    )


# ─── Admin helpers ───────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_user_id'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    """Decorator: user must be logged in AND have one of the given roles."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('admin_user_id'):
                return redirect(url_for('admin_login'))
            if session.get('admin_user_role') not in roles:
                flash('You do not have permission to access this page.', 'error')
                return redirect(url_for('admin_dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator


owner_required   = role_required('owner')
manager_required = role_required('owner', 'manager')


def get_current_branch_id():
    return session.get('admin_branch_id', 1)


def get_admin_context():
    db = get_db()
    branches = db.execute(
        'SELECT * FROM branches WHERE is_active=1 ORDER BY id'
    ).fetchall()
    bid = get_current_branch_id()
    current_branch = db.execute(
        'SELECT * FROM branches WHERE id=? AND is_active=1', (bid,)
    ).fetchone()
    if not current_branch and branches:
        bid = branches[0]['id']
        session['admin_branch_id'] = bid
        current_branch = branches[0]
    low_stock_count = db.execute(
        '''SELECT COUNT(*) as cnt FROM products
           WHERE stock_qty <= alert_threshold AND is_active=1 AND branch_id=?''',
        (bid,)
    ).fetchone()['cnt']
    return dict(
        branches=branches,
        current_branch_id=bid,
        current_branch=current_branch,
        low_stock_count=low_stock_count,
        admin_user_role=session.get('admin_user_role', ''),
        admin_user_name=session.get('admin_user_name', ''),
        admin_user_email=session.get('admin_user_email', ''),
    )


# ─── OTP helpers ─────────────────────────────────────────────────────────────

def generate_otp():
    return str(random.randint(100000, 999999))


def mask_phone(phone):
    """Mask a 10-digit mobile number while keeping the last four digits visible."""
    digits = ''.join(ch for ch in str(phone) if ch.isdigit())
    return ('*' * max(len(digits) - 4, 0)) + digits[-4:]


def mask_email(email):
    """Mask the local part of an email address while keeping its domain visible."""
    email = str(email or '')
    if '@' not in email:
        return email
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        masked_local = local[:1] + ('*' if len(local) == 2 else '')
    else:
        masked_local = local[:2] + '*' * max(len(local) - 2, 2)
    return f'{masked_local}@{domain}'


def seconds_left():
    pb = session.get('pending_booking', {})
    return max(0, int(pb.get('expires_at', 0) - time.time())) if pb else 0


# ─── Customer-facing routes ──────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', services=get_services_dict())


@app.route('/book', methods=['GET', 'POST'])
def book():
    services = get_services_dict()
    if request.method == 'POST':
        name      = request.form.get('name', '').strip()
        phone     = request.form.get('phone', '').strip()
        email     = request.form.get('email', '').strip()
        category  = request.form.get('category', '').strip()
        service   = request.form.get('service', '').strip()
        date      = request.form.get('date', '').strip()
        appt_time = request.form.get('time', '').strip()

        errors = []
        if not name:    errors.append('Name is required.')
        if not phone:   errors.append('Phone number is required.')
        elif not (phone.isdigit() and len(phone) == 10):
            errors.append('Enter a valid 10-digit mobile number.')
        if not email:
            errors.append('Email address is required for booking verification.')
        elif '@' not in email or '.' not in email.split('@')[-1]:
            errors.append('Enter a valid email address.')
        if not category: errors.append('Please select a service category.')
        if not service:  errors.append('Please select a service.')
        if not date:     errors.append('Date is required.')
        if not appt_time: errors.append('Time slot is required.')

        if errors:
            return render_template('book.html', services=services,
                                   time_slots=TIME_SLOTS, errors=errors,
                                   form=request.form)

        # A double-click or browser retry should reuse the already-sent OTP
        # instead of creating another email for the same booking form.
        existing_pending = session.get('pending_booking')
        booking_fields = {
            'name': name, 'phone': phone, 'email': email,
            'category': category, 'service': service,
            'date': date, 'time': appt_time,
        }
        if (
            existing_pending
            and existing_pending.get('expires_at', 0) > time.time()
            and all(existing_pending.get(key) == value for key, value in booking_fields.items())
        ):
            return redirect(url_for('verify_otp'))

        otp = generate_otp()
        try:
            from notifications import send_otp_email
            email_sent = send_otp_email(email, otp, name)
        except Exception as e:
            app.logger.error(f'Booking email OTP error: {e}')
            email_sent = False
        if not email_sent:
            _salon = getattr(g, 'salon', None)
            _phone = _salon['phone'] if _salon and _salon['phone'] else ''
            if _phone:
                errors.append(
                    f"We couldn't send your verification OTP — please call us at {_phone} to book directly."
                )
            else:
                errors.append(
                    "We couldn't send your verification OTP. Please try again or call the salon to book directly."
                )
            return render_template('book.html', services=services,
                                   time_slots=TIME_SLOTS, errors=errors,
                                   form=request.form)

        session['pending_booking'] = {
            'booking_token': uuid.uuid4().hex,
            'name': name, 'phone': phone, 'email': email,
            'category': category, 'service': service,
            'date': date, 'time': appt_time,
            'otp': otp,
            'expires_at':    time.time() + OTP_EXPIRY_SECONDS,
            'attempts_left': OTP_MAX_ATTEMPTS,
            'last_resend':   time.time(),
        }
        return redirect(url_for('verify_otp'))

    # Pre-fill from query params (e.g. "Book Again" from customer portal)
    prefill = {
        'name':     request.args.get('name', ''),
        'phone':    request.args.get('phone', ''),
        'email':    request.args.get('email', ''),
        'category': request.args.get('category', ''),
        'service':  request.args.get('service', ''),
    }
    return render_template('book.html', services=services,
                           time_slots=TIME_SLOTS, errors=[], form=prefill)


@app.route('/book/verify', methods=['GET', 'POST'])
def verify_otp():
    pb = session.get('pending_booking')
    if not pb:
        flash('Session expired. Please fill the booking form again.', 'error')
        return redirect(url_for('book'))

    secs = seconds_left()
    if secs <= 0:
        session.pop('pending_booking', None)
        flash('OTP expired. Please start a new booking.', 'error')
        return redirect(url_for('book'))

    masked_email = mask_email(pb['email'])
    resend_blocked = (time.time() - pb.get('last_resend', 0)) < OTP_RESEND_COOLDOWN

    if request.method == 'POST':
        entered = request.form.get('otp', '').strip()

        if entered != pb['otp']:
            pb['attempts_left'] -= 1
            session['pending_booking'] = pb
            session.modified = True
            if pb['attempts_left'] <= 0:
                session.pop('pending_booking', None)
                flash('Too many wrong attempts. Please start a new booking.', 'error')
                return redirect(url_for('book'))
            return render_template('otp_verify.html',
                                   masked_email=masked_email, expire_seconds=secs,
                                   attempts_left=pb['attempts_left'],
                                   resend_blocked=resend_blocked,
                                   error='Incorrect OTP. Please try again.')

        # ✅ OTP verified — upsert customer, save appointment
        db = get_db()
        booking_token = pb.get('booking_token')
        if booking_token:
            already_saved = db.execute(
                'SELECT id FROM appointments WHERE booking_token=?',
                (booking_token,)
            ).fetchone()
            if already_saved:
                session.pop('pending_booking', None)
                return render_template(
                    'book_success.html',
                    booking_id=already_saved['id'], name=pb['name'],
                    phone=pb['phone'], service=pb['service'],
                    date=pb['date'], time=pb['time']
                )

        default_branch = db.execute(
            'SELECT id FROM branches WHERE is_active=1 ORDER BY id LIMIT 1'
        ).fetchone()
        pub_branch_id = default_branch['id'] if default_branch else 1

        existing = db.execute(
            'SELECT id FROM customers WHERE phone=? AND branch_id=?',
            (pb['phone'], pub_branch_id)
        ).fetchone()
        if existing:
            cust_id = existing['id']
            db.execute('''UPDATE customers SET
                visit_count = visit_count + 1,
                last_visit_date = ?,
                email = COALESCE(NULLIF(email,''), ?)
                WHERE id=?''',
                (pb['date'], pb['email'], cust_id))
        else:
            cur = db.execute('''
                INSERT INTO customers
                    (name, phone, email, visit_count, last_visit_date, branch_id)
                VALUES (?,?,?,1,?,?)
            ''', (pb['name'], pb['phone'], pb['email'], pb['date'], pub_branch_id))
            cust_id = cur.lastrowid

        cursor = db.execute('''
            INSERT INTO appointments
                (name, phone, email, category, service, date, time,
                 customer_id, branch_id, booking_token)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        ''', (pb['name'], pb['phone'], pb['email'],
              pb['category'], pb['service'], pb['date'], pb['time'],
              cust_id, pub_branch_id, booking_token))
        db.commit()
        booking_id = cursor.lastrowid
        session.pop('pending_booking', None)

        if pb.get('email'):
            try:
                from notifications import send_booking_confirmation_email
                send_booking_confirmation_email(
                    pb['email'], pb['name'], pb['phone'],
                    pb['service'], pb['date'], pb['time'], booking_id
                )
            except Exception as e:
                app.logger.warning(f'Confirmation email error: {e}')
        try:
            from notifications import notify_new_booking
            # Do not send the salon's owner alert to the same inbox that
            # received the customer's OTP/confirmation.
            from notifications import OWNER_EMAIL
            if pb.get('email', '').strip().lower() != OWNER_EMAIL.lower():
                notify_new_booking(pb['name'], pb['phone'], pb['service'],
                                   pb['date'], pb['time'], pb['email'])
        except Exception as e:
            app.logger.warning(f'Owner notification error: {e}')

        return render_template('book_success.html',
                               booking_id=booking_id, name=pb['name'],
                               phone=pb['phone'], service=pb['service'],
                               date=pb['date'], time=pb['time'])

    return render_template('otp_verify.html',
                           masked_email=masked_email, expire_seconds=secs,
                           attempts_left=pb['attempts_left'],
                           resend_blocked=resend_blocked, error=None)


@app.route('/book/resend-otp', methods=['POST'])
def resend_otp():
    pb = session.get('pending_booking')
    if not pb:
        flash('Session expired.', 'error')
        return redirect(url_for('book'))
    if seconds_left() <= 0:
        session.pop('pending_booking', None)
        flash('OTP expired. Please start a new booking.', 'error')
        return redirect(url_for('book'))
    cooldown_remaining = OTP_RESEND_COOLDOWN - (time.time() - pb.get('last_resend', 0))
    if cooldown_remaining > 0:
        return redirect(url_for('verify_otp'))
    new_otp = generate_otp()
    pb['otp']           = new_otp
    pb['expires_at']    = time.time() + OTP_EXPIRY_SECONDS
    pb['last_resend']   = time.time()
    pb['attempts_left'] = OTP_MAX_ATTEMPTS
    session['pending_booking'] = pb
    session.modified = True
    try:
        from notifications import send_otp_email
        email_sent = send_otp_email(pb['email'], new_otp, pb['name'])
    except Exception as e:
        app.logger.error(f'Booking email OTP resend error: {e}')
        email_sent = False
    if not email_sent:
        flash('We could not resend the verification email. Please try again.', 'error')
    else:
        flash('A new OTP has been sent to your email address.', 'success')
    return redirect(url_for('verify_otp'))


@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    all_services = get_all_services_flat()
    if request.method == 'POST':
        customer_name = request.form.get('customer_name', '').strip()
        service       = request.form.get('service', '').strip()
        rating        = request.form.get('rating', '').strip()
        comment       = request.form.get('comment', '').strip()
        media = request.files.get('media')
        date          = datetime.now().strftime('%Y-%m-%d')
        errors = []
        if not customer_name: errors.append('Your name is required.')
        if not service:       errors.append('Please select a service.')
        if not rating:        errors.append('Please choose a rating.')
        elif not rating.isdigit() or not (1 <= int(rating) <= 5):
            errors.append('Rating must be between 1 and 5.')
        media_filename = None
        media_type = None
        if media and media.filename:
            original_name = secure_filename(media.filename)
            extension = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''
            media_type = FEEDBACK_ALLOWED_EXTENSIONS.get(extension)
            if not media_type:
                errors.append('Upload a JPG, PNG, WEBP, GIF, MP4, MOV, or WEBM file.')
            else:
                media_filename = f'{uuid.uuid4().hex}.{extension}'
        if errors:
            return render_template('feedback.html', all_services=all_services,
                                   errors=errors, form=request.form)
        if media_filename:
            os.makedirs(FEEDBACK_UPLOAD_DIR, exist_ok=True)
            media.save(os.path.join(FEEDBACK_UPLOAD_DIR, media_filename))
        db = get_db()
        try:
            db.execute(
                '''INSERT INTO feedback
                   (customer_name, service, rating, comment, date, media_path, media_type)
                   VALUES (?,?,?,?,?,?,?)''',
                (customer_name, service, int(rating), comment, date,
                 media_filename, media_type)
            )
            db.commit()
        except Exception:
            db.rollback()
            if media_filename:
                try:
                    os.remove(os.path.join(FEEDBACK_UPLOAD_DIR, media_filename))
                except OSError:
                    pass
            raise
        return render_template('feedback_success.html',
                               customer_name=customer_name, rating=int(rating))
    return render_template('feedback.html', all_services=all_services,
                           errors=[], form={})


@app.route('/uploads/feedback/<path:filename>')
def feedback_upload(filename):
    """Serve feedback media by its generated, non-user-controlled filename."""
    return send_from_directory(FEEDBACK_UPLOAD_DIR, filename)


# ─── Admin: auth ─────────────────────────────────────────────────────────────

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_user_id'):
        return redirect(url_for('admin_dashboard'))
    error = None
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute(
            'SELECT * FROM admin_users WHERE email=? AND is_active=1', (email,)
        ).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            first_branch = db.execute(
                'SELECT id FROM branches WHERE is_active=1 ORDER BY id LIMIT 1'
            ).fetchone()
            # All checks passed — now commit to session
            db.execute('UPDATE admin_users SET last_login=? WHERE id=?',
                       (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user['id']))
            db.commit()
            session['admin_user_id']    = user['id']
            session['admin_user_role']  = user['role']
            session['admin_user_name']  = user['full_name']
            session['admin_user_email'] = user['email']
            session['admin_branch_id']  = first_branch['id'] if first_branch else 1
            return redirect(url_for('admin_dashboard'))
        error = 'Incorrect email or password. Please try again.'
    return render_template('admin_login.html', error=error)


@app.route('/admin/logout')
def admin_logout():
    for k in ('admin_user_id', 'admin_user_role', 'admin_user_name',
              'admin_user_email', 'admin_branch_id'):
        session.pop(k, None)
    return redirect(url_for('admin_login'))


@app.route('/admin/switch-branch/<int:branch_id>')
@admin_required
def switch_branch(branch_id):
    db        = get_db()
    branch    = db.execute(
        'SELECT id FROM branches WHERE id=? AND is_active=1',
        (branch_id,)
    ).fetchone()
    if not branch:
        flash('Branch not found.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    session['admin_branch_id'] = branch_id
    return redirect(request.referrer or url_for('admin_dashboard'))


# ─── Admin: dashboard ────────────────────────────────────────────────────────

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    db  = get_db()
    bid = get_current_branch_id()

    total_bookings   = db.execute('SELECT COUNT(*) as c FROM appointments WHERE branch_id=?', (bid,)).fetchone()['c']
    total_customers  = db.execute('SELECT COUNT(*) as c FROM customers WHERE branch_id=?', (bid,)).fetchone()['c']
    total_feedback   = db.execute('SELECT COUNT(*) as c FROM feedback').fetchone()['c']
    avg_rating_row   = db.execute('SELECT AVG(rating) as avg FROM feedback').fetchone()
    avg_rating       = round(avg_rating_row['avg'], 1) if avg_rating_row['avg'] else None

    today_str        = datetime.now().strftime('%Y-%m-%d')
    today_bookings   = db.execute(
        'SELECT COUNT(*) as c FROM appointments WHERE branch_id=? AND date=?', (bid, today_str)
    ).fetchone()['c']
    new_customers_month = db.execute(
        "SELECT COUNT(*) as c FROM customers WHERE branch_id=? AND created_at >= date('now','start of month')", (bid,)
    ).fetchone()['c']

    # Revenue stats from invoices
    rev_today = db.execute(
        "SELECT COALESCE(SUM(total),0) as r FROM invoices WHERE branch_id=? AND status='paid' AND DATE(created_at)=?",
        (bid, today_str)
    ).fetchone()['r']
    rev_week = db.execute(
        "SELECT COALESCE(SUM(total),0) as r FROM invoices WHERE branch_id=? AND status='paid' AND DATE(created_at)>=DATE('now','-6 days')",
        (bid,)
    ).fetchone()['r']
    rev_month = db.execute(
        "SELECT COALESCE(SUM(total),0) as r FROM invoices WHERE branch_id=? AND status='paid' AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')",
        (bid,)
    ).fetchone()['r']
    total_invoices = db.execute(
        'SELECT COUNT(*) as c FROM invoices WHERE branch_id=?', (bid,)
    ).fetchone()['c']

    recent_appts     = db.execute(
        'SELECT * FROM appointments WHERE branch_id=? ORDER BY created_at DESC LIMIT 6', (bid,)
    ).fetchall()
    recent_customers = db.execute(
        'SELECT * FROM customers WHERE branch_id=? ORDER BY created_at DESC LIMIT 5', (bid,)
    ).fetchall()
    recent_feedback  = db.execute(
        'SELECT * FROM feedback ORDER BY created_at DESC LIMIT 4'
    ).fetchall()

    upcoming_appts   = db.execute(
        "SELECT * FROM appointments WHERE branch_id=? AND date >= ? ORDER BY date ASC, time ASC LIMIT 5",
        (bid, today_str)
    ).fetchall()

    recent_invoices = db.execute(
        '''SELECT i.*, c.name as cust_name FROM invoices i
           LEFT JOIN customers c ON c.id = i.customer_id
           WHERE i.branch_id=? ORDER BY i.created_at DESC LIMIT 4''',
        (bid,)
    ).fetchall()

    expiring_memberships = db.execute('''
        SELECT cm.id, cm.expiry_date, c.name as cust_name, mp.name as plan_name
        FROM customer_memberships cm
        JOIN customers c ON c.id = cm.customer_id
        JOIN membership_plans mp ON mp.id = cm.plan_id
        WHERE c.branch_id=? AND cm.status='active'
          AND cm.expiry_date >= ? AND cm.expiry_date <= DATE(?, '+7 days')
        ORDER BY cm.expiry_date ASC
    ''', (bid, today_str, today_str)).fetchall()

    # Check if required messaging credentials are missing
    try:
        from notifications import get_messaging_creds as _get_creds
        _creds = _get_creds()
        messaging_warning = not _creds.get('gmail_password') or not _creds.get('fast2sms_key')
    except Exception:
        messaging_warning = True

    return render_template('admin_dashboard.html',
                           total_bookings=total_bookings,
                           total_customers=total_customers,
                           total_feedback=total_feedback,
                           avg_rating=avg_rating,
                           today_bookings=today_bookings,
                           new_customers_month=new_customers_month,
                           rev_today=rev_today,
                           rev_week=rev_week,
                           rev_month=rev_month,
                           total_invoices=total_invoices,
                           recent_appts=recent_appts,
                           recent_customers=recent_customers,
                           recent_feedback=recent_feedback,
                           upcoming_appts=upcoming_appts,
                           recent_invoices=recent_invoices,
                           expiring_memberships=expiring_memberships,
                           messaging_warning=messaging_warning,
                           **get_admin_context())


# ─── Admin: appointments ─────────────────────────────────────────────────────

@app.route('/admin/appointments')
@admin_required
def admin_appointments():
    db  = get_db()
    bid = get_current_branch_id()
    q   = request.args.get('q', '').strip()
    df  = request.args.get('date_from', '')
    dt  = request.args.get('date_to', '')

    active_staff = db.execute(
        'SELECT id, name, role FROM staff WHERE branch_id=? AND is_active=1 ORDER BY name', (bid,)
    ).fetchall()

    sql  = 'SELECT a.*, s.name as staff_name FROM appointments a LEFT JOIN staff s ON s.id = a.staff_id WHERE a.branch_id=?'
    args = [bid]
    if q:
        sql  += ' AND (a.name LIKE ? OR a.phone LIKE ? OR a.service LIKE ?)'
        args += [f'%{q}%', f'%{q}%', f'%{q}%']
    if df:
        sql += ' AND a.date >= ?'; args.append(df)
    if dt:
        sql += ' AND a.date <= ?'; args.append(dt)
    sql += ' ORDER BY a.date DESC, a.time DESC'

    appointments = db.execute(sql, args).fetchall()
    calendar_appointments = [dict(appointment) for appointment in appointments]
    return render_template('admin_appointments.html',
                           appointments=appointments, q=q,
                           date_from=df, date_to=dt,
                           active_staff=active_staff,
                           calendar_appointments=calendar_appointments,
                           calendar_today=datetime.now().strftime('%Y-%m-%d'),
                           **get_admin_context())


@app.route('/admin/delete-appointment/<int:appt_id>', methods=['POST'])
@admin_required
def delete_appointment(appt_id):
    db = get_db()
    db.execute('DELETE FROM appointments WHERE id=?', (appt_id,))
    db.commit()
    flash('Appointment deleted.', 'success')
    return redirect(request.referrer or url_for('admin_appointments'))


# ─── Admin: feedback ─────────────────────────────────────────────────────────

@app.route('/admin/feedback')
@admin_required
def admin_feedback():
    db        = get_db()
    feedbacks = db.execute('SELECT * FROM feedback ORDER BY created_at DESC').fetchall()
    avg_row   = db.execute('SELECT AVG(rating) as avg FROM feedback').fetchone()
    avg_rating = round(avg_row['avg'], 1) if avg_row['avg'] else None
    return render_template('admin_feedback.html',
                           feedbacks=feedbacks, avg_rating=avg_rating,
                           **get_admin_context())


@app.route('/admin/delete-feedback/<int:feedback_id>', methods=['POST'])
@admin_required
def delete_feedback(feedback_id):
    db = get_db()
    feedback_row = db.execute(
        'SELECT media_path FROM feedback WHERE id=?', (feedback_id,)
    ).fetchone()
    db.execute('DELETE FROM feedback WHERE id=?', (feedback_id,))
    db.commit()
    if feedback_row and feedback_row['media_path']:
        try:
            os.remove(os.path.join(FEEDBACK_UPLOAD_DIR, feedback_row['media_path']))
        except OSError:
            pass
    flash('Review deleted.', 'success')
    return redirect(request.referrer or url_for('admin_feedback'))


# ─── Admin: customers ────────────────────────────────────────────────────────

@app.route('/admin/customers')
@admin_required
def admin_customers():
    db   = get_db()
    bid  = get_current_branch_id()
    expire_all_customer_loyalty(db)
    db.commit()
    q    = request.args.get('q', '').strip()
    sort = request.args.get('sort', 'recent')
    order_map = {
        'recent': 'c.created_at DESC',
        'visits': 'c.visit_count DESC',
        'points': 'c.loyalty_points DESC',
        'name':   'c.name ASC',
    }
    order_sql = order_map.get(sort, 'c.created_at DESC')

    if q:
        customers = db.execute(f'''
            SELECT c.* FROM customers c
            WHERE c.branch_id=?
              AND (c.name LIKE ? OR c.phone LIKE ? OR c.email LIKE ?)
            ORDER BY {order_sql}
        ''', (bid, f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    else:
        customers = db.execute(f'''
            SELECT c.* FROM customers c
            WHERE c.branch_id=?
            ORDER BY {order_sql}
        ''', (bid,)).fetchall()

    return render_template('admin_customers.html',
                           customers=customers, q=q, sort=sort,
                           **get_admin_context())


@app.route('/admin/customers/<int:cust_id>')
@admin_required
def admin_customer_detail(cust_id):
    db       = get_db()
    bid      = get_current_branch_id()
    customer = db.execute('SELECT * FROM customers WHERE id=? AND branch_id=?', (cust_id, bid)).fetchone()
    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin_customers'))
    appointments = db.execute(
        'SELECT * FROM appointments WHERE customer_id=? ORDER BY date DESC, time DESC',
        (cust_id,)
    ).fetchall()
    feedbacks = db.execute(
        'SELECT * FROM feedback WHERE customer_name=? ORDER BY created_at DESC',
        (customer['name'],)
    ).fetchall()
    today = datetime.now().strftime('%Y-%m-%d')
    active_membership = db.execute('''
        SELECT cm.*, mp.name as plan_name, mp.discount_pct, mp.price as plan_price
        FROM customer_memberships cm
        JOIN membership_plans mp ON mp.id = cm.plan_id
        WHERE cm.customer_id=? AND cm.status='active' AND cm.expiry_date >= ?
        ORDER BY cm.expiry_date DESC LIMIT 1
    ''', (cust_id, today)).fetchone()
    membership_plans = db.execute(
        'SELECT id, name, price FROM membership_plans WHERE is_active=1 ORDER BY price'
    ).fetchall()
    invoices = db.execute(
        'SELECT * FROM invoices WHERE customer_id=? AND branch_id=? ORDER BY created_at DESC LIMIT 10',
        (cust_id, bid)
    ).fetchall()
    return render_template('admin_customer_detail.html',
                           customer=customer,
                           appointments=appointments,
                           feedbacks=feedbacks,
                           active_membership=active_membership,
                           membership_plans=membership_plans,
                           invoices=invoices,
                           **get_admin_context())


@app.route('/admin/customers/<int:cust_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_customer_edit(cust_id):
    db       = get_db()
    customer = db.execute('SELECT * FROM customers WHERE id=?', (cust_id,)).fetchone()
    if not customer:
        flash('Customer not found.', 'error')
        return redirect(url_for('admin_customers'))
    if request.method == 'POST':
        try:
            loyalty_points = max(0, int(request.form.get('loyalty_points', 0) or 0))
        except (ValueError, TypeError):
            loyalty_points = 0
        loyalty_expiry = (
            (datetime.now() + timedelta(days=LOYALTY_EXPIRY_DAYS)).strftime('%Y-%m-%d')
            if loyalty_points else None
        )
        db.execute('''UPDATE customers
                      SET name=?, email=?, birthday=?, notes=?, loyalty_points=?,
                          loyalty_points_expiry_date=?
                      WHERE id=?''',
                   (request.form.get('name', '').strip(),
                    request.form.get('email', '').strip(),
                    request.form.get('birthday', '').strip(),
                    request.form.get('notes', '').strip(),
                    loyalty_points, loyalty_expiry,
                    cust_id))
        db.commit()
        flash('Customer updated successfully.', 'success')
        return redirect(url_for('admin_customer_detail', cust_id=cust_id))
    return render_template('admin_customer_edit.html',
                           customer=customer, **get_admin_context())


# ─── Admin: branches ─────────────────────────────────────────────────────────

@app.route('/admin/branches')
@manager_required
def admin_branches():
    db = get_db()
    all_branches = db.execute('''
        SELECT b.*,
               COUNT(DISTINCT a.id) as appt_count,
               COUNT(DISTINCT c.id) as cust_count
        FROM branches b
        LEFT JOIN appointments a ON a.branch_id = b.id
        LEFT JOIN customers    c ON c.branch_id = b.id
        GROUP BY b.id
        ORDER BY b.id
    ''').fetchall()
    return render_template('admin_branches.html',
                           all_branches=all_branches, **get_admin_context())


@app.route('/admin/branches/add', methods=['GET', 'POST'])
@manager_required
def admin_branch_add():
    if request.method == 'POST':
        name      = request.form.get('name', '').strip()
        address   = request.form.get('address', '').strip()
        phone     = request.form.get('phone', '').strip()
        mgr       = request.form.get('manager_name', '').strip()
        if name:
            db = get_db()
            db.execute(
                'INSERT INTO branches (name, address, phone, manager_name) VALUES (?,?,?,?)',
                (name, address, phone, mgr)
            )
            db.commit()
            flash(f'Branch "{name}" added.', 'success')
            return redirect(url_for('admin_branches'))
        flash('Branch name is required.', 'error')
    return render_template('admin_branch_form.html', branch=None, **get_admin_context())


@app.route('/admin/branches/<int:branch_id>/edit', methods=['GET', 'POST'])
@manager_required
def admin_branch_edit(branch_id):
    db = get_db()
    branch    = db.execute(
        'SELECT * FROM branches WHERE id=?', (branch_id,)
    ).fetchone()
    if not branch:
        flash('Branch not found.', 'error')
        return redirect(url_for('admin_branches'))
    if request.method == 'POST':
        db.execute(
            'UPDATE branches SET name=?, address=?, phone=?, manager_name=? WHERE id=?',
            (request.form.get('name', '').strip(),
             request.form.get('address', '').strip(),
             request.form.get('phone', '').strip(),
             request.form.get('manager_name', '').strip(),
             branch_id)
        )
        db.commit()
        flash('Branch updated.', 'success')
        return redirect(url_for('admin_branches'))
    return render_template('admin_branch_form.html', branch=branch, **get_admin_context())


@app.route('/admin/branches/<int:branch_id>/delete', methods=['POST'])
@manager_required
def admin_branch_delete(branch_id):
    db = get_db()
    branch    = db.execute(
        'SELECT id FROM branches WHERE id=?', (branch_id,)
    ).fetchone()
    if not branch:
        flash('Branch not found.', 'error')
        return redirect(url_for('admin_branches'))
    # Protect the installation's primary branch
    first = db.execute('SELECT id FROM branches ORDER BY id LIMIT 1').fetchone()
    if first and first['id'] == branch_id:
        flash('Cannot deactivate the primary branch.', 'error')
        return redirect(url_for('admin_branches'))
    db.execute('UPDATE branches SET is_active=0 WHERE id=?', (branch_id,))
    db.commit()
    flash('Branch deactivated.', 'success')
    return redirect(url_for('admin_branches'))


# ─── Admin: services ─────────────────────────────────────────────────────────

@app.route('/admin/services')
@manager_required
def admin_services():
    db   = get_db()
    cats = db.execute(
        'SELECT * FROM service_categories ORDER BY sort_order, name'
    ).fetchall()
    svcs_by_cat = {}
    for cat in cats:
        svcs_by_cat[cat['id']] = db.execute(
            'SELECT * FROM services WHERE category_id=? ORDER BY sort_order, name',
            (cat['id'],)
        ).fetchall()
    return render_template('admin_services.html',
                           cats=cats, svcs_by_cat=svcs_by_cat,
                           **get_admin_context())


@app.route('/admin/services/add-category', methods=['POST'])
@manager_required
def admin_add_category():
    name = request.form.get('name', '').strip()
    icon = request.form.get('icon', 'fa-spa').strip()
    if name:
        db = get_db()
        try:
            db.execute(
                'INSERT INTO service_categories (name, icon) VALUES (?,?)', (name, icon)
            )
            db.commit()
            flash(f'Category "{name}" added.', 'success')
        except Exception:
            flash('A category with that name already exists.', 'error')
    return redirect(url_for('admin_services'))


@app.route('/admin/services/add-service', methods=['POST'])
@manager_required
def admin_add_service():
    cat_id = request.form.get('category_id', '')
    name   = request.form.get('name', '').strip()
    price  = request.form.get('price', '0').strip() or '0'
    dur    = request.form.get('duration_minutes', '60').strip() or '60'
    if cat_id and name:
        db = get_db()
        try:
            db.execute(
                'INSERT INTO services (category_id, name, price, duration_minutes) VALUES (?,?,?,?)',
                (int(cat_id), name, float(price), int(dur))
            )
            db.commit()
            flash(f'Service "{name}" added.', 'success')
        except Exception as e:
            flash(f'Error: {e}', 'error')
    return redirect(url_for('admin_services'))


@app.route('/admin/services/<int:svc_id>/edit', methods=['POST'])
@manager_required
def admin_edit_service(svc_id):
    db = get_db()
    db.execute('''UPDATE services
                  SET name=?, price=?, duration_minutes=?, is_active=?
                  WHERE id=?''',
               (request.form.get('name', '').strip(),
                float(request.form.get('price', 0) or 0),
                int(request.form.get('duration_minutes', 60) or 60),
                1 if request.form.get('is_active') else 0,
                svc_id))
    db.commit()
    flash('Service updated.', 'success')
    return redirect(url_for('admin_services'))


@app.route('/admin/services/<int:svc_id>/delete', methods=['POST'])
@manager_required
def admin_delete_service(svc_id):
    db = get_db()
    db.execute('DELETE FROM services WHERE id=?', (svc_id,))
    db.commit()
    flash('Service deleted.', 'success')
    return redirect(url_for('admin_services'))


@app.route('/admin/services/category/<int:cat_id>/delete', methods=['POST'])
@manager_required
def admin_delete_category(cat_id):
    db = get_db()
    db.execute('DELETE FROM services WHERE category_id=?', (cat_id,))
    db.execute('DELETE FROM service_categories WHERE id=?', (cat_id,))
    db.commit()
    flash('Category and all its services deleted.', 'success')
    return redirect(url_for('admin_services'))


# ─── Admin: billing ──────────────────────────────────────────────────────────

@app.route('/admin/billing')
@admin_required
def admin_billing():
    import json
    db  = get_db()
    bid = get_current_branch_id()
    q             = request.args.get('q', '').strip()
    date_from     = request.args.get('date_from', '')
    date_to       = request.args.get('date_to', '')
    status_filter = request.args.get('status', '')
    payment_filter = request.args.get('payment', '')

    sql  = '''SELECT i.*,
                     COALESCE(i.customer_name, c.name, 'Walk-in') as cust_display,
                     COUNT(ii.id) as item_count
              FROM invoices i
              LEFT JOIN customers c ON c.id = i.customer_id
              LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
              WHERE i.branch_id=?'''
    args = [bid]
    if q:
        sql  += ' AND (i.customer_name LIKE ? OR i.customer_phone LIKE ? OR c.name LIKE ?)'
        args += [f'%{q}%', f'%{q}%', f'%{q}%']
    if date_from: sql += ' AND DATE(i.created_at) >= ?'; args.append(date_from)
    if date_to:   sql += ' AND DATE(i.created_at) <= ?'; args.append(date_to)
    if status_filter:  sql += ' AND i.status=?';          args.append(status_filter)
    if payment_filter: sql += ' AND i.payment_method=?';  args.append(payment_filter)
    sql += ' GROUP BY i.id ORDER BY i.created_at DESC'

    invoices = db.execute(sql, args).fetchall()

    rev_today = db.execute(
        "SELECT COALESCE(SUM(total),0) as r FROM invoices WHERE branch_id=? AND status='paid' AND DATE(created_at)=DATE('now')",
        (bid,)
    ).fetchone()['r']
    rev_week = db.execute(
        "SELECT COALESCE(SUM(total),0) as r FROM invoices WHERE branch_id=? AND status='paid' AND DATE(created_at)>=DATE('now','-6 days')",
        (bid,)
    ).fetchone()['r']
    rev_month = db.execute(
        "SELECT COALESCE(SUM(total),0) as r FROM invoices WHERE branch_id=? AND status='paid' AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')",
        (bid,)
    ).fetchone()['r']

    return render_template('admin_billing.html',
                           invoices=invoices, q=q,
                           date_from=date_from, date_to=date_to,
                           status_filter=status_filter, payment_filter=payment_filter,
                           rev_today=rev_today, rev_week=rev_week, rev_month=rev_month,
                           **get_admin_context())


@app.route('/admin/billing/new', methods=['GET', 'POST'])
@admin_required
def admin_billing_new():
    import json
    db  = get_db()
    bid = get_current_branch_id()

    if request.method == 'POST':
        items_json     = request.form.get('items_json', '[]')
        customer_id    = request.form.get('customer_id') or None
        customer_name  = request.form.get('customer_name', '').strip()
        customer_phone = request.form.get('customer_phone', '').strip()
        payment_method = request.form.get('payment_method', 'cash')
        status         = request.form.get('status', 'paid')
        discount_type  = request.form.get('discount_type', 'flat')
        notes          = request.form.get('notes', '').strip()

        # Validate scalar fields
        if payment_method not in ('cash', 'card', 'upi'):
            payment_method = 'cash'
        if status not in ('paid', 'unpaid'):
            status = 'paid'
        if discount_type not in ('flat', 'percent'):
            discount_type = 'flat'
        try:
            discount_value = max(0.0, float(request.form.get('discount_value', 0) or 0))
            gst_pct = float(request.form.get('gst_pct', 0) or 0)
            if gst_pct not in (0, 5, 12, 18):
                gst_pct = 0
        except (ValueError, TypeError):
            discount_value, gst_pct = 0.0, 0.0

        try:
            raw_items = json.loads(items_json)
            if not isinstance(raw_items, list) or not raw_items:
                raise ValueError('empty')
        except Exception:
            flash('Please add at least one service or product.', 'error')
            return redirect(url_for('admin_billing_new'))

        # Validate every item against DB — fetch canonical name and price by ID
        validated_items = []
        for raw in raw_items:
            item_type = raw.get('type')
            item_id   = int(raw.get('item_id', 0))
            qty       = int(raw.get('qty', 1))
            if item_type not in ('service', 'product') or item_id <= 0 or qty <= 0:
                flash('Invalid item in invoice. Please re-add items and try again.', 'error')
                return redirect(url_for('admin_billing_new'))

            if item_type == 'service':
                row = db.execute(
                    'SELECT id, name, price FROM services WHERE id=? AND is_active=1',
                    (item_id,)
                ).fetchone()
                if not row:
                    flash(f'Service ID {item_id} not found.', 'error')
                    return redirect(url_for('admin_billing_new'))
                validated_items.append({
                    'type': 'service', 'service_id': row['id'], 'product_id': None,
                    'name': row['name'], 'price': float(row['price']), 'qty': qty
                })
            else:  # product — must belong to this branch
                row = db.execute(
                    'SELECT id, name, sale_price, stock_qty FROM products WHERE id=? AND branch_id=? AND is_active=1',
                    (item_id, bid)
                ).fetchone()
                if not row:
                    flash(f'Product ID {item_id} not found in this branch.', 'error')
                    return redirect(url_for('admin_billing_new'))
                validated_items.append({
                    'type': 'product', 'service_id': None, 'product_id': row['id'],
                    'name': row['name'], 'price': float(row['sale_price']), 'qty': qty,
                    'stock_qty': row['stock_qty']
                })

        # If customer_id given, pull name/phone from DB (never trust client-supplied)
        if customer_id:
            cust = db.execute(
                'SELECT name, phone FROM customers WHERE id=? AND branch_id=?',
                (customer_id, bid)
            ).fetchone()
            if cust:
                customer_name  = cust['name']
                customer_phone = cust['phone']
            else:
                customer_id = None  # customer not in this branch

        # Compute totals from validated DB prices
        subtotal = sum(i['price'] * i['qty'] for i in validated_items)
        discount_amount = (subtotal * discount_value / 100) if discount_type == 'percent' else min(discount_value, subtotal)
        after_discount  = max(0.0, subtotal - discount_amount)
        gst_amount      = after_discount * gst_pct / 100
        total           = after_discount + gst_amount

        # Loyalty points redemption — validate and cap
        points_redeemed = 0
        cust_reserved   = 0
        if customer_id:
            if expire_customer_loyalty(db, customer_id):
                db.commit()
            try:
                points_redeemed = max(0, int(request.form.get('points_redeemed', 0) or 0))
            except (ValueError, TypeError):
                points_redeemed = 0
            if points_redeemed > 0:
                cust_pts_row = db.execute(
                    'SELECT loyalty_points, points_reserved FROM customers WHERE id=?',
                    (customer_id,)
                ).fetchone()
                if cust_pts_row:
                    remaining = int(cust_pts_row['loyalty_points'] or 0)
                    cust_reserved = int(cust_pts_row['points_reserved'] or 0)
                    # Total available = remaining balance + already-deducted reservation
                    total_available = remaining + cust_reserved
                else:
                    total_available = 0
                # Can't redeem more than balance or more than invoice total (1 pt = ₹1)
                points_redeemed = min(points_redeemed, total_available, int(total))
                total = max(0.0, total - points_redeemed)

        # Validate staff_id
        staff_id = request.form.get('staff_id') or None
        if staff_id:
            ok = db.execute('SELECT id FROM staff WHERE id=? AND branch_id=? AND is_active=1',
                            (staff_id, bid)).fetchone()
            if not ok:
                staff_id = None

        cur = db.execute('''
            INSERT INTO invoices
                (customer_id, branch_id, staff_id, customer_name, customer_phone,
                 subtotal, discount_type, discount_value, discount_amount,
                 gst_pct, gst_amount, total, payment_method, status, notes, points_redeemed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (customer_id, bid, staff_id, customer_name or 'Walk-in', customer_phone,
              subtotal, discount_type, discount_value, discount_amount,
              gst_pct, gst_amount, total, payment_method, status, notes, points_redeemed))
        inv_id = cur.lastrowid

        # Insert line items + deduct stock by product ID and branch
        for item in validated_items:
            line_total = item['price'] * item['qty']
            db.execute('''INSERT INTO invoice_items
                              (invoice_id, item_type, item_name, unit_price, qty, line_total,
                               service_id, product_id)
                          VALUES (?,?,?,?,?,?,?,?)''',
                       (inv_id, item['type'], item['name'], item['price'],
                        item['qty'], line_total, item['service_id'], item['product_id']))
            if item['type'] == 'product':
                db.execute(
                    'UPDATE products SET stock_qty = MAX(0, stock_qty - ?) WHERE id=? AND branch_id=?',
                    (item['qty'], item['product_id'], bid)
                )

        # Update customer total_spend and loyalty balance
        if customer_id and status == 'paid':
            db.execute('UPDATE customers SET total_spend = total_spend + ? WHERE id=?',
                       (total, customer_id))
            award_invoice_loyalty_points(db, customer_id, total)

        # Deduct redeemed points and clear any reservation
        if points_redeemed > 0 and customer_id:
            # loyalty_points already had points_reserved deducted; restore reservation,
            # then apply the full redemption so the net change is correct.
            db.execute(
                '''UPDATE customers
                   SET loyalty_points = MAX(0, loyalty_points + points_reserved - ?),
                       points_reserved = 0
                   WHERE id=?''',
                (points_redeemed, customer_id)
            )
        elif customer_id and cust_reserved > 0:
            # Invoice created but no points redeemed — clear the reservation and refund
            db.execute(
                '''UPDATE customers
                   SET loyalty_points = loyalty_points + points_reserved,
                       points_reserved = 0
                   WHERE id=?''',
                (customer_id,)
            )

        db.commit()
        flash(f'Invoice #{inv_id} created successfully.', 'success')
        return redirect(url_for('admin_invoice_detail', inv_id=inv_id))

    # GET — load services and products for the form
    cats = db.execute(
        'SELECT id, name FROM service_categories WHERE is_active=1 ORDER BY sort_order'
    ).fetchall()
    services = []
    for cat in cats:
        svcs = db.execute(
            'SELECT id, name, price FROM services WHERE category_id=? AND is_active=1 ORDER BY sort_order',
            (cat['id'],)
        ).fetchall()
        for s in svcs:
            services.append({'id': s['id'], 'name': s['name'],
                             'price': float(s['price']), 'category': cat['name']})

    products = []
    for p in db.execute(
        'SELECT id, name, sale_price, stock_qty, unit FROM products WHERE is_active=1 AND branch_id=? ORDER BY name',
        (bid,)
    ).fetchall():
        products.append({'id': p['id'], 'name': p['name'],
                        'price': float(p['sale_price']), 'stock': p['stock_qty'],
                        'unit': p['unit']})

    active_staff = db.execute(
        'SELECT id, name, role FROM staff WHERE branch_id=? AND is_active=1 ORDER BY name', (bid,)
    ).fetchall()

    import json as _json
    return render_template('admin_billing_new.html',
                           services_json=_json.dumps(services),
                           products_json=_json.dumps(products),
                           active_staff=active_staff,
                           **get_admin_context())


@app.route('/admin/billing/<int:inv_id>')
@admin_required
def admin_invoice_detail(inv_id):
    db  = get_db()
    bid = get_current_branch_id()
    invoice = db.execute('SELECT * FROM invoices WHERE id=? AND branch_id=?', (inv_id, bid)).fetchone()
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('admin_billing'))
    items = db.execute(
        'SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id', (inv_id,)
    ).fetchall()
    return render_template('admin_invoice.html',
                           invoice=invoice, items=items,
                           **get_admin_context())


@app.route('/admin/billing/<int:inv_id>/delete', methods=['POST'])
@admin_required
def admin_invoice_delete(inv_id):
    db  = get_db()
    bid = get_current_branch_id()
    invoice = db.execute('SELECT id FROM invoices WHERE id=? AND branch_id=?', (inv_id, bid)).fetchone()
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('admin_billing'))
    db.execute('DELETE FROM invoice_items WHERE invoice_id=?', (inv_id,))
    db.execute('DELETE FROM invoices WHERE id=?', (inv_id,))
    db.commit()
    flash('Invoice deleted.', 'success')
    return redirect(url_for('admin_billing'))


@app.route('/admin/billing/<int:inv_id>/send-whatsapp', methods=['POST'])
@admin_required
def admin_send_invoice_whatsapp(inv_id):
    db  = get_db()
    bid = get_current_branch_id()
    invoice = db.execute('SELECT * FROM invoices WHERE id=? AND branch_id=?', (inv_id, bid)).fetchone()
    if not invoice or not invoice['customer_phone']:
        flash('No customer phone number on this invoice.', 'error')
        return redirect(url_for('admin_invoice_detail', inv_id=inv_id))
    items = db.execute(
        'SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id', (inv_id,)
    ).fetchall()
    try:
        from notifications import send_invoice_whatsapp
        ok = send_invoice_whatsapp(
            phone=invoice['customer_phone'],
            customer_name=invoice['customer_name'] or 'Customer',
            invoice_id=inv_id,
            items=[dict(i) for i in items],
            subtotal=invoice['subtotal'],
            discount_amount=invoice['discount_amount'],
            gst_amount=invoice['gst_amount'],
            total=invoice['total'],
            payment_method=invoice['payment_method'],
        )
        if ok:
            flash('Receipt sent via WhatsApp!', 'success')
        else:
            flash('WhatsApp not configured — complete Meta Cloud API setup first.', 'error')
    except Exception as e:
        flash(f'WhatsApp error: {e}', 'error')
    return redirect(url_for('admin_invoice_detail', inv_id=inv_id))


# ─── Admin: inventory ─────────────────────────────────────────────────────────

@app.route('/admin/inventory')
@manager_required
def admin_inventory():
    db  = get_db()
    bid = get_current_branch_id()
    products = db.execute(
        'SELECT * FROM products WHERE is_active=1 AND branch_id=? ORDER BY name', (bid,)
    ).fetchall()
    low_stock_items = [p for p in products if p['stock_qty'] <= p['alert_threshold']]
    return render_template('admin_inventory.html',
                           products=products,
                           low_stock_items=low_stock_items,
                           **get_admin_context())


@app.route('/admin/inventory/add', methods=['POST'])
@manager_required
def admin_inventory_add():
    db  = get_db()
    bid = get_current_branch_id()
    name      = request.form.get('name', '').strip()
    category  = request.form.get('category', '').strip()
    unit      = request.form.get('unit', 'piece')
    cost      = float(request.form.get('cost_price', 0) or 0)
    sale      = float(request.form.get('sale_price', 0) or 0)
    stock     = int(request.form.get('stock_qty', 0) or 0)
    threshold = int(request.form.get('alert_threshold', 5) or 5)
    if name:
        db.execute('''INSERT INTO products
                          (branch_id, name, category, unit, cost_price, sale_price,
                           stock_qty, alert_threshold)
                      VALUES (?,?,?,?,?,?,?,?)''',
                   (bid, name, category, unit, cost, sale, stock, threshold))
        db.commit()
        flash(f'Product "{name}" added.', 'success')
    else:
        flash('Product name is required.', 'error')
    return redirect(url_for('admin_inventory'))


@app.route('/admin/inventory/<int:prod_id>/edit', methods=['POST'])
@manager_required
def admin_inventory_edit(prod_id):
    db  = get_db()
    bid = get_current_branch_id()
    prod = db.execute('SELECT id FROM products WHERE id=? AND branch_id=?', (prod_id, bid)).fetchone()
    if not prod:
        flash('Product not found.', 'error')
        return redirect(url_for('admin_inventory'))
    name = request.form.get('name', '').strip()
    if not name:
        flash('Product name is required.', 'error')
        return redirect(url_for('admin_inventory'))
    try:
        cost      = max(0.0, float(request.form.get('cost_price', 0) or 0))
        sale      = max(0.0, float(request.form.get('sale_price', 0) or 0))
        threshold = max(0, int(request.form.get('alert_threshold', 5) or 5))
    except (ValueError, TypeError):
        cost, sale, threshold = 0.0, 0.0, 5
    db.execute('''UPDATE products
                  SET name=?, category=?, unit=?, cost_price=?, sale_price=?, alert_threshold=?
                  WHERE id=? AND branch_id=?''',
               (name,
                request.form.get('category', '').strip(),
                request.form.get('unit', 'piece'),
                cost, sale, threshold,
                prod_id, bid))
    db.commit()
    flash('Product updated.', 'success')
    return redirect(url_for('admin_inventory'))


@app.route('/admin/inventory/<int:prod_id>/adjust', methods=['POST'])
@manager_required
def admin_inventory_adjust(prod_id):
    db  = get_db()
    bid = get_current_branch_id()
    prod = db.execute('SELECT id FROM products WHERE id=? AND branch_id=?', (prod_id, bid)).fetchone()
    if not prod:
        flash('Product not found.', 'error')
        return redirect(url_for('admin_inventory'))
    adjust_type = request.form.get('adjust_type', 'add')
    try:
        qty = max(0, int(request.form.get('qty', 0) or 0))
    except (ValueError, TypeError):
        qty = 0
    if adjust_type == 'add':
        db.execute('UPDATE products SET stock_qty = stock_qty + ? WHERE id=? AND branch_id=?', (qty, prod_id, bid))
    elif adjust_type == 'deduct':
        db.execute('UPDATE products SET stock_qty = MAX(0, stock_qty - ?) WHERE id=? AND branch_id=?', (qty, prod_id, bid))
    else:  # set
        db.execute('UPDATE products SET stock_qty = ? WHERE id=? AND branch_id=?', (qty, prod_id, bid))
    db.commit()
    flash('Stock updated.', 'success')
    return redirect(url_for('admin_inventory'))


@app.route('/admin/inventory/<int:prod_id>/delete', methods=['POST'])
@manager_required
def admin_inventory_delete(prod_id):
    db  = get_db()
    bid = get_current_branch_id()
    prod = db.execute('SELECT id FROM products WHERE id=? AND branch_id=?', (prod_id, bid)).fetchone()
    if not prod:
        flash('Product not found.', 'error')
        return redirect(url_for('admin_inventory'))
    db.execute('UPDATE products SET is_active=0 WHERE id=? AND branch_id=?', (prod_id, bid))
    db.commit()
    flash('Product removed.', 'success')
    return redirect(url_for('admin_inventory'))


# ─── Admin: JSON API ──────────────────────────────────────────────────────────

@app.route('/admin/api/customers')
@admin_required
def api_customers():
    from flask import jsonify
    db  = get_db()
    bid = get_current_branch_id()
    q   = request.args.get('q', '').strip()
    rows = db.execute('''
        SELECT id, name, phone FROM customers
        WHERE branch_id=? AND (name LIKE ? OR phone LIKE ?)
        ORDER BY name LIMIT 10
    ''', (bid, f'%{q}%', f'%{q}%')).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/admin/api/services')
@admin_required
def api_services():
    from flask import jsonify
    db   = get_db()
    rows = db.execute(
        'SELECT id, name, price FROM services WHERE is_active=1 ORDER BY name'
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/admin/api/products')
@admin_required
def api_products():
    from flask import jsonify
    db  = get_db()
    bid = get_current_branch_id()
    rows = db.execute(
        'SELECT id, name, sale_price as price, stock_qty, unit FROM products WHERE is_active=1 AND branch_id=? ORDER BY name',
        (bid,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ─── Admin: staff ────────────────────────────────────────────────────────────

@app.route('/admin/staff')
@manager_required
def admin_staff():
    db  = get_db()
    bid = get_current_branch_id()
    staff = db.execute('''
        SELECT s.*,
               COUNT(DISTINCT i.id) as invoice_count,
               COALESCE(SUM(i.total * s.commission_pct / 100.0), 0) as commission_month
        FROM staff s
        LEFT JOIN invoices i ON i.staff_id = s.id
            AND i.branch_id = s.branch_id
            AND i.status = 'paid'
            AND strftime('%Y-%m', i.created_at) = strftime('%Y-%m', 'now')
        WHERE s.branch_id = ?
        GROUP BY s.id
        ORDER BY s.is_active DESC, s.name
    ''', (bid,)).fetchall()
    return render_template('admin_staff.html', staff=staff, **get_admin_context())


@app.route('/admin/staff/new', methods=['GET', 'POST'])
@manager_required
def admin_staff_new():
    db  = get_db()
    bid = get_current_branch_id()
    if request.method == 'POST':
        name   = request.form.get('name', '').strip()
        phone  = request.form.get('phone', '').strip()
        role   = request.form.get('role', 'Stylist').strip() or 'Stylist'
        try:
            comm = max(0.0, min(100.0, float(request.form.get('commission_pct', 0) or 0)))
        except (ValueError, TypeError):
            comm = 0.0
        if not name:
            flash('Staff name is required.', 'error')
            return redirect(url_for('admin_staff_new'))
        cur = db.execute(
            'INSERT INTO staff (branch_id, name, phone, role, commission_pct) VALUES (?,?,?,?,?)',
            (bid, name, phone, role, comm)
        )
        db.commit()
        # Seed default schedule (Mon-Sat working, Sun off)
        for day in range(7):
            is_working = 1 if day < 6 else 0
            try:
                db.execute('INSERT INTO staff_schedules (staff_id, day_of_week, is_working) VALUES (?,?,?)',
                           (cur.lastrowid, day, is_working))
            except Exception:
                pass
        db.commit()
        flash(f'Staff member "{name}" added.', 'success')
        return redirect(url_for('admin_staff'))
    return render_template('admin_staff_form.html', staff=None, **get_admin_context())


@app.route('/admin/staff/<int:staff_id>', methods=['GET'])
@manager_required
def admin_staff_detail(staff_id):
    db  = get_db()
    bid = get_current_branch_id()
    member = db.execute('SELECT * FROM staff WHERE id=? AND branch_id=?', (staff_id, bid)).fetchone()
    if not member:
        flash('Staff member not found.', 'error')
        return redirect(url_for('admin_staff'))

    date_from = request.args.get('date_from', datetime.now().strftime('%Y-%m-01'))
    date_to   = request.args.get('date_to',   datetime.now().strftime('%Y-%m-%d'))

    invoices = db.execute('''
        SELECT i.*, COALESCE(i.total * ? / 100.0, 0) as commission_earned
        FROM invoices i
        WHERE i.staff_id=? AND i.branch_id=? AND i.status='paid'
          AND DATE(i.created_at) BETWEEN ? AND ?
        ORDER BY i.created_at DESC
    ''', (member['commission_pct'], staff_id, bid, date_from, date_to)).fetchall()

    total_commission = sum(row['commission_earned'] for row in invoices)
    total_revenue    = sum(row['total'] for row in invoices)

    appointments = db.execute('''
        SELECT * FROM appointments WHERE staff_id=? AND branch_id=?
          AND date BETWEEN ? AND ?
        ORDER BY date DESC, time DESC
    ''', (staff_id, bid, date_from, date_to)).fetchall()

    schedule = {row['day_of_week']: row['is_working']
                for row in db.execute(
                    'SELECT day_of_week, is_working FROM staff_schedules WHERE staff_id=?',
                    (staff_id,)).fetchall()}

    return render_template('admin_staff_detail.html',
                           member=member, invoices=invoices,
                           total_commission=total_commission, total_revenue=total_revenue,
                           appointments=appointments, schedule=schedule,
                           date_from=date_from, date_to=date_to,
                           **get_admin_context())


@app.route('/admin/staff/<int:staff_id>/edit', methods=['POST'])
@manager_required
def admin_staff_edit(staff_id):
    db  = get_db()
    bid = get_current_branch_id()
    member = db.execute('SELECT id FROM staff WHERE id=? AND branch_id=?', (staff_id, bid)).fetchone()
    if not member:
        flash('Staff member not found.', 'error')
        return redirect(url_for('admin_staff'))
    name  = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    role  = request.form.get('role', 'Stylist').strip() or 'Stylist'
    try:
        comm = max(0.0, min(100.0, float(request.form.get('commission_pct', 0) or 0)))
    except (ValueError, TypeError):
        comm = 0.0
    if not name:
        flash('Name is required.', 'error')
        return redirect(url_for('admin_staff_detail', staff_id=staff_id))
    db.execute('UPDATE staff SET name=?, phone=?, role=?, commission_pct=? WHERE id=? AND branch_id=?',
               (name, phone, role, comm, staff_id, bid))
    db.commit()
    flash('Staff updated.', 'success')
    return redirect(url_for('admin_staff_detail', staff_id=staff_id))


@app.route('/admin/staff/<int:staff_id>/toggle', methods=['POST'])
@manager_required
def admin_staff_toggle(staff_id):
    db  = get_db()
    bid = get_current_branch_id()
    member = db.execute('SELECT is_active FROM staff WHERE id=? AND branch_id=?', (staff_id, bid)).fetchone()
    if member:
        db.execute('UPDATE staff SET is_active=? WHERE id=? AND branch_id=?',
                   (0 if member['is_active'] else 1, staff_id, bid))
        db.commit()
    return redirect(request.referrer or url_for('admin_staff'))


@app.route('/admin/staff/<int:staff_id>/delete', methods=['POST'])
@manager_required
def admin_staff_delete(staff_id):
    db  = get_db()
    bid = get_current_branch_id()
    db.execute('UPDATE staff SET is_active=0 WHERE id=? AND branch_id=?', (staff_id, bid))
    db.commit()
    flash('Staff member deactivated.', 'success')
    return redirect(url_for('admin_staff'))


@app.route('/admin/staff/schedule', methods=['GET', 'POST'])
@manager_required
def admin_staff_schedule():
    db  = get_db()
    bid = get_current_branch_id()
    if request.method == 'POST':
        staff_list = db.execute(
            'SELECT id FROM staff WHERE branch_id=? AND is_active=1', (bid,)
        ).fetchall()
        for s in staff_list:
            for day in range(7):
                key = f'sched_{s["id"]}_{day}'
                is_working = 1 if request.form.get(key) else 0
                db.execute('''INSERT INTO staff_schedules (staff_id, day_of_week, is_working)
                              VALUES (?,?,?)
                              ON CONFLICT(staff_id, day_of_week) DO UPDATE SET is_working=excluded.is_working''',
                           (s['id'], day, is_working))
        db.commit()
        flash('Schedule saved.', 'success')
        return redirect(url_for('admin_staff_schedule'))

    staff_list = db.execute(
        'SELECT * FROM staff WHERE branch_id=? AND is_active=1 ORDER BY name', (bid,)
    ).fetchall()
    schedules = {}
    for row in db.execute(
        '''SELECT ss.staff_id, ss.day_of_week, ss.is_working
           FROM staff_schedules ss
           JOIN staff s ON s.id = ss.staff_id
           WHERE s.branch_id=?''', (bid,)
    ).fetchall():
        if row['staff_id'] not in schedules:
            schedules[row['staff_id']] = {}
        schedules[row['staff_id']][row['day_of_week']] = row['is_working']
    return render_template('admin_staff_schedule.html',
                           staff_list=staff_list, schedules=schedules,
                           days=['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                           **get_admin_context())


@app.route('/admin/appointments/<int:appt_id>/assign-staff', methods=['POST'])
@manager_required
def admin_appt_assign_staff(appt_id):
    db  = get_db()
    bid = get_current_branch_id()
    staff_id = request.form.get('staff_id') or None
    if staff_id:
        # Verify staff belongs to branch
        ok = db.execute('SELECT id FROM staff WHERE id=? AND branch_id=?', (staff_id, bid)).fetchone()
        if not ok:
            flash('Staff not found.', 'error')
            return redirect(request.referrer or url_for('admin_appointments'))
    db.execute('UPDATE appointments SET staff_id=? WHERE id=? AND branch_id=?',
               (staff_id, appt_id, bid))
    db.commit()
    flash('Staff assigned.', 'success')
    return redirect(request.referrer or url_for('admin_appointments'))


# ─── Admin: memberships ───────────────────────────────────────────────────────

@app.route('/admin/memberships')
@manager_required
def admin_memberships():
    db  = get_db()
    bid = get_current_branch_id()
    plans = db.execute(
        'SELECT * FROM membership_plans WHERE is_active=1 ORDER BY price'
    ).fetchall()
    enrollments = db.execute('''
        SELECT cm.*, c.name as cust_name, c.phone as cust_phone,
               mp.name as plan_name, mp.discount_pct
        FROM customer_memberships cm
        JOIN customers c ON c.id = cm.customer_id
        JOIN membership_plans mp ON mp.id = cm.plan_id
        WHERE c.branch_id = ?
        ORDER BY cm.expiry_date ASC
    ''', (bid,)).fetchall()

    today = datetime.now().strftime('%Y-%m-%d')
    expiring_soon = [e for e in enrollments
                     if e['status'] == 'active' and e['expiry_date'] >= today
                     and (datetime.strptime(e['expiry_date'], '%Y-%m-%d') -
                          datetime.strptime(today, '%Y-%m-%d')).days <= 7]

    # Revenue from membership plan sales (enrolled customers × plan price)
    membership_revenue = db.execute('''
        SELECT COALESCE(SUM(mp.price), 0) as rev
        FROM customer_memberships cm
        JOIN membership_plans mp ON mp.id = cm.plan_id
        JOIN customers c ON c.id = cm.customer_id
        WHERE c.branch_id=?
    ''', (bid,)).fetchone()['rev']

    customers_for_enroll = db.execute(
        'SELECT id, name, phone FROM customers WHERE branch_id=? ORDER BY name', (bid,)
    ).fetchall()

    return render_template('admin_memberships.html',
                           plans=plans, enrollments=enrollments,
                           expiring_soon=expiring_soon,
                           membership_revenue=membership_revenue,
                           customers=customers_for_enroll,
                           today=today,
                           **get_admin_context())


@app.route('/admin/memberships/plans/add', methods=['POST'])
@manager_required
def admin_membership_plan_add():
    db   = get_db()
    name = request.form.get('name', '').strip()
    if not name:
        flash('Plan name is required.', 'error')
        return redirect(url_for('admin_memberships'))
    try:
        price    = max(0.0, float(request.form.get('price', 0) or 0))
        validity = max(1, int(request.form.get('validity_days', 30) or 30))
        disc     = max(0.0, min(100.0, float(request.form.get('discount_pct', 0) or 0)))
    except (ValueError, TypeError):
        price, validity, disc = 0.0, 30, 0.0
    desc = request.form.get('description', '').strip()
    db.execute('''INSERT INTO membership_plans (name, price, validity_days, discount_pct, description)
                  VALUES (?,?,?,?,?)''', (name, price, validity, disc, desc))
    db.commit()
    flash(f'Plan "{name}" created.', 'success')
    return redirect(url_for('admin_memberships'))


@app.route('/admin/memberships/plans/<int:plan_id>/edit', methods=['POST'])
@manager_required
def admin_membership_plan_edit(plan_id):
    db   = get_db()
    plan = db.execute('SELECT id FROM membership_plans WHERE id=?', (plan_id,)).fetchone()
    if not plan:
        flash('Plan not found.', 'error')
        return redirect(url_for('admin_memberships'))
    name = request.form.get('name', '').strip()
    if not name:
        flash('Plan name is required.', 'error')
        return redirect(url_for('admin_memberships'))
    try:
        price    = max(0.0, float(request.form.get('price', 0) or 0))
        validity = max(1, int(request.form.get('validity_days', 30) or 30))
        disc     = max(0.0, min(100.0, float(request.form.get('discount_pct', 0) or 0)))
    except (ValueError, TypeError):
        price, validity, disc = 0.0, 30, 0.0
    db.execute('''UPDATE membership_plans
                  SET name=?, price=?, validity_days=?, discount_pct=?, description=?
                  WHERE id=?''',
               (name, price, validity, disc,
                request.form.get('description', '').strip(), plan_id))
    db.commit()
    flash('Plan updated.', 'success')
    return redirect(url_for('admin_memberships'))


@app.route('/admin/memberships/plans/<int:plan_id>/delete', methods=['POST'])
@manager_required
def admin_membership_plan_delete(plan_id):
    db = get_db()
    db.execute('UPDATE membership_plans SET is_active=0 WHERE id=?', (plan_id,))
    db.commit()
    flash('Plan removed.', 'success')
    return redirect(url_for('admin_memberships'))


@app.route('/admin/memberships/enroll', methods=['POST'])
@manager_required
def admin_membership_enroll():
    db          = get_db()
    bid         = get_current_branch_id()
    customer_id = request.form.get('customer_id', '')
    plan_id     = request.form.get('plan_id', '')
    if not customer_id or not plan_id:
        flash('Customer and plan are required.', 'error')
        return redirect(url_for('admin_memberships'))

    # Verify customer belongs to branch
    cust = db.execute('SELECT id FROM customers WHERE id=? AND branch_id=?', (customer_id, bid)).fetchone()
    plan = db.execute('SELECT * FROM membership_plans WHERE id=? AND is_active=1', (plan_id,)).fetchone()
    if not cust or not plan:
        flash('Customer or plan not found.', 'error')
        return redirect(url_for('admin_memberships'))

    from datetime import timedelta
    start  = datetime.now().strftime('%Y-%m-%d')
    expiry = (datetime.now() + timedelta(days=plan['validity_days'])).strftime('%Y-%m-%d')

    # Cancel any existing active membership for this customer
    db.execute('''UPDATE customer_memberships SET status='cancelled'
                  WHERE customer_id=? AND status='active' ''', (customer_id,))
    db.execute('''INSERT INTO customer_memberships (customer_id, plan_id, start_date, expiry_date, status)
                  VALUES (?,?,?,?,'active')''', (customer_id, plan_id, start, expiry))
    db.commit()
    flash('Customer enrolled in membership.', 'success')
    return redirect(url_for('admin_memberships'))


@app.route('/admin/memberships/<int:enroll_id>/cancel', methods=['POST'])
@manager_required
def admin_membership_cancel(enroll_id):
    db  = get_db()
    bid = get_current_branch_id()
    # Verify membership belongs to a customer in this branch
    row = db.execute('''SELECT cm.id FROM customer_memberships cm
                        JOIN customers c ON c.id = cm.customer_id
                        WHERE cm.id=? AND c.branch_id=?''', (enroll_id, bid)).fetchone()
    if not row:
        flash('Membership not found.', 'error')
        return redirect(url_for('admin_memberships'))
    db.execute("UPDATE customer_memberships SET status='cancelled' WHERE id=?", (enroll_id,))
    db.commit()
    flash('Membership cancelled.', 'success')
    return redirect(url_for('admin_memberships'))


@app.route('/admin/memberships/<int:enroll_id>/send-reminder', methods=['POST'])
@manager_required
def admin_membership_reminder(enroll_id):
    db  = get_db()
    bid = get_current_branch_id()
    row = db.execute('''
        SELECT cm.expiry_date, c.name, c.phone, mp.name as plan_name
        FROM customer_memberships cm
        JOIN customers c ON c.id = cm.customer_id
        JOIN membership_plans mp ON mp.id = cm.plan_id
        WHERE cm.id=? AND c.branch_id=?
    ''', (enroll_id, bid)).fetchone()
    if not row:
        flash('Membership not found.', 'error')
        return redirect(url_for('admin_memberships'))
    try:
        from notifications import send_membership_reminder_whatsapp
        ok = send_membership_reminder_whatsapp(
            row['phone'], row['name'], row['plan_name'], row['expiry_date']
        )
        if ok:
            flash('Reminder sent via WhatsApp!', 'success')
        else:
            flash('WhatsApp not configured — complete Meta Cloud API setup first.', 'error')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin_memberships'))


# ─── Admin: JSON API (additions) ─────────────────────────────────────────────

@app.route('/admin/api/staff')
@admin_required
def api_staff():
    from flask import jsonify
    db  = get_db()
    bid = get_current_branch_id()
    rows = db.execute(
        'SELECT id, name, role FROM staff WHERE branch_id=? AND is_active=1 ORDER BY name', (bid,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/admin/api/customer-loyalty')
@admin_required
def api_customer_loyalty():
    from flask import jsonify
    db  = get_db()
    bid = get_current_branch_id()
    customer_id = request.args.get('customer_id', '')
    if not customer_id:
        return jsonify({'points': 0, 'points_reserved': 0})
    if expire_customer_loyalty(db, customer_id):
        db.commit()
    cust = db.execute(
        '''SELECT loyalty_points, points_reserved, loyalty_points_expiry_date
           FROM customers WHERE id=? AND branch_id=?''',
        (customer_id, bid)
    ).fetchone()
    if cust:
        pts      = int(cust['loyalty_points']  or 0)
        reserved = int(cust['points_reserved'] or 0)
        return jsonify({
            # Total available = remaining balance + pre-reserved (already deducted when reserved)
            'points':          pts + reserved,
            'points_reserved': reserved,
            'expiry_date':     cust['loyalty_points_expiry_date'],
        })
    return jsonify({'points': 0, 'points_reserved': 0, 'expiry_date': None})
@app.route('/admin/api/customer-membership')
@admin_required
def api_customer_membership():
    from flask import jsonify
    db          = get_db()
    bid         = get_current_branch_id()
    customer_id = request.args.get('customer_id', '')
    if not customer_id:
        return jsonify({'active': False})
    today = datetime.now().strftime('%Y-%m-%d')
    # Scope to current branch: customer must belong to admin's active branch
    row = db.execute('''
        SELECT cm.id, mp.name as plan_name, mp.discount_pct, cm.expiry_date
        FROM customer_memberships cm
        JOIN customers c ON c.id = cm.customer_id
        JOIN membership_plans mp ON mp.id = cm.plan_id
        WHERE cm.customer_id=? AND c.branch_id=?
          AND cm.status='active' AND cm.expiry_date >= ?
        ORDER BY cm.expiry_date DESC LIMIT 1
    ''', (customer_id, bid, today)).fetchone()
    if row:
        return jsonify({'active': True, 'plan_name': row['plan_name'],
                        'discount_pct': row['discount_pct'], 'expiry_date': row['expiry_date']})
    return jsonify({'active': False})


# ─── Segmentation engine ─────────────────────────────────────────────────────

def get_segment_customers(db, segment_type, segment_params, branch_id):
    """Return list of {id, name, phone} dicts matching the segment."""
    params  = json.loads(segment_params) if isinstance(segment_params, str) else (segment_params or {})
    today   = datetime.now().strftime('%Y-%m-%d')
    mm_dd   = datetime.now().strftime('%m-%d')
    mm      = datetime.now().strftime('%m')

    if segment_type == 'all':
        rows = db.execute(
            'SELECT id, name, phone FROM customers WHERE branch_id=? AND phone IS NOT NULL',
            (branch_id,)
        ).fetchall()

    elif segment_type == 'inactive':
        days   = int(params.get('days', 30))
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        rows = db.execute('''
            SELECT id, name, phone FROM customers
            WHERE branch_id=? AND phone IS NOT NULL
              AND (last_visit_date IS NULL OR last_visit_date < ?)
        ''', (branch_id, cutoff)).fetchall()

    elif segment_type == 'birthday_month':
        rows = db.execute('''
            SELECT id, name, phone FROM customers
            WHERE branch_id=? AND phone IS NOT NULL
              AND birthday IS NOT NULL AND birthday != ''
              AND strftime('%m', birthday) = ?
        ''', (branch_id, mm)).fetchall()

    elif segment_type == 'birthday_today':
        rows = db.execute('''
            SELECT id, name, phone FROM customers
            WHERE branch_id=? AND phone IS NOT NULL
              AND birthday IS NOT NULL AND birthday != ''
              AND strftime('%m-%d', birthday) = ?
        ''', (branch_id, mm_dd)).fetchall()

    elif segment_type == 'membership':
        rows = db.execute('''
            SELECT DISTINCT c.id, c.name, c.phone FROM customers c
            JOIN customer_memberships cm ON cm.customer_id = c.id
            WHERE c.branch_id=? AND c.phone IS NOT NULL
              AND cm.status='active' AND cm.expiry_date >= ?
        ''', (branch_id, today)).fetchall()

    else:
        rows = []

    return [dict(r) for r in rows]


# ─── Daily automation ─────────────────────────────────────────────────────────

def _run_daily_automation():
    """Run birthday greetings and follow-up rules — once per calendar day per branch."""
    with app.app_context():
        db      = get_db()
        today   = datetime.now().strftime('%Y-%m-%d')
        mm_dd   = datetime.now().strftime('%m-%d')

        # Check if already ran today
        already = db.execute(
            'SELECT id FROM automation_log WHERE run_date=?', (today,)
        ).fetchone()
        if already:
            return

        rules_fired   = 0
        messages_sent = 0

        try:
            from notifications import send_birthday_whatsapp, send_campaign_whatsapp

            # ── Birthday automation (all branches) ───────────────────────────
            birthday_customers = db.execute('''
                SELECT id, name, phone, branch_id FROM customers
                WHERE phone IS NOT NULL AND birthday IS NOT NULL AND birthday != ''
                  AND strftime('%m-%d', birthday) = ?
            ''', (mm_dd,)).fetchall()

            for cust in birthday_customers:
                # Check not already sent today
                already_sent = db.execute(
                    '''SELECT id FROM campaign_recipients cr
                       JOIN campaigns c ON c.id = cr.campaign_id
                       WHERE cr.customer_id=? AND c.segment_type='birthday_today'
                         AND DATE(cr.sent_at) = ?''',
                    (cust['id'], today)
                ).fetchone()
                if already_sent:
                    continue
                ok, _ = send_birthday_whatsapp(cust['phone'], cust['name'])
                if ok:
                    # Log as a pseudo-campaign
                    cur = db.execute(
                        '''INSERT INTO campaigns (branch_id, name, message_body, segment_type, sent_count)
                           VALUES (?, 'Birthday Auto', '🎂 Birthday greeting', 'birthday_today', 1)''',
                        (cust['branch_id'],)
                    )
                    db.execute(
                        'INSERT INTO campaign_recipients (campaign_id, customer_id, status) VALUES (?,?,?)',
                        (cur.lastrowid, cust['id'], 'sent')
                    )
                    messages_sent += 1

            # ── Follow-up rules (all active) ─────────────────────────────────
            rules = db.execute(
                'SELECT * FROM follow_up_rules WHERE is_active=1'
            ).fetchall()

            for rule in rules:
                branch_id = rule['branch_id']
                params    = {'days': rule['trigger_value']}

                if rule['trigger_type'] == 'inactive':
                    customers = get_segment_customers(db, 'inactive', params, branch_id)
                elif rule['trigger_type'] == 'birthday_today':
                    customers = get_segment_customers(db, 'birthday_today', {}, branch_id)
                elif rule['trigger_type'] == 'membership_expiry':
                    # Customers whose membership expires in N days
                    expiry_cutoff = (datetime.now() + timedelta(days=rule['trigger_value'])).strftime('%Y-%m-%d')
                    rows = db.execute('''
                        SELECT DISTINCT c.id, c.name, c.phone FROM customers c
                        JOIN customer_memberships cm ON cm.customer_id = c.id
                        WHERE c.branch_id=? AND c.phone IS NOT NULL
                          AND cm.status='active' AND cm.expiry_date <= ?
                          AND cm.expiry_date >= ?
                    ''', (branch_id, expiry_cutoff, today)).fetchall()
                    customers = [dict(r) for r in rows]
                else:
                    customers = []

                if not customers:
                    continue

                rules_fired += 1
                cur = db.execute(
                    '''INSERT INTO campaigns (branch_id, name, message_body, segment_type, segment_params, sent_count)
                       VALUES (?,?,?,?,?,0)''',
                    (branch_id, f'Auto: {rule["name"]}',
                     rule['message_template'], rule['trigger_type'],
                     json.dumps(params))
                )
                camp_id = cur.lastrowid
                sent = 0
                for cust in customers:
                    # Deduplicate: skip if sent in last 7 days for this rule
                    recent = db.execute(
                        '''SELECT id FROM campaign_recipients cr
                           JOIN campaigns c ON c.id = cr.campaign_id
                           WHERE cr.customer_id=? AND c.name=?
                             AND DATE(cr.sent_at) >= DATE(?, '-7 days')''',
                        (cust['id'], f'Auto: {rule["name"]}', today)
                    ).fetchone()
                    if recent:
                        continue
                    ok, _ = send_campaign_whatsapp(cust['phone'], cust['name'], rule['message_template'])
                    db.execute(
                        'INSERT INTO campaign_recipients (campaign_id, customer_id, status) VALUES (?,?,?)',
                        (camp_id, cust['id'], 'sent' if ok else 'failed')
                    )
                    if ok:
                        sent += 1
                        messages_sent += 1
                db.execute('UPDATE campaigns SET sent_count=? WHERE id=?', (sent, camp_id))

                db.execute('UPDATE follow_up_rules SET last_run_date=? WHERE id=?',
                           (today, rule['id']))

        except Exception as e:
            app.logger.warning(f'[Automation] Error: {e}')

        db.execute(
            'INSERT OR IGNORE INTO automation_log (run_date, rules_fired, messages_sent) VALUES (?,?,?)',
            (today, rules_fired, messages_sent)
        )
        db.commit()


@app.before_request
def trigger_automation():
    """Run daily automation once per day on any admin request."""
    if request.endpoint and request.endpoint.startswith('admin') and \
       session.get('admin_user_id') and not getattr(g, '_automation_checked', False):
        g._automation_checked = True
        try:
            _run_daily_automation()
        except Exception as e:
            app.logger.warning(f'[Automation] Trigger error: {e}')


# ─── Admin: Marketing ─────────────────────────────────────────────────────────

@app.route('/admin/marketing')
@manager_required
def admin_marketing():
    db  = get_db()
    bid = get_current_branch_id()
    campaigns = db.execute('''
        SELECT c.*, COUNT(cr.id) as recipient_count
        FROM campaigns c
        LEFT JOIN campaign_recipients cr ON cr.campaign_id = c.id
        WHERE c.branch_id=?
        GROUP BY c.id ORDER BY c.created_at DESC LIMIT 20
    ''', (bid,)).fetchall()

    total_sent    = db.execute(
        'SELECT COALESCE(SUM(sent_count),0) as t FROM campaigns WHERE branch_id=?', (bid,)
    ).fetchone()['t']
    active_rules  = db.execute(
        'SELECT COUNT(*) as c FROM follow_up_rules WHERE branch_id=? AND is_active=1', (bid,)
    ).fetchone()['c']
    camp_count    = db.execute(
        'SELECT COUNT(*) as c FROM campaigns WHERE branch_id=?', (bid,)
    ).fetchone()['c']

    return render_template('admin_marketing.html',
                           campaigns=campaigns,
                           total_sent=total_sent,
                           active_rules=active_rules,
                           camp_count=camp_count,
                           **get_admin_context())


@app.route('/admin/marketing/new', methods=['GET', 'POST'])
@manager_required
def admin_marketing_new():
    db  = get_db()
    bid = get_current_branch_id()

    if request.method == 'POST':
        name          = request.form.get('name', '').strip() or 'Campaign'
        message_body  = request.form.get('message_body', '').strip()
        segment_type  = request.form.get('segment_type', 'all')
        days_inactive = request.form.get('days_inactive', '30')

        if not message_body:
            flash('Message body is required.', 'error')
            return redirect(url_for('admin_marketing_new'))

        params = {}
        if segment_type == 'inactive':
            try:
                params = {'days': int(days_inactive)}
            except (ValueError, TypeError):
                params = {'days': 30}

        customers = get_segment_customers(db, segment_type, params, bid)
        if not customers:
            flash('No customers match the selected segment.', 'error')
            return redirect(url_for('admin_marketing_new'))

        try:
            from notifications import send_campaign_whatsapp as _send_camp
        except Exception:
            flash('Notification module error.', 'error')
            return redirect(url_for('admin_marketing_new'))

        cur = db.execute(
            '''INSERT INTO campaigns (branch_id, name, message_body, segment_type, segment_params, status, sent_count)
               VALUES (?,?,?,?,?,'sending',0)''',
            (bid, name, message_body, segment_type, json.dumps(params))
        )
        camp_id = cur.lastrowid
        db.commit()

        sent = 0
        for cust in customers:
            ok, _ = _send_camp(cust['phone'], cust['name'], message_body)
            db.execute(
                'INSERT INTO campaign_recipients (campaign_id, customer_id, status) VALUES (?,?,?)',
                (camp_id, cust['id'], 'sent' if ok else 'failed')
            )
            if ok:
                sent += 1

        db.execute("UPDATE campaigns SET status='sent', sent_count=? WHERE id=?", (sent, camp_id))
        db.commit()

        flash(f'Campaign sent to {sent}/{len(customers)} recipients!', 'success')
        return redirect(url_for('admin_marketing_detail', camp_id=camp_id))

    return render_template('admin_marketing_new.html', **get_admin_context())


@app.route('/admin/marketing/<int:camp_id>')
@manager_required
def admin_marketing_detail(camp_id):
    db  = get_db()
    bid = get_current_branch_id()
    campaign = db.execute(
        'SELECT * FROM campaigns WHERE id=? AND branch_id=?', (camp_id, bid)
    ).fetchone()
    if not campaign:
        flash('Campaign not found.', 'error')
        return redirect(url_for('admin_marketing'))
    recipients = db.execute('''
        SELECT cr.*, c.name, c.phone FROM campaign_recipients cr
        JOIN customers c ON c.id = cr.customer_id
        WHERE cr.campaign_id=? ORDER BY cr.sent_at DESC
    ''', (camp_id,)).fetchall()
    return render_template('admin_marketing_detail.html',
                           campaign=campaign, recipients=recipients,
                           **get_admin_context())


@app.route('/admin/marketing/rules')
@manager_required
def admin_marketing_rules():
    db  = get_db()
    bid = get_current_branch_id()
    rules = db.execute(
        'SELECT * FROM follow_up_rules WHERE branch_id=? ORDER BY created_at DESC', (bid,)
    ).fetchall()
    return render_template('admin_marketing_rules.html', rules=rules, **get_admin_context())


@app.route('/admin/marketing/rules/add', methods=['POST'])
@manager_required
def admin_marketing_rule_add():
    db  = get_db()
    bid = get_current_branch_id()
    name     = request.form.get('name', '').strip()
    trigger  = request.form.get('trigger_type', 'inactive')
    message  = request.form.get('message_template', '').strip()
    if not name or not message:
        flash('Name and message are required.', 'error')
        return redirect(url_for('admin_marketing_rules'))
    try:
        tval = max(1, int(request.form.get('trigger_value', 30) or 30))
    except (ValueError, TypeError):
        tval = 30
    if trigger not in ('inactive', 'birthday_today', 'membership_expiry'):
        trigger = 'inactive'
    db.execute(
        '''INSERT INTO follow_up_rules (branch_id, name, trigger_type, trigger_value, message_template)
           VALUES (?,?,?,?,?)''',
        (bid, name, trigger, tval, message)
    )
    db.commit()
    flash(f'Rule "{name}" created.', 'success')
    return redirect(url_for('admin_marketing_rules'))


@app.route('/admin/marketing/rules/<int:rule_id>/toggle', methods=['POST'])
@manager_required
def admin_marketing_rule_toggle(rule_id):
    db  = get_db()
    bid = get_current_branch_id()
    rule = db.execute('SELECT is_active FROM follow_up_rules WHERE id=? AND branch_id=?', (rule_id, bid)).fetchone()
    if rule:
        db.execute('UPDATE follow_up_rules SET is_active=? WHERE id=? AND branch_id=?',
                   (0 if rule['is_active'] else 1, rule_id, bid))
        db.commit()
    return redirect(url_for('admin_marketing_rules'))


@app.route('/admin/marketing/rules/<int:rule_id>/delete', methods=['POST'])
@manager_required
def admin_marketing_rule_delete(rule_id):
    db  = get_db()
    bid = get_current_branch_id()
    db.execute('DELETE FROM follow_up_rules WHERE id=? AND branch_id=?', (rule_id, bid))
    db.commit()
    flash('Rule deleted.', 'success')
    return redirect(url_for('admin_marketing_rules'))


@app.route('/admin/marketing/rules/<int:rule_id>/run', methods=['POST'])
@manager_required
def admin_marketing_rule_run(rule_id):
    db  = get_db()
    bid = get_current_branch_id()
    rule = db.execute('SELECT * FROM follow_up_rules WHERE id=? AND branch_id=?', (rule_id, bid)).fetchone()
    if not rule:
        flash('Rule not found.', 'error')
        return redirect(url_for('admin_marketing_rules'))

    try:
        from notifications import send_campaign_whatsapp as _send_camp
        params = {'days': rule['trigger_value']}
        if rule['trigger_type'] == 'inactive':
            customers = get_segment_customers(db, 'inactive', params, bid)
        elif rule['trigger_type'] == 'birthday_today':
            customers = get_segment_customers(db, 'birthday_today', {}, bid)
        elif rule['trigger_type'] == 'membership_expiry':
            today  = datetime.now().strftime('%Y-%m-%d')
            cutoff = (datetime.now() + timedelta(days=rule['trigger_value'])).strftime('%Y-%m-%d')
            rows   = db.execute('''
                SELECT DISTINCT c.id, c.name, c.phone FROM customers c
                JOIN customer_memberships cm ON cm.customer_id = c.id
                WHERE c.branch_id=? AND c.phone IS NOT NULL
                  AND cm.status='active' AND cm.expiry_date <= ? AND cm.expiry_date >= ?
            ''', (bid, cutoff, today)).fetchall()
            customers = [dict(r) for r in rows]
        else:
            customers = []

        if not customers:
            flash('No customers match this rule right now.', 'error')
            return redirect(url_for('admin_marketing_rules'))

        cur = db.execute(
            '''INSERT INTO campaigns (branch_id, name, message_body, segment_type, segment_params, sent_count)
               VALUES (?,?,?,?,?,0)''',
            (bid, f'Manual: {rule["name"]}', rule['message_template'],
             rule['trigger_type'], json.dumps(params))
        )
        camp_id = cur.lastrowid
        sent = 0
        for cust in customers:
            ok, _ = _send_camp(cust['phone'], cust['name'], rule['message_template'])
            db.execute(
                'INSERT INTO campaign_recipients (campaign_id, customer_id, status) VALUES (?,?,?)',
                (camp_id, cust['id'], 'sent' if ok else 'failed')
            )
            if ok:
                sent += 1
        db.execute('UPDATE campaigns SET sent_count=? WHERE id=?', (sent, camp_id))
        db.execute('UPDATE follow_up_rules SET last_run_date=? WHERE id=?',
                   (datetime.now().strftime('%Y-%m-%d'), rule_id))
        db.commit()
        flash(f'Rule sent to {sent}/{len(customers)} customers.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin_marketing_rules'))


@app.route('/admin/api/segment-count')
@manager_required
def api_segment_count():
    from flask import jsonify
    db  = get_db()
    bid = get_current_branch_id()
    segment_type  = request.args.get('segment_type', 'all')
    days_inactive = request.args.get('days_inactive', '30')
    try:
        days = int(days_inactive)
    except (ValueError, TypeError):
        days = 30
    params = {'days': days} if segment_type == 'inactive' else {}
    customers = get_segment_customers(db, segment_type, params, bid)
    return jsonify({'count': len(customers)})


# ─── Admin: Analytics ─────────────────────────────────────────────────────────

@app.route('/admin/analytics')
@owner_required
def admin_analytics():
    db  = get_db()
    bid = get_current_branch_id()

    date_from = request.args.get('date_from', (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'))
    date_to   = request.args.get('date_to',   datetime.now().strftime('%Y-%m-%d'))

    # ── Revenue charts ────────────────────────────────────────────────────────
    # Daily: last 14 days
    daily_rows = db.execute('''
        SELECT DATE(created_at) as day, COALESCE(SUM(total),0) as rev, COUNT(*) as cnt
        FROM invoices WHERE branch_id=? AND status='paid'
          AND DATE(created_at) >= DATE('now','-13 days')
        GROUP BY day ORDER BY day
    ''', (bid,)).fetchall()
    # Fill gaps
    daily_map = {r['day']: {'rev': r['rev'], 'cnt': r['cnt']} for r in daily_rows}
    daily_labels, daily_rev, daily_cnt = [], [], []
    for i in range(13, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        daily_labels.append((datetime.now() - timedelta(days=i)).strftime('%d %b'))
        daily_rev.append(round(daily_map.get(d, {}).get('rev', 0), 0))
        daily_cnt.append(daily_map.get(d, {}).get('cnt', 0))

    # Monthly: last 12 months
    monthly_rows = db.execute('''
        SELECT strftime('%Y-%m', created_at) as month,
               COALESCE(SUM(total),0) as rev, COUNT(*) as cnt
        FROM invoices WHERE branch_id=? AND status='paid'
          AND DATE(created_at) >= DATE('now','-365 days')
        GROUP BY month ORDER BY month
    ''', (bid,)).fetchall()
    monthly_labels = [r['month'] for r in monthly_rows]
    monthly_rev    = [round(r['rev'], 0) for r in monthly_rows]

    # ── Top services ──────────────────────────────────────────────────────────
    top_svcs_rev = db.execute('''
        SELECT ii.item_name, COALESCE(SUM(ii.line_total),0) as rev, COALESCE(SUM(ii.qty),0) as cnt
        FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
        WHERE i.branch_id=? AND i.status='paid' AND ii.item_type='service'
          AND DATE(i.created_at) BETWEEN ? AND ?
        GROUP BY ii.item_name ORDER BY rev DESC LIMIT 5
    ''', (bid, date_from, date_to)).fetchall()

    top_svcs_cnt = db.execute('''
        SELECT ii.item_name, COALESCE(SUM(ii.qty),0) as cnt
        FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
        WHERE i.branch_id=? AND i.status='paid' AND ii.item_type='service'
          AND DATE(i.created_at) BETWEEN ? AND ?
        GROUP BY ii.item_name ORDER BY cnt DESC LIMIT 5
    ''', (bid, date_from, date_to)).fetchall()

    # ── Top customers ─────────────────────────────────────────────────────────
    top_customers = db.execute('''
        SELECT name, phone, total_spend, visit_count
        FROM customers WHERE branch_id=?
        ORDER BY total_spend DESC LIMIT 10
    ''', (bid,)).fetchall()

    # ── Staff performance ─────────────────────────────────────────────────────
    staff_perf = db.execute('''
        SELECT s.name, s.role, s.commission_pct,
               COUNT(i.id) as invoice_count,
               COALESCE(SUM(i.total),0) as revenue,
               COALESCE(SUM(i.total * s.commission_pct / 100.0),0) as commission
        FROM staff s
        LEFT JOIN invoices i ON i.staff_id = s.id AND i.branch_id = s.branch_id
            AND i.status='paid' AND DATE(i.created_at) BETWEEN ? AND ?
        WHERE s.branch_id=? AND s.is_active=1
        GROUP BY s.id ORDER BY revenue DESC
    ''', (date_from, date_to, bid)).fetchall()

    # ── Summary KPIs ──────────────────────────────────────────────────────────
    summary = db.execute('''
        SELECT COALESCE(SUM(total),0) as total_rev,
               COUNT(*) as invoice_cnt,
               COALESCE(AVG(total),0) as avg_order
        FROM invoices WHERE branch_id=? AND status='paid'
          AND DATE(created_at) BETWEEN ? AND ?
    ''', (bid, date_from, date_to)).fetchone()

    # Retention rate: customers with visit_count >= 2 / customers with visit_count >= 1
    ret = db.execute('''
        SELECT
            CAST(SUM(CASE WHEN visit_count >= 2 THEN 1 ELSE 0 END) AS REAL)
            / NULLIF(SUM(CASE WHEN visit_count >= 1 THEN 1 ELSE 0 END), 0) * 100
            as retention
        FROM customers WHERE branch_id=?
    ''', (bid,)).fetchone()
    retention_rate = round(ret['retention'] or 0, 1)

    # Branch comparison for this single-salon installation
    branches_rev = db.execute('''
        SELECT b.name, COALESCE(SUM(i.total),0) as rev, COUNT(i.id) as cnt
        FROM branches b
        LEFT JOIN invoices i ON i.branch_id = b.id AND i.status='paid'
          AND DATE(i.created_at) BETWEEN ? AND ?
        WHERE b.is_active=1
        GROUP BY b.id ORDER BY rev DESC
    ''', (date_from, date_to)).fetchall()

    return render_template('admin_analytics.html',
                           date_from=date_from, date_to=date_to,
                           daily_labels=json.dumps(daily_labels),
                           daily_rev=json.dumps(daily_rev),
                           daily_cnt=json.dumps(daily_cnt),
                           monthly_labels=json.dumps(monthly_labels),
                           monthly_rev=json.dumps(monthly_rev),
                           top_svcs_rev=top_svcs_rev,
                           top_svcs_cnt=top_svcs_cnt,
                           top_customers=top_customers,
                           staff_perf=staff_perf,
                           summary=summary,
                           retention_rate=retention_rate,
                           branches_rev=branches_rev,
                           **get_admin_context())


@app.route('/admin/analytics/export')
@owner_required
def admin_analytics_export():
    from flask import Response, stream_with_context
    db  = get_db()
    bid = get_current_branch_id()
    date_from = request.args.get('date_from', (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'))
    date_to   = request.args.get('date_to',   datetime.now().strftime('%Y-%m-%d'))

    invoices = db.execute('''
        SELECT i.id, DATE(i.created_at) as date, i.customer_name, i.customer_phone,
               i.subtotal, i.discount_amount, i.gst_amount, i.total,
               i.payment_method, i.status,
               GROUP_CONCAT(ii.item_name || ' x' || ii.qty, '; ') as items
        FROM invoices i
        LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
        WHERE i.branch_id=? AND DATE(i.created_at) BETWEEN ? AND ?
        GROUP BY i.id ORDER BY i.created_at DESC
    ''', (bid, date_from, date_to)).fetchall()

    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Invoice #', 'Date', 'Customer', 'Phone',
                     'Subtotal', 'Discount', 'GST', 'Total',
                     'Payment', 'Status', 'Items'])
    for inv in invoices:
        writer.writerow([
            inv['id'], inv['date'], inv['customer_name'], inv['customer_phone'],
            inv['subtotal'], inv['discount_amount'], inv['gst_amount'], inv['total'],
            inv['payment_method'], inv['status'], inv['items'] or ''
        ])

    branch_name = db.execute('SELECT name FROM branches WHERE id=?', (bid,)).fetchone()['name']
    filename    = f"invoices_{branch_name}_{date_from}_{date_to}.csv".replace(' ', '_')

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


# ─── Customer portal ─────────────────────────────────────────────────────────

def customer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('customer_id'):
            return redirect(url_for('customer_login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/my/login', methods=['GET', 'POST'])
def customer_login():
    if session.get('customer_id'):
        return redirect(url_for('customer_portal'))

    error = None
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        if not phone or not (phone.isdigit() and len(phone) == 10):
            error = 'Please enter a valid 10-digit mobile number.'
        else:
            db   = get_db()
            cust = db.execute(
                'SELECT id, name, email FROM customers WHERE phone=?', (phone,)
            ).fetchone()
            if not cust:
                error = 'No account found for this number. Please book an appointment first.'
            elif not (cust['email'] or '').strip():
                error = 'No email address is saved for this account. Please ask the salon to update your email before signing in.'
            else:
                existing_pending = session.get('pending_customer_login')
                if (
                    existing_pending
                    and existing_pending.get('phone') == phone
                    and existing_pending.get('expires_at', 0) > time.time()
                ):
                    return redirect(url_for('customer_login_verify'))
                otp = generate_otp()
                try:
                    from notifications import send_otp_email
                    email_sent = send_otp_email(
                        cust['email'].strip(), otp, cust['name'], purpose='login'
                    )
                except Exception as e:
                    app.logger.error(f'Customer login OTP error: {e}')
                    email_sent = False
                if not email_sent:
                    error = 'We could not send the login verification email. Please try again.'
                    return render_template('customer_login.html', error=error)
                session['pending_customer_login'] = {
                    'customer_id': cust['id'],
                    'phone': phone,
                    'email': cust['email'].strip(),
                    'name': cust['name'],
                    'otp': otp,
                    'expires_at': time.time() + OTP_EXPIRY_SECONDS,
                    'attempts_left': OTP_MAX_ATTEMPTS,
                    'last_resend': time.time(),
                }
                session.modified = True
                return redirect(url_for('customer_login_verify'))

    return render_template('customer_login.html', error=error)


@app.route('/my/login/verify', methods=['GET', 'POST'])
def customer_login_verify():
    pending = session.get('pending_customer_login')
    if not pending:
        flash('Please enter your phone number to request a login code.', 'error')
        return redirect(url_for('customer_login'))

    seconds = max(0, int(pending.get('expires_at', 0) - time.time()))
    if seconds <= 0:
        session.pop('pending_customer_login', None)
        flash('Login OTP expired. Please request a new code.', 'error')
        return redirect(url_for('customer_login'))

    masked_email = mask_email(pending['email'])
    resend_blocked = (
        time.time() - pending.get('last_resend', 0)
    ) < OTP_RESEND_COOLDOWN

    if request.method == 'POST':
        entered = request.form.get('otp', '').strip()
        if entered != pending['otp']:
            pending['attempts_left'] -= 1
            session['pending_customer_login'] = pending
            session.modified = True
            if pending['attempts_left'] <= 0:
                session.pop('pending_customer_login', None)
                flash('Too many incorrect attempts. Please request a new login code.', 'error')
                return redirect(url_for('customer_login'))
            return render_template(
                'customer_otp.html',
                masked_email=masked_email,
                expire_seconds=seconds,
                attempts_left=pending['attempts_left'],
                resend_blocked=resend_blocked,
                error='Incorrect OTP. Please try again.',
            )

        session.pop('pending_customer_login', None)
        session['customer_id'] = pending['customer_id']
        session['customer_phone'] = pending['phone']
        session.modified = True
        return redirect(url_for('customer_portal'))

    return render_template(
        'customer_otp.html',
        masked_email=masked_email,
        expire_seconds=seconds,
        attempts_left=pending['attempts_left'],
        resend_blocked=resend_blocked,
        error=None,
    )


@app.route('/my/login/resend', methods=['POST'])
def customer_login_resend():
    pending = session.get('pending_customer_login')
    if not pending:
        flash('Please enter your phone number first.', 'error')
        return redirect(url_for('customer_login'))
    if (
        pending.get('expires_at', 0) <= time.time()
        or (time.time() - pending.get('last_resend', 0)) < OTP_RESEND_COOLDOWN
    ):
        return redirect(url_for('customer_login_verify'))

    new_otp = generate_otp()
    pending['otp'] = new_otp
    pending['expires_at'] = time.time() + OTP_EXPIRY_SECONDS
    pending['last_resend'] = time.time()
    pending['attempts_left'] = OTP_MAX_ATTEMPTS
    try:
        from notifications import send_otp_email
        email_sent = send_otp_email(
            pending['email'], new_otp, pending['name'], purpose='login'
        )
    except Exception as e:
        app.logger.error(f'Customer login OTP resend error: {e}')
        email_sent = False

    session['pending_customer_login'] = pending
    session.modified = True
    if email_sent:
        flash('A new login OTP has been sent to your email address.', 'success')
    else:
        flash('We could not resend the login verification email. Please try again.', 'error')
    return redirect(url_for('customer_login_verify'))


@app.route('/my')
@customer_required
def customer_portal():
    db    = get_db()
    cid   = session['customer_id']
    today = datetime.now().strftime('%Y-%m-%d')

    if expire_customer_loyalty(db, cid):
        db.commit()
    customer = db.execute('SELECT * FROM customers WHERE id=?', (cid,)).fetchone()

    # Upcoming appointments (today or future, confirmed/pending)
    upcoming = db.execute('''
        SELECT * FROM appointments
        WHERE customer_id=? AND date >= ?
        ORDER BY date ASC, time ASC
        LIMIT 10
    ''', (cid, today)).fetchall()

    # Past appointments (last 5 completed visits for "Book Again")
    past = db.execute('''
        SELECT * FROM appointments
        WHERE customer_id=? AND date < ?
        ORDER BY date DESC, time DESC
        LIMIT 5
    ''', (cid, today)).fetchall()

    # Last 5 invoices
    invoices = db.execute('''
        SELECT i.*, GROUP_CONCAT(ii.item_name, ', ') as items
        FROM invoices i
        LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
        WHERE i.customer_id=? OR i.customer_phone=?
        GROUP BY i.id
        ORDER BY i.created_at DESC
        LIMIT 5
    ''', (cid, customer['phone'] if customer else '')).fetchall()

    # Active membership
    membership = db.execute('''
        SELECT cm.*, mp.name as plan_name, mp.discount_pct,
               mp.validity_days, mp.description as plan_description
        FROM customer_memberships cm
        JOIN membership_plans mp ON mp.id = cm.plan_id
        WHERE cm.customer_id=? AND cm.status='active' AND cm.expiry_date >= ?
        ORDER BY cm.expiry_date DESC
        LIMIT 1
    ''', (cid, today)).fetchone()

    # Days until membership expiry
    expiry_days = None
    if membership:
        try:
            exp = datetime.strptime(membership['expiry_date'], '%Y-%m-%d')
            expiry_days = (exp - datetime.now()).days
        except Exception:
            pass

    return render_template('customer_portal.html',
                           customer=customer,
                           upcoming=upcoming,
                           past=past,
                           invoices=invoices,
                           membership=membership,
                           expiry_days=expiry_days,
                           today=today)

@app.route('/my/loyalty/reserve', methods=['POST'])
@customer_required
def customer_loyalty_reserve():
    """Customer reserves a specific number of points for their next visit.
    Points are immediately deducted from their balance and held as points_reserved.
    The admin invoice form shows this reservation so staff can apply it.
    """
    db  = get_db()
    cid = session['customer_id']

    if expire_customer_loyalty(db, cid):
        db.commit()

    try:
        amount = max(0, int(request.form.get('points_amount', 0) or 0))
    except (ValueError, TypeError):
        amount = 0

    if amount <= 0:
        flash('Please enter a valid number of points to reserve.', 'error')
        return redirect(url_for('customer_portal'))

    cust = db.execute(
        'SELECT loyalty_points, points_reserved FROM customers WHERE id=?', (cid,)
    ).fetchone()
    if not cust:
        flash('Customer record not found.', 'error')
        return redirect(url_for('customer_portal'))

    available = int(cust['loyalty_points'] or 0)
    currently_reserved = int(cust['points_reserved'] or 0)

    if amount > available:
        flash(f'You only have {available} points available.', 'error')
        return redirect(url_for('customer_portal'))

    # Refund any existing reservation first, then apply the new one
    # Net change = amount - currently_reserved
    net_deduction = amount - currently_reserved
    new_balance   = max(0, available - net_deduction)

    db.execute(
        '''UPDATE customers
           SET loyalty_points = ?, points_reserved = ?
           WHERE id=?''',
        (new_balance, amount, cid)
    )
    db.commit()
    flash(
        f'{amount} points (₹{amount} off) reserved for your next visit. '
        f'Tell the staff and they\'ll apply the discount when creating your invoice.',
        'success'
    )
    return redirect(url_for('customer_portal'))
@app.route('/my/appointments/<int:appt_id>/cancel', methods=['POST'])
@customer_required
def customer_cancel_appointment(appt_id):
    db  = get_db()
    cid = session['customer_id']

    # Fetch the appointment and verify ownership
    appt = db.execute(
        'SELECT * FROM appointments WHERE id=? AND customer_id=?', (appt_id, cid)
    ).fetchone()

    if not appt:
        flash('Appointment not found.', 'error')
        return redirect(url_for('customer_portal'))

    today = datetime.now().strftime('%Y-%m-%d')
    if appt['date'] < today:
        flash('You can only cancel upcoming appointments.', 'error')
        return redirect(url_for('customer_portal'))

    if appt['status'] == 'cancelled':
        flash('This appointment is already cancelled.', 'error')
        return redirect(url_for('customer_portal'))

    # Mark as cancelled
    db.execute(
        "UPDATE appointments SET status='cancelled' WHERE id=?", (appt_id,)
    )
    db.commit()

    # Notify owner
    try:
        from notifications import notify_cancellation
        notify_cancellation(
            appt['name'], appt['phone'], appt['service'],
            appt['date'], appt['time'], appt_id
        )
    except Exception as e:
        app.logger.warning(f'Cancellation notification error: {e}')

    # Send cancellation confirmation to the customer
    try:
        from notifications import send_cancellation_confirmation_email
        send_cancellation_confirmation_email(
            appt['email'], appt['name'], appt['service'],
            appt['date'], appt['time'], appt_id
        )
    except Exception as e:
        app.logger.warning(f'Customer cancellation email error: {e}')

    flash('Your appointment has been cancelled. We hope to see you again soon!', 'success')
    return redirect(url_for('customer_portal'))


@app.route('/my/appointments/<int:appt_id>/reschedule', methods=['GET', 'POST'])
@customer_required
def customer_reschedule_appointment(appt_id):
    db  = get_db()
    cid = session['customer_id']

    # Fetch the appointment and verify ownership
    appt = db.execute(
        'SELECT * FROM appointments WHERE id=? AND customer_id=?', (appt_id, cid)
    ).fetchone()

    if not appt:
        flash('Appointment not found.', 'error')
        return redirect(url_for('customer_portal'))

    today = datetime.now().strftime('%Y-%m-%d')
    if appt['date'] < today:
        flash('You can only reschedule upcoming appointments.', 'error')
        return redirect(url_for('customer_portal'))

    if appt['status'] == 'cancelled':
        flash('Cancelled appointments cannot be rescheduled.', 'error')
        return redirect(url_for('customer_portal'))

    errors = []

    if request.method == 'POST':
        new_date = request.form.get('date', '').strip()
        new_time = request.form.get('time', '').strip()

        if not new_date:
            errors.append('Please select a new date.')
        elif new_date < today:
            errors.append('Please choose a future date.')

        if not new_time:
            errors.append('Please select a time slot.')
        elif new_time not in TIME_SLOTS:
            errors.append('Invalid time slot selected.')

        # Check it's actually a change
        if not errors and new_date == appt['date'] and new_time == appt['time']:
            errors.append('The new date and time are the same as the current booking. Please choose a different slot.')

        if errors:
            return render_template('reschedule.html',
                                   appt=appt, today=today,
                                   time_slots=TIME_SLOTS,
                                   errors=errors,
                                   form={'date': new_date, 'time': new_time})

        # Update appointment in place
        db.execute(
            'UPDATE appointments SET date=?, time=? WHERE id=?',
            (new_date, new_time, appt_id)
        )
        db.commit()

        flash(
            f'Your {appt["service"]} appointment has been rescheduled to {new_date} at {new_time}.',
            'success'
        )
        return redirect(url_for('customer_portal'))

    return render_template('reschedule.html',
                           appt=appt, today=today,
                           time_slots=TIME_SLOTS,
                           errors=[],
                           form={'date': '', 'time': ''})


@app.route('/my/logout', methods=['POST'])
def customer_logout():
    session.pop('customer_id', None)
    session.pop('customer_phone', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('customer_login'))


# ─── Staff Portal ─────────────────────────────────────────────────────────────

def staff_portal_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('staff_portal_logged_in'):
            return redirect(url_for('staff_login'))
        return f(*args, **kwargs)
    return decorated


def get_staff_portal_context():
    return {
        'staff_name':      session.get('staff_portal_name', ''),
        'staff_id':        session.get('staff_portal_id'),
        'staff_branch_id': session.get('staff_portal_branch_id', 1),
    }


@app.route('/staff', methods=['GET', 'POST'])
def staff_login():
    if session.get('staff_portal_logged_in'):
        return redirect(url_for('staff_dashboard'))
    error = None
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        pin   = request.form.get('pin', '').strip()
        db    = get_db()
        staff = db.execute(
            'SELECT * FROM staff WHERE phone=? AND is_active=1 AND portal_pin IS NOT NULL',
            (phone,)
        ).fetchone()
        if staff and staff['portal_pin'] == pin:
            session['staff_portal_logged_in'] = True
            session['staff_portal_id']        = staff['id']
            session['staff_portal_name']      = staff['name']
            session['staff_portal_branch_id'] = staff['branch_id']
            return redirect(url_for('staff_dashboard'))
        error = 'Invalid phone number or PIN. Please check with your manager.'
    return render_template('staff_portal_login.html', error=error)


@app.route('/staff/logout')
def staff_logout():
    for k in ('staff_portal_logged_in', 'staff_portal_id',
              'staff_portal_name', 'staff_portal_branch_id'):
        session.pop(k, None)
    return redirect(url_for('staff_login'))


@app.route('/staff/dashboard')
@staff_portal_required
def staff_dashboard():
    db    = get_db()
    bid   = session['staff_portal_branch_id']
    sid   = session['staff_portal_id']
    today = datetime.now().strftime('%Y-%m-%d')
    stats = db.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(total),0) as rev
        FROM invoices WHERE staff_id=? AND branch_id=? AND DATE(created_at)=? AND status='paid'
    ''', (sid, bid, today)).fetchone()
    recent = db.execute('''
        SELECT id, customer_name, total, payment_method, created_at
        FROM invoices WHERE staff_id=? AND branch_id=?
        ORDER BY created_at DESC LIMIT 5
    ''', (sid, bid)).fetchall()
    return render_template('staff_portal_dashboard.html',
                           today_count=stats['cnt'], today_rev=stats['rev'],
                           recent_invoices=recent,
                           **get_staff_portal_context())


@app.route('/staff/invoice/new', methods=['GET', 'POST'])
@staff_portal_required
def staff_invoice_new():
    db  = get_db()
    bid = session['staff_portal_branch_id']
    sid = session['staff_portal_id']
    expire_all_customer_loyalty(db)
    db.commit()

    if request.method == 'POST':
        items_json     = request.form.get('items_json', '[]')
        customer_id    = request.form.get('customer_id') or None
        customer_name  = request.form.get('customer_name', '').strip()
        customer_phone = request.form.get('customer_phone', '').strip()
        payment_method = request.form.get('payment_method', 'cash')
        discount_type  = request.form.get('discount_type', 'flat')
        notes          = request.form.get('notes', '').strip()

        if payment_method not in ('cash', 'card', 'upi'):
            payment_method = 'cash'
        if discount_type not in ('flat', 'percent'):
            discount_type = 'flat'
        try:
            discount_value = max(0.0, float(request.form.get('discount_value', 0) or 0))
            gst_pct = float(request.form.get('gst_pct', 0) or 0)
            if gst_pct not in (0, 5, 12, 18):
                gst_pct = 0
        except (ValueError, TypeError):
            discount_value, gst_pct = 0.0, 0.0

        try:
            raw_items = json.loads(items_json)
            if not isinstance(raw_items, list) or not raw_items:
                raise ValueError('empty')
        except Exception:
            flash('Please add at least one service or product.', 'error')
            return redirect(url_for('staff_invoice_new'))

        validated_items = []
        for raw in raw_items:
            item_type = raw.get('type')
            item_id   = int(raw.get('item_id', 0))
            qty       = max(1, int(raw.get('qty', 1)))
            if item_type == 'service':
                row = db.execute(
                    'SELECT id, name, price FROM services WHERE id=? AND is_active=1', (item_id,)
                ).fetchone()
                if not row:
                    flash('Service not found.', 'error')
                    return redirect(url_for('staff_invoice_new'))
                validated_items.append({'type': 'service', 'service_id': row['id'],
                                        'product_id': None, 'name': row['name'],
                                        'price': float(row['price']), 'qty': qty})
            elif item_type == 'product':
                row = db.execute(
                    'SELECT id, name, sale_price FROM products WHERE id=? AND branch_id=? AND is_active=1',
                    (item_id, bid)
                ).fetchone()
                if not row:
                    flash('Product not found.', 'error')
                    return redirect(url_for('staff_invoice_new'))
                validated_items.append({'type': 'product', 'service_id': None,
                                        'product_id': row['id'], 'name': row['name'],
                                        'price': float(row['sale_price']), 'qty': qty})

        if customer_id:
            cust = db.execute(
                'SELECT name, phone FROM customers WHERE id=? AND branch_id=?', (customer_id, bid)
            ).fetchone()
            if cust:
                customer_name, customer_phone = cust['name'], cust['phone']
            else:
                customer_id = None

        subtotal        = sum(i['price'] * i['qty'] for i in validated_items)
        discount_amount = (subtotal * discount_value / 100) if discount_type == 'percent' \
                          else min(discount_value, subtotal)
        after_discount  = max(0.0, subtotal - discount_amount)
        gst_amount      = after_discount * gst_pct / 100
        total           = after_discount + gst_amount

        # Loyalty points redemption — validate and cap
        points_redeemed  = 0
        staff_cust_reserved = 0
        if customer_id:
            if expire_customer_loyalty(db, customer_id):
                db.commit()
            try:
                points_redeemed = max(0, int(request.form.get('points_redeemed', 0) or 0))
            except (ValueError, TypeError):
                points_redeemed = 0
            if points_redeemed > 0:
                cust_pts_row = db.execute(
                    'SELECT loyalty_points, points_reserved FROM customers WHERE id=?', (customer_id,)
                ).fetchone()
                if cust_pts_row:
                    remaining = int(cust_pts_row['loyalty_points'] or 0)
                    staff_cust_reserved = int(cust_pts_row['points_reserved'] or 0)
                    total_available = remaining + staff_cust_reserved
                else:
                    total_available = 0
                points_redeemed = min(points_redeemed, total_available, int(total))
                total = max(0.0, total - points_redeemed)

        cur = db.execute('''
            INSERT INTO invoices
                (customer_id, branch_id, staff_id, customer_name, customer_phone,
                 subtotal, discount_type, discount_value, discount_amount,
                 gst_pct, gst_amount, total, payment_method, status, notes, points_redeemed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (customer_id, bid, sid, customer_name or 'Walk-in', customer_phone,
              subtotal, discount_type, discount_value, discount_amount,
              gst_pct, gst_amount, total, payment_method, 'paid', notes, points_redeemed))
        inv_id = cur.lastrowid

        for item in validated_items:
            line_total = item['price'] * item['qty']
            db.execute('''INSERT INTO invoice_items
                          (invoice_id, item_type, item_name, unit_price, qty, line_total, service_id, product_id)
                          VALUES (?,?,?,?,?,?,?,?)''',
                       (inv_id, item['type'], item['name'], item['price'],
                        item['qty'], line_total, item['service_id'], item['product_id']))
            if item['type'] == 'product':
                db.execute(
                    'UPDATE products SET stock_qty = MAX(0, stock_qty - ?) WHERE id=? AND branch_id=?',
                    (item['qty'], item['product_id'], bid)
                )

        if customer_id:
            db.execute(
                '''UPDATE customers SET total_spend=total_spend+?,
                   visit_count=visit_count+1, last_visit_date=DATE('now') WHERE id=?''',
                (total, customer_id)
            )
            award_invoice_loyalty_points(db, customer_id, total)
            if points_redeemed > 0:
                # loyalty_points already had staff_cust_reserved deducted; restore reservation
                # then apply the full redemption so the net balance change is correct.
                db.execute(
                    '''UPDATE customers
                       SET loyalty_points = MAX(0, loyalty_points + points_reserved - ?),
                           points_reserved = 0
                       WHERE id=?''',
                    (points_redeemed, customer_id)
                )
            elif staff_cust_reserved > 0:
                # No points redeemed — refund the reservation back to balance
                db.execute(
                    '''UPDATE customers
                       SET loyalty_points = loyalty_points + points_reserved,
                           points_reserved = 0
                       WHERE id=?''',
                    (customer_id,)
                )
        db.commit()
        flash(f'Invoice #{inv_id} created successfully!', 'success')
        return redirect(url_for('staff_invoice_detail', inv_id=inv_id))

    # GET — build catalogue
    cats = db.execute(
        'SELECT id, name FROM service_categories WHERE is_active=1 ORDER BY sort_order'
    ).fetchall()
    services = []
    for cat in cats:
        for s in db.execute(
            'SELECT id, name, price FROM services WHERE category_id=? AND is_active=1 ORDER BY sort_order',
            (cat['id'],)
        ).fetchall():
            services.append({'id': s['id'], 'name': s['name'],
                             'price': float(s['price']), 'category': cat['name']})

    products = []
    for p in db.execute(
        'SELECT id, name, sale_price, stock_qty, unit FROM products WHERE is_active=1 AND branch_id=? ORDER BY name',
        (bid,)
    ).fetchall():
        products.append({'id': p['id'], 'name': p['name'], 'price': float(p['sale_price']),
                        'stock': p['stock_qty'], 'unit': p['unit']})

    customers = [dict(r) for r in db.execute(
        '''SELECT id, name, phone, loyalty_points, points_reserved,
                  loyalty_points_expiry_date
           FROM customers WHERE branch_id=? ORDER BY name''',
        (bid,)
    ).fetchall()]
    # Compute total_available for each customer (loyalty_points already has reservation deducted)
    for c in customers:
        c['total_points'] = (c['loyalty_points'] or 0) + (c['points_reserved'] or 0)

    return render_template('staff_portal_invoice_new.html',
                           services_json=json.dumps(services),
                           products_json=json.dumps(products),
                           customers_json=json.dumps(customers),
                           **get_staff_portal_context())


@app.route('/staff/invoice/<int:inv_id>')
@staff_portal_required
def staff_invoice_detail(inv_id):
    db  = get_db()
    bid = session['staff_portal_branch_id']
    sid = session['staff_portal_id']
    invoice = db.execute(
        'SELECT * FROM invoices WHERE id=? AND branch_id=? AND staff_id=?', (inv_id, bid, sid)
    ).fetchone()
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('staff_dashboard'))
    items = db.execute('SELECT * FROM invoice_items WHERE invoice_id=?', (inv_id,)).fetchall()
    return render_template('staff_portal_invoice.html',
                           invoice=invoice, items=items,
                           **get_staff_portal_context())


# Admin: set staff portal PIN (manager+ only — staff management action)
@app.route('/admin/staff/<int:staff_id>/set-pin', methods=['POST'])
@manager_required
def admin_staff_set_pin(staff_id):
    db  = get_db()
    bid = get_current_branch_id()
    pin = request.form.get('pin', '').strip()
    if not pin.isdigit() or not (4 <= len(pin) <= 6):
        flash('PIN must be 4–6 digits.', 'error')
        return redirect(url_for('admin_staff'))
    staff = db.execute('SELECT id FROM staff WHERE id=? AND branch_id=?', (staff_id, bid)).fetchone()
    if not staff:
        flash('Staff not found.', 'error')
        return redirect(url_for('admin_staff'))
    db.execute('UPDATE staff SET portal_pin=? WHERE id=?', (pin, staff_id))
    db.commit()
    flash('Portal PIN updated successfully.', 'success')
    return redirect(url_for('admin_staff'))


# ─── Admin: team management ──────────────────────────────────────────────────

@app.route('/admin/team')
@owner_required
def admin_team():
    db = get_db()
    users = db.execute(
        'SELECT * FROM admin_users ORDER BY role DESC, full_name'
    ).fetchall()
    return render_template('admin_team.html', users=users, **get_admin_context())


@app.route('/admin/team/add', methods=['POST'])
@owner_required
def admin_team_add():
    db = get_db()
    email     = request.form.get('email', '').strip().lower()
    full_name = request.form.get('full_name', '').strip()
    role      = request.form.get('role', 'receptionist')
    password  = request.form.get('password', '').strip()

    if not email or not full_name or not password:
        flash('Email, name, and password are all required.', 'error')
        return redirect(url_for('admin_team'))
    if role not in ('owner', 'manager', 'receptionist'):
        role = 'receptionist'
    if len(password) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('admin_team'))

    existing = db.execute('SELECT id FROM admin_users WHERE email=?', (email,)).fetchone()
    if existing:
        flash('An account with that email already exists.', 'error')
        return redirect(url_for('admin_team'))

    db.execute('''INSERT INTO admin_users (email, password_hash, full_name, role)
                  VALUES (?, ?, ?, ?)''',
               (email, generate_password_hash(password), full_name, role))
    db.commit()
    flash(f'Account for {full_name} created successfully.', 'success')
    return redirect(url_for('admin_team'))


@app.route('/admin/team/<int:user_id>/toggle', methods=['POST'])
@owner_required
def admin_team_toggle(user_id):
    db = get_db()
    user = db.execute(
        'SELECT * FROM admin_users WHERE id=?', (user_id,)
    ).fetchone()
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_team'))
    if user['id'] == session.get('admin_user_id'):
        flash('You cannot disable your own account.', 'error')
        return redirect(url_for('admin_team'))
    new_state = 0 if user['is_active'] else 1
    db.execute('UPDATE admin_users SET is_active=? WHERE id=?', (new_state, user_id))
    db.commit()
    flash(f'Account {"enabled" if new_state else "disabled"}.', 'success')
    return redirect(url_for('admin_team'))


@app.route('/admin/team/<int:user_id>/reset-password', methods=['POST'])
@owner_required
def admin_team_reset_password(user_id):
    db = get_db()
    user = db.execute(
        'SELECT id FROM admin_users WHERE id=?', (user_id,)
    ).fetchone()
    password  = request.form.get('new_password', '').strip()
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_team'))
    if len(password) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('admin_team'))
    db.execute('UPDATE admin_users SET password_hash=? WHERE id=?',
               (generate_password_hash(password), user_id))
    db.commit()
    flash('Password updated successfully.', 'success')
    return redirect(url_for('admin_team'))


@app.route('/admin/team/<int:user_id>/change-role', methods=['POST'])
@owner_required
def admin_team_change_role(user_id):
    db = get_db()
    user = db.execute(
        'SELECT * FROM admin_users WHERE id=?', (user_id,)
    ).fetchone()
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_team'))
    if user['id'] == session.get('admin_user_id'):
        flash('You cannot change your own role.', 'error')
        return redirect(url_for('admin_team'))
    new_role = request.form.get('role', 'receptionist')
    if new_role not in ('owner', 'manager', 'receptionist'):
        new_role = 'receptionist'
    db.execute('UPDATE admin_users SET role=? WHERE id=?', (new_role, user_id))
    db.commit()
    flash('Role updated.', 'success')
    return redirect(url_for('admin_team'))


# ─── Admin: Desktop Update Settings ──────────────────────────────────────────

@app.route('/admin/desktop-settings')
@owner_required
def admin_desktop_settings():
    """
    UI for controlling when auto-updates install on the desktop build.
    Only meaningful when running inside Electron (FLASK_DESKTOP_MODE=1).
    The actual preferences are persisted by the Electron main process via IPC;
    this page simply provides the owner-facing form.
    """
    return render_template('admin_desktop_settings.html', **get_admin_context())


# ─── Admin: Branding ──────────────────────────────────────────────────────────

@app.route('/admin/branding', methods=['GET', 'POST'])
@owner_required
def admin_branding():
    db = get_db()
    salon = db.execute('SELECT * FROM salon_settings WHERE id=1').fetchone()
    if request.method == 'POST':
        name          = request.form.get('name', '').strip()
        tagline       = request.form.get('tagline', '').strip()
        primary_color = request.form.get('primary_color', '#c9a96e').strip()
        accent_color  = request.form.get('accent_color', '#d4af37').strip()
        bg_color      = request.form.get('bg_color', '#0a0a0a').strip()
        phone         = request.form.get('phone', '').strip()
        email         = request.form.get('email', '').strip()
        address       = request.form.get('address', '').strip()
        if not name:
            flash('Salon name is required.', 'error')
            return redirect(url_for('admin_branding'))
        # Logo upload
        logo_filename = (salon['logo_filename'] if salon else None) or 'brand-logo.jpeg'
        logo_file = request.files.get('logo')
        if logo_file and logo_file.filename:
            ext = logo_file.filename.rsplit('.', 1)[-1].lower()
            if ext in LOGO_ALLOWED_EXTENSIONS:
                safe_name = f"salon_logo.{ext}"
                os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
                logo_file.save(os.path.join(LOGO_UPLOAD_DIR, safe_name))
                logo_filename = safe_name
        db.execute('''UPDATE salon_settings
                      SET name=?, tagline=?, logo_filename=?,
                          primary_color=?, accent_color=?, bg_color=?,
                          phone=?, email=?, address=?
                      WHERE id=1''',
                   (name, tagline, logo_filename,
                    primary_color, accent_color, bg_color,
                     phone, email, address))
        db.commit()
        flash('Branding updated successfully!', 'success')
        return redirect(url_for('admin_branding'))
    return render_template('admin_branding.html', **get_admin_context())


@app.route('/my/loyalty/clear', methods=['POST'])
@customer_required
def customer_loyalty_clear():
    """Cancels the customer's active points reservation and refunds the points."""
    db  = get_db()
    cid = session['customer_id']

    if expire_customer_loyalty(db, cid):
        db.commit()

    cust = db.execute(
        'SELECT loyalty_points, points_reserved FROM customers WHERE id=?', (cid,)
    ).fetchone()
    if not cust:
        flash('Customer record not found.', 'error')
        return redirect(url_for('customer_portal'))

    reserved = int(cust['points_reserved'] or 0)
    if reserved <= 0:
        flash('No active points reservation to cancel.', 'error')
        return redirect(url_for('customer_portal'))

    # Refund reserved points back to balance
    db.execute(
        '''UPDATE customers
           SET loyalty_points = loyalty_points + ?, points_reserved = 0
           WHERE id=?''',
        (reserved, cid)
    )
    db.commit()
    flash(f'{reserved} reserved points have been returned to your balance.', 'success')
    return redirect(url_for('customer_portal'))


# ─── Admin: Messaging & Notifications ────────────────────────────────────────

@app.route('/admin/messaging', methods=['GET', 'POST'])
@owner_required
def admin_messaging():
    from notifications import encrypt_cred, decrypt_cred
    db = get_db()

    # Ensure the row exists
    db.execute(
        'INSERT OR IGNORE INTO messaging_credentials (id) VALUES (1)'
    )
    db.commit()

    row = db.execute('SELECT * FROM messaging_credentials WHERE id=1').fetchone()

    if request.method == 'POST':
        try:
            def _save(field, encrypted=False):
                val = request.form.get(field, '').strip()
                if val:
                    return encrypt_cred(val) if encrypted else val
                # Keep existing value if the user left the field blank
                return row[field] if row else ''

            gmail_user        = request.form.get('gmail_user', '').strip() or (row['gmail_user'] if row else '')
            gmail_password    = _save('gmail_password',    encrypted=True)
            twilio_sid        = _save('twilio_sid',        encrypted=True)
            twilio_token      = _save('twilio_token',      encrypted=True)
            twilio_number     = request.form.get('twilio_number', '').strip() or (row['twilio_number'] if row else '')
            whatsapp_token    = _save('whatsapp_token',    encrypted=True)
            whatsapp_phone_id = request.form.get('whatsapp_phone_id', '').strip() or (row['whatsapp_phone_id'] if row else '')
            whatsapp_otp_tmpl = request.form.get('whatsapp_otp_template', '').strip() or 'vanshika_otp'
            fast2sms_key      = _save('fast2sms_key',      encrypted=True)
        except RuntimeError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('admin_messaging'))

        db.execute('''UPDATE messaging_credentials
                      SET gmail_user=?, gmail_password=?,
                          twilio_sid=?, twilio_token=?, twilio_number=?,
                          whatsapp_token=?, whatsapp_phone_id=?, whatsapp_otp_template=?,
                          fast2sms_key=?, updated_at=CURRENT_TIMESTAMP
                      WHERE id=1''',
                   (gmail_user, gmail_password,
                    twilio_sid, twilio_token, twilio_number,
                    whatsapp_token, whatsapp_phone_id, whatsapp_otp_tmpl,
                    fast2sms_key))
        db.commit()

        # Invalidate the in-process creds cache so next notification uses new values
        import notifications as _notify_mod
        _notify_mod.invalidate_creds_cache()

        flash('Messaging credentials saved successfully!', 'success')
        return redirect(url_for('admin_messaging'))

    # Build a "status" dict for the template.
    # For each encrypted field, distinguish three states:
    #   None  → not stored in DB at all
    #   True  → stored and decrypts successfully with current SESSION_SECRET
    #   False → stored but decryption fails (SESSION_SECRET mismatch / tampered)
    def _decrypt_status(field):
        if not (row and row[field]):
            return None
        decrypted = decrypt_cred(row[field])
        return bool(decrypted)

    gp  = _decrypt_status('gmail_password')
    ts  = _decrypt_status('twilio_sid')
    tt  = _decrypt_status('twilio_token')
    wat = _decrypt_status('whatsapp_token')
    f2s = _decrypt_status('fast2sms_key')

    # Collect human-readable names of fields that are stored but cannot be decrypted
    _MISMATCH_LABELS = [
        ('Gmail Password',   gp),
        ('Twilio SID',       ts),
        ('Twilio Auth Token',tt),
        ('WhatsApp Token',   wat),
        ('Fast2SMS Key',     f2s),
    ]
    key_mismatch_fields = [label for label, ok in _MISMATCH_LABELS if ok is False]

    cred_status = {
        'gmail_user':               row['gmail_user'] if row else '',
        'gmail_password':           gp is True,
        'gmail_password_mismatch':  gp is False,
        'twilio_sid':               ts is True,
        'twilio_sid_mismatch':      ts is False,
        'twilio_token':             tt is True,
        'twilio_token_mismatch':    tt is False,
        'twilio_number':            row['twilio_number'] if row else '',
        'whatsapp_token':           wat is True,
        'whatsapp_token_mismatch':  wat is False,
        'whatsapp_phone_id':        row['whatsapp_phone_id'] if row else '',
        'whatsapp_otp_template':    row['whatsapp_otp_template'] if row else 'vanshika_otp',
        'fast2sms_key':             f2s is True,
        'fast2sms_key_mismatch':    f2s is False,
        'key_mismatch':             bool(key_mismatch_fields),
        'key_mismatch_fields':      key_mismatch_fields,
    }
    return render_template('admin_messaging.html', cred_status=cred_status, **get_admin_context())


@app.route('/admin/messaging/test/<channel>', methods=['POST'])
@owner_required
def admin_messaging_test(channel):
    """
    Send a test message on the given channel to verify credentials work.

    The endpoint accepts live credential values from the form so the admin can
    test before saving.  Any field left blank falls back to the saved DB value
    (or env var if nothing is in the DB yet).
    """
    from notifications import get_messaging_creds, send_booking_email, \
        send_booking_sms, _send_whatsapp, send_booking_otp_sms
    import json as _json

    # Build a creds dict: start from DB/env, then override with form values
    base = get_messaging_creds()
    creds = dict(base)

    def _f(key):
        return request.form.get(key, '').strip()

    # Override only with non-empty submitted values
    for key in ('gmail_user', 'gmail_password', 'twilio_sid', 'twilio_token',
                'twilio_number', 'whatsapp_token', 'whatsapp_phone_id', 'fast2sms_key'):
        v = _f(key)
        if v:
            creds[key] = v

    ok = False
    result = 'Unknown channel.'

    if channel == 'email':
        owner_to = creds.get('owner_email', base['owner_email'])
        ok = send_booking_email(
            name='Test Admin', phone='+91 00000 00000',
            service='Test Service', date='2099-01-01', time='10:00 AM',
            customer_email='', _creds=creds,
        )
        result = (f'Test email sent to {owner_to}.' if ok
                  else 'Email send failed — check credentials and logs.')

    elif channel == 'sms':
        owner_phone = creds.get('owner_phone', base['owner_phone'])
        ok = send_booking_sms(
            name='Test Admin', phone='+91 00000 00000',
            service='Test Service', date='2099-01-01', time='10:00 AM',
            _creds=creds,
        )
        result = (f'Test SMS sent to {owner_phone} via Twilio.' if ok
                  else 'SMS send failed — check Twilio credentials and logs.')

    elif channel == 'whatsapp':
        test_phone = _f('test_phone')
        if not test_phone:
            return _json.dumps({'ok': False, 'message': 'Enter a phone number (with country code, no +) to test WhatsApp.'}), 400, {'Content-Type': 'application/json'}
        payload = {
            'messaging_product': 'whatsapp',
            'to': test_phone.replace('+', '').replace(' ', ''),
            'type': 'text',
            'text': {'body': '👑 Vanshika Makeover Academy\n\nThis is a test message to confirm your WhatsApp integration is working correctly. ✅'},
        }
        ok, msg_or_err = _send_whatsapp(payload, _creds=creds)
        result = (f'WhatsApp test sent (id: {msg_or_err})' if ok
                  else f'WhatsApp failed: {msg_or_err}')

    elif channel == 'fast2sms':
        test_phone = _f('test_phone')
        if not test_phone:
            return _json.dumps({'ok': False, 'message': 'Enter a 10-digit mobile number to test Fast2SMS.'}), 400, {'Content-Type': 'application/json'}
        ok = send_booking_otp_sms(test_phone, '123456', 'Test Admin', _creds=creds)
        result = ('Fast2SMS test OTP sent (code: 123456).' if ok
                  else 'Fast2SMS failed — check API key and logs.')

    else:
        return _json.dumps({'ok': False, 'message': 'Unknown channel.'}), 400, {'Content-Type': 'application/json'}

    return _json.dumps({'ok': ok, 'message': result}), 200, {'Content-Type': 'application/json'}


# ─── Bootstrap ───────────────────────────────────────────────────────────────

init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

