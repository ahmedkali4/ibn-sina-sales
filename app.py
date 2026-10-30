# -*- coding: utf-8 -*-
import os
import sys
import json
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, jsonify, make_response, send_file
)
from werkzeug.security import check_password_hash, generate_password_hash

import database
import licensing_engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.secret_key = os.environ.get("SALES_SECRET_KEY", "al-tabeeb-medical-sales-hub-secret-2026-xyz-master")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Ensure database is initialized
try:
    database.init_db()
except Exception as e:
    print("Database init check note:", e)


# ----------------- Helper Functions & Auth -----------------

def is_admin_request(req) -> bool:
    """
    Option 1: Checks if current request is from system admin/owner.
    Ensures admin browsing never pollutes visitor analytics.
    """
    if session.get('sales_admin_id'):
        return True
    if req.cookies.get('al_tabeeb_admin') == '1':
        return True
    if req.headers.get('X-Admin-Access') == '1':
        return True
    if req.args.get('preview') == 'admin':
        return True
    return False


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'sales_admin_id' not in session:
            flash('يرجى تسجيل الدخول أولاً للوصول إلى لوحة الإدارة.', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def get_setting(key: str, default: str = "") -> str:
    try:
        conn = database.get_db()
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default


def set_setting(key: str, value: str):
    try:
        conn = database.get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving setting {key}: {e}")


def log_landing_event(event_type: str, req):
    """
    Records visitor events ONLY if not an admin.
    Excludes admin pageviews and downloads from landing_events.
    """
    if is_admin_request(req):
        return  # Admin browsing is never logged as visitor

    try:
        ip = req.headers.get('CF-Connecting-IP') or req.headers.get('X-Forwarded-For', '').split(',')[0].strip() or req.remote_addr or '127.0.0.1'
        ua = req.headers.get('User-Agent', '')[:250]
        ref = req.headers.get('Referer', '')[:250]

        conn = database.get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO landing_events (event_type, ip_address, user_agent, referrer, is_admin)
            VALUES (?, ?, ?, ?, 0)
        """, (event_type, ip, ua, ref))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Analytics log error:", e)


# ----------------- Context Processors -----------------

@app.context_processor
def inject_global_settings():
    return {
        'system_title': 'منظومة ابن سينا لإدارة العيادات والمراكز الطبية',
        'sales_phone': get_setting('sales_phone', '01550597688'),
        'sales_whatsapp': get_setting('sales_whatsapp', '201550597688'),
        'download_url': get_setting('download_url', 'https://drive.google.com/file/d/1lvS5eNAj5hYz1nqZhxuepn2C_GSHjxoJ/view?usp=sharing'),
        'current_admin': session.get('sales_admin_name', ''),
        'is_admin_viewing': is_admin_request(request)
    }


# ----------------- Public Landing Page -----------------

@app.route('/')
@app.route('/api/index')
@app.route('/api/index/')
def home():
    """
    Public Landing Page with Egyptian Medical Clinic Presentation.
    Option 1: If admin is logged in or has admin cookie, shows admin toolbar
    and suppresses visitor tracking.
    """
    is_admin = is_admin_request(request)
    if not is_admin:
        log_landing_event('PAGE_VIEW', request)

    prices = {
        'single': get_setting('single_clinic_price', '3500'),
        'multi': get_setting('multi_clinic_price', '5500'),
        'annual': get_setting('annual_support_price', '1200')
    }

    return render_template('landing.html', is_admin=is_admin, prices=prices)


@app.route('/download')
def track_download():
    """Tracks genuine doctor download clicks and redirects to download link."""
    log_landing_event('DOWNLOAD_CLICK', request)
    url = get_setting('download_url', 'https://drive.google.com/file/d/1lvS5eNAj5hYz1nqZhxuepn2C_GSHjxoJ/view?usp=sharing')
    return redirect(url)


@app.route('/leads/submit', methods=['POST'])
def submit_lead():
    """Captures demo request or sales inquiry from landing page."""
    doctor_name = request.form.get('doctor_name', '').strip()
    clinic_name = request.form.get('clinic_name', '').strip()
    phone = request.form.get('phone', '').strip()
    specialty = request.form.get('specialty', 'طب عام').strip()
    city = request.form.get('city', 'القاهرة').strip()
    notes = request.form.get('notes', '').strip()

    if not doctor_name or not phone:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'success': False, 'message': 'يرجى إدخال اسم الطبيب ورقم الهاتف للتواصل.'}), 400
        flash('يرجى إدخال اسم الطبيب ورقم الهاتف للتواصل.', 'danger')
        return redirect(url_for('home'))

    try:
        conn = database.get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO leads (doctor_name, clinic_name, phone, specialty, city, status, notes)
            VALUES (?, ?, ?, ?, ?, 'جديد', ?)
        """, (doctor_name, clinic_name or f"عيادة د. {doctor_name}", phone, specialty, city, notes))
        conn.commit()
        conn.close()

        log_landing_event('LEAD_SUBMITTED', request)

        msg = f"شكراً دكتور {doctor_name}! تم استلام طلبك بنجاح وسيتواصل معك مستشار النظم الطبية خلال دقائق."
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'success': True, 'message': msg})
        flash(msg, 'success')
        return redirect(url_for('home') + "#contact")
    except Exception as e:
        err_msg = f"حدث خطأ أثناء إرسال البيانات: {e}"
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'success': False, 'message': err_msg}), 500
        flash(err_msg, 'danger')
        return redirect(url_for('home'))


