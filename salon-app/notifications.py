"""
Vanshika Makeover Academy — Notification helpers
Sends email + SMS to the salon owner when a booking is made.

Credentials are loaded from the messaging_credentials DB table (per-tenant),
falling back to environment variables for backward compatibility.

Encryption at rest:
  Each credential is encrypted with a unique random 32-byte salt, a key derived
  via PBKDF2-HMAC-SHA256(SESSION_SECRET, salt, 100 000 iterations), and
  authenticated with HMAC-SHA256 before base64 storage.  SESSION_SECRET *must*
  be set in the environment; the app will refuse to save credentials without it.
"""

import os
import hmac as _hmac
import hashlib
import base64
import sqlite3
import smtplib
import logging
import time
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── DB path (mirrors app.py logic) ──────────────────────────────────────────

_APP_DIR  = os.path.dirname(__file__)
_DATA_DIR = os.environ.get('SALON_DATA_DIR', '')
_DB_PATH  = os.path.join(_DATA_DIR or _APP_DIR, 'salon.db')


# ─── Credential encryption ────────────────────────────────────────────────────

_PBKDF2_ITERS = 100_000
_SALT_LEN     = 32
_MAC_LEN      = 32


def _require_secret() -> bytes:
    """Return SESSION_SECRET as bytes, raising if it is unset or default."""
    secret = os.environ.get('SESSION_SECRET', '').strip()
    if not secret:
        raise RuntimeError(
            'SESSION_SECRET environment variable must be set before messaging '
            'credentials can be stored.  Generate a strong random secret and '
            'set it in your environment / Replit Secrets.'
        )
    return secret.encode('utf-8')


