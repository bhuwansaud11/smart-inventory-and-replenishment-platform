import csv
import io
import math
import os
import secrets
import sqlite3
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request, session, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

ROLES = ['Administrator', 'Inventory Manager', 'Storekeeper', 'Procurement Officer']

def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY') or secrets.token_hex(32),
                      DATABASE=os.environ.get('DATABASE', str(Path(__file__).with_name('inventory.db'))),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=1024*1024)
    if config: app.config.update(config)

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=15)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
        return g.db

    @app.teardown_appcontext
    def close(_):
        if 'db' in g: g.db.close()

    def guard(*roles):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*a, **kw):
                u = db().execute('SELECT id,name,role FROM users WHERE id=?', (session.get('uid'),)).fetchone()
                if not u: return jsonify(error='Please sign in.'), 401
                g.user = dict(u)
                if roles and u['role'] not in roles: return jsonify(error='Your role cannot perform this action.'), 403
                if request.method != 'GET' and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', 'invalid')):
                    return jsonify(error='Session token is invalid. Sign in again.'), 403
                return fn(*a, **kw)
            return wrapped
        return decorate

    @app.errorhandler(ValueError)
    def invalid(e): return jsonify(error=str(e)), 400

    @app.errorhandler(sqlite3.IntegrityError)
    def conflict(e): return jsonify(error='That SKU or username already exists, or a referenced record is invalid.'), 409

    def body():
        v = request.get_json(silent=True)
        if not isinstance(v, dict): raise ValueError('A JSON object is required.')
        return v

    def number(v, name, minimum=0, integer=False):
        try: n = float(v)
        except (TypeError, ValueError): raise ValueError(f'{name} must be a number.')
        if not math.isfinite(n) or n < minimum or n > 100000000 or (integer and n != int(n)):
            raise ValueError(f'{name} is outside the allowed range.')
        return int(n) if integer else n

    def label(v, name):
        if not isinstance(v, str) or not v.strip() or len(v) > 160: raise ValueError(f'{name} is required (maximum 160 characters).')
        return v.strip()

    def products():
        rows = db().execute("SELECT p.*, (SELECT MAX(created_at) FROM movements m WHERE m.product_id=p.id AND m.kind='issue') last_issue FROM products p ORDER BY name").fetchall()
        result = []
        for r in rows:
            p = dict(r)
            p['reorder_point'] = math.ceil(p['daily_demand'] * p['lead_days'] + p['safety_stock'])
            p['target_stock'] = math.ceil(p['daily_demand'] * (p['lead_days'] + p['review_days']) + p['safety_stock'])
            p['suggested_order'] = max(0, p['target_stock'] - p['quantity'])
            p['status'] = 'Out of stock' if p['quantity'] == 0 else 'Low stock' if p['quantity'] <= p['reorder_point'] else 'Excess stock' if p['quantity'] > max(1, p['target_stock']) * 2 else 'Healthy'
            result.append(p)
        return result

    @app.get('/api/health')
    def health():
        db().execute('SELECT 1')
        return jsonify(status='ok')

    @app.get('/metrics')
    def metrics(): return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    @app.post('/api/login')
    def login():
        d = body()
        u = db().execute('SELECT * FROM users WHERE username=?', (d.get('username'),)).fetchone()
        if not u or not check_password_hash(u['password'], str(d.get('password', ''))): return jsonify(error='Incorrect username or password.'), 401
        session.clear()
        session.update(uid=u['id'], csrf=secrets.token_hex(32))
        session.permanent = True
        return jsonify(user={k:u[k] for k in ('id','name','role')}, csrf=session['csrf'])

    @app.get('/api/me')
    @guard()
    def me(): return jsonify(user=g.user, csrf=session['csrf'])

    @app.post('/api/logout')
    @guard()
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/products')
    @guard()
    def list_products(): return jsonify(products())

    @app.post('/api/products')
    @guard('Administrator', 'Inventory Manager')
    def add_product():
        d = body()
        values = [label(d.get(k), k) for k in ('sku','name','category','warehouse')]
        values += [number(d.get('unit_cost', 0), 'Unit cost')]
        with db():
            cur = db().execute('INSERT INTO products(sku,name,category,warehouse,unit_cost) VALUES(?,?,?,?,?)', values)
        return jsonify(id=cur.lastrowid), 201

    @app.put('/api/products/<int:pid>/rules')
    @guard('Administrator', 'Inventory Manager', 'Procurement Officer')
    def rules(pid):
        d = body()
        keys = ('daily_demand','lead_days','safety_stock','review_days')
        vals = [number(d.get(k), k, integer=k != 'daily_demand') for k in keys]
        with db():
            c = db().execute('UPDATE products SET daily_demand=?,lead_days=?,safety_stock=?,review_days=? WHERE id=?', (*vals,pid))
        if not c.rowcount: return jsonify(error='Product not found.'), 404
        return jsonify(ok=True)

    @app.post('/api/movements')
    @guard('Administrator', 'Inventory Manager', 'Storekeeper')
    def movement():
        d = body()
        kind = d.get('kind')
        if kind not in ('receipt','issue','return','adjustment'): raise ValueError('Choose a valid movement type.')
        qty = number(d.get('quantity'), 'Quantity', minimum=-100000000 if kind == 'adjustment' else 1, integer=True)
        if qty == 0: raise ValueError('Quantity cannot be zero.')
        pid = number(d.get('product_id'), 'Product', minimum=1, integer=True)
        note = label(d.get('note'), 'Reference / reason')
        delta = -qty if kind == 'issue' else qty
        with db():
            db().execute('BEGIN IMMEDIATE')
            p = db().execute('SELECT quantity FROM products WHERE id=?', (pid,)).fetchone()
            if not p: return jsonify(error='Product not found.'), 404
            balance = p['quantity'] + delta
            if balance < 0: raise ValueError('Insufficient stock. This movement would create a negative balance.')
            if balance > 100000000: raise ValueError('Stock balance exceeds the supported range.')
            db().execute('UPDATE products SET quantity=? WHERE id=?', (balance,pid))
            db().execute('INSERT INTO movements(product_id,kind,delta,balance,note,user_id) VALUES(?,?,?,?,?,?)', (pid,kind,delta,balance,note,g.user['id']))
        return jsonify(balance=balance), 201

    def movement_rows():
        start, end = request.args.get('start','0000'), request.args.get('end','9999')
        return [dict(r) for r in db().execute('SELECT m.*,p.name,p.sku,p.warehouse,u.name actor FROM movements m JOIN products p ON p.id=m.product_id JOIN users u ON u.id=m.user_id WHERE substr(m.created_at,1,10)>=? AND substr(m.created_at,1,10)<=? ORDER BY m.id DESC', (start,end))]

    @app.get('/api/movements')
    @guard()
    def list_movements(): return jsonify(movement_rows())

    @app.get('/api/reports.csv')
    @guard()
    def report():
        output = io.StringIO()
        w = csv.writer(output)
        keys = ['created_at','sku','name','warehouse','kind','delta','balance','note','actor']
        w.writerow(keys)
        for r in movement_rows():
            w.writerow([("'"+str(r[k])) if isinstance(r[k],str) and r[k].startswith(('=','+','-','@','\t','\r')) else r[k] for k in keys])
        return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition':'attachment; filename=stock-movements.csv'})

    @app.get('/api/users')
    @guard('Administrator')
    def users(): return jsonify([dict(r) for r in db().execute('SELECT id,username,name,role FROM users')])

    @app.post('/api/users')
    @guard('Administrator')
    def add_user():
        d = body()
        if d.get('role') not in ROLES: raise ValueError('Invalid role.')
        if not isinstance(d.get('password'),str) or len(d['password']) < 12: raise ValueError('Password must have at least 12 characters.')
        with db(): db().execute('INSERT INTO users(username,name,role,password) VALUES(?,?,?,?)', (label(d.get('username'),'Username'),label(d.get('name'),'Name'),d['role'],generate_password_hash(d['password'])))
        return jsonify(ok=True), 201

    @app.get('/')
    @app.get('/<path:path>')
    def frontend(path='index.html'):
        if path.startswith('api/'): return jsonify(error='Endpoint not found'), 404
        return send_from_directory(Path(__file__).parent.parent/'frontend'/'dist', path)

    @app.cli.command('init-db')
    def init_command():
        init_db(app)
        print('Database initialized. Set DEMO_PASSWORD before seeding demo users.')

    return app