@app.route('/api/track', methods=['POST'])
def api_track_event():
    """Client-side beacon for detailed event tracking (CTA clicks, modal opens)."""
    if is_admin_request(request):
        return jsonify({'status': 'ignored_admin'})

    data = request.get_json(silent=True) or {}
    event_type = data.get('event_type', 'INTERACTION')
    log_landing_event(event_type, request)
    return jsonify({'status': 'recorded'})


# ----------------- Admin Authentication -----------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'sales_admin_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        conn = database.get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, username, password_hash, full_name FROM admin_users WHERE username = ?", (username,))
        admin = cur.fetchone()
        conn.close()

        if admin and check_password_hash(admin['password_hash'], password):
            session['sales_admin_id'] = admin['id']
            session['sales_admin_user'] = admin['username']
            session['sales_admin_name'] = admin['full_name']
            session.permanent = True

            flash(f"مرحباً بك يا أستاذ {admin['full_name']}! تم تسجيل الدخول بنجاح.", 'success')

            # Option 1: Set persistent admin cookie for 1 year
            resp = make_response(redirect(url_for('dashboard')))
            resp.set_cookie(
                'al_tabeeb_admin',
                '1',
                max_age=365 * 24 * 3600,
                httponly=False,  # Accessible to JS so landing page can detect admin
                samesite='Lax'
            )
            return resp
        else:
            flash('اسم المستخدم أو كلمة المرور غير صحيحة.', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('تم تسجيل الخروج بنجاح.', 'info')
    resp = make_response(redirect(url_for('login')))
    # Keep the al_tabeeb_admin cookie by default so owner visits are still not counted,
    # or user can clear it explicitly if requested.
    return resp


@app.route('/admin/clear-admin-cookie')
def clear_admin_cookie():
    """Helper route for testing as an absolute regular stranger."""
    session.clear()
    resp = make_response(redirect(url_for('home')))
    resp.delete_cookie('al_tabeeb_admin')
    flash('تم مسح هوية الإدارة من المتصفح بنجاح، أنت الآن تتصفح كزائر عادي تماماً.', 'info')
    return resp


# ----------------- Admin Dashboard -----------------

@app.route('/dashboard')
@login_required
def dashboard():
    conn = database.get_db()
    c = conn.cursor()

    # 1. Genuine Visitor Traffic Analytics (Excluding Admin!)
    c.execute("SELECT COUNT(*) FROM landing_events WHERE is_admin = 0 AND event_type = 'PAGE_VIEW'")
    total_views = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(DISTINCT ip_address) FROM landing_events WHERE is_admin = 0 AND event_type = 'PAGE_VIEW'")
    unique_visitors = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*) FROM landing_events WHERE is_admin = 0 AND event_type = 'DOWNLOAD_CLICK'")
    total_downloads = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*) FROM leads")
    total_leads = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*) FROM leads WHERE status = 'جديد'")
    new_leads_count = c.fetchone()[0] or 0

    conversion_rate = round((total_leads / unique_visitors * 100), 1) if unique_visitors > 0 else 0.0

    # 2. Clients & Financial Metrics
    c.execute("SELECT COUNT(*) FROM clients")
    total_clients = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*) FROM clients WHERE status = 'نشط'")
    active_clients = c.fetchone()[0] or 0

    c.execute("SELECT COALESCE(SUM(total_price), 0), COALESCE(SUM(paid_amount), 0), COALESCE(SUM(remaining_amount), 0) FROM clients")
    fin_row = c.fetchone()
    total_sales = fin_row[0] or 0.0
    total_collected = fin_row[1] or 0.0
    total_pending = fin_row[2] or 0.0

    # 3. Recent 5 Leads
    c.execute("SELECT id, doctor_name, clinic_name, phone, specialty, city, status, created_at FROM leads ORDER BY id DESC LIMIT 5")
    recent_leads = c.fetchall()

    # 4. Recent 5 Clients
    c.execute("SELECT id, doctor_name, clinic_name, specialty, phone, current_edition, payment_model, paid_amount, remaining_amount FROM clients ORDER BY id DESC LIMIT 5")
    recent_clients = c.fetchall()

    # 5. Upcoming Installments
    c.execute("""
        SELECT i.id, i.client_id, c.doctor_name, c.clinic_name, c.phone, i.title, i.due_date, i.amount, i.status
        FROM installments i
        JOIN clients c ON i.client_id = c.id
        WHERE i.status = 'مستحق'
        ORDER BY i.due_date ASC LIMIT 5
    """)
    upcoming_installments = c.fetchall()

    # 6. Active Telemetry / Radar Devices Count
    c.execute("SELECT COUNT(*) FROM telemetry_devices")
    radar_devices_count = c.fetchone()[0] or 0

    conn.close()

    metrics = {
        'total_views': total_views,
        'unique_visitors': unique_visitors,
        'total_downloads': total_downloads,
        'total_leads': total_leads,
        'new_leads_count': new_leads_count,
        'conversion_rate': conversion_rate,
        'total_clients': total_clients,
        'active_clients': active_clients,
        'total_sales': total_sales,
        'total_collected': total_collected,
        'total_pending': total_pending,
        'radar_devices_count': radar_devices_count
    }

    return render_template(
        'dashboard.html',
        metrics=metrics,
        recent_leads=recent_leads,
        recent_clients=recent_clients,
        upcoming_installments=upcoming_installments
    )