def _derive_key(secret: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', secret, salt, _PBKDF2_ITERS)


def encrypt_cred(value: str) -> str:
    """
    Encrypt a credential for DB storage.
    Format (base64): salt[32] | hmac[32] | ciphertext
    Raises RuntimeError if SESSION_SECRET is not set.
    """
    if not value:
        return ''
    secret     = _require_secret()
    salt       = os.urandom(_SALT_LEN)
    key        = _derive_key(secret, salt)
    data       = value.encode('utf-8')
    # Counter-mode keystream from SHA-256
    stream = b''
    ctr = 0
    while len(stream) < len(data):
        stream += hashlib.sha256(key + ctr.to_bytes(4, 'big')).digest()
        ctr += 1
    ciphertext = bytes(a ^ b for a, b in zip(data, stream[:len(data)]))
    mac = _hmac.new(key, salt + ciphertext, 'sha256').digest()
    return base64.b64encode(salt + mac + ciphertext).decode('ascii')


def decrypt_cred(value: str) -> str:
    """
    Decrypt a stored credential.  Returns '' on any failure (wrong key, tampered
    data, or missing SESSION_SECRET).
    """
    if not value:
        return ''
    try:
        secret = _require_secret()
        raw    = base64.b64decode(value.encode('ascii'))
        if len(raw) < _SALT_LEN + _MAC_LEN:
            return ''
        salt       = raw[:_SALT_LEN]
        mac_stored = raw[_SALT_LEN:_SALT_LEN + _MAC_LEN]
        ciphertext = raw[_SALT_LEN + _MAC_LEN:]
        key        = _derive_key(secret, salt)
        mac_calc   = _hmac.new(key, salt + ciphertext, 'sha256').digest()
        if not _hmac.compare_digest(mac_stored, mac_calc):
            logging.error(
                '[Notify] Credential MAC mismatch — SESSION_SECRET may have '
                'changed.  Re-enter credentials in Admin → Messaging.'
            )
            return ''
        stream = b''
        ctr = 0
        while len(stream) < len(ciphertext):
            stream += hashlib.sha256(key + ctr.to_bytes(4, 'big')).digest()
            ctr += 1
        return bytes(a ^ b for a, b in zip(ciphertext, stream[:len(ciphertext)])).decode('utf-8')
    except RuntimeError:
        logging.warning('[Notify] SESSION_SECRET not set — cannot decrypt credential')
        return ''
    except Exception as exc:
        logging.error(f'[Notify] Credential decryption error: {exc}')
        return ''


# ─── Credential loader with in-memory cache ───────────────────────────────────

_creds_cache: dict | None = None
_creds_cache_ts: float = 0.0
_CREDS_CACHE_TTL: float = 300.0   # 5 minutes


def invalidate_creds_cache():
    """Call after saving new credentials so the next send reloads them."""
    global _creds_cache, _creds_cache_ts
    _creds_cache    = None
    _creds_cache_ts = 0.0


def get_messaging_creds() -> dict:
    """
    Load messaging credentials from the DB (id=1 row), decrypting stored values.
    Falls back to environment variables for any credential not set in the DB.
    Also loads owner_email / owner_phone from salon_settings for alert routing.
    Results are cached for 5 minutes to amortise PBKDF2 cost.
    """
    global _creds_cache, _creds_cache_ts
    now = time.monotonic()
    if _creds_cache is not None and (now - _creds_cache_ts) < _CREDS_CACHE_TTL:
        return _creds_cache

    # Defaults from environment variables
    creds: dict = {
        'owner_email':           os.environ.get('OWNER_EMAIL', 'info.vanshikamakeoveracademy@gmail.com'),
        'owner_phone':           os.environ.get('OWNER_PHONE', '+919582206858'),
        'gmail_user':            os.environ.get('GMAIL_USER', 'info.vanshikamakeoveracademy@gmail.com'),
        'gmail_password':        ''.join(os.environ.get('GMAIL_APP_PASSWORD', '').split()),
        'twilio_sid':            os.environ.get('TWILIO_ACCOUNT_SID', ''),
        'twilio_token':          os.environ.get('TWILIO_AUTH_TOKEN', ''),
        'twilio_number':         os.environ.get('TWILIO_FROM_NUMBER', ''),
        'whatsapp_token':        os.environ.get('WHATSAPP_ACCESS_TOKEN', ''),
        'whatsapp_phone_id':     os.environ.get('WHATSAPP_PHONE_NUMBER_ID', ''),
        'fast2sms_key':          os.environ.get('FAST2SMS_API_KEY', ''),
        'whatsapp_otp_template': os.environ.get('WHATSAPP_OTP_TEMPLATE', 'vanshika_otp'),
    }

    try:
        con = sqlite3.connect(_DB_PATH)
        con.row_factory = sqlite3.Row

        # Owner contact details from salon_settings
        salon = con.execute('SELECT email, phone FROM salon_settings WHERE id=1').fetchone()
        if salon:
            if salon['email']:
                creds['owner_email'] = salon['email']
            if salon['phone']:
                creds['owner_phone'] = salon['phone']

        # Messaging credentials
        row = con.execute('SELECT * FROM messaging_credentials WHERE id=1').fetchone()
        con.close()

        if row:
            if row['gmail_user']:
                creds['gmail_user'] = row['gmail_user']
            if row['gmail_password']:
                creds['gmail_password'] = decrypt_cred(row['gmail_password'])
            if row['twilio_sid']:
                creds['twilio_sid'] = decrypt_cred(row['twilio_sid'])
            if row['twilio_token']:
                creds['twilio_token'] = decrypt_cred(row['twilio_token'])
            if row['twilio_number']:
                creds['twilio_number'] = row['twilio_number']
            if row['whatsapp_token']:
                creds['whatsapp_token'] = decrypt_cred(row['whatsapp_token'])
            if row['whatsapp_phone_id']:
                creds['whatsapp_phone_id'] = row['whatsapp_phone_id']
            if row['fast2sms_key']:
                creds['fast2sms_key'] = decrypt_cred(row['fast2sms_key'])
            if row['whatsapp_otp_template']:
                creds['whatsapp_otp_template'] = row['whatsapp_otp_template']

    except Exception as exc:
        logging.debug(f'[Notify] Could not load DB messaging creds: {exc}')

    _creds_cache    = creds
    _creds_cache_ts = now
    return creds


# ─── Email via Gmail SMTP ─────────────────────────────────────────────────────

def send_booking_email(name, phone, service, date, time, customer_email="", _creds=None):
    """Send a rich HTML email to the salon owner for every new booking."""
    creds      = _creds or get_messaging_creds()
    gmail_pass = creds['gmail_password']
    gmail_user = creds['gmail_user']
    owner_to   = creds['owner_email']

    if not gmail_pass:
        logging.warning("[Notify] Gmail app password not configured — email skipped")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"\U0001f4c5 New Booking: {name} \u2014 {service}"
        msg["From"]    = f"Vanshika Makeover Academy <{gmail_user}>"
        msg["To"]      = owner_to

        plain = (
            f"New appointment booking received!\n\n"
            f"Customer : {name}\n"
            f"Phone    : {phone}\n"
            f"Email    : {customer_email or 'Not provided'}\n"
            f"Service  : {service}\n"
            f"Date     : {date}\n"
            f"Time     : {time}\n\n"
            f"Login to /admin to manage all bookings."
        )

        html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#fff0f6;font-family:Arial,sans-serif;">
<div style="max-width:520px;margin:32px auto;background:#ffffff;border-radius:16px;
            overflow:hidden;box-shadow:0 4px 24px rgba(180,83,9,0.12);">

  <div style="background:linear-gradient(135deg,#9d174d 0%,#be185d 60%,#b45309 100%);
              padding:28px 24px;text-align:center;">
    <div style="font-size:36px;margin-bottom:8px;">&#128081;</div>
    <h1 style="color:#fbbf24;margin:0;font-size:22px;font-weight:700;
               font-family:Georgia,serif;">New Appointment Booked!</h1>
    <p style="color:#fce7f3;margin:6px 0 0;font-size:12px;letter-spacing:2px;">
      VANSHIKA MAKEOVER ACADEMY
    </p>
  </div>

  <div style="height:3px;background:linear-gradient(90deg,#92400e,#fbbf24,#d97706,#fbbf24,#92400e);"></div>

  <div style="padding:28px 24px;">
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <tr>
        <td style="padding:11px 12px;color:#9ca3af;width:38%;">&#128100; Customer</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{name}</td>
      </tr>
      <tr style="background:#fffbf0;">
        <td style="padding:11px 12px;color:#9ca3af;">&#128222; Phone</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{phone}</td>
      </tr>
      <tr>
        <td style="padding:11px 12px;color:#9ca3af;">&#9993; Email</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{customer_email or 'Not provided'}</td>
      </tr>
      <tr>
        <td style="padding:11px 12px;color:#9ca3af;">&#10024; Service</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{service}</td>
      </tr>
      <tr style="background:#fffbf0;">
        <td style="padding:11px 12px;color:#9ca3af;">&#128197; Date</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{date}</td>
      </tr>
      <tr>
        <td style="padding:11px 12px;color:#9ca3af;">&#128336; Time</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{time}</td>
      </tr>
    </table>
  </div>

  <div style="background:linear-gradient(135deg,#fef3c7,#fde68a);
              padding:18px 24px;text-align:center;">
    <p style="color:#92400e;margin:0;font-size:13px;font-weight:600;">
      &#128081; Open Admin Dashboard to confirm the appointment slot
    </p>
  </div>

</div>
</body>
</html>"""

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html,  "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, owner_to, msg.as_string())

        logging.info(f"[Notify] Email sent → {owner_to} for booking by {name}")
        return True

    except Exception as exc:
        logging.error(f"[Notify] Email failed: {exc}")
        return False


# ─── SMS via Twilio ───────────────────────────────────────────────────────────

def send_booking_sms(name, phone, service, date, time, _creds=None):
    """Send an SMS to the salon owner via Twilio."""
    creds       = _creds or get_messaging_creds()
    account_sid = creds['twilio_sid']
    auth_token  = creds['twilio_token']
    from_number = creds['twilio_number']
    owner_phone = creds['owner_phone']

    if not (account_sid and auth_token and from_number):
        logging.warning("[Notify] Twilio credentials not configured — SMS skipped")
        return False

    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)

        body = (
            f"Vanshika Makeover Academy\n"
            f"New Booking!\n\n"
            f"Name   : {name}\n"
            f"Phone  : {phone}\n"
            f"Service: {service}\n"
            f"Date   : {date}\n"
            f"Time   : {time}"
        )

        message = client.messages.create(
            body=body,
            from_=from_number,
            to=owner_phone,
        )

        logging.info(f"[Notify] SMS sent → {owner_phone} (SID: {message.sid})")
        return True

    except Exception as exc:
        logging.error(f"[Notify] SMS failed: {exc}")
        return False


# ─── WhatsApp via Meta Cloud API ─────────────────────────────────────────────

def _send_whatsapp(payload, _creds=None):
    """
    Low-level POST to Meta WhatsApp Cloud API.
    Returns (True, msg_id) on success or (False, error_message) on failure.
    """
    creds    = _creds or get_messaging_creds()
    token    = creds['whatsapp_token']
    phone_id = creds['whatsapp_phone_id']

    if not (token and phone_id):
        return False, "WhatsApp token or phone ID not configured"

    try:
        import requests as req
        r = req.post(
            f"https://graph.facebook.com/v20.0/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        data = r.json()
        if r.status_code in (200, 201):
            msg_id = data.get("messages", [{}])[0].get("id", "")
            return True, msg_id
        return False, data.get("error", {}).get("message", str(data))
    except Exception as exc:
        return False, str(exc)


def _wa_to(phone):
    """Convert 10-digit Indian number to WhatsApp E.164 (no +)."""
    p = phone.replace("+", "").replace(" ", "")
    return f"91{p}" if len(p) == 10 else p


def send_otp_whatsapp(phone, otp, name, _creds=None):
    """Send OTP to customer via WhatsApp using Meta Cloud API."""
    creds         = _creds or get_messaging_creds()
    template_name = creds['whatsapp_otp_template']
    payload = {
        "messaging_product": "whatsapp",
        "to": _wa_to(phone),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": otp}],
                },
            ],
        },
    }
    ok, result = _send_whatsapp(payload, _creds=creds)
    if ok:
        logging.info(f"[Notify] WhatsApp OTP sent → +91{phone} (id: {result})")
    else:
        logging.error(f"[Notify] WhatsApp OTP failed: {result}")
    return ok


def send_booking_confirmation_whatsapp(phone, name, service, date, appt_time, booking_id, _creds=None):
    """Send booking confirmation to customer via WhatsApp (freeform text)."""
    creds = _creds or get_messaging_creds()
    payload = {
        "messaging_product": "whatsapp",
        "to": _wa_to(phone),
        "type": "text",
        "text": {
            "body": (
                f"👑 *Vanshika Makeover Academy*\n"
                f"✅ *Booking Confirmed!*\n\n"
                f"🔖 Booking ID : *#VMA-{booking_id}*\n"
                f"👤 Name       : {name}\n"
                f"✨ Service    : {service}\n"
                f"📅 Date       : {date}\n"
                f"🕐 Time       : {appt_time}\n\n"
                f"Please arrive 10 mins early. See you soon! 💅"
            )
        },
    }
    ok, result = _send_whatsapp(payload, _creds=creds)
    if ok:
        logging.info(f"[Notify] WhatsApp confirmation sent → +91{phone} (id: {result})")
    else:
        logging.error(f"[Notify] WhatsApp confirmation failed: {result}")
    return ok


# ─── Booking OTP via Fast2SMS ─────────────────────────────────────────────────

def send_booking_otp_sms(phone, otp, name, _creds=None):
    """Send the booking verification OTP to the customer's mobile via Fast2SMS."""
    creds   = _creds or get_messaging_creds()
    api_key = creds['fast2sms_key']

    if not api_key:
        logging.error("[Notify] Fast2SMS API key not configured — booking OTP not sent")
        return False

    number = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(number) != 10:
        logging.error("[Notify] Invalid mobile number — booking OTP not sent")
        return False

    try:
        response = requests.post(
            "https://www.fast2sms.com/dev/bulkV2",
            headers={
                "authorization": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "route": "otp",
                "variables_values": otp,
                "numbers": number,
            },
            timeout=10,
        )
        data = response.json()
        if response.ok and data.get("return") is True:
            logging.info("[Notify] Fast2SMS booking OTP sent to mobile ending %s", number[-4:])
            return True

        message = data.get("message", "Fast2SMS rejected the request")
        logging.error("[Notify] Fast2SMS booking OTP failed: %s", message)
        return False
    except Exception as exc:
        logging.error("[Notify] Fast2SMS booking OTP failed: %s", exc)
        return False


# ─── OTP email to customer ────────────────────────────────────────────────────

def send_otp_email(customer_email, otp, name, purpose="booking", _creds=None):
    """Send a booking or customer-login OTP to the customer's email."""
    creds      = _creds or get_messaging_creds()
    gmail_pass = creds['gmail_password']
    gmail_user = creds['gmail_user']

    if not gmail_pass:
        logging.warning("[Notify] Gmail app password not configured — OTP email skipped")
        return False

    try:
        msg = MIMEMultipart("alternative")
        is_login = purpose == "login"
        subject_label = "login" if is_login else "booking verification"
        msg["Subject"] = f"👑 Your Vanshika Makeover Academy {subject_label} OTP: {otp}"
        msg["From"]    = f"Vanshika Makeover Academy <{gmail_user}>"
        msg["To"]      = customer_email

        plain = (
            f"Hi {name},\n\n"
            f"Your OTP for {('signing in to your account' if is_login else 'confirming your appointment')} "
            f"at Vanshika Makeover Academy is:\n\n"
            f"  {otp}\n\n"
            f"Valid for 10 minutes. Do not share this code with anyone.\n\n"
            f"If you did not request this, please ignore this email."
        )

        html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#fff0f6;font-family:Arial,sans-serif;">
<div style="max-width:480px;margin:32px auto;background:#ffffff;border-radius:16px;
            overflow:hidden;box-shadow:0 4px 24px rgba(180,83,9,0.12);">

  <div style="background:linear-gradient(135deg,#9d174d 0%,#be185d 60%,#b45309 100%);
              padding:24px;text-align:center;">
    <div style="font-size:32px;margin-bottom:6px;">👑</div>
    <h1 style="color:#fbbf24;margin:0;font-size:20px;font-weight:700;font-family:Georgia,serif;">
      Vanshika Makeover Academy
    </h1>
    <p style="color:#fce7f3;margin:4px 0 0;font-size:11px;letter-spacing:2px;">
      {('ACCOUNT LOGIN' if is_login else 'BOOKING VERIFICATION')}
    </p>
  </div>

  <div style="height:3px;background:linear-gradient(90deg,#92400e,#fbbf24,#d97706,#fbbf24,#92400e);"></div>

  <div style="padding:32px 24px;text-align:center;">
    <p style="color:#374151;font-size:15px;margin:0 0 8px;">Hi <strong>{name}</strong>,</p>
    <p style="color:#6b7280;font-size:13px;margin:0 0 28px;">
       Use the OTP below to {('sign in to your account' if is_login else 'confirm your appointment booking')}.
    </p>
    <div style="background:linear-gradient(135deg,#fef3c7,#fde68a);
                border:2px solid #d97706; border-radius:16px;
                padding:20px; margin-bottom:24px; display:inline-block; min-width:200px;">
      <div style="font-size:11px;color:#92400e;letter-spacing:3px;text-transform:uppercase;
                  font-weight:700;margin-bottom:10px;">Your OTP</div>
      <div style="font-size:42px;font-weight:900;letter-spacing:12px;color:#92400e;
                  font-family:monospace;">{otp}</div>
    </div>
    <p style="color:#9ca3af;font-size:12px;margin:0 0 4px;">Valid for <strong>10 minutes</strong></p>
    <p style="color:#9ca3af;font-size:11px;margin:0;">Do not share this code with anyone.</p>
  </div>

  <div style="background:#fef3c7;padding:14px 24px;text-align:center;">
    <p style="color:#92400e;margin:0;font-size:12px;">
      If you didn't request this, you can safely ignore this email.
    </p>
  </div>

</div>
</body>
</html>"""

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, customer_email, msg.as_string())

        logging.info(f"[Notify] OTP email sent → {customer_email}")
        return True

    except Exception as exc:
        logging.error(f"[Notify] OTP email failed: {exc}")
        return False


# ─── Booking confirmation email to customer ───────────────────────────────────

def send_booking_confirmation_email(customer_email, name, phone, service, date, time, booking_id, _creds=None):
    """Send a beautiful booking confirmation email to the customer."""
    creds      = _creds or get_messaging_creds()
    gmail_pass = creds['gmail_password']
    gmail_user = creds['gmail_user']

    if not gmail_pass:
        logging.warning("[Notify] Gmail app password not configured — customer confirmation email skipped")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"✅ Booking Confirmed! #VMA-{booking_id} — Vanshika Makeover Academy"
        msg["From"]    = f"Vanshika Makeover Academy <{gmail_user}>"
        msg["To"]      = customer_email

        plain = (
            f"Hi {name},\n\n"
            f"Your appointment is confirmed!\n\n"
            f"Booking ID : #VMA-{booking_id}\n"
            f"Name       : {name}\n"
            f"Phone      : {phone}\n"
            f"Service    : {service}\n"
            f"Date       : {date}\n"
            f"Time       : {time}\n\n"
            f"Please arrive 10 minutes early and bring any reference looks you love!\n\n"
            f"See you soon,\nVanshika Makeover Academy"
        )

        html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#fff0f6;font-family:Arial,sans-serif;">
<div style="max-width:520px;margin:32px auto;background:#ffffff;border-radius:16px;
            overflow:hidden;box-shadow:0 4px 24px rgba(180,83,9,0.12);">

  <div style="background:linear-gradient(135deg,#9d174d 0%,#be185d 60%,#b45309 100%);
              padding:28px 24px;text-align:center;">
    <div style="font-size:36px;margin-bottom:8px;">✅</div>
    <h1 style="color:#fbbf24;margin:0;font-size:22px;font-weight:700;font-family:Georgia,serif;">
      Booking Confirmed!
    </h1>
    <p style="color:#fce7f3;margin:6px 0 0;font-size:11px;letter-spacing:2px;">
      VANSHIKA MAKEOVER ACADEMY
    </p>
  </div>

  <div style="height:3px;background:linear-gradient(90deg,#92400e,#fbbf24,#d97706,#fbbf24,#92400e);"></div>

  <div style="background:linear-gradient(135deg,#fef3c7,#fde68a);padding:16px 24px;text-align:center;
              border-bottom:1px solid #fcd34d;">
    <span style="font-size:11px;color:#92400e;letter-spacing:2px;text-transform:uppercase;font-weight:700;">Booking ID</span>
    <div style="font-size:24px;font-weight:900;color:#92400e;font-family:monospace;margin-top:4px;">#VMA-{booking_id}</div>
  </div>

  <div style="padding:24px;">
    <p style="color:#374151;font-size:14px;margin:0 0 20px;">Hi <strong>{name}</strong>, your beauty session is all set! 💅</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr>
        <td style="padding:10px 12px;color:#9ca3af;width:36%;">👤 Name</td>
        <td style="padding:10px 12px;font-weight:700;color:#111827;">{name}</td>
      </tr>
      <tr style="background:#fffbf0;">
        <td style="padding:10px 12px;color:#9ca3af;">📱 Phone</td>
        <td style="padding:10px 12px;font-weight:700;color:#111827;">{phone}</td>
      </tr>
      <tr>
        <td style="padding:10px 12px;color:#9ca3af;">✨ Service</td>
        <td style="padding:10px 12px;font-weight:700;color:#111827;">{service}</td>
      </tr>
      <tr style="background:#fffbf0;">
        <td style="padding:10px 12px;color:#9ca3af;">📅 Date</td>
        <td style="padding:10px 12px;font-weight:700;color:#111827;">{date}</td>
      </tr>
      <tr>
        <td style="padding:10px 12px;color:#9ca3af;">🕐 Time</td>
        <td style="padding:10px 12px;font-weight:700;color:#111827;">{time}</td>
      </tr>
    </table>
  </div>

  <div style="background:linear-gradient(135deg,#fef3c7,#fde68a);padding:18px 24px;text-align:center;">
    <p style="color:#92400e;margin:0;font-size:13px;font-weight:600;">
      👑 Please arrive 10 minutes early &amp; bring reference looks you love!
    </p>
  </div>

</div>
</body>
</html>"""

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, customer_email, msg.as_string())

        logging.info(f"[Notify] Customer confirmation email sent → {customer_email}")
        return True

    except Exception as exc:
        logging.error(f"[Notify] Customer confirmation email failed: {exc}")
        return False


