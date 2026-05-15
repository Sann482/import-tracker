from flask import Flask, render_template, request, redirect, url_for, jsonify
import sqlite3
from datetime import datetime, date
import json

app = Flask(__name__)
DB = 'import_tracker.db'

def get_db():
    conn = sqlite3.connect(DB, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
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
            price_usd REAL DEFAULT 0,
            FOREIGN KEY (agreement_id) REFERENCES agreements(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            value TEXT NOT NULL,
            UNIQUE(category, value)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS visible_columns (
            col_key TEXT PRIMARY KEY,
            col_label TEXT,
            visible INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        );
    ''')
    # Migrate old columns safely
    for col, typ in [
        ('goods_payment_percent','REAL DEFAULT 0'),
        ('docs_received_date','DATE'),
        ('docs_notes','TEXT'),
    ]:
        try: conn.execute(f"ALTER TABLE agreements ADD COLUMN {col} {typ}")
        except: pass

    # Default options
    defaults = {
        'pol': ['Tianjin, China','Ningbo, China','Shanghai, China','Guangzhou, China','Ho Chi Minh, Vietnam','Haiphong, Vietnam','Busan, South Korea'],
        'pod': ['Gdansk, Poland','Hamburg, Germany','Rotterdam, Netherlands','Klaipeda, Lithuania','Riga, Latvia'],
        'shipping_line': ['MSC','Maersk','CMA CGM','Evergreen','COSCO','ONE','Hapag-Lloyd'],
        'supplier': [],
        'container_count': ['1*20','1*40','2*20','2*40','3*40','1*40HC','2*40HC'],
        'good_name': ['Стільці','Столи','Дивани','Ліжка','Шафи','Матраци','Крісла','Тумби'],
    }
    for cat, vals in defaults.items():
        for v in vals:
            try: conn.execute("INSERT OR IGNORE INTO options (category,value) VALUES (?,?)", (cat,v))
            except: pass

    # Default visible columns
    cols = [
        ('agreement_number','Угода',1,0),
        ('supplier','Постачальник',1,1),
        ('route','Маршрут',1,2),
        ('shipping_line','Лінія',0,3),
        ('container_count','Контейнери',0,4),
        ('delivery_terms','Умови',0,5),
        ('etd','ETD',1,6),
        ('eta','ETA',1,7),
        ('status','Статус',1,8),
        ('freight_usd','Фрахт $',0,9),
        ('freight','Доставка $',1,10),
        ('payment','Оплата товару',1,11),
        ('docs','Документи',0,12),
        ('record_number','Номер запису',0,13),
    ]
    for c in cols:
        try: conn.execute("INSERT OR IGNORE INTO visible_columns VALUES (?,?,?,?)", c)
        except: pass

    conn.commit()
    conn.close()

def days_until(eta_str):
    if not eta_str: return None
    try:
        return (datetime.strptime(str(eta_str),'%Y-%m-%d').date() - date.today()).days
    except: return None

def get_options(cat):
    conn = get_db()
    rows = conn.execute("SELECT value FROM options WHERE category=? ORDER BY value",(cat,)).fetchall()
    conn.close()
    return [r['value'] for r in rows]

def get_visible_cols():
    conn = get_db()
    rows = conn.execute("SELECT * FROM visible_columns ORDER BY sort_order").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ---- OPTIONS API ----
@app.route('/api/options/<cat>', methods=['GET'])
def api_get_options(cat):
    return jsonify(get_options(cat))

@app.route('/api/options/<cat>', methods=['POST'])
def api_add_option(cat):
    val = request.json.get('value','').strip()
    if val:
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO options (category,value) VALUES (?,?)",(cat,val))
        conn.commit(); conn.close()
    return jsonify(get_options(cat))

@app.route('/api/options/<cat>/<path:val>', methods=['DELETE'])
def api_del_option(cat, val):
    conn = get_db()
    conn.execute("DELETE FROM options WHERE category=? AND value=?",(cat,val))
    conn.commit(); conn.close()
    return jsonify(get_options(cat))

# ---- COLUMNS API ----
@app.route('/api/columns', methods=['GET'])
def api_get_cols():
    return jsonify(get_visible_cols())

@app.route('/api/columns', methods=['POST'])
def api_save_cols():
    conn = get_db()
    for c in request.json:
        conn.execute("UPDATE visible_columns SET visible=? WHERE col_key=?",(c['visible'],c['col_key']))
    conn.commit(); conn.close()
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

    allowed_sorts = {'eta','etd','agreement_number','supplier','shipping_line','created_at'}
    if sort_by not in allowed_sorts: sort_by = 'eta'
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

    agreements = conn.execute(query, params).fetchall()
    result = []
    for a in agreements:
        goods = conn.execute("SELECT * FROM goods WHERE agreement_id=?",(a['id'],)).fetchall()
        goods_total = sum((g['quantity'] or 0)*(g['price_usd'] or 0) for g in goods)
        goods_paid  = goods_total*(a['goods_payment_percent'] or 0)/100
        delivery_total = (a['freight_usd'] or 0)+(a['inland_usd'] or 0)
        result.append({
            'data': dict(a),
            'goods': [dict(g) for g in goods],
            'goods_total': goods_total,
            'goods_paid': goods_paid,
            'goods_remaining': goods_total-goods_paid,
            'delivery_total': delivery_total,
            'days_until_eta': days_until(a['eta'])
        })

    all_suppliers = [r['supplier'] for r in conn.execute("SELECT DISTINCT supplier FROM agreements WHERE status=? AND supplier IS NOT NULL ORDER BY supplier",(status_filter,)).fetchall()]
    all_lines     = [r['shipping_line'] for r in conn.execute("SELECT DISTINCT shipping_line FROM agreements WHERE status=? AND shipping_line IS NOT NULL ORDER BY shipping_line",(status_filter,)).fetchall()]
    all_pol       = [r['pol'] for r in conn.execute("SELECT DISTINCT pol FROM agreements WHERE status=? AND pol IS NOT NULL ORDER BY pol",(status_filter,)).fetchall()]
    all_pod       = [r['pod'] for r in conn.execute("SELECT DISTINCT pod FROM agreements WHERE status=? AND pod IS NOT NULL ORDER BY pod",(status_filter,)).fetchall()]
    conn.close()

    return render_template('index.html',
        agreements=result, status_filter=status_filter, search=search,
        sort_by=sort_by, sort_dir=sort_dir,
        all_suppliers=all_suppliers, all_lines=all_lines, all_pol=all_pol, all_pod=all_pod,
        f_supplier=f_supplier, f_line=f_line, f_pol=f_pol, f_pod=f_pod, f_terms=f_terms,
        vis_cols=get_visible_cols())

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
    )
    if aid:
        conn.execute('''UPDATE agreements SET agreement_number=?,container_count=?,delivery_terms=?,
            etd=?,eta=?,container_numbers=?,supplier=?,pol=?,pod=?,shipping_line=?,
            freight_usd=?,inland_usd=?,goods_payment_percent=?,docs_received=?,docs_received_date=?,
            docs_notes=?,record_number=?,notes=? WHERE id=?''', fields+(aid,))
        return aid
    else:
        cur = conn.execute('''INSERT INTO agreements (agreement_number,container_count,delivery_terms,
            etd,eta,container_numbers,supplier,pol,pod,shipping_line,
            freight_usd,inland_usd,goods_payment_percent,docs_received,docs_received_date,
            docs_notes,record_number,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', fields)
        return cur.lastrowid

def save_goods(conn, aid):
    conn.execute('DELETE FROM goods WHERE agreement_id=?',(aid,))
    names  = request.form.getlist('good_name[]')
    qtys   = request.form.getlist('good_qty[]')
    units  = request.form.getlist('good_unit[]')
    prices = request.form.getlist('good_price[]')
    customs = request.form.getlist('good_name_custom[]')
    for i, name in enumerate(names):
        final_name = (customs[i].strip() if i < len(customs) and customs[i].strip() else name.strip())
        if final_name:
            conn.execute('INSERT INTO goods (agreement_id,name,quantity,unit,price_usd) VALUES (?,?,?,?,?)',
                (aid,final_name,float(qtys[i] or 0),units[i] or 'шт',float(prices[i] or 0)))
    supplier = request.form.get('supplier','').strip()
    if supplier:
        try: conn.execute("INSERT OR IGNORE INTO options (category,value) VALUES ('supplier',?)",(supplier,))
        except: pass
    for name in names:
        if name.strip():
            try: conn.execute("INSERT OR IGNORE INTO options (category,value) VALUES ('good_name',?)",(name.strip(),))
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
            conn.rollback()
            raise e
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
            conn.rollback()
            raise e
        finally:
            conn.close()
        return redirect(url_for('index'))
    conn = get_db()
    agreement = conn.execute('SELECT * FROM agreements WHERE id=?',(aid,)).fetchone()
    goods     = conn.execute('SELECT * FROM goods WHERE agreement_id=?',(aid,)).fetchall()
    result = dict(agreement)
    goods_list = [dict(g) for g in goods]
    conn.close()
    opts = {c: get_options(c) for c in ['pol','pod','shipping_line','supplier','container_count','good_name']}
    return render_template('form.html', agreement=result, goods=goods_list, opts=opts)

@app.route('/agreement/<int:aid>/archive', methods=['POST'])
def archive_agreement(aid):
    conn = get_db()
    conn.execute("UPDATE agreements SET status='archived',archived_at=CURRENT_TIMESTAMP WHERE id=?",(aid,))
    conn.commit(); conn.close()
    return redirect(url_for('index'))

@app.route('/agreement/<int:aid>/restore', methods=['POST'])
def restore_agreement(aid):
    conn = get_db()
    conn.execute("UPDATE agreements SET status='active',archived_at=NULL WHERE id=?",(aid,))
    conn.commit(); conn.close()
    return redirect(url_for('index', status='archived'))

@app.route('/agreement/<int:aid>/delete', methods=['POST'])
def delete_agreement(aid):
    conn = get_db()
    conn.execute("DELETE FROM agreements WHERE id=?",(aid,))
    conn.commit(); conn.close()
    return redirect(url_for('index'))

@app.route('/agreement/<int:aid>')
def view_agreement(aid):
    conn = get_db()
    agreement = conn.execute('SELECT * FROM agreements WHERE id=?',(aid,)).fetchone()
    goods     = conn.execute('SELECT * FROM goods WHERE agreement_id=?',(aid,)).fetchall()
    conn.close()
    if not agreement: return redirect(url_for('index'))
    a = dict(agreement)
    g = [dict(x) for x in goods]
    goods_total    = sum((x['quantity'] or 0)*(x['price_usd'] or 0) for x in g)
    goods_paid     = goods_total*(a['goods_payment_percent'] or 0)/100
    delivery_total = (a['freight_usd'] or 0)+(a['inland_usd'] or 0)
    return render_template('detail.html', a=a, goods=g,
        goods_total=goods_total, goods_paid=goods_paid,
        goods_remaining=goods_total-goods_paid,
        delivery_total=delivery_total,
        days_until_eta=days_until(a['eta']))

# ---- SETTINGS ----
@app.route('/settings/theme', methods=['GET','POST'])
def theme_settings():
    conn = get_db()
    if request.method == 'POST':
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('theme',?)",(json.dumps(request.json),))
        conn.commit(); conn.close()
        return jsonify({'ok':True})
    row = conn.execute("SELECT value FROM settings WHERE key='theme'").fetchone()
    conn.close()
    return jsonify(json.loads(row['value']) if row else {})

@app.route('/settings')
def settings_page():
    opts = {c: get_options(c) for c in ['pol','pod','shipping_line','supplier','container_count','good_name']}
    return render_template('settings.html', opts=opts, vis_cols=get_visible_cols())

# ---- DASHBOARD ----
@app.route('/dashboard')
def dashboard():
    conn = get_db()
    total    = conn.execute("SELECT COUNT(*) as c FROM agreements WHERE status='active'").fetchone()['c']
    archived = conn.execute("SELECT COUNT(*) as c FROM agreements WHERE status='archived'").fetchone()['c']
    overdue  = conn.execute("SELECT COUNT(*) as c FROM agreements WHERE status='active' AND eta < date('now') AND eta IS NOT NULL").fetchone()['c']
    soon     = conn.execute("SELECT COUNT(*) as c FROM agreements WHERE status='active' AND eta >= date('now') AND eta <= date('now','+7 days')").fetchone()['c']
    delivery_total = conn.execute("SELECT COALESCE(SUM(freight_usd+inland_usd),0) as s FROM agreements WHERE status='active'").fetchone()['s']
    goods_rows = conn.execute("""
        SELECT a.goods_payment_percent, COALESCE(SUM(g.quantity*g.price_usd),0) as gt
        FROM agreements a LEFT JOIN goods g ON g.agreement_id=a.id
        WHERE a.status='active' GROUP BY a.id
    """).fetchall()
    goods_total_all = sum(r['gt'] for r in goods_rows)
    goods_unpaid    = sum(r['gt']*(1-(r['goods_payment_percent'] or 0)/100) for r in goods_rows)
    recent    = conn.execute("SELECT * FROM agreements ORDER BY created_at DESC LIMIT 5").fetchall()
    suppliers = conn.execute("SELECT supplier, COUNT(*) as cnt FROM agreements WHERE status='active' GROUP BY supplier ORDER BY cnt DESC LIMIT 5").fetchall()
    conn.close()
    return render_template('dashboard.html',
        total=total, archived=archived, overdue=overdue, soon=soon,
        delivery_total=delivery_total,
        goods_total_all=goods_total_all,
        goods_unpaid=goods_unpaid,
        recent=[dict(r) for r in recent],
        suppliers=[dict(s) for s in suppliers],
        days_until=days_until)

if __name__ == '__main__':
    init_db()
    print('\n✅ Програма запущена! http://127.0.0.1:8080\n')
    app.run(debug=True, host='0.0.0.0', port=8080)