# ----------------- Leads Management -----------------

@app.route('/leads')
@login_required
def leads_list():
    status_filter = request.args.get('status', '').strip()
    search = request.args.get('search', '').strip()

    conn = database.get_db()
    c = conn.cursor()

    query = "SELECT * FROM leads WHERE 1=1"
    params = []

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)

    if search:
        query += " AND (doctor_name LIKE ? OR clinic_name LIKE ? OR phone LIKE ? OR specialty LIKE ? OR city LIKE ?)"
        wild = f"%{search}%"
        params.extend([wild, wild, wild, wild, wild])

    query += " ORDER BY id DESC"
    c.execute(query, params)
    leads = c.fetchall()

    # Counts by status
    c.execute("SELECT status, COUNT(*) FROM leads GROUP BY status")
    status_counts = dict(c.fetchall())

    conn.close()
    return render_template('leads.html', leads=leads, status_counts=status_counts, current_status=status_filter, search=search)


@app.route('/leads/add', methods=['POST'])
@login_required
def add_lead_manual():
    doctor_name = request.form.get('doctor_name', '').strip()
    clinic_name = request.form.get('clinic_name', '').strip()
    phone = request.form.get('phone', '').strip()
    specialty = request.form.get('specialty', 'طب عام').strip()
    city = request.form.get('city', 'القاهرة').strip()
    status = request.form.get('status', 'جديد').strip()
    notes = request.form.get('notes', '').strip()

    if not doctor_name or not phone:
        flash('يرجى إدخال اسم الطبيب ورقم الهاتف.', 'danger')
        return redirect(url_for('leads_list'))

    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO leads (doctor_name, clinic_name, phone, specialty, city, status, source, notes)
        VALUES (?, ?, ?, ?, ?, ?, 'إدخال يدوي', ?)
    """, (doctor_name, clinic_name, phone, specialty, city, status, notes))
    conn.commit()
    conn.close()

    flash(f"تم إضافة الطبيب {doctor_name} إلى قائمة العملاء المحتملين بنجاح.", 'success')
    return redirect(url_for('leads_list'))


@app.route('/leads/update-status/<int:lead_id>', methods=['POST'])
@login_required
def update_lead_status(lead_id):
    new_status = request.form.get('status', '').strip()
    notes = request.form.get('notes', '').strip()

    conn = database.get_db()
    c = conn.cursor()
    if notes:
        c.execute("UPDATE leads SET status = ?, notes = ? WHERE id = ?", (new_status, notes, lead_id))
    else:
        c.execute("UPDATE leads SET status = ? WHERE id = ?", (new_status, lead_id))
    conn.commit()
    conn.close()

    flash('تم تحديث حالة العميل بنجاح.', 'success')
    return redirect(request.referrer or url_for('leads_list'))


@app.route('/leads/delete/<int:lead_id>', methods=['POST'])
@login_required
def delete_lead(lead_id):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()

    flash('تم حذف الطلب بنجاح.', 'info')
    return redirect(url_for('leads_list'))


# ----------------- Clients & Medical Clinics -----------------

@app.route('/clients')
@login_required
def clients_list():
    search = request.args.get('search', '').strip()
    conn = database.get_db()
    c = conn.cursor()

    if search:
        wild = f"%{search}%"
        c.execute("""
            SELECT * FROM clients
            WHERE doctor_name LIKE ? OR clinic_name LIKE ? OR phone LIKE ? OR specialty LIKE ? OR machine_id LIKE ?
            ORDER BY id DESC
        """, (wild, wild, wild, wild, wild))
    else:
        c.execute("SELECT * FROM clients ORDER BY id DESC")
    clients = c.fetchall()

    c.execute("SELECT id, name FROM sales_reps WHERE is_active = 1")
    reps = c.fetchall()

    conn.close()
    return render_template('clients.html', clients=clients, reps=reps, search=search)


@app.route('/clients/add', methods=['POST'])
@login_required
def add_client():
    doctor_name = request.form.get('doctor_name', '').strip()
    clinic_name = request.form.get('clinic_name', '').strip()
    specialty = request.form.get('specialty', 'طب عام').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    city = request.form.get('city', 'القاهرة').strip()
    machine_id = request.form.get('machine_id', '').strip().upper()
    current_edition = request.form.get('current_edition', 'FULL').strip()
    payment_model = request.form.get('payment_model', 'كاش كامل').strip()
    total_price = float(request.form.get('total_price', 0.0) or 0.0)
    paid_amount = float(request.form.get('paid_amount', 0.0) or 0.0)
    remaining_amount = max(0.0, total_price - paid_amount)
    next_payment_date = request.form.get('next_payment_date', '').strip()
    notes = request.form.get('notes', '').strip()
    sales_rep_id = request.form.get('sales_rep_id') or None
    rep_commission = float(request.form.get('rep_commission', 0.0) or 0.0)

    if not doctor_name or not clinic_name or not machine_id:
        flash('يرجى ملء اسم الطبيب، اسم العيادة، وكود جهاز الكمبيوتر (Machine ID).', 'danger')
        return redirect(url_for('clients_list'))

    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO clients (
            doctor_name, clinic_name, specialty, phone, email, city, machine_id,
            current_edition, payment_model, total_price, paid_amount, remaining_amount,
            next_payment_date, notes, sales_rep_id, rep_commission
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        doctor_name, clinic_name, specialty, phone, email, city, machine_id,
        current_edition, payment_model, total_price, paid_amount, remaining_amount,
        next_payment_date, notes, sales_rep_id, rep_commission
    ))
    client_id = c.lastrowid

    # If first paid amount > 0, generate automatic payment receipt
    if paid_amount > 0:
        receipt_no = f"REC-MED-{datetime.now().strftime('%Y%m%d')}-{client_id:04d}"
        c.execute("""
            INSERT INTO payments (client_id, amount, payment_date, payment_method, receipt_number, notes)
            VALUES (?, ?, ?, 'إنستاباي / كاش', ?, 'الدفعة الأولى / دفعة التعاقد')
        """, (client_id, paid_amount, datetime.now().strftime('%Y-%m-%d'), receipt_no))

    # Add commission record if sales rep assigned
    if sales_rep_id and rep_commission > 0:
        c.execute("""
            INSERT INTO rep_commissions (rep_id, client_id, sale_amount, commission_amount, status)
            VALUES (?, ?, ?, ?, 'مستحق')
        """, (sales_rep_id, client_id, total_price, rep_commission))

    conn.commit()
    conn.close()

    flash(f"تم تسجيل العيادة ({clinic_name}) بنجاح! يمكنك الآن إصدار مفتاح التفعيل الرسمي.", 'success')
    return redirect(url_for('client_detail', client_id=client_id))


@app.route('/clients/<int:client_id>')
@login_required
def client_detail(client_id):
    conn = database.get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    client = c.fetchone()
    if not client:
        conn.close()
        flash('العيادة غير موجودة.', 'danger')
        return redirect(url_for('clients_list'))

    # Licenses history
    c.execute("SELECT * FROM licenses WHERE client_id = ? ORDER BY id DESC", (client_id,))
    licenses = c.fetchall()

    # Payments history
    c.execute("SELECT * FROM payments WHERE client_id = ? ORDER BY id DESC", (client_id,))
    payments = c.fetchall()

    # Installments
    c.execute("SELECT * FROM installments WHERE client_id = ? ORDER BY due_date ASC", (client_id,))
    installments = c.fetchall()

    # Hardware migrations
    c.execute("SELECT * FROM device_migrations WHERE client_id = ? ORDER BY id DESC", (client_id,))
    migrations = c.fetchall()

    c.execute("SELECT id, name FROM sales_reps WHERE is_active = 1")
    reps = c.fetchall()

    conn.close()
    return render_template(
        'client_detail.html',
        client=client,
        licenses=licenses,
        payments=payments,
        installments=installments,
        migrations=migrations,
        reps=reps
    )


@app.route('/clients/<int:client_id>/record-payment', methods=['POST'])
@login_required
def record_payment(client_id):
    amount = float(request.form.get('amount', 0.0) or 0.0)
    payment_method = request.form.get('payment_method', 'إنستاباي (InstaPay)').strip()
    transaction_ref = request.form.get('transaction_ref', '').strip()
    notes = request.form.get('notes', '').strip()

    if amount <= 0:
        flash('يرجى إدخال مبلغ صحيح.', 'danger')
        return redirect(url_for('client_detail', client_id=client_id))

    receipt_no = f"REC-MED-{datetime.now().strftime('%Y%m%d%H%M')}-{client_id}"

    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO payments (client_id, amount, payment_date, payment_method, transaction_ref, receipt_number, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (client_id, amount, datetime.now().strftime('%Y-%m-%d'), payment_method, transaction_ref, receipt_no, notes))

    # Update client balance
    c.execute("""
        UPDATE clients
        SET paid_amount = paid_amount + ?,
            remaining_amount = MAX(0.0, remaining_amount - ?)
        WHERE id = ?
    """, (amount, amount, client_id))

    conn.commit()
    conn.close()

    flash(f"تم تسجيل استلام مبلغ {amount:,.0f} ج.م بنجاح برقم إيصال {receipt_no}!", 'success')
    return redirect(url_for('client_detail', client_id=client_id))


@app.route('/clients/<int:client_id>/migrate-hardware', methods=['POST'])
@login_required
def migrate_hardware(client_id):
    new_machine_id = request.form.get('new_machine_id', '').strip().upper()
    reason = request.form.get('reason', 'تغيير جهاز الكمبيوتر بالعيادة').strip()

    if not new_machine_id:
        flash('يرجى إدخال كود الجهاز الجديد.', 'danger')
        return redirect(url_for('client_detail', client_id=client_id))

    conn = database.get_db()
    c = conn.cursor()
    c.execute("SELECT machine_id, clinic_name, current_edition, specialty FROM clients WHERE id = ?", (client_id,))
    client = c.fetchone()
    old_machine_id = client['machine_id']

    # Record migration log
    c.execute("""
        INSERT INTO device_migrations (client_id, old_machine_id, new_machine_id, reason)
        VALUES (?, ?, ?, ?)
    """, (client_id, old_machine_id, new_machine_id, reason))

    # Update client's active machine ID
    c.execute("UPDATE clients SET machine_id = ? WHERE id = ?", (new_machine_id, client_id))

    # Invalidate previous licenses
    c.execute("UPDATE licenses SET is_active = 0 WHERE client_id = ?", (client_id,))

    # Generate new official license for new machine
    token, exp_at, _ = licensing_engine.generate_signed_license(
        machine_id=new_machine_id,
        client_name=client['clinic_name'],
        license_type=client['current_edition'],
        duration_type='LIFETIME',
        specialty=client['specialty']
    )

    c.execute("""
        INSERT INTO licenses (client_id, license_key, license_type, duration_type, expires_at, machine_id, specialty, is_active, notes)
        VALUES (?, ?, ?, 'LIFETIME', ?, ?, ?, 1, ?)
    """, (client_id, token, client['current_edition'], exp_at, new_machine_id, client['specialty'], f"نقل ترخيص: {reason}"))

    conn.commit()
    conn.close()

    flash(f"تم نقل الترخيص بنجاح إلى الجهاز الجديد ({new_machine_id}) وإصدار مفتاح تفعيل جديد!", 'success')
    return redirect(url_for('client_detail', client_id=client_id))


@app.route('/payments/receipt/<int:payment_id>')
@login_required
def print_receipt(payment_id):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        SELECT p.*, c.doctor_name, c.clinic_name, c.phone, c.specialty, c.city, c.total_price, c.paid_amount, c.remaining_amount
        FROM payments p
        JOIN clients c ON p.client_id = c.id
        WHERE p.id = payment_id
    """)
    receipt = c.fetchone()
    conn.close()

    if not receipt:
        flash('إيصال القبض غير موجود.', 'danger')
        return redirect(url_for('clients_list'))

    return render_template('receipt.html', receipt=receipt)