# ─── Invoice WhatsApp ─────────────────────────────────────────────────────────

def send_invoice_whatsapp(phone, customer_name, invoice_id, items, subtotal,
                          discount_amount, gst_amount, total, payment_method, _creds=None):
    """Send a formatted invoice receipt to the customer via WhatsApp."""
    creds = _creds or get_messaging_creds()
    items_text = ""
    for item in items:
        qty_str = f" x{item['qty']}" if item.get('qty', 1) > 1 else ""
        items_text += f"  • {item['item_name']}{qty_str} — ₹{item['line_total']:.0f}\n"

    discount_line = f"💸 Discount       : -₹{discount_amount:.0f}\n" if discount_amount > 0 else ""
    gst_line      = f"🧾 GST            :  ₹{gst_amount:.0f}\n" if gst_amount > 0 else ""
    method_map    = {'cash': 'Cash 💵', 'card': 'Card 💳', 'upi': 'UPI 📱'}
    method_label  = method_map.get(payment_method, payment_method.title())

    body = (
        f"👑 *Vanshika Makeover Academy*\n"
        f"🧾 *Invoice #{invoice_id} — Thank you!*\n\n"
        f"Hi {customer_name}! Here's your receipt:\n\n"
        f"{items_text}\n"
        f"─────────────────────\n"
        f"📋 Subtotal       : ₹{subtotal:.0f}\n"
        f"{discount_line}"
        f"{gst_line}"
        f"✅ *Total Paid     : ₹{total:.0f}*\n"
        f"💳 Payment        : {method_label}\n\n"
        f"Thank you for choosing us! 💅✨"
    )

    payload = {
        "messaging_product": "whatsapp",
        "to": _wa_to(phone),
        "type": "text",
        "text": {"body": body},
    }
    ok, result = _send_whatsapp(payload, _creds=creds)
    if ok:
        logging.info(f"[Notify] Invoice WhatsApp sent → {phone} (id: {result})")
    else:
        logging.error(f"[Notify] Invoice WhatsApp failed: {result}")
    return ok


