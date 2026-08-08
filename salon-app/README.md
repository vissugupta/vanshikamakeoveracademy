# Vanshika Makeover Academy — ERP

A full-featured salon management system built with Python Flask + SQLite.

## Features
- 📅 Online booking with email OTP verification
- 🏢 Multi-branch management
- 👥 Customer CRM with loyalty points & memberships
- 🧾 Billing & invoicing with GST
- 📦 Inventory management
- 👩‍💼 Staff management with commission tracking
- 📱 WhatsApp notifications (Meta Cloud API)
- 📊 Analytics dashboard with Chart.js
- 📣 WhatsApp marketing campaigns with customer segmentation
- 🤖 Automated follow-up rules (daily)
- 📸 Feedback photo and short video uploads for staff review

---

## Running Locally

### 1. Prerequisites
- Python 3.10 or higher
- pip

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Set environment variables (optional)

Create a `.env` file or export these before running:

```bash
# Admin password (default: admin@123)
export ADMIN_PASSWORD=admin@123

# Flask session secret
export SESSION_SECRET=your-secret-key-here

# Fast2SMS (optional SMS notifications)
export FAST2SMS_API_KEY=your-fast2sms-api-key

# Gmail (for booking OTP, client confirmations, and salon notifications)
export GMAIL_APP_PASSWORD=your-gmail-app-password
export GMAIL_USER=info.vanshikamakeoveracademy@gmail.com

# Twilio SMS (optional owner notifications)
export TWILIO_ACCOUNT_SID=ACxxxxxxxxxx
export TWILIO_AUTH_TOKEN=your-auth-token
export TWILIO_FROM_NUMBER=+14155551234

# WhatsApp Cloud API (Meta)
export WHATSAPP_ACCESS_TOKEN=your-token
export WHATSAPP_PHONE_NUMBER_ID=your-phone-id
```

### 4. Run the app
```bash
python app.py
```

The app will start on **http://localhost:5000**

- Customer booking: http://localhost:5000/
- Admin panel:      http://localhost:5000/admin  (password: `admin@123`)
- Customer portal:  http://localhost:5000/my

Feedback uploads are optional and support JPG, PNG, WEBP, GIF, MP4, MOV, and
WEBM files up to 25 MB. Uploaded media is available to authorized staff from
Admin → Feedback and the customer profile review history.

### 5. First run
On first run the database (`salon.db`) is created automatically with:
- Default branch "Vanshika Makeover Academy"
- All service categories and services pre-loaded

### Reset demo data
To remove operational data and rebuild the clean walkthrough dataset, run:

```bash
cd artifacts/salon-app
python3 seed_demo_data.py --reset
```

This preserves all branches, the service catalog, and admin user accounts. It
clears customers, invoices, appointments, staff, memberships, campaigns,
feedback, inventory, and other operational records before reseeding. Use this
only when you intentionally want to discard those records.

---

## File Structure
```
salon-app/
├── app.py              # Main Flask application (all routes)
├── notifications.py    # Fast2SMS, email, SMS & WhatsApp helpers
├── requirements.txt    # Python dependencies
├── salon.db            # SQLite database (auto-created)
├── wsgi.py             # WSGI entry point (for production)
└── templates/          # Jinja2 HTML templates
    ├── admin_base.html
    ├── admin_dashboard.html
    ├── admin_appointments.html
    ├── admin_billing*.html
    ├── admin_customers*.html
    ├── admin_staff*.html
    ├── admin_memberships.html
    ├── admin_marketing*.html
    ├── admin_analytics.html
    ├── admin_inventory*.html
    ├── index.html
    ├── book.html
    └── ...
```

---

## Default Credentials
| Field    | Value      |
|----------|------------|
| Password | `admin@123`|

---

## Tech Stack
| Layer      | Technology                  |
|------------|-----------------------------|
| Backend    | Python 3 + Flask            |
| Database   | SQLite (via sqlite3)        |
| Templates  | Jinja2 + Tailwind CSS (CDN) |
| Charts     | Chart.js (CDN)              |
| WhatsApp   | Meta WhatsApp Cloud API     |
| Email      | Gmail SMTP                  |
| Booking OTP email | Gmail SMTP           |
| Optional SMS      | Fast2SMS / Twilio    |
| Owner SMS       | Twilio                 |