# ----------------- License Generator -----------------

@app.route('/licenses/generate', methods=['GET', 'POST'])
@login_required
def generate_license_view():
    prefill_client_id = request.args.get('client_id')
    prefill_client = None

    conn = database.get_db()
    c = conn.cursor()

    if prefill_client_id:
        c.execute("SELECT * FROM clients WHERE id = ?", (prefill_client_id,))
        prefill_client = c.fetchone()

    c.execute("SELECT id, doctor_name, clinic_name, machine_id, specialty FROM clients ORDER BY clinic_name ASC")
    all_clients = c.fetchall()

    if request.method == 'POST':
        client_id = request.form.get('client_id') or None
        clinic_name = request.form.get('clinic_name', '').strip()
        doctor_name = request.form.get('doctor_name', '').strip()
        machine_id = request.form.get('machine_id', '').strip().upper()
        license_type = request.form.get('license_type', 'FULL').strip()
        duration_type = request.form.get('duration_type', 'LIFETIME').strip()
        custom_days = request.form.get('custom_days')
        specialty = request.form.get('specialty', 'طب عام').strip()
        max_doctors = int(request.form.get('max_doctors', 1) or 1)
        notes = request.form.get('notes', '').strip()

        if not clinic_name or not machine_id:
            flash('يرجى إدخال اسم العيادة وكود الجهاز (Machine ID).', 'danger')
            return render_template('generate_key.html', all_clients=all_clients, prefill=prefill_client)

        token, expires_at, payload = licensing_engine.generate_signed_license(
            machine_id=machine_id,
            client_name=clinic_name,
            license_type=license_type,
            duration_type=duration_type,
            custom_days=int(custom_days) if custom_days else None,
            max_doctors=max_doctors,
            specialty=specialty
        )

        # Save to database
        c.execute("""
            INSERT INTO licenses (
                client_id, license_key, license_type, duration_type, expires_at,
                machine_id, max_doctors, specialty, is_active, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (client_id, token, license_type, duration_type, expires_at, machine_id, max_doctors, specialty, notes))
        conn.commit()
        conn.close()

        # WhatsApp pre-filled text
        wa_text = f"""مرحباً دكتور {doctor_name or clinic_name}،
تم إصدار كود الترخيص الرسمي المعتمد لمنظومة الطبيب لإدارة العيادات والمراكز الطبية:

🏥 المركز/العيادة: {clinic_name}
💻 كود الجهاز: {machine_id}
📜 نوع الترخيص: {duration_type} ({license_type})
⏳ تاريخ الانتهاء: {expires_at or 'ترخيص دائم مدى الحياة'}

🔑 رمز التفعيل:
{token}

طريقة التفعيل:
افتح شاشة البرنامج واضغط على (تفعيل النسخة)، ثم الصق الرمز واضغط حفظ. مبارك لحضرتك!"""

        return render_template(
            'generate_success.html',
            token=token,
            clinic_name=clinic_name,
            doctor_name=doctor_name,
            machine_id=machine_id,
            duration_type=duration_type,
            expires_at=expires_at,
            wa_text=wa_text,
            wa_link=f"https://wa.me/?text={quote(wa_text)}"
        )

    conn.close()
    return render_template('generate_key.html', all_clients=all_clients, prefill=prefill_client)


# ----------------- Sales Reps & Medical Delegates -----------------

@app.route('/reps')
@login_required
def reps_list():
    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        SELECT r.*,
               (SELECT COUNT(*) FROM clients WHERE sales_rep_id = r.id) as clients_count,
               (SELECT COALESCE(SUM(commission_amount), 0) FROM rep_commissions WHERE rep_id = r.id) as total_commissions,
               (SELECT COALESCE(SUM(commission_amount), 0) FROM rep_commissions WHERE rep_id = r.id AND status = 'مسدد') as paid_commissions,
               (SELECT COALESCE(SUM(commission_amount), 0) FROM rep_commissions WHERE rep_id = r.id AND status = 'مستحق') as due_commissions
        FROM sales_reps r
        ORDER BY r.id DESC
    """)
    reps = c.fetchall()
    conn.close()
    return render_template('reps.html', reps=reps)