# ─── Campaign / birthday / membership WhatsApp ────────────────────────────────

def send_campaign_whatsapp(phone, customer_name, message_body, _creds=None):
    """Send a campaign message with {{customer_name}} merge tag support."""
    creds = _creds or get_messaging_creds()
    body  = message_body.replace('{{customer_name}}', customer_name)
    payload = {
        "messaging_product": "whatsapp",
        "to": _wa_to(phone),
        "type": "text",
        "text": {"body": body},
    }
    ok, result = _send_whatsapp(payload, _creds=creds)
    if ok:
        logging.info(f"[Campaign] WhatsApp sent → {phone}")
    else:
        logging.warning(f"[Campaign] WhatsApp failed → {phone}: {result}")
    return ok, result


def send_birthday_whatsapp(phone, customer_name, discount_code=None, _creds=None):
    """Send a birthday greeting with optional discount code via WhatsApp."""
    creds     = _creds or get_messaging_creds()
    disc_line = f"\n\n🎁 Use code *{discount_code}* for a special birthday discount!" if discount_code else ""
    body = (
        f"👑 *Vanshika Makeover Academy*\n\n"
        f"🎂 *Happy Birthday, {customer_name}!* 🎉\n\n"
        f"Wishing you a beautiful day filled with joy! Come in and let us pamper you on your special day.{disc_line}\n\n"
        f"See you soon! 💅✨"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": _wa_to(phone),
        "type": "text",
        "text": {"body": body},
    }
    ok, result = _send_whatsapp(payload, _creds=creds)
    if ok:
        logging.info(f"[Birthday] WhatsApp sent → {phone}")
    else:
        logging.warning(f"[Birthday] WhatsApp failed → {phone}: {result}")
    return ok, result


