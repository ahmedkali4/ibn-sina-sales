# -*- coding: utf-8 -*-
import sqlite3
import os
import sys
import json
import urllib.request
import urllib.error
from werkzeug.security import generate_password_hash

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Local DB path with Vercel serverless /tmp fallback
if os.path.exists("/tmp"):
    DB_PATH = os.environ.get('SALES_HUB_DB_PATH', "/tmp/ibn_sina_sales.db")
else:
    DB_PATH = os.environ.get('SALES_HUB_DB_PATH', os.path.join(BASE_DIR, "ibn_sina_sales.db"))

TURSO_DB_URL = os.environ.get('TURSO_DB_URL', "").strip().strip('"').strip("'")
TURSO_AUTH_TOKEN = os.environ.get('TURSO_AUTH_TOKEN', "").strip().strip('"').strip("'")

# Built-in fallback Turso if desired for zero-setup cloud sync
if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
    TURSO_DB_URL = os.environ.get("TURSO_DATABASE_URL", "")
    TURSO_AUTH_TOKEN = os.environ.get("TURSO_TOKEN", "")

USE_TURSO = bool(TURSO_DB_URL and TURSO_AUTH_TOKEN and ("turso.io" in TURSO_DB_URL))


class TursoRow:
    def __init__(self, cols, values):
        self._cols = list(cols or [])
        self._values = list(values or [])
        self._col_map = {str(c).lower(): v for c, v in zip(self._cols, self._values)}
        self._orig_map = {str(c): v for c, v in zip(self._cols, self._values)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        s_key = str(key)
        if s_key in self._orig_map:
            return self._orig_map[s_key]
        return self._col_map.get(s_key.lower())

    def __getattr__(self, name):
        if name in self.__dict__:
            return self.__dict__[name]
        s_name = str(name)
        if s_name in self._orig_map:
            return self._orig_map[s_name]
        if s_name.lower() in self._col_map:
            return self._col_map[s_name.lower()]
        raise AttributeError(f"'TursoRow' object has no attribute '{name}'")

    def get(self, key, default=None):
        try:
            val = self[key]
            return val if val is not None else default
        except Exception:
            return default

    def keys(self):
        return self._cols

    def values(self):
        return self._values

    def items(self):
        return list(zip(self._cols, self._values))

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"<TursoRow {dict(zip(self._cols, self._values))}>"


class TursoCursor:
    def __init__(self, client):
        self.client = client
        self.description = None
        self._rows = []
        self._idx = 0
        self.rowcount = 0
        self.lastrowid = None

    def execute(self, sql, params=None):
        clean_sql = sql.strip()
        p = list(params) if params is not None else []
        res = self.client.execute(clean_sql, p)
        self.description = [(c, None, None, None, None, None, None) for c in (res.columns or [])]
        self._rows = [TursoRow(res.columns, r) for r in (res.rows or [])]
        self._idx = 0
        self.rowcount = res.rows_affected or len(self._rows)
        self.lastrowid = res.last_insert_rowid
        return self

    def fetchone(self):
        if self._idx < len(self._rows):
            r = self._rows[self._idx]
            self._idx += 1
            return r
        return None

    def fetchall(self):
        r = self._rows[self._idx:]
        self._idx = len(self._rows)
        return r


class TursoClient:
    def __init__(self, url, token):
        self.url = url.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"
        self.token = token

    def cursor(self):
        return TursoCursor(self)

    def commit(self):
        pass

    def close(self):
        pass

    def execute(self, sql, params=None):
        class TursoResult:
            def __init__(self, cols, rows, affected, last_id):
                self.columns = cols or []
                self.rows = rows or []
                self.rows_affected = affected
                self.last_insert_rowid = last_id

        p = list(params) if params is not None else []
        args = []
        for val in p:
            if val is None:
                args.append({"type": "null"})
            elif isinstance(val, int):
                args.append({"type": "integer", "value": str(val)})
            elif isinstance(val, float):
                args.append({"type": "float", "value": val})
            else:
                args.append({"type": "text", "value": str(val)})

        req_body = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": sql,
                        "args": args
                    }
                },
                {"type": "close"}
            ]
        }
        data = json.dumps(req_body).encode('utf-8')
        req = urllib.request.Request(
            self.url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "AlTabeebSales/1.0"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                resp_json = json.loads(response.read().decode('utf-8'))
                res_obj = resp_json.get("results", [{}])[0].get("response", {}).get("result", {})
                cols = [c["name"] for c in res_obj.get("cols", [])]
                rows = []
                for row_data in res_obj.get("rows", []):
                    row_vals = []
                    for cell in row_data:
                        cell_val = cell.get("value")
                        if cell.get("type") == "null" or cell_val is None:
                            row_vals.append(None)
                        elif cell.get("type") == "integer":
                            row_vals.append(int(cell_val))
                        elif cell.get("type") == "float":
                            row_vals.append(float(cell_val))
                        else:
                            row_vals.append(cell_val)
                    rows.append(row_vals)
                return TursoResult(cols, rows, res_obj.get("affected_row_count", 0), res_obj.get("last_insert_rowid"))
        except Exception as e:
            raise Exception(f"Turso Execution Error: {e}")


def get_db():
    if USE_TURSO:
        try:
            return TursoClient(TURSO_DB_URL, TURSO_AUTH_TOKEN)
        except Exception as e:
            print("Turso connection fallback to SQLite:", e)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # 1. Admin Users
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 2. Clients Table (Clinics & Medical Centers)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_name TEXT NOT NULL,
            clinic_name TEXT NOT NULL,
            specialty TEXT DEFAULT 'طب عام',
            phone TEXT NOT NULL,
            email TEXT,
            city TEXT,
            machine_id TEXT NOT NULL,
            current_edition TEXT NOT NULL DEFAULT 'FULL',
            payment_model TEXT NOT NULL DEFAULT 'كاش كامل',
            total_price REAL DEFAULT 0.0,
            paid_amount REAL DEFAULT 0.0,
            remaining_amount REAL DEFAULT 0.0,
            next_payment_date TEXT,
            status TEXT DEFAULT 'نشط',
            notes TEXT,
            referred_by_id INTEGER,
            referral_discount REAL DEFAULT 0.0,
            sales_rep_id INTEGER,
            rep_commission REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3. Issued Licenses History
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            license_key TEXT NOT NULL,
            license_type TEXT NOT NULL,
            duration_type TEXT NOT NULL DEFAULT 'LIFETIME',
            expires_at TEXT,
            machine_id TEXT NOT NULL,
            max_doctors INTEGER DEFAULT 1,
            specialty TEXT,
            is_active INTEGER DEFAULT 1,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE SET NULL
        )
    """)

    # 4. Financial Payments (Receipts)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_date TEXT NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'إنستاباي (InstaPay)',
            transaction_ref TEXT,
            receipt_number TEXT UNIQUE,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE CASCADE
        )
    """)

    # 5. Device Migrations & Hardware Changes Log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS device_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            old_machine_id TEXT NOT NULL,
            new_machine_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            migrated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE CASCADE
        )
    """)

    # 6. Sales Leads & Follow-ups Pipeline (Doctors interested in demo/purchase)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_name TEXT NOT NULL,
            clinic_name TEXT,
            specialty TEXT DEFAULT 'عام',
            phone TEXT NOT NULL,
            city TEXT,
            interested_edition TEXT DEFAULT 'FULL',
            expected_contact_date TEXT,
            status TEXT DEFAULT 'جديد', -- جديد, تم التواصل, جاري التجربة, تم البيع, مؤجل, غير مهتم
            source TEXT DEFAULT 'صفحة الهبوط',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 7. Installments & Scheduled Payments Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS installments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            installment_no INTEGER NOT NULL,
            title TEXT NOT NULL,
            due_date TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'مستحق', -- مستحق, مسدد
            paid_date TEXT,
            payment_method TEXT,
            receipt_number TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE CASCADE
        )
    """)

    # 8. Sales Reps Table (مناديب وسفراء المبيعات الطبية)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales_reps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            city TEXT,
            coverage_area TEXT,
            rep_code TEXT UNIQUE,
            total_earned REAL DEFAULT 0.0,
            total_paid REAL DEFAULT 0.0,
            balance REAL DEFAULT 0.0,
            is_active INTEGER DEFAULT 1,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 9. Rep Commissions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rep_commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rep_id INTEGER NOT NULL,
            client_id INTEGER,
            sale_amount REAL NOT NULL,
            commission_amount REAL NOT NULL,
            status TEXT DEFAULT 'مستحق',
            paid_date TEXT,
            payout_method TEXT,
            transaction_ref TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (rep_id) REFERENCES sales_reps (id) ON DELETE CASCADE,
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE SET NULL
        )
    """)

    # 10. Remote Licensing Radar & Silent Beacon
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telemetry_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            machine_id TEXT UNIQUE NOT NULL,
            clinic_name TEXT NOT NULL,
            doctor_name TEXT,
            contact_phone TEXT,
            specialty TEXT DEFAULT 'طب عام',
            city TEXT,
            current_tier TEXT NOT NULL DEFAULT 'TRIAL',
            reported_tier TEXT DEFAULT 'TRIAL',
            status TEXT NOT NULL DEFAULT 'TRIAL',
            days_remaining INTEGER DEFAULT 7,
            trial_started_at TEXT,
            trial_expires_at TEXT,
            last_heartbeat TEXT,
            last_ip TEXT,
            patients_count INTEGER DEFAULT 0,
            prescriptions_count INTEGER DEFAULT 0,
            app_version TEXT DEFAULT '2.0',
            remote_command TEXT,
            remote_command_param TEXT,
            broadcast_message TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE SET NULL
        )
    """)

    # Schema migration helper for telemetry_devices
    cursor.execute("PRAGMA table_info(telemetry_devices)")
    existing_telem_cols = [row[1] for row in cursor.fetchall()]
    telem_new_cols = [
        ("specialty", "TEXT DEFAULT 'طب عام'"),
        ("city", "TEXT"),
        ("remote_command_param", "TEXT"),
        ("broadcast_message", "TEXT")
    ]
    for col_n, col_d in telem_new_cols:
        if col_n not in existing_telem_cols:
            try:
                cursor.execute(f"ALTER TABLE telemetry_devices ADD COLUMN {col_n} {col_d}")
            except Exception:
                pass

    # 11. Telemetry Audit Logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telemetry_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER,
            machine_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            ip_address TEXT,
            payload_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (device_id) REFERENCES telemetry_devices (id) ON DELETE CASCADE
        )
    """)

    # 12. Landing Traffic & Conversion Analytics (Option 1 Admin Excluded)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS landing_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            referrer TEXT,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 13. System Settings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Default settings
    default_settings = [
        ('system_name', 'منظومة ابن سينا لإدارة العيادات والمراكز الطبية'),
        ('sales_phone', '01550597688'),
        ('sales_whatsapp', '201550597688'),
        ('download_url', 'https://drive.google.com/file/d/1lvS5eNAj5hYz1nqZhxuepn2C_GSHjxoJ/view?usp=sharing'),
        ('support_hours', 'يومياً من 9 صباحاً حتى 11 مساءً'),
        ('single_clinic_price', '3500'),
        ('multi_clinic_price', '5500'),
        ('annual_support_price', '1200')
    ]
    for k, v in default_settings:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Create default admin user if none exists
    cursor.execute("SELECT id FROM admin_users WHERE username = 'ahmed'")
    if not cursor.fetchone():
        pwd_hash = generate_password_hash("admin123")
        cursor.execute("""
            INSERT INTO admin_users (username, password_hash, full_name, phone, email)
            VALUES (?, ?, ?, ?, ?)
        """, ('ahmed', pwd_hash, 'أحمد مبروك (المالك والمدير)', '01550597688', 'Ahmed.Mabrouk20011@gmail.com'))

    conn.commit()
    conn.close()
    print("SUCCESS: Al-Tabeeb Sales Database initialized successfully!")


def create_backup(target_dir=None, max_keep=30) -> str:
    """Atomic backup of local database."""
    from datetime import datetime
    if target_dir is None:
        target_dir = os.path.join(BASE_DIR, "backups")
    os.makedirs(target_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"al_tabeeb_sales_backup_{timestamp}.db"
    backup_path = os.path.join(target_dir, backup_filename)
    
    if os.path.exists(DB_PATH):
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(backup_path)
        with dst:
            src.backup(dst, pages=100)
        dst.close()
        src.close()
    return backup_path


if __name__ == '__main__':
    init_db()