@app.route('/reps/add', methods=['POST'])
@login_required
def add_rep():
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    city = request.form.get('city', 'القاهرة').strip()
    coverage_area = request.form.get('coverage_area', '').strip()
    rep_code = request.form.get('rep_code', '').strip().upper() or f"MED-REP-{datetime.now().strftime('%M%S')}"
    notes = request.form.get('notes', '').strip()

    if not name or not phone:
        flash('يرجى إدخال اسم المندوب ورقم هاتفه.', 'danger')
        return redirect(url_for('reps_list'))

    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO sales_reps (name, phone, email, city, coverage_area, rep_code, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, phone, email, city, coverage_area, rep_code, notes))
    conn.commit()
    conn.close()

    flash(f"تم تسجيل المندوب الطبي ({name}) بكود ترويجي: {rep_code}", 'success')
    return redirect(url_for('reps_list'))


# ----------------- Radar & Silent Beacon Telemetry -----------------

@app.route('/radar')
@login_required
def radar_view():
    conn = database.get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM telemetry_devices ORDER BY last_heartbeat DESC")
    devices = c.fetchall()
    conn.close()
    return render_template('radar.html', devices=devices)


@app.route('/api/telemetry/install', methods=['POST'])
def clinic_install_beacon():
    """
    Initial Installation Beacon:
    Fires when a new clinic completes the first-time setup wizard.
    1. Registers or updates the machine in telemetry_devices.
    2. Auto-creates a Sales Lead in leads table so sales team can contact the doctor.
    3. Records an audit entry in telemetry_logs.
    """
    data = request.get_json(silent=True) or {}
    machine_id = data.get('machine_id', '').strip().upper()
    clinic_name = data.get('clinic_name', 'عيادة غير محددة').strip()
    doctor_name = data.get('doctor_name', '').strip()
    phone = data.get('phone', '').strip()
    specialty = data.get('specialty', 'طب عام').strip()
    city = data.get('city', '').strip()
    app_version = data.get('app_version', '2.0')
    current_tier = data.get('current_tier', 'TRIAL')
    days_remaining = int(data.get('days_remaining', 7))

    if not machine_id:
        return jsonify({'status': 'error', 'message': 'Machine ID missing'}), 400

    ip = request.headers.get('CF-Connecting-IP') or request.remote_addr or '127.0.0.1'
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    exp_str = (datetime.now() + timedelta(days=days_remaining)).strftime('%Y-%m-%d 23:59:59')

    conn = database.get_db()
    c = conn.cursor()

    # 1. Upsert into telemetry_devices
    c.execute("""
        INSERT INTO telemetry_devices (
            machine_id, clinic_name, doctor_name, contact_phone, specialty, city,
            current_tier, reported_tier, status, days_remaining, trial_started_at,
            trial_expires_at, last_heartbeat, last_ip, app_version, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'TRIAL', ?, ?, ?, ?, ?, ?, 'بيكون التثبيت لأول مرة')
        ON CONFLICT(machine_id) DO UPDATE SET
            clinic_name = excluded.clinic_name,
            doctor_name = excluded.doctor_name,
            contact_phone = excluded.contact_phone,
            specialty = excluded.specialty,
            city = excluded.city,
            last_heartbeat = excluded.last_heartbeat,
            last_ip = excluded.last_ip,
            app_version = excluded.app_version
    """, (
        machine_id, clinic_name, doctor_name, phone, specialty, city,
        current_tier, current_tier, days_remaining, now_str, exp_str, now_str, ip, app_version
    ))

    # 2. Auto-create or update Lead in CRM
    lead_query = "SELECT id FROM leads WHERE notes LIKE ?"
    lead_params = [f"%{machine_id}%"]
    if phone:
        lead_query += " OR phone = ?"
        lead_params.append(phone)
    c.execute(lead_query, lead_params)
    existing_lead = c.fetchone()

    if existing_lead:
        c.execute("""
            UPDATE leads SET
                doctor_name = ?,
                clinic_name = ?,
                phone = ?,
                specialty = ?,
                city = ?,
                notes = ?
            WHERE id = ?
        """, (
            doctor_name or clinic_name, clinic_name, phone or '', specialty, city,
            f"كود الجهاز: {machine_id} - تجربة سارية 7 أيام", existing_lead['id']
        ))
    else:
        c.execute("""
            INSERT INTO leads (
                doctor_name, clinic_name, phone, specialty, city, status, source, notes
            ) VALUES (?, ?, ?, ?, ?, 'جديد', 'بيكون تثبيت المنظومة لأول مرة', ?)
        """, (
            doctor_name or clinic_name, clinic_name, phone or '', specialty, city,
            f"كود الجهاز: {machine_id} - تجربة سارية 7 أيام"
        ))

    # 3. Log audit event
    c.execute("""
        INSERT INTO telemetry_logs (machine_id, event_type, ip_address, payload_json)
        VALUES (?, 'INSTALL_BEACON', ?, ?)
    """, (machine_id, ip, json.dumps(data, ensure_ascii=False)))

    conn.commit()
    conn.close()

    return jsonify({
        'status': 'ok',
        'action': 'CONTINUE',
        'authorized_tier': current_tier,
        'days_remaining': days_remaining,
        'server_time': now_str
    })