def send_membership_reminder_whatsapp(phone, customer_name, plan_name, expiry_date, _creds=None):
    """Send a membership expiry reminder via WhatsApp."""
    creds = _creds or get_messaging_creds()
    body  = (
        f"👑 *Vanshika Makeover Academy*\n\n"
        f"Hi {customer_name}! 💅\n\n"
        f"Your *{plan_name}* membership is expiring on *{expiry_date}*.\n\n"
        f"Renew now to keep enjoying your exclusive benefits! Call us or visit the salon. ✨"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": _wa_to(phone),
        "type": "text",
        "text": {"body": body},
    }
    ok, result = _send_whatsapp(payload, _creds=creds)
    if ok:
        logging.info(f"[Notify] Membership reminder sent → {phone}")
    else:
        logging.error(f"[Notify] Membership reminder failed: {result}")
    return ok


def notify_new_booking(name, phone, service, date, time, customer_email=""):
    """Fire both owner notifications (best-effort, never raises)."""
    send_booking_email(name, phone, service, date, time, customer_email)
    send_booking_sms(name, phone, service, date, time)


# ─── Cancellation notifications ───────────────────────────────────────────────

def send_cancellation_email(name, phone, service, date, time, booking_id, _creds=None):
    """Send a cancellation alert email to the salon owner."""
    creds      = _creds or get_messaging_creds()
    gmail_pass = creds['gmail_password']
    gmail_user = creds['gmail_user']
    owner_to   = creds['owner_email']

    if not gmail_pass:
        logging.warning("[Notify] Gmail app password not configured — cancellation email skipped")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"❌ Appointment Cancelled: {name} — {service}"
        msg["From"]    = f"Vanshika Makeover Academy <{gmail_user}>"
        msg["To"]      = owner_to

        plain = (
            f"An appointment has been cancelled by the customer.\n\n"
            f"Booking ID : #VMA-{booking_id}\n"
            f"Customer   : {name}\n"
            f"Phone      : {phone}\n"
            f"Service    : {service}\n"
            f"Date       : {date}\n"
            f"Time       : {time}\n\n"
            f"Login to /admin to manage your schedule."
        )

        html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#fff0f6;font-family:Arial,sans-serif;">
