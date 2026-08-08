"""
seed_demo_data.py — Idempotent demo-data seeder for Vanshika Makeover Academy ERP.

Run once (safe to re-run; duplicate-check guard on phone numbers and invoice count):
    python3 seed_demo_data.py

Reset operational data and rebuild the demo dataset from scratch:
    python3 seed_demo_data.py --reset

Populates:
  • 10 demo customers (with birthdays, loyalty points, visit history)
  • 5 staff members across the main branch
  • 3 membership plans + 4 active/expiring enrollments
  • 30 invoices with line items spread over the last 90 days
  • 30 matching appointments
  • Some customer feedback entries
"""

import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta

# ── locate DB ────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE   = os.path.join(SCRIPT_DIR, 'salon.db')

if not os.path.exists(DATABASE):
    print(f"ERROR: Database not found at {DATABASE}")
    print("Start the app first so init_db() creates the schema, then run this script.")
    sys.exit(1)

conn = sqlite3.connect(DATABASE)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
db = conn

# ── helpers ──────────────────────────────────────────────────────────────────

def dt_str(d):
    """datetime → 'YYYY-MM-DD'"""
    return d.strftime('%Y-%m-%d')


def dt_iso(d):
    """datetime → 'YYYY-MM-DD HH:MM:SS'"""
    return d.strftime('%Y-%m-%d %H:%M:%S')


TODAY = datetime.now()