@app.route('/api/telemetry/heartbeat', methods=['POST'])
def clinic_heartbeat():
    """
    Periodic Heartbeat Beacon:
    Receives pings from running clinics, audits license state, and delivers remote commands.
    """
    data = request.get_json(silent=True) or {}
    machine_id = data.get('machine_id', '').strip().upper()
    clinic_name = data.get('clinic_name', '')
    doctor_name = data.get('doctor_name', '')
    phone = data.get('phone', '')
    current_tier = data.get('current_tier', 'TRIAL')
    days_remaining = int(data.get('days_remaining', 7))
    patients_count = int(data.get('patients_count', 0))
    prescriptions_count = int(data.get('prescriptions_count', 0))
    app_version = data.get('app_version', '2.0')

    if not machine_id:
        return jsonify({'status': 'error', 'message': 'Machine ID missing'}), 400

    ip = request.headers.get('CF-Connecting-IP') or request.remote_addr or '127.0.0.1'
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = database.get_db()
    c = conn.cursor()

    # Retrieve existing device state and pending commands
    c.execute("SELECT * FROM telemetry_devices WHERE machine_id = ?", (machine_id,))
    dev = c.fetchone()

    pending_command = None
    pending_param = None
    broadcast_msg = ""
    status = 'TRIAL'
    authorized_tier = current_tier

    if dev:
        pending_command = dev['remote_command']
        pending_param = dev['remote_command_param']
        broadcast_msg = dev['broadcast_message'] or ""
        status = dev['status'] or 'TRIAL'
        authorized_tier = dev['current_tier'] or current_tier

        # Execute remote command state transition
        if pending_command == 'LOCK':
            status = 'LOCKED'
        elif pending_command == 'UNLOCK':
            status = 'ACTIVE' if authorized_tier != 'TRIAL' else 'TRIAL'
        elif pending_command in ('EXTEND_DAYS', 'SET_DAYS'):
            days_remaining = int(pending_param or 7)
            status = 'TRIAL'
        elif pending_command == 'UPGRADE':
            authorized_tier = pending_param or 'FULL'
            status = 'ACTIVE'

        # Clear pending command after dispatching
        c.execute("""
            UPDATE telemetry_devices SET
                status = ?,
                current_tier = ?,
                days_remaining = ?,
                last_heartbeat = ?,
                last_ip = ?,
                patients_count = ?,
                prescriptions_count = ?,
                app_version = ?,
                remote_command = NULL,
                remote_command_param = NULL
            WHERE machine_id = ?
        """, (
            status, authorized_tier, days_remaining, now_str, ip,
            patients_count, prescriptions_count, app_version, machine_id
        ))
    else:
        # First heartbeat without prior install beacon
        c.execute("""
            INSERT INTO telemetry_devices (
                machine_id, clinic_name, doctor_name, contact_phone, current_tier,
                days_remaining, last_heartbeat, last_ip, patients_count,
                prescriptions_count, app_version, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TRIAL')
        """, (
            machine_id, clinic_name or 'عيادة جديدة', doctor_name, phone,
            current_tier, days_remaining, now_str, ip, patients_count,
            prescriptions_count, app_version
        ))

    conn.commit()
    conn.close()

    # Determine client action
    action = "CONTINUE"
    msg = ""
    if status in ('LOCKED', 'REVOKED'):
        action = "LOCK"
        msg = "تم إيقاف تشغيل النظام من الإدارة المركزية."
    elif status == 'EXPIRED':
        action = "EXPIRED"
        msg = "انتهت فترة التجربة المجانية للبرنامج."

    return jsonify({
        'status': status,
        'action': action,
        'message': msg,
        'authorized_tier': authorized_tier,
        'days_remaining': days_remaining,
        'remote_command': pending_command,
        'remote_command_param': pending_param,
        'broadcast_message': broadcast_msg,
        'server_time': now_str
    })


