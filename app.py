from flask import Flask, render_template, request, redirect, url_for, jsonify
from datetime import datetime, date
import json
import os

import os
_here = os.path.dirname(os.path.abspath(__file__))
# Support templates both in ./templates/ and directly next to app.py
_tmpl = os.path.join(_here, 'templates')
if not os.path.isdir(_tmpl):
    _tmpl = _here
app = Flask(__name__, template_folder=_tmpl)

# ---- DATABASE SETUP ----
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    # PostgreSQL on Railway
    import psycopg2
    import psycopg2.extras
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

    def get_db():
        conn = psycopg2.connect(DATABASE_URL)
        return conn

    def q(sql):
        # Convert SQLite ? placeholders to PostgreSQL %s
        return sql.replace('?', '%s')

    PG = True
else:
    # SQLite locally
    import sqlite3
    DB = 'import_tracker.db'

    def get_db():
        conn = sqlite3.connect(DB, timeout=60, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        return conn

    def q(sql):
        return sql

    PG = False


def fetchall(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(q(sql), params)
    if PG:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    else:
        rows = cur.fetchall()
        return [dict(r) for r in rows]

def fetchone(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(q(sql), params)
    if PG:
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None
    else:
        row = cur.fetchone()
        return dict(row) if row else None

def execute(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(q(sql), params)
    return cur


def init_db():
    conn = get_db()
    cur = conn.cursor()

    if PG:
        cur.execute('''CREATE TABLE IF NOT EXISTS agreements (
            id SERIAL PRIMARY KEY,
            agreement_number TEXT,
            container_count TEXT,
            delivery_terms TEXT,
            etd DATE,
            eta DATE,
            container_numbers TEXT,
            supplier TEXT,
            pol TEXT,
            pod TEXT,
            shipping_line TEXT,
            freight_usd REAL DEFAULT 0,
            inland_usd REAL DEFAULT 0,
            goods_payment_percent REAL DEFAULT 0,
            docs_received INTEGER DEFAULT 0,
            docs_received_date DATE,
            docs_notes TEXT,
            record_number TEXT,
            notes TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            archived_at TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS goods (
            id SERIAL PRIMARY KEY,
            agreement_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            quantity REAL,
            unit TEXT DEFAULT 'шт',
            price_usd REAL DEFAULT 0
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS options (
            id SERIAL PRIMARY KEY,
            category TEXT NOT NULL,
            value TEXT NOT NULL,
            UNIQUE(category, value)
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS visible_columns (
            col_key TEXT PRIMARY KEY,
            col_label TEXT,
            visible INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        )''')
    else:
        cur.executescript('''
            CREATE TABLE IF NOT EXISTS agreements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agreement_number TEXT,
                container_count TEXT,
                delivery_terms TEXT,
                etd DATE,
                eta DATE,
                container_numbers TEXT,
                supplier TEXT,
                pol TEXT,
                pod TEXT,
                shipping_line TEXT,
                freight_usd REAL DEFAULT 0,
                inland_usd REAL DEFAULT 0,
                goods_payment_percent REAL DEFAULT 0,
                docs_received INTEGER DEFAULT 0,
                docs_received_date DATE,
                docs_notes TEXT,
                record_number TEXT,
                notes TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                archived_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS goods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agreement_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                quantity REAL,
                unit TEXT DEFAULT 'шт',
                price_usd REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                value TEXT NOT NULL,
                UNIQUE(category, value)
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS visible_columns (
                col_key TEXT PRIMARY KEY,
                col_label TEXT,
                visible INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0
            );
        ''')
        # SQLite migrations
        for col, typ in [('goods_payment_percent','REAL DEFAULT 0'),('docs_received_date','DATE'),('docs_notes','TEXT'),('eta_sklad','DATE')]:
            try: cur.execute(f"ALTER TABLE agreements ADD COLUMN {col} {typ}")
            except: pass

    # Default options
    defaults = {
        'pol': ['Tianjin, China','Ningbo, China','Shanghai, China','Guangzhou, China','Ho Chi Minh, Vietnam','Haiphong, Vietnam'],
        'pod': ['Gdansk, Poland','Hamburg, Germany','Rotterdam, Netherlands','Klaipeda, Lithuania','Riga, Latvia'],
        'shipping_line': ['MSC','Maersk','CMA CGM','Evergreen','COSCO','ONE','Hapag-Lloyd'],
        'supplier': [],
        'container_count': ['1*20','1*40','2*20','2*40','3*40','1*40HC','2*40HC'],
        'good_name': ['Стільці','Столи','Дивани','Ліжка','Шафи','Матраци','Крісла','Тумби'],
    }
    for cat, vals in defaults.items():
        for v in vals:
            try:
                cur.execute(q("INSERT INTO options (category,value) SELECT ?,? WHERE NOT EXISTS (SELECT 1 FROM options WHERE category=? AND value=?)") if not PG else
                    "INSERT INTO options (category,value) VALUES (%s,%s) ON CONFLICT DO NOTHING", (cat,v) if PG else (cat,v,cat,v))
            except: pass

    cols = [
        ('agreement_number','Угода',1,0),('supplier','Постачальник',1,1),('route','Маршрут',1,2),
        ('shipping_line','Лінія',0,3),('container_count','Контейнери',0,4),('delivery_terms','Умови',0,5),
        ('etd','ETD',1,6),('eta','ETA',1,7),('status','Статус',1,8),('freight_usd','Фрахт $',0,9),
        ('freight','Доставка $',1,10),('payment','Оплата товару',1,11),('docs','Документи',0,12),
        ('record_number','Номер запису',0,13),
    ]
    for c in cols:
        try:
            if PG:
                cur.execute("INSERT INTO visible_columns VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", c)
            else:
                cur.execute("INSERT OR IGNORE INTO visible_columns VALUES (?,?,?,?)", c)
        except: pass

    conn.commit()
    conn.close()


def days_until(eta_str):
    if not eta_str: return None
    try:
        if isinstance(eta_str, (datetime, date)):
            eta = eta_str if isinstance(eta_str, date) else eta_str.date()
        else:
            eta = datetime.strptime(str(eta_str)[:10], '%Y-%m-%d').date()
        return (eta - date.today()).days
    except: return None

def get_options(cat):
    conn = get_db()
    rows = fetchall(conn, "SELECT value FROM options WHERE category=? ORDER BY value", (cat,))
    conn.close()
    return [r['value'] for r in rows]

def get_visible_cols():
    conn = get_db()
    rows = fetchall(conn, "SELECT * FROM visible_columns ORDER BY sort_order")
    conn.close()
    return rows

# ---- OPTIONS API ----
@app.route('/api/options/<cat>', methods=['GET'])
def api_get_options(cat):
    return jsonify(get_options(cat))

@app.route('/api/options/<cat>', methods=['POST'])
def api_add_option(cat):
    val = request.json.get('value','').strip()
    if val:
        conn = get_db()
        try:
            if PG:
                execute(conn, "INSERT INTO options (category,value) VALUES (?,?) ON CONFLICT DO NOTHING", (cat,val))
            else:
                execute(conn, "INSERT OR IGNORE INTO options (category,value) VALUES (?,?)", (cat,val))
            conn.commit()
        finally:
            conn.close()
    return jsonify(get_options(cat))

@app.route('/api/options/<cat>/<path:val>', methods=['DELETE'])
def api_del_option(cat, val):
    conn = get_db()
    try:
        execute(conn, "DELETE FROM options WHERE category=? AND value=?", (cat,val))
        conn.commit()
    finally:
        conn.close()
    return jsonify(get_options(cat))

@app.route('/api/columns', methods=['GET'])
def api_get_cols():
    return jsonify(get_visible_cols())

@app.route('/api/columns', methods=['POST'])
def api_save_cols():
    conn = get_db()
    try:
        for c in request.json:
            execute(conn, "UPDATE visible_columns SET visible=? WHERE col_key=?", (c['visible'],c['col_key']))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok':True})

# ---- INDEX ----
@app.route('/')
def index():
    conn = get_db()
    status_filter = request.args.get('status','active')
    search        = request.args.get('search','')
    sort_by       = request.args.get('sort','eta')
    sort_dir      = request.args.get('dir','asc')
    f_supplier    = request.args.get('f_supplier','')
    f_line        = request.args.get('f_line','')
    f_pol         = request.args.get('f_pol','')
    f_pod         = request.args.get('f_pod','')
    f_terms       = request.args.get('f_terms','')

    allowed = {'eta','etd','agreement_number','supplier','shipping_line','created_at'}
    if sort_by not in allowed: sort_by = 'eta'
    order = f"{sort_by} {'ASC' if sort_dir=='asc' else 'DESC'} NULLS LAST, created_at DESC"

    query = "SELECT * FROM agreements WHERE status=?"
    params = [status_filter]
    if search:
        s = f'%{search}%'
        query += " AND (agreement_number LIKE ? OR supplier LIKE ? OR container_numbers LIKE ? OR record_number LIKE ?)"
        params += [s,s,s,s]
    if f_supplier: query += " AND supplier=?"; params.append(f_supplier)
    if f_line:     query += " AND shipping_line=?"; params.append(f_line)
    if f_pol:      query += " AND pol=?"; params.append(f_pol)
    if f_pod:      query += " AND pod=?"; params.append(f_pod)
    if f_terms:    query += " AND delivery_terms=?"; params.append(f_terms)
    query += f" ORDER BY {order}"

    agreements = fetchall(conn, query, params)
    result = []
    for a in agreements:
        goods = fetchall(conn, "SELECT * FROM goods WHERE agreement_id=?", (a['id'],))
        goods_total    = sum((g['quantity'] or 0)*(g['price_usd'] or 0) for g in goods)
        goods_paid     = goods_total*(a['goods_payment_percent'] or 0)/100
        delivery_total = (a['freight_usd'] or 0)+(a['inland_usd'] or 0)
        result.append({'data':a,'goods':goods,'goods_total':goods_total,
            'goods_paid':goods_paid,'goods_remaining':goods_total-goods_paid,
            'delivery_total':delivery_total,'days_until_eta':days_until(a['eta_sklad'] or a['eta'])})

    all_suppliers = [r['supplier'] for r in fetchall(conn, "SELECT DISTINCT supplier FROM agreements WHERE status=? AND supplier IS NOT NULL ORDER BY supplier",(status_filter,))]
    all_lines     = [r['shipping_line'] for r in fetchall(conn, "SELECT DISTINCT shipping_line FROM agreements WHERE status=? AND shipping_line IS NOT NULL ORDER BY shipping_line",(status_filter,))]
    all_pol       = [r['pol'] for r in fetchall(conn, "SELECT DISTINCT pol FROM agreements WHERE status=? AND pol IS NOT NULL ORDER BY pol",(status_filter,))]
    all_pod       = [r['pod'] for r in fetchall(conn, "SELECT DISTINCT pod FROM agreements WHERE status=? AND pod IS NOT NULL ORDER BY pod",(status_filter,))]
    conn.close()

    return render_template('index.html', agreements=result, status_filter=status_filter, search=search,
        sort_by=sort_by, sort_dir=sort_dir, all_suppliers=all_suppliers, all_lines=all_lines,
        all_pol=all_pol, all_pod=all_pod, f_supplier=f_supplier, f_line=f_line,
        f_pol=f_pol, f_pod=f_pod, f_terms=f_terms, vis_cols=get_visible_cols())

def save_agreement_from_form(conn, aid=None):
    fields = (
        request.form.get('agreement_number') or None,
        request.form.get('container_count_custom') or request.form.get('container_count'),
        request.form.get('delivery_terms'),
        request.form.get('etd') or None,
        request.form.get('eta') or None,
        request.form.get('container_numbers'),
        request.form.get('supplier_custom') or request.form.get('supplier'),
        request.form.get('pol_custom') or request.form.get('pol'),
        request.form.get('pod_custom') or request.form.get('pod'),
        request.form.get('shipping_line_custom') or request.form.get('shipping_line'),
        float(request.form.get('freight_usd') or 0),
        float(request.form.get('inland_usd') or 0),
        float(request.form.get('goods_payment_percent') or 0),
        1 if request.form.get('docs_received') else 0,
        request.form.get('docs_received_date') or None,
        request.form.get('docs_notes',''),
        request.form.get('record_number'),
        request.form.get('notes'),
        request.form.get('eta_sklad') or None,
    )
    if aid:
        execute(conn, '''UPDATE agreements SET agreement_number=?,container_count=?,delivery_terms=?,
            etd=?,eta=?,container_numbers=?,supplier=?,pol=?,pod=?,shipping_line=?,
            freight_usd=?,inland_usd=?,goods_payment_percent=?,docs_received=?,docs_received_date=?,
            docs_notes=?,record_number=?,notes=?,eta_sklad=? WHERE id=?''', fields+(aid,))
        return aid
    else:
        if PG:
            cur = execute(conn, '''INSERT INTO agreements (agreement_number,container_count,delivery_terms,
                etd,eta,container_numbers,supplier,pol,pod,shipping_line,freight_usd,inland_usd,
                goods_payment_percent,docs_received,docs_received_date,docs_notes,record_number,notes,eta_sklad)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id''', fields)
            return cur.fetchone()[0]
        else:
            cur = execute(conn, '''INSERT INTO agreements (agreement_number,container_count,delivery_terms,
                etd,eta,container_numbers,supplier,pol,pod,shipping_line,freight_usd,inland_usd,
                goods_payment_percent,docs_received,docs_received_date,docs_notes,record_number,notes,eta_sklad)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', fields)
            return cur.lastrowid

def save_goods(conn, aid):
    execute(conn, 'DELETE FROM goods WHERE agreement_id=?', (aid,))
    names   = request.form.getlist('good_name[]')
    customs = request.form.getlist('good_name_custom[]')
    qtys    = request.form.getlist('good_qty[]')
    units   = request.form.getlist('good_unit[]')
    prices  = request.form.getlist('good_price[]')
    for i, name in enumerate(names):
        final = (customs[i].strip() if i < len(customs) and customs[i].strip() else name.strip())
        if final:
            execute(conn, 'INSERT INTO goods (agreement_id,name,quantity,unit,price_usd) VALUES (?,?,?,?,?)',
                (aid, final, float(qtys[i] or 0), units[i] or 'шт', float(prices[i] or 0)))
    supplier = (request.form.get('supplier_custom') or request.form.get('supplier','') or '').strip()
    if supplier:
        try:
            if PG: execute(conn, "INSERT INTO options (category,value) VALUES (?,?) ON CONFLICT DO NOTHING", ('supplier',supplier))
            else:  execute(conn, "INSERT OR IGNORE INTO options (category,value) VALUES (?,?)", ('supplier',supplier))
        except: pass

@app.route('/agreement/new', methods=['GET','POST'])
def new_agreement():
    if request.method == 'POST':
        try:
            conn = get_db()
            aid = save_agreement_from_form(conn)
            save_goods(conn, aid)
            conn.commit()
        except Exception as e:
            conn.rollback(); raise e
        finally:
            conn.close()
        return redirect(url_for('index'))
    opts = {c: get_options(c) for c in ['pol','pod','shipping_line','supplier','container_count','good_name']}
    return render_template('form.html', agreement=None, goods=[], opts=opts)

@app.route('/agreement/<int:aid>/edit', methods=['GET','POST'])
def edit_agreement(aid):
    if request.method == 'POST':
        try:
            conn = get_db()
            save_agreement_from_form(conn, aid)
            save_goods(conn, aid)
            conn.commit()
        except Exception as e:
            conn.rollback(); raise e
        finally:
            conn.close()
        return redirect(url_for('index'))
    conn = get_db()
    agreement = fetchone(conn, 'SELECT * FROM agreements WHERE id=?', (aid,))
    goods     = fetchall(conn, 'SELECT * FROM goods WHERE agreement_id=?', (aid,))
    conn.close()
    opts = {c: get_options(c) for c in ['pol','pod','shipping_line','supplier','container_count','good_name']}
    return render_template('form.html', agreement=agreement, goods=goods, opts=opts)

@app.route('/agreement/<int:aid>/archive', methods=['POST'])
def archive_agreement(aid):
    conn = get_db()
    try:
        execute(conn, "UPDATE agreements SET status='archived',archived_at=CURRENT_TIMESTAMP WHERE id=?", (aid,))
        conn.commit()
    finally: conn.close()
    return redirect(url_for('index'))

@app.route('/agreement/<int:aid>/restore', methods=['POST'])
def restore_agreement(aid):
    conn = get_db()
    try:
        execute(conn, "UPDATE agreements SET status='active',archived_at=NULL WHERE id=?", (aid,))
        conn.commit()
    finally: conn.close()
    return redirect(url_for('index', status='archived'))

@app.route('/agreement/<int:aid>/delete', methods=['POST'])
def delete_agreement(aid):
    conn = get_db()
    try:
        execute(conn, "DELETE FROM goods WHERE agreement_id=?", (aid,))
        execute(conn, "DELETE FROM agreements WHERE id=?", (aid,))
        conn.commit()
    finally: conn.close()
    return redirect(url_for('index'))

@app.route('/agreement/<int:aid>')
def view_agreement(aid):
    conn = get_db()
    a = fetchone(conn, 'SELECT * FROM agreements WHERE id=?', (aid,))
    goods = fetchall(conn, 'SELECT * FROM goods WHERE agreement_id=?', (aid,))
    conn.close()
    if not a: return redirect(url_for('index'))
    goods_total    = sum((x['quantity'] or 0)*(x['price_usd'] or 0) for x in goods)
    goods_paid     = goods_total*(a['goods_payment_percent'] or 0)/100
    delivery_total = (a['freight_usd'] or 0)+(a['inland_usd'] or 0)
    return render_template('detail.html', a=a, goods=goods,
        goods_total=goods_total, goods_paid=goods_paid,
        goods_remaining=goods_total-goods_paid,
        delivery_total=delivery_total, days_until_eta=days_until(a['eta_sklad'] or a['eta']))

@app.route('/settings/theme', methods=['GET','POST'])
def theme_settings():
    conn = get_db()
    if request.method == 'POST':
        try:
            if PG: execute(conn, "INSERT INTO settings VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", ('theme', json.dumps(request.json)))
            else:  execute(conn, "INSERT OR REPLACE INTO settings VALUES (?,?)", ('theme', json.dumps(request.json)))
            conn.commit()
        finally: conn.close()
        return jsonify({'ok':True})
    row = fetchone(conn, "SELECT value FROM settings WHERE key=?", ('theme',))
    conn.close()
    return jsonify(json.loads(row['value']) if row else {})

@app.route('/settings')
def settings_page():
    opts = {c: get_options(c) for c in ['pol','pod','shipping_line','supplier','container_count','good_name']}
    return render_template('settings.html', opts=opts, vis_cols=get_visible_cols())

@app.route('/dashboard')
def dashboard():
    conn = get_db()
    total    = fetchone(conn, "SELECT COUNT(*) as c FROM agreements WHERE status='active'")['c']
    archived = fetchone(conn, "SELECT COUNT(*) as c FROM agreements WHERE status='archived'")['c']
    overdue  = fetchone(conn, "SELECT COUNT(*) as c FROM agreements WHERE status='active' AND eta < CURRENT_DATE AND eta IS NOT NULL")['c']
    soon     = fetchone(conn, "SELECT COUNT(*) as c FROM agreements WHERE status='active' AND eta >= CURRENT_DATE AND eta <= CURRENT_DATE + INTERVAL '7 days'" if PG else
               "SELECT COUNT(*) as c FROM agreements WHERE status='active' AND eta >= date('now') AND eta <= date('now','+7 days')")['c']
    delivery_total  = fetchone(conn, "SELECT COALESCE(SUM(freight_usd+inland_usd),0) as s FROM agreements WHERE status='active'")['s']
    goods_rows = fetchall(conn, """SELECT a.goods_payment_percent, COALESCE(SUM(g.quantity*g.price_usd),0) as gt
        FROM agreements a LEFT JOIN goods g ON g.agreement_id=a.id
        WHERE a.status='active' GROUP BY a.id, a.goods_payment_percent""")
    goods_total_all = sum(r['gt'] for r in goods_rows)
    goods_unpaid    = sum(r['gt']*(1-(r['goods_payment_percent'] or 0)/100) for r in goods_rows)
    recent    = fetchall(conn, "SELECT * FROM agreements ORDER BY created_at DESC LIMIT 5")
    suppliers = fetchall(conn, "SELECT supplier, COUNT(*) as cnt FROM agreements WHERE status='active' GROUP BY supplier ORDER BY cnt DESC LIMIT 5")
    conn.close()
    return render_template('dashboard.html', total=total, archived=archived, overdue=overdue, soon=soon,
        delivery_total=delivery_total, goods_total_all=goods_total_all, goods_unpaid=goods_unpaid,
        recent=recent, suppliers=suppliers, days_until=days_until)

if __name__ == '__main__':
    init_db()
    print('\n✅ Програма запущена! http://127.0.0.1:8080\n')
    app.run(debug=True, host='0.0.0.0', port=8080)

# For Railway/Gunicorn
init_db()

# ---- EXPORT / IMPORT ----
import csv
import io

@app.route('/export')
def export_data():
    conn = get_db()
    agreements = fetchall(conn, "SELECT * FROM agreements ORDER BY created_at")
    goods = fetchall(conn, "SELECT * FROM goods ORDER BY agreement_id")
    conn.close()

    output = io.StringIO()
    output.write('=== УГОДИ ===\n')
    if agreements:
        writer = csv.DictWriter(output, fieldnames=agreements[0].keys())
        writer.writeheader()
        writer.writerows(agreements)
    output.write('\n=== ТОВАРИ ===\n')
    if goods:
        writer = csv.DictWriter(output, fieldnames=goods[0].keys())
        writer.writeheader()
        writer.writerows(goods)

    content = output.getvalue()
    from flask import Response
    return Response(
        content,
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename=import_tracker_{date.today()}.csv'}
    )

@app.route('/import-data', methods=['GET','POST'])
def import_data():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f:
            return "Файл не вибрано", 400

        content = f.read().decode('utf-8')
        lines = content.splitlines()

        # Split into sections
        agreement_lines = []
        goods_lines = []
        section = None
        for line in lines:
            if line.strip() == '=== УГОДИ ===':
                section = 'agreements'
            elif line.strip() == '=== ТОВАРИ ===':
                section = 'goods'
            elif line.strip() == '':
                continue
            elif section == 'agreements':
                agreement_lines.append(line)
            elif section == 'goods':
                goods_lines.append(line)

        conn = get_db()
        try:
            imported_agreements = 0
            imported_goods = 0
            id_map = {}  # old_id -> new_id

            if len(agreement_lines) > 1:
                reader = csv.DictReader(agreement_lines)
                for row in reader:
                    old_id = row.get('id')
                    fields = (
                        row.get('agreement_number') or None,
                        row.get('container_count'),
                        row.get('delivery_terms'),
                        row.get('etd') or None,
                        row.get('eta') or None,
                        row.get('container_numbers'),
                        row.get('supplier'),
                        row.get('pol'),
                        row.get('pod'),
                        row.get('shipping_line'),
                        float(row.get('freight_usd') or 0),
                        float(row.get('inland_usd') or 0),
                        float(row.get('goods_payment_percent') or 0),
                        int(row.get('docs_received') or 0),
                        row.get('docs_received_date') or None,
                        row.get('docs_notes') or '',
                        row.get('record_number'),
                        row.get('notes'),
                        row.get('status') or 'active',
                        row.get('eta_sklad') or None,
                    )
                    if PG:
                        cur = execute(conn, '''INSERT INTO agreements
                            (agreement_number,container_count,delivery_terms,etd,eta,
                            container_numbers,supplier,pol,pod,shipping_line,freight_usd,
                            inland_usd,goods_payment_percent,docs_received,docs_received_date,
                            docs_notes,record_number,notes,status,eta_sklad)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            RETURNING id''', fields)
                        new_id = cur.fetchone()[0]
                    else:
                        cur = execute(conn, '''INSERT INTO agreements
                            (agreement_number,container_count,delivery_terms,etd,eta,
                            container_numbers,supplier,pol,pod,shipping_line,freight_usd,
                            inland_usd,goods_payment_percent,docs_received,docs_received_date,
                            docs_notes,record_number,notes,status,eta_sklad)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', fields)
                        new_id = cur.lastrowid
                    if old_id:
                        id_map[old_id] = new_id
                    imported_agreements += 1

            if len(goods_lines) > 1:
                reader = csv.DictReader(goods_lines)
                for row in reader:
                    old_agr_id = row.get('agreement_id')
                    new_agr_id = id_map.get(old_agr_id)
                    if not new_agr_id:
                        continue
                    execute(conn, 'INSERT INTO goods (agreement_id,name,quantity,unit,price_usd) VALUES (?,?,?,?,?)',
                        (new_agr_id, row.get('name',''), float(row.get('quantity') or 0),
                         row.get('unit','шт'), float(row.get('price_usd') or 0)))
                    imported_goods += 1

            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            return f"Помилка імпорту: {e}", 400
        finally:
            conn.close()

        return redirect(url_for('import_success', agreements=imported_agreements, goods=imported_goods))

    return render_template('import_export.html')

@app.route('/import-success')
def import_success():
    agreements = request.args.get('agreements', 0)
    goods = request.args.get('goods', 0)
    return render_template('import_export.html', success=True, agreements=agreements, goods=goods)