<div style="max-width:520px;margin:32px auto;background:#ffffff;border-radius:16px;
            overflow:hidden;box-shadow:0 4px 24px rgba(180,83,9,0.12);">

  <div style="background:linear-gradient(135deg,#7f1d1d 0%,#991b1b 60%,#b45309 100%);
              padding:28px 24px;text-align:center;">
    <div style="font-size:36px;margin-bottom:8px;">❌</div>
    <h1 style="color:#fbbf24;margin:0;font-size:22px;font-weight:700;
               font-family:Georgia,serif;">Appointment Cancelled</h1>
    <p style="color:#fce7f3;margin:6px 0 0;font-size:12px;letter-spacing:2px;">
      VANSHIKA MAKEOVER ACADEMY
    </p>
  </div>

  <div style="height:3px;background:linear-gradient(90deg,#92400e,#fbbf24,#d97706,#fbbf24,#92400e);"></div>

  <div style="padding:28px 24px;">
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <tr>
        <td style="padding:11px 12px;color:#9ca3af;width:38%;">🔖 Booking ID</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">#VMA-{booking_id}</td>
      </tr>
      <tr style="background:#fffbf0;">
        <td style="padding:11px 12px;color:#9ca3af;">👤 Customer</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{name}</td>
      </tr>
      <tr>
        <td style="padding:11px 12px;color:#9ca3af;">📱 Phone</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{phone}</td>
      </tr>
      <tr style="background:#fffbf0;">
        <td style="padding:11px 12px;color:#9ca3af;">✨ Service</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{service}</td>
      </tr>
      <tr>
        <td style="padding:11px 12px;color:#9ca3af;">📅 Date</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{date}</td>
      </tr>
      <tr style="background:#fffbf0;">
        <td style="padding:11px 12px;color:#9ca3af;">🕐 Time</td>
        <td style="padding:11px 12px;font-weight:700;color:#111827;">{time}</td>
      </tr>
    </table>
  </div>

  <div style="background:linear-gradient(135deg,#fef3c7,#fde68a);padding:18px 24px;text-align:center;">
    <p style="color:#92400e;margin:0;font-size:13px;font-weight:600;">
      👑 Open Admin Dashboard to update your schedule
    </p>
  </div>