@app.route('/radar/device/<int:device_id>/command', methods=['POST'])
@login_required
def send_device_command(device_id):
    """Admin endpoint to dispatch remote commands to clinic devices."""
    command = request.form.get('command', '').strip()
    param = request.form.get('param', '').strip()
    broadcast = request.form.get('broadcast_message', '').strip()

    conn = database.get_db()
    c = conn.cursor()

    if command == 'BROADCAST':
        c.execute("UPDATE telemetry_devices SET broadcast_message = ? WHERE id = ?", (broadcast, device_id))
        flash('تم تحديث رسالة البث للمركز الطبي.', 'success')
    elif command in ('LOCK', 'UNLOCK'):
        c.execute("UPDATE telemetry_devices SET remote_command = ?, remote_command_param = ? WHERE id = ?", (command, param, device_id))
        flash(f"تم إرسال أمر {('قفل النظام' if command == 'LOCK' else 'فك القفل')} للجهاز بنجاح.", 'warning' if command == 'LOCK' else 'success')
    elif command == 'EXTEND_DAYS':
        days = int(param or 7)
        c.execute("""
            UPDATE telemetry_devices SET
                remote_command = 'EXTEND_DAYS',
                remote_command_param = ?,
                days_remaining = days_remaining + ?,
                status = 'TRIAL'
            WHERE id = ?
        """, (str(days), days, device_id))
        flash(f"تم تمديد فترة التجربة بمقدار {days} يوماً إضافية بنجاح!", 'success')
    elif command == 'CLEAR_BROADCAST':
        c.execute("UPDATE telemetry_devices SET broadcast_message = NULL WHERE id = ?", (device_id,))
        flash('تم مسح رسالة البث.', 'info')

    conn.commit()
    conn.close()
    return redirect(url_for('radar_view'))