def reset_demo_data():
    """Clear operational/demo rows while preserving app configuration and admins."""
    # Keep branches, service categories/services, and admin_users intact so a
    # walkthrough reset cannot remove the owner's access or salon setup.
    tables = [
        'campaign_recipients',
        'invoice_items',
        'customer_memberships',
        'staff_schedules',
        'appointments',
        'invoices',
        'campaigns',
        'feedback',
        'automation_log',
        'follow_up_rules',
        'customers',
        'staff',
        'membership_plans',
        'products',
    ]

    print("Resetting demo data …")
    try:
        db.execute('BEGIN')
        for table in tables:
            db.execute(f'DELETE FROM "{table}"')
        # Reset auto-increment counters for cleared tables where SQLite tracks
        # them, so the rebuilt demo database behaves like a fresh walkthrough.
        placeholders = ','.join('?' for _ in tables)
        db.execute(
            f'DELETE FROM sqlite_sequence WHERE name IN ({placeholders})',
            tables
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print("  Preserved branches, service catalog, and admin users")
    print(f"  Cleared {len(tables)} operational tables")


if '--help' in sys.argv or '-h' in sys.argv:
    print(__doc__.strip())
    sys.exit(0)

unknown_args = [arg for arg in sys.argv[1:] if arg != '--reset']
if unknown_args:
    print(f"ERROR: Unknown argument(s): {' '.join(unknown_args)}")
    print("Usage: python3 seed_demo_data.py [--reset]")
    conn.close()
    sys.exit(2)

if '--reset' in sys.argv:
    reset_demo_data()

# ── 1. Guard: skip if demo data already seeded ───────────────────────────────
demo_phones = [
    '9811122334', '9822233445', '9833344556', '9844455667', '9855566778',
    '9866677889', '9877788990', '9888899001', '9899900112', '9800011223',
]
already_seeded = db.execute(
    "SELECT COUNT(*) as c FROM customers WHERE phone IN ({})".format(
        ','.join('?' for _ in demo_phones)
    ), demo_phones
).fetchone()['c']

if already_seeded >= 5:
    print(f"Demo data already present ({already_seeded} demo customers found). Nothing to do.")
    conn.close()
    sys.exit(0)

print("Seeding demo data …")

# ── 2. Ensure branch 1 exists ────────────────────────────────────────────────
branch_row = db.execute('SELECT id FROM branches WHERE id=1').fetchone()
if not branch_row:
    db.execute(
        "INSERT INTO branches (id, name, address, phone, manager_name) VALUES (1,?,?,?,?)",
        ('Vanshika Makeover Academy', 'Main Branch', '+91 98992 23426', 'Vanshika')
    )
    db.commit()
    print("  Created branch 1")

# ── 3. Fetch service names ────────────────────────────────────────────────────
svc_rows = db.execute(
    "SELECT s.id, s.name, s.price, sc.name as category FROM services s "
    "JOIN service_categories sc ON sc.id = s.category_id WHERE s.is_active=1"
).fetchall()

if not svc_rows:
    print("ERROR: No services found. Start the app first so _seed_services() runs.")
    conn.close()
    sys.exit(1)

# Prices are set authoritatively by app.py (_seed_services / _migrate_db).
# No update needed here; just use the prices already in the database.
services = [dict(r) for r in svc_rows]

# ── 4. Customers ──────────────────────────────────────────────────────────────
CUSTOMERS = [
    # name,                    phone,        email,                           birthday,     notes
    ('Priya Sharma',           '9811122334', 'priya.sharma@gmail.com',        '1992-07-15', 'Prefers fragrance-free products'),
    ('Ananya Gupta',           '9822233445', 'ananya.gupta@gmail.com',        '1988-05-22', 'Regular bridal client'),
    ('Kavya Reddy',            '9833344556', 'kavya.reddy@yahoo.com',         '1995-08-10', 'Sensitive skin'),
    ('Meera Patel',            '9844455667', 'meera.patel@hotmail.com',       '1990-03-30', 'VIP client — always complimentary tea'),
    ('Ritika Singh',           '9855566778', 'ritika.singh@gmail.com',        '1997-12-05', None),
    ('Deepika Nair',           '9866677889', 'deepika.nair@gmail.com',        '1993-07-26', 'Prefers afternoon slots'),
    ('Sneha Rao',              '9877788990', 'sneha.rao@outlook.com',         '1999-01-18', None),
    ('Pooja Verma',            '9888899001', 'pooja.verma@gmail.com',         '1985-09-08', 'Allergic to shellac gel'),
    ('Aditi Mehta',            '9899900112', 'aditi.mehta@gmail.com',         '1994-11-14', 'Referred by Priya'),
    ('Simran Kapoor',          '9800011223', 'simran.kapoor@gmail.com',       '1991-07-20', 'Monthly membership holder'),
]

cust_ids = {}
for name, phone, email, birthday, notes in CUSTOMERS:
    existing = db.execute('SELECT id FROM customers WHERE phone=?', (phone,)).fetchone()
    if existing:
        cust_ids[phone] = existing['id']
    else:
        # Stagger created_at across last 90 days so "new this month" count is realistic
        days_ago = random.randint(5, 90)
        created_at = dt_iso(TODAY - timedelta(days=days_ago))
        cur = db.execute(
            '''INSERT INTO customers
                   (name, phone, email, birthday, notes, loyalty_points,
                    visit_count, total_spend, last_visit_date, branch_id, created_at)
               VALUES (?,?,?,?,?,0,0,0,NULL,1,?)''',
            (name, phone, email, birthday, notes, created_at)
        )
        cust_ids[phone] = cur.lastrowid

db.commit()
print(f"  Customers: {len(cust_ids)} upserted")

# ── 5. Staff ──────────────────────────────────────────────────────────────────
STAFF_SEED = [
    ('Rekha Sharma',   '9712345678', 'Senior Stylist',    30.0),
    ('Sunita Yadav',   '9723456789', 'Makeup Artist',     25.0),
    ('Priti Mehta',    '9734567890', 'Nail Technician',   20.0),
    ('Kavita Bose',    '9745678901', 'Skin Specialist',   28.0),
    ('Ritu Chauhan',   '9756789012', 'Hair Specialist',   25.0),
]
staff_ids = []
for sname, sphone, srole, comm in STAFF_SEED:
    existing = db.execute('SELECT id FROM staff WHERE phone=?', (sphone,)).fetchone()
    if existing:
        staff_ids.append(existing['id'])
    else:
        cur = db.execute(
            "INSERT INTO staff (branch_id, name, phone, role, commission_pct, is_active) VALUES (1,?,?,?,?,1)",
            (sname, sphone, srole, comm)
        )
        staff_ids.append(cur.lastrowid)
db.commit()
print(f"  Staff: {len(staff_ids)} upserted")

# ── 6. Membership plans ───────────────────────────────────────────────────────
PLAN_SEED = [
    ('Silver',   999,  30,  10.0, '10 % off all services for 30 days'),
    ('Gold',    1999,  60,  20.0, '20 % off all services for 60 days'),
    ('Platinum', 3499, 90,  30.0, '30 % off + priority booking for 90 days'),
]
plan_ids = {}
for pname, pprice, pvalid, pdiscount, pdesc in PLAN_SEED:
    existing = db.execute('SELECT id FROM membership_plans WHERE name=?', (pname,)).fetchone()
    if existing:
        plan_ids[pname] = existing['id']
    else:
        cur = db.execute(
            "INSERT INTO membership_plans (name, price, validity_days, discount_pct, description, is_active) VALUES (?,?,?,?,?,1)",
            (pname, pprice, pvalid, pdiscount, pdesc)
        )
        plan_ids[pname] = cur.lastrowid
db.commit()
print(f"  Membership plans: {len(plan_ids)} upserted")

# ── 7. Enroll 4 customers in memberships ─────────────────────────────────────
phone_list = list(cust_ids.keys())

ENROLL_SEED = [
    # (phone,          plan_name,   start_days_ago, status)
    ('9800011223',     'Platinum',   20,             'active'),   # Simran — active, expires in 70 days
    ('9811122334',     'Gold',       55,             'active'),   # Priya  — active, expires in 5 days (expiring soon)
    ('9822233445',     'Silver',     35,             'expired'),  # Ananya — expired
    ('9844455667',     'Gold',        3,             'active'),   # Meera  — just enrolled
]
for phone, plan_name, start_ago, status in ENROLL_SEED:
    cid = cust_ids.get(phone)
    pid = plan_ids.get(plan_name)
    if not cid or not pid:
        continue
    # Check if already enrolled in this plan
    existing = db.execute(
        'SELECT id FROM customer_memberships WHERE customer_id=? AND plan_id=?',
        (cid, pid)
    ).fetchone()
    if existing:
        continue
    validity = [v for n, p, v, d, desc in PLAN_SEED if n == plan_name][0]
    start  = TODAY - timedelta(days=start_ago)
    expiry = start + timedelta(days=validity)
    # Determine real status based on dates
    real_status = 'active' if expiry.date() >= TODAY.date() else 'expired'
    if status == 'expired':
        real_status = 'expired'
    db.execute(
        "INSERT INTO customer_memberships (customer_id, plan_id, start_date, expiry_date, status) VALUES (?,?,?,?,?)",
        (cid, pid, dt_str(start), dt_str(expiry), real_status)
    )
db.commit()
print("  Memberships enrolled")

# ── 8. Invoices + line items + appointments ───────────────────────────────────

# Check how many invoices already exist (to avoid re-seeding)
existing_invoice_count = db.execute(
    'SELECT COUNT(*) as c FROM invoices WHERE branch_id=1'
).fetchone()['c']

if existing_invoice_count >= 25:
    print(f"  Invoices: {existing_invoice_count} already exist — skipping invoice seeding")
else:
    TIME_SLOTS = [
        '09:00 AM', '10:00 AM', '11:00 AM', '12:00 PM',
        '02:00 PM', '03:00 PM', '04:00 PM', '05:00 PM',
        '06:00 PM', '07:00 PM',
    ]
    PAYMENT_METHODS = ['cash', 'upi', 'card', 'upi', 'cash']  # weighted toward upi/cash

    INVOICE_COUNT = 30
    phones = list(cust_ids.keys())

    invoices_created = 0
    for i in range(INVOICE_COUNT):
        # Spread invoices over last 90 days, weighted toward recent
        days_back = int(random.triangular(0, 90, 10))
        inv_date = TODAY - timedelta(days=days_back)
        inv_ts   = dt_iso(inv_date)

        phone    = random.choice(phones)
        cid      = cust_ids[phone]
        sid      = random.choice(staff_ids)

        # Pick 1-3 services for this visit
        num_svcs = random.choices([1, 2, 3], weights=[50, 35, 15])[0]
        chosen   = random.sample(services, min(num_svcs, len(services)))

        subtotal = 0.0
        items    = []
        for svc in chosen:
            price     = float(svc['price']) if svc['price'] else random.choice([600, 800, 1200, 1500, 2000])
            # Small random variance (±10 %)
            price     = round(price * random.uniform(0.9, 1.1) / 50) * 50
            qty       = 1
            line_tot  = price * qty
            subtotal += line_tot
            items.append((svc['id'], svc['name'], price, qty, line_tot))

        # Occasional discount
        discount_amount = 0.0
        discount_type   = 'flat'
        discount_value  = 0.0
        if random.random() < 0.2:
            discount_type  = 'percent'
            discount_value = random.choice([5, 10, 15])
            discount_amount = round(subtotal * discount_value / 100, 2)

        after_disc = subtotal - discount_amount
        # GST 18 % on ~40 % of invoices
        gst_pct    = 18.0 if random.random() < 0.4 else 0.0
        gst_amount = round(after_disc * gst_pct / 100, 2)
        total      = round(after_disc + gst_amount, 2)

        payment    = random.choice(PAYMENT_METHODS)

        # Get customer name and phone
        cust_row = db.execute('SELECT name, phone FROM customers WHERE id=?', (cid,)).fetchone()

        cur = db.execute(
            '''INSERT INTO invoices
                   (customer_id, branch_id, staff_id, customer_name, customer_phone,
                    subtotal, discount_type, discount_value, discount_amount,
                    gst_pct, gst_amount, total, payment_method, status, created_at)
               VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,'paid',?)''',
            (cid, sid, cust_row['name'], cust_row['phone'],
             subtotal, discount_type, discount_value, discount_amount,
             gst_pct, gst_amount, total, payment, inv_ts)
        )
        inv_id = cur.lastrowid

        for svc_id, svc_name, price, qty, line_tot in items:
            db.execute(
                '''INSERT INTO invoice_items (invoice_id, item_type, item_name, unit_price, qty, line_total, service_id)
                   VALUES (?,'service',?,?,?,?,?)''',
                (inv_id, svc_name, price, qty, line_tot, svc_id)
            )

        # Matching appointment
        slot = random.choice(TIME_SLOTS)
        primary_svc  = chosen[0]
        db.execute(
            '''INSERT INTO appointments
                   (name, phone, email, category, service, date, time,
                    customer_id, branch_id, staff_id, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,1,?,'completed',?)''',
            (cust_row['name'], cust_row['phone'],
             db.execute('SELECT email FROM customers WHERE id=?', (cid,)).fetchone()['email'],
             primary_svc['category'], primary_svc['name'],
             dt_str(inv_date), slot, cid, sid, inv_ts)
        )
        invoices_created += 1

    db.commit()
    print(f"  Invoices created: {invoices_created}")

# ── 9. Update customer aggregate stats ───────────────────────────────────────
for phone, cid in cust_ids.items():
    agg = db.execute(
        '''SELECT COUNT(*) as vc,
                  COALESCE(SUM(total),0) as ts,
                  MAX(DATE(created_at)) as lv
           FROM invoices WHERE customer_id=? AND branch_id=1 AND status='paid' ''',
        (cid,)
    ).fetchone()
    visit_count     = agg['vc']
    total_spend     = agg['ts']
    last_visit_date = agg['lv']
    loyalty_points  = int(total_spend // 1000) * 50   # 50 points per ₹1,000 spent
    loyalty_expiry  = (
        (datetime.now() + timedelta(days=60)).strftime('%Y-%m-%d')
        if loyalty_points else None
    )

    db.execute(
        '''UPDATE customers
           SET visit_count=?, total_spend=?, last_visit_date=?, loyalty_points=?,
               loyalty_points_expiry_date=?
           WHERE id=?''',
        (visit_count, total_spend, last_visit_date, loyalty_points,
         loyalty_expiry, cid)
    )
db.commit()
print("  Customer stats recalculated")

# ── 10. Feedback entries ─────────────────────────────────────────────────────
FEEDBACK_SEED = [
    ('Priya Sharma',    'Bridal Package',     5, 'Absolutely stunning work! My bridal look was perfect.'),
    ('Ananya Gupta',    'Keratin Treatment',  5, 'My hair has never felt smoother. Highly recommend!'),
    ('Kavya Reddy',     'Hydra Facial',       4, 'Great service. Skin felt very refreshed afterwards.'),
    ('Meera Patel',     'Gel Extensions',     5, 'Priti is amazing — nail art was exactly what I wanted.'),
    ('Ritika Singh',    'Haircut',            4, 'Quick and professional. Will come back.'),
    ('Deepika Nair',    'Gold Facial',        5, 'Loved the experience. Very relaxing atmosphere.'),
    ('Sneha Rao',       'Manicure',           4, 'Good service, clean salon. Nice staff.'),
    ('Pooja Verma',     'Rica Waxing',        3, 'Service was okay, a bit rushed today.'),
    ('Aditi Mehta',     'Party Makeup',       5, 'Got so many compliments at the party! Thank you!'),
    ('Simran Kapoor',   'Smoothening',        5, 'Worth every rupee. Hair looks amazing.'),
]
for fname, fsvc, frating, fcomment in FEEDBACK_SEED:
    existing = db.execute(
        'SELECT id FROM feedback WHERE customer_name=? AND service=?', (fname, fsvc)
    ).fetchone()
    if not existing:
        days_ago = random.randint(1, 60)
        fdate    = dt_str(TODAY - timedelta(days=days_ago))
        db.execute(
            "INSERT INTO feedback (customer_name, service, rating, comment, date) VALUES (?,?,?,?,?)",
            (fname, fsvc, frating, fcomment, fdate)
        )
db.commit()
print("  Feedback seeded")

# ── 11. Add a few upcoming appointments ──────────────────────────────────────
UPCOMING_SEED = [
    ('Priya Sharma',   '9811122334', 'Skincare & Advanced Facials', 'Hydra Facial',      1, '11:00 AM'),
    ('Simran Kapoor',  '9800011223', 'Hair Styling & Treatments',   'Keratin Treatment', 2, '02:00 PM'),
    ('Meera Patel',    '9844455667', 'Bridal & Professional Makeup','Party Makeup',      3, '04:00 PM'),
    ('Aditi Mehta',    '9899900112', 'Nail Studio',                 'Gel Extensions',    4, '11:00 AM'),
    ('Deepika Nair',   '9866677889', 'Hair Removal & Grooming',     'Rica Waxing',       5, '03:00 PM'),
]
for uname, uphone, ucat, usvc, days_from_now, utime in UPCOMING_SEED:
    udate = dt_str(TODAY + timedelta(days=days_from_now))
    existing = db.execute(
        'SELECT id FROM appointments WHERE phone=? AND date=?', (uphone, udate)
    ).fetchone()
    if not existing:
        cid   = cust_ids.get(uphone)
        email = db.execute('SELECT email FROM customers WHERE id=?', (cid,)).fetchone()['email'] if cid else ''
        db.execute(
            '''INSERT INTO appointments
                   (name, phone, email, category, service, date, time,
                    customer_id, branch_id, status)
               VALUES (?,?,?,?,?,?,?,?,1,'confirmed')''',
            (uname, uphone, email, ucat, usvc, udate, utime, cid)
        )
db.commit()
print("  Upcoming appointments added")

# ── Done ──────────────────────────────────────────────────────────────────────
conn.close()
print("\n✅ Demo data seeded successfully!")
print("   → Analytics dashboard should now show non-zero revenue bars")
print("   → Staff performance table will show commissions")
print("   → Marketing segments will return realistic customer counts")
print("   → Memberships page will show active + expiring-soon enrollments")