</div>
</body>
</html>"""

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html,  "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, owner_to, msg.as_string())

        logging.info(f"[Notify] Cancellation email sent → {owner_to} for booking #{booking_id}")
        return True

    except Exception as exc:
        logging.error(f"[Notify] Cancellation email failed: {exc}")
        return False


def send_cancellation_confirmation_email(customer_email, name, service, date, time, booking_id, _creds=None):
    """Send a cancellation confirmation email to the customer."""
    creds      = _creds or get_messaging_creds()
    gmail_pass = creds['gmail_password']
    gmail_user = creds['gmail_user']

    if not gmail_pass:
        logging.warning("[Notify] Gmail app password not configured — customer cancellation email skipped")
        return False

    if not customer_email:
        logging.info("[Notify] No customer email — customer cancellation email skipped")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Appointment Cancelled — #VMA-{booking_id} | Vanshika Makeover Academy"
        msg["From"]    = f"Vanshika Makeover Academy <{gmail_user}>"
        msg["To"]      = customer_email

        plain = (
            f"Hi {name},\n\n"
            f"Your appointment has been successfully cancelled.\n\n"
            f"Booking ID : #VMA-{booking_id}\n"
            f"Service    : {service}\n"
            f"Date       : {date}\n"
            f"Time       : {time}\n\n"
            f"We hope to see you again soon!\n\n"
            f"Warm regards,\nVanshika Makeover Academy"
        )

        html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#fff0f6;font-family:Arial,sans-serif;">
<div style="max-width:520px;margin:32px auto;background:#ffffff;border-radius:16px;
            overflow:hidden;box-shadow:0 4px 24px rgba(180,83,9,0.12);">

  <div style="background:linear-gradient(135deg,#9d174d 0%,#be185d 60%,#b45309 100%);
              padding:28px 24px;text-align:center;">
    <div style="font-size:36px;margin-bottom:8px;">&#10060;</div>
    <h1 style="color:#fbbf24;margin:0;font-size:22px;font-weight:700;font-family:Georgia,serif;">
      Appointment Cancelled
    </h1>
    <p style="color:#fce7f3;margin:6px 0 0;font-size:11px;letter-spacing:2px;">VANSHIKA MAKEOVER ACADEMY</p>
  </div>

  <div style="height:3px;background:linear-gradient(90deg,#92400e,#fbbf24,#d97706,#fbbf24,#92400e);"></div>

  <div style="background:linear-gradient(135deg,#fef3c7,#fde68a);padding:16px 24px;text-align:center;
              border-bottom:1px solid #fcd34d;">
    <span style="font-size:11px;color:#92400e;letter-spacing:2px;text-transform:uppercase;font-weight:700;">Booking ID</span>
    <div style="font-size:24px;font-weight:900;color:#92400e;font-family:monospace;margin-top:4px;">#VMA-{booking_id}</div>
  </div>

  <div style="padding:28px 24px;">
    <p style="color:#374151;font-size:14px;margin:0 0 16px;">
      Hi <strong>{name}</strong>, your appointment has been successfully cancelled.
    </p>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr>
        <td style="padding:10px 12px;color:#9ca3af;width:36%;">&#10024; Service</td>
        <td style="padding:10px 12px;font-weight:700;color:#111827;">{service}</td>
      </tr>
      <tr style="background:#fffbf0;">
        <td style="padding:10px 12px;color:#9ca3af;">&#128197; Date</td>
        <td style="padding:10px 12px;font-weight:700;color:#111827;">{date}</td>
      </tr>
      <tr>
        <td style="padding:10px 12px;color:#9ca3af;">&#128336; Time</td>
        <td style="padding:10px 12px;font-weight:700;color:#111827;">{time}</td>
      </tr>
    </table>
  </div>

  <div style="background:linear-gradient(135deg,#fef3c7,#fde68a);padding:18px 24px;text-align:center;">
    <p style="color:#92400e;margin:0;font-size:13px;font-weight:600;">
      &#128081; We hope to see you again soon — rebook anytime!
    </p>
  </div>

</div>
</body>
</html>"""

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html,  "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, customer_email, msg.as_string())

        logging.info(f"[Notify] Cancellation confirmation email sent → {customer_email} for booking #{booking_id}")
        return True

    except Exception as exc:
        logging.error(f"[Notify] Customer cancellation email failed: {exc}")
        return False


def notify_cancellation(name, phone, service, date, time, booking_id):
    """Fire owner cancellation notification (best-effort, never raises)."""
    send_cancellation_email(name, phone, service, date, time, booking_id)