def init_db(app):
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,name TEXT NOT NULL,role TEXT NOT NULL,password TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,sku TEXT UNIQUE NOT NULL,name TEXT NOT NULL,category TEXT NOT NULL,warehouse TEXT NOT NULL,unit_cost REAL NOT NULL DEFAULT 0,quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),daily_demand REAL NOT NULL DEFAULT 2,lead_days INTEGER NOT NULL DEFAULT 7,safety_stock INTEGER NOT NULL DEFAULT 10,review_days INTEGER NOT NULL DEFAULT 14);
    CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY,product_id INTEGER NOT NULL REFERENCES products(id),kind TEXT NOT NULL,delta INTEGER NOT NULL,balance INTEGER NOT NULL,note TEXT NOT NULL,user_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%SZ','now')));
    ''')
    password = os.environ.get('DEMO_PASSWORD')
    if password and not conn.execute('SELECT 1 FROM users').fetchone():
        if len(password) < 12: raise ValueError('DEMO_PASSWORD must contain at least 12 characters.')
        for username, role in zip(['admin','manager','storekeeper','procurement'],ROLES):
            conn.execute('INSERT INTO users(username,name,role,password) VALUES(?,?,?,?)',(username,role,role,generate_password_hash(password)))
        for i,(name,cat,qty,cost) in enumerate([('Wireless keyboard','Electronics',18,1499),('USB-C cable · 1m','Electronics',145,249),('A4 copier paper','Office supplies',0,320),('Packing tape · 48mm','Packaging',32,85),('Corrugated boxes · M','Packaging',240,38),('Thermal label roll','Packaging',12,220),('Wireless mouse','Electronics',65,899),('Notebook · A5','Office supplies',85,95)]):
            cur=conn.execute('INSERT INTO products(sku,name,category,warehouse,unit_cost,quantity) VALUES(?,?,?,?,?,?)',(f'SKU-{1001+i}',name,cat,'Main warehouse' if i%3 else 'Retail store',cost,qty))
            if qty: conn.execute("INSERT INTO movements(product_id,kind,delta,balance,note,user_id) VALUES(?,'receipt',?,?,'Opening demo stock',1)",(cur.lastrowid,qty,qty))
    conn.commit()
    conn.close()

app = create_app()
if __name__ == '__main__':
    init_db(app)
    from waitress import serve
    serve(app, host='0.0.0.0', port=int(os.environ.get('PORT',5000)))