@app.route('/radar/device/<int:device_id>/delete', methods=['POST'])
@login_required
def delete_device(device_id):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("DELETE FROM telemetry_devices WHERE id = ?", (device_id,))
    conn.commit()
    conn.close()
    flash('تم إزالة الجهاز من الرادار بنجاح.', 'info')
    return redirect(url_for('radar_view'))


# ----------------- Settings & Management -----------------

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_view():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'save_general':
            set_setting('sales_phone', request.form.get('sales_phone', '').strip())
            set_setting('sales_whatsapp', request.form.get('sales_whatsapp', '').strip())
            set_setting('download_url', request.form.get('download_url', '').strip())
            set_setting('single_clinic_price', request.form.get('single_clinic_price', '3500').strip())
            set_setting('multi_clinic_price', request.form.get('multi_clinic_price', '5500').strip())
            set_setting('annual_support_price', request.form.get('annual_support_price', '1200').strip())
            flash('تم حفظ الإعدادات العامة بنجاح.', 'success')

        elif action == 'change_password':
            curr_pass = request.form.get('current_password', '').strip()
            new_pass = request.form.get('new_password', '').strip()
            admin_id = session.get('sales_admin_id')

            conn = database.get_db()
            c = conn.cursor()
            c.execute("SELECT password_hash FROM admin_users WHERE id = ?", (admin_id,))
            row = c.fetchone()

            if row and check_password_hash(row['password_hash'], curr_pass):
                if len(new_pass) >= 6:
                    c.execute("UPDATE admin_users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_pass), admin_id))
                    conn.commit()
                    flash('تم تغيير كلمة المرور بنجاح!', 'success')
                else:
                    flash('كلمة المرور يجب أن لا تقل عن 6 أحرف.', 'danger')
            else:
                flash('كلمة المرور الحالية غير صحيحة.', 'danger')
            conn.close()

        return redirect(url_for('settings_view'))

    settings = {
        'sales_phone': get_setting('sales_phone', '01550597688'),
        'sales_whatsapp': get_setting('sales_whatsapp', '201550597688'),
        'download_url': get_setting('download_url', 'https://drive.google.com/file/d/1lvS5eNAj5hYz1nqZhxuepn2C_GSHjxoJ/view?usp=sharing'),
        'single_clinic_price': get_setting('single_clinic_price', '3500'),
        'multi_clinic_price': get_setting('multi_clinic_price', '5500'),
        'annual_support_price': get_setting('annual_support_price', '1200')
    }
    return render_template('settings.html', settings=settings)


@app.route('/backup/download')
@login_required
def download_backup():
    """Generates atomic backup and triggers browser download."""
    try:
        backup_file = database.create_backup()
        return send_file(backup_file, as_attachment=True, download_name=os.path.basename(backup_file))
    except Exception as e:
        flash(f"تعذر إنشاء النسخة الاحتياطية: {e}", 'danger')
        return redirect(url_for('settings_view'))


# ----------------- Run Local Server -----------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"\n========================================================")
    print(f"  منظومة الطبيب - بوابة المبيعات وصفحة الهبوط والإدارة")
    print(f"  العنوان المحلي: http://127.0.0.1:{port}")
    print(f"  بيانات دخول المدير الافتراضية: ahmed / admin123")
    print(f"========================================================\n")
    app.run(host='0.0.0.0', port=port, debug=True)
