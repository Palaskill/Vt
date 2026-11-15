#!/usr/bin/env python3
"""VetStock: a small local, offline-first veterinary stock server.

All amounts are stored as integer millimes.  No external services are used.
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import hmac
import io
import ipaddress
import json
import os
import secrets
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.parse
import webbrowser
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('VETOSTOCK_DATA', ROOT / 'data')).resolve()
DB = DATA / 'vetstock.sqlite3'
STATIC = ROOT / 'static'
SESSIONS: dict[str, dict] = {}
SESSION_LOCK = threading.Lock()
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
MAX_BODY = 8 * 1024 * 1024


class AppError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def db():
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','employee')),
          password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          alerts INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS medicines(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, strength TEXT NOT NULL DEFAULT '',
          form TEXT NOT NULL DEFAULT '', barcode TEXT UNIQUE, pack_size INTEGER NOT NULL CHECK(pack_size>0),
          price_pack INTEGER, price_unit INTEGER, threshold INTEGER NOT NULL DEFAULT 0 CHECK(threshold>=0),
          threshold_mode TEXT NOT NULL CHECK(threshold_mode IN ('units','boxes')),
          expiry_days INTEGER NOT NULL DEFAULT 90 CHECK(expiry_days>=0), active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS lots(
          id INTEGER PRIMARY KEY, medicine_id INTEGER NOT NULL REFERENCES medicines(id),
          batch TEXT NOT NULL, expiry TEXT, boxes INTEGER NOT NULL CHECK(boxes>=0),
          loose INTEGER NOT NULL CHECK(loose>=0), received_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS suppliers(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
          email TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
          status TEXT NOT NULL DEFAULT 'open', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_lines(
          id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
          medicine_id INTEGER NOT NULL REFERENCES medicines(id), boxes INTEGER NOT NULL CHECK(boxes>0),
          received INTEGER NOT NULL DEFAULT 0 CHECK(received>=0));
        CREATE TABLE IF NOT EXISTS receipts(
          id INTEGER PRIMARY KEY, medicine_id INTEGER NOT NULL REFERENCES medicines(id),
          lot_id INTEGER NOT NULL REFERENCES lots(id), order_line_id INTEGER REFERENCES order_lines(id),
          boxes INTEGER NOT NULL, loose INTEGER NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS supplier_invoices(
          id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
          invoice_no TEXT NOT NULL, total INTEGER NOT NULL CHECK(total>=0), due_date TEXT,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS supplier_payments(
          id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES supplier_invoices(id),
          amount INTEGER NOT NULL CHECK(amount>0), method TEXT NOT NULL,
          user_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS supplier_invoice_files(
          invoice_id INTEGER PRIMARY KEY REFERENCES supplier_invoices(id), filename TEXT NOT NULL,
          content BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS sales(
          id INTEGER PRIMARY KEY, client_name TEXT NOT NULL DEFAULT '', subtotal INTEGER NOT NULL,
          discount INTEGER NOT NULL, total INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active',
          user_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, void_reason TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS sale_lines(
          id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id),
          medicine_id INTEGER NOT NULL REFERENCES medicines(id), medicine_name TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('box','unit')), qty INTEGER NOT NULL CHECK(qty>0),
          unit_price INTEGER NOT NULL, total INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sale_allocations(
          id INTEGER PRIMARY KEY, line_id INTEGER NOT NULL REFERENCES sale_lines(id),
          lot_id INTEGER NOT NULL REFERENCES lots(id), kind TEXT NOT NULL, qty INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sale_payments(
          id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id),
          amount INTEGER NOT NULL CHECK(amount>0), method TEXT NOT NULL,
          user_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
          details TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_lots_med ON lots(medicine_id, expiry);
        CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(created_at);
        ''')
        defaults = {'clinic_name': 'VetStock', 'clinic_address': '', 'clinic_phone': '',
                    'clinic_tax_id': '', 'backup_dir': '', 'last_backup': ''}
        for key, val in defaults.items():
            c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, val))


def now():
    return dt.datetime.now().replace(microsecond=0).isoformat(timespec='seconds')


def today():
    return dt.date.today().isoformat()


def textval(value, maxlen=160, required=False):
    s = str(value or '').strip()
    if len(s) > maxlen or (required and not s):
        raise AppError('INVALID_FIELD')
    return s


def intval(value, minimum=0, maximum=1000000):
    try:
        s = str(value).strip()
        if not s or any(ch not in '0123456789' for ch in s):
            raise ValueError
        n = int(s)
        if not minimum <= n <= maximum:
            raise ValueError
        return n
    except (TypeError, ValueError):
        raise AppError('INVALID_NUMBER')


def money(value, allow_blank=False):
    if value is None or str(value).strip() == '':
        if allow_blank:
            return None
        raise AppError('INVALID_MONEY')
    try:
        v = Decimal(str(value).strip().replace(',', '.'))
        if not v.is_finite() or v < 0 or v > Decimal('100000000') or v.as_tuple().exponent < -3:
            raise InvalidOperation
        return int((v * 1000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (ValueError, InvalidOperation):
        raise AppError('INVALID_MONEY')


def dateval(value, allow_blank=True):
    s = str(value or '').strip()
    if not s and allow_blank:
        return None
    try:
        if len(s) != 10 or dt.date.fromisoformat(s).isoformat() != s:
            raise ValueError
        return s
    except ValueError:
        raise AppError('INVALID_DATE')


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    raw = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 310_000)
    return f'pbkdf2_sha256$310000${salt.hex()}${raw.hex()}'


def check_password(password, stored):
    try:
        alg, rounds, salt, digest = stored.split('$')
        if alg != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, digest)
    except Exception:
        return False


def passwordval(value):
    password = str(value or '')
    if not 10 <= len(password) <= 256:
        raise AppError('PASSWORD_LENGTH')
    return password


def user_public(row):
    return {k: row[k] for k in ('id', 'username', 'display_name', 'role', 'active', 'alerts')}


def audit(c, user_id, action, details=''):
    c.execute('INSERT INTO audit(user_id, action, details, created_at) VALUES(?,?,?,?)',
              (user_id, action, textval(details, 1000), now()))


def settings(c):
    return {r['key']: r['value'] for r in c.execute('SELECT key,value FROM settings')}


def medicine_rows(c):
    rows = c.execute('''SELECT m.*,
      COALESCE(SUM(CASE WHEN l.expiry IS NULL OR l.expiry>=? THEN l.boxes ELSE 0 END),0) AS boxes,
      COALESCE(SUM(CASE WHEN l.expiry IS NULL OR l.expiry>=? THEN l.loose ELSE 0 END),0) AS loose,
      COALESCE(SUM(CASE WHEN l.expiry<? THEN l.boxes*m.pack_size+l.loose ELSE 0 END),0) AS expired_units
      FROM medicines m LEFT JOIN lots l ON l.medicine_id=m.id
      WHERE m.active=1 GROUP BY m.id ORDER BY m.name COLLATE NOCASE''', (today(), today(), today())).fetchall()
    result = []
    for r in rows:
        v = dict(r)
        v['total_units'] = v['boxes'] * v['pack_size'] + v['loose']
        measured = v['boxes'] if v['threshold_mode'] == 'boxes' else v['total_units']
        v['low'] = measured <= v['threshold']
        result.append(v)
    return result


def lots_for(c, medicine_id=None):
    sql = '''SELECT l.*, m.name, m.pack_size, m.expiry_days FROM lots l
             JOIN medicines m ON m.id=l.medicine_id WHERE (l.boxes>0 OR l.loose>0)'''
    params = []
    if medicine_id:
        sql += ' AND l.medicine_id=?'
        params.append(medicine_id)
    sql += ' ORDER BY CASE WHEN l.expiry IS NULL THEN 1 ELSE 0 END, l.expiry, l.id'
    return [dict(r) for r in c.execute(sql, params)]


def expiring_lots(c):
    result = []
    current = dt.date.today()
    for lot in lots_for(c):
        if not lot['expiry']:
            lot['days_left'] = None
            continue
        days = (dt.date.fromisoformat(lot['expiry']) - current).days
        if days <= lot['expiry_days']:
            lot['days_left'] = days
            result.append(lot)
    return result


def supplier_rows(c):
    return [dict(r) for r in c.execute('SELECT * FROM suppliers ORDER BY name COLLATE NOCASE')]


def invoice_rows(c):
    return [dict(r) for r in c.execute('''SELECT i.*,s.name AS supplier,
      EXISTS(SELECT 1 FROM supplier_invoice_files f WHERE f.invoice_id=i.id) AS has_file,
      COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.invoice_id=i.id),0) AS paid
      FROM supplier_invoices i JOIN suppliers s ON s.id=i.supplier_id ORDER BY i.id DESC''')]


def sale_rows(c):
    return [dict(r) for r in c.execute('''SELECT s.*,u.display_name AS seller,
      COALESCE((SELECT SUM(p.amount) FROM sale_payments p WHERE p.sale_id=s.id),0) AS paid
      FROM sales s JOIN users u ON u.id=s.user_id ORDER BY s.id DESC''')]


def order_rows(c):
    result = []
    for o in c.execute('''SELECT o.*,s.name AS supplier FROM orders o JOIN suppliers s ON s.id=o.supplier_id
                          ORDER BY o.id DESC''').fetchall():
        d = dict(o)
        d['lines'] = [dict(x) for x in c.execute('''SELECT l.*,m.name AS medicine FROM order_lines l
                         JOIN medicines m ON m.id=l.medicine_id WHERE l.order_id=? ORDER BY l.id''', (o['id'],))]
        result.append(d)
    return result


def dashboard(c, user):
    all_meds = medicine_rows(c)
    can_alert = bool(user['alerts'])
    result = {
      'medicine_count': len(all_meds),
      'low': [m for m in all_meds if m['low']] if can_alert else [],
      'expiring': expiring_lots(c) if can_alert else [],
      'alerts_enabled': can_alert,
      'open_orders': [o for o in order_rows(c) if o['status'] == 'open'],
    }
    if user['role'] == 'admin':
        cfg = settings(c)
        last = cfg.get('last_backup', '')
        result['backup_stale'] = not last or last[:10] < (dt.date.today()-dt.timedelta(days=1)).isoformat()
        result['backup_configured'] = bool(cfg.get('backup_dir'))
        result['today_total'] = c.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE status='active' AND substr(created_at,1,10)=?", (today(),)).fetchone()[0]
        result['customer_due'] = c.execute('''SELECT COALESCE(SUM(s.total-COALESCE((SELECT SUM(p.amount)
          FROM sale_payments p WHERE p.sale_id=s.id),0)),0) FROM sales s WHERE s.status='active' ''').fetchone()[0]
        result['supplier_due'] = c.execute('''SELECT COALESCE(SUM(i.total-COALESCE((SELECT SUM(p.amount)
          FROM supplier_payments p WHERE p.invoice_id=i.id),0)),0) FROM supplier_invoices i''').fetchone()[0]
    return result


def write_medicine(c, p, user_id):
    med_id = intval(p.get('id'), 1) if p.get('id') else None
    name = textval(p.get('name'), 160, True)
    barcode = textval(p.get('barcode'), 100) or None
    pack_size = intval(p.get('pack_size', 1), 1, 100000)
    mode = p.get('threshold_mode', 'units')
    if mode not in ('units', 'boxes'):
        raise AppError('INVALID_FIELD')
    values = (name, textval(p.get('strength'), 80), textval(p.get('form'), 80), barcode,
              pack_size, money(p.get('price_pack'), True), money(p.get('price_unit'), True),
              intval(p.get('threshold', 0)), mode, intval(p.get('expiry_days', 90), 0, 3650))
    if med_id:
        old = c.execute('SELECT * FROM medicines WHERE id=? AND active=1', (med_id,)).fetchone()
        if not old:
            raise AppError('NOT_FOUND', 404)
        if old['pack_size'] != pack_size and c.execute('SELECT 1 FROM lots WHERE medicine_id=? LIMIT 1', (med_id,)).fetchone():
            raise AppError('PACK_SIZE_LOCKED')
        c.execute('''UPDATE medicines SET name=?,strength=?,form=?,barcode=?,pack_size=?,price_pack=?,price_unit=?,
                    threshold=?,threshold_mode=?,expiry_days=? WHERE id=?''', values + (med_id,))
        audit(c, user_id, 'medicine_updated', f'{med_id}: {name}')
    else:
        cur = c.execute('''INSERT INTO medicines(name,strength,form,barcode,pack_size,price_pack,price_unit,
                         threshold,threshold_mode,expiry_days) VALUES(?,?,?,?,?,?,?,?,?,?)''', values)
        med_id = cur.lastrowid
        audit(c, user_id, 'medicine_created', f'{med_id}: {name}')
    return med_id


def do_receipt(c, p, user_id):
    med_id = intval(p.get('medicine_id'), 1)
    if not c.execute('SELECT id FROM medicines WHERE id=? AND active=1', (med_id,)).fetchone():
        raise AppError('NOT_FOUND', 404)
    boxes = intval(p.get('boxes', 0))
    loose = intval(p.get('loose', 0))
    if boxes == 0 and loose == 0:
        raise AppError('NO_QUANTITY')
    batch = textval(p.get('batch'), 100, True)
    # A missing expiry cannot be treated as a verified non-expired lot.
    expiry = dateval(p.get('expiry'), allow_blank=False)
    line_id = intval(p.get('order_line_id'), 1) if p.get('order_line_id') else None
    if line_id:
        line = c.execute('''SELECT l.*,o.status FROM order_lines l JOIN orders o ON o.id=l.order_id
                            WHERE l.id=?''', (line_id,)).fetchone()
        if not line or line['status'] != 'open' or line['medicine_id'] != med_id:
            raise AppError('ORDER_LINE_INVALID')
        if boxes > line['boxes'] - line['received'] or loose:
            raise AppError('ORDER_QUANTITY_INVALID')
    cur = c.execute('INSERT INTO lots(medicine_id,batch,expiry,boxes,loose,received_at) VALUES(?,?,?,?,?,?)',
                    (med_id, batch, expiry, boxes, loose, now()))
    lot_id = cur.lastrowid
    c.execute('''INSERT INTO receipts(medicine_id,lot_id,order_line_id,boxes,loose,user_id,created_at)
                 VALUES(?,?,?,?,?,?,?)''', (med_id, lot_id, line_id, boxes, loose, user_id, now()))
    if line_id:
        c.execute('UPDATE order_lines SET received=received+? WHERE id=?', (boxes, line_id))
        order_id = line['order_id']
        remaining = c.execute('SELECT COUNT(*) FROM order_lines WHERE order_id=? AND received<boxes', (order_id,)).fetchone()[0]
        if remaining == 0:
            c.execute("UPDATE orders SET status='received' WHERE id=?", (order_id,))
    audit(c, user_id, 'receipt', f'medicine={med_id} lot={lot_id} boxes={boxes} loose={loose}')
    return lot_id


def allocate_sale(c, line_id, med, kind, quantity, lot_id=None):
    # Each allocation and the sale are inside one BEGIN IMMEDIATE transaction.
    sql = '''SELECT * FROM lots WHERE medicine_id=? AND (expiry IS NULL OR expiry>=?)
             AND (boxes>0 OR loose>0)'''
    params = [med['id'], today()]
    if lot_id:
        sql += ' AND id=?'
        params.append(lot_id)
    sql += ' ORDER BY CASE WHEN expiry IS NULL THEN 1 ELSE 0 END, expiry, id'
    lots = c.execute(sql, params).fetchall()
    available = sum(l['boxes'] if kind == 'box' else l['boxes'] * med['pack_size'] + l['loose'] for l in lots)
    if available < quantity:
        raise AppError('NOT_ENOUGH_STOCK')
    remaining = quantity
    for lot in lots:
        if remaining <= 0:
            break
        boxes, loose = lot['boxes'], lot['loose']
        if kind == 'box':
            take = min(remaining, boxes)
            boxes -= take
        else:
            take = min(remaining, boxes * med['pack_size'] + loose)
            missing = max(0, take - loose)
            opened = (missing + med['pack_size'] - 1) // med['pack_size']
            boxes -= opened
            loose += opened * med['pack_size'] - take
        if take:
            c.execute('UPDATE lots SET boxes=?,loose=? WHERE id=?', (boxes, loose, lot['id']))
            c.execute('INSERT INTO sale_allocations(line_id,lot_id,kind,qty) VALUES(?,?,?,?)',
                      (line_id, lot['id'], kind, take))
            remaining -= take


def record_sale(c, p, user_id):
    lines = p.get('lines')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise AppError('NO_SALE_LINES')
    prepared = []
    subtotal = 0
    for item in lines:
        if not isinstance(item, dict):
            raise AppError('INVALID_FIELD')
        med_id = intval(item.get('medicine_id'), 1)
        med = c.execute('SELECT * FROM medicines WHERE id=? AND active=1', (med_id,)).fetchone()
        if not med:
            raise AppError('NOT_FOUND', 404)
        kind = item.get('kind')
        if kind not in ('box', 'unit'):
            raise AppError('INVALID_FIELD')
        qty = intval(item.get('qty'), 1)
        price = med['price_pack'] if kind == 'box' else med['price_unit']
        if price is None:
            raise AppError('PRICE_MISSING')
        if 'expected_price' not in item or intval(item.get('expected_price'), 0, 100000000000) != price:
            raise AppError('PRICE_CHANGED')
        lot_id = intval(item.get('lot_id'), 1) if item.get('lot_id') else None
        line_total = price * qty
        subtotal += line_total
        prepared.append((med, kind, qty, price, line_total, lot_id))
    discount = money(p.get('discount', '0'))
    if discount > subtotal:
        raise AppError('DISCOUNT_TOO_HIGH')
    total = subtotal - discount
    payment = money(p.get('payment', '0'))
    if payment > total:
        raise AppError('PAYMENT_TOO_HIGH')
    cur = c.execute('''INSERT INTO sales(client_name,subtotal,discount,total,user_id,created_at)
                       VALUES(?,?,?,?,?,?)''', (textval(p.get('client_name'), 160), subtotal, discount, total, user_id, now()))
    sale_id = cur.lastrowid
    for med, kind, qty, price, line_total, lot_id in prepared:
        cur = c.execute('''INSERT INTO sale_lines(sale_id,medicine_id,medicine_name,kind,qty,unit_price,total)
                           VALUES(?,?,?,?,?,?,?)''', (sale_id, med['id'], med['name'], kind, qty, price, line_total))
        allocate_sale(c, cur.lastrowid, med, kind, qty, lot_id)
    if payment:
        method = payment_method(p.get('method'))
        c.execute('INSERT INTO sale_payments(sale_id,amount,method,user_id,created_at) VALUES(?,?,?,?,?)',
                  (sale_id, payment, method, user_id, now()))
    audit(c, user_id, 'sale_created', f'sale={sale_id} total_millimes={total}')
    return sale_id


def payment_method(value):
    if value not in ('cash', 'card', 'transfer', 'cheque', 'other'):
        raise AppError('INVALID_PAYMENT_METHOD')
    return value


def void_sale(c, sale_id, reason, user_id):
    sale = c.execute('SELECT * FROM sales WHERE id=?', (sale_id,)).fetchone()
    if not sale or sale['status'] != 'active':
        raise AppError('SALE_NOT_ACTIVE')
    for a in c.execute('''SELECT a.* FROM sale_allocations a JOIN sale_lines l ON l.id=a.line_id
                          WHERE l.sale_id=?''', (sale_id,)).fetchall():
        # Opened boxes stay open after cancellation; never invent sealed packaging.
        col = 'boxes' if a['kind'] == 'box' else 'loose'
        c.execute(f'UPDATE lots SET {col}={col}+? WHERE id=?', (a['qty'], a['lot_id']))
    c.execute("UPDATE sales SET status='void',void_reason=? WHERE id=?", (reason, sale_id))
    audit(c, user_id, 'sale_void', f'sale={sale_id} reason={reason}')


def create_backup(c, folder):
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise AppError('BACKUP_FOLDER_MISSING')
    filename = folder / ('VetStock-' + dt.date.today().isoformat() + '.sqlite3')
    tmp = folder / ('.vetstock-' + secrets.token_hex(6) + '.tmp')
    try:
        out = sqlite3.connect(tmp)
        try:
            c.backup(out)
        finally:
            out.close()
        os.replace(tmp, filename)
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('last_backup',?)", (now(),))
        c.commit()
    finally:
        if tmp.exists():
            tmp.unlink()
    return str(filename)


def backup_loop():
    while True:
        time.sleep(300)
        try:
            with db() as c:
                cfg = settings(c)
                if cfg['backup_dir'] and cfg['last_backup'][:10] != today():
                    create_backup(c, cfg['backup_dir'])
        except Exception as exc:
            print('Automatic backup failed:', exc, file=sys.stderr)


def normalise_header(value):
    x = unicodedata.normalize('NFKD', str(value or '').strip().lower())
    return ''.join(ch for ch in x if ch.isalnum() and not unicodedata.combining(ch))


IMPORT_ALIASES = {
    'name': {'nom', 'nommedicament', 'medicament', 'produit', 'name', 'product'},
    'barcode': {'codebarres', 'codebarre', 'barcode', 'ean', 'gtin'},
    'strength': {'dosage', 'concentration', 'strength'},
    'form': {'forme', 'presentation', 'form'},
    'pack_size': {'unitesparboite', 'nbparboite', 'quantiteparboite', 'packsize'},
    'price_pack': {'prixboite', 'prixdevente', 'pricepack'},
    'price_unit': {'prixunite', 'prixcomprime', 'priceunit'},
    'threshold': {'seuil', 'seuilminimal', 'stockminimum', 'threshold'},
    'threshold_mode': {'modeseuil', 'modesuivi', 'thresholdmode'},
    'expiry_days': {'joursalerte', 'alertedays', 'jourspreemption'},
    'batch': {'lot', 'numerolot', 'batch'},
    'expiry': {'peremption', 'datedeperemption', 'expiry', 'expiration'},
    'boxes': {'boites', 'boitesfermees', 'boxes'},
    'loose': {'unitesouvertes', 'uniteslibres', 'loose'},
}


def read_import(data, filename):
    if len(data) > 5 * 1024 * 1024:
        raise AppError('IMPORT_TOO_LARGE')
    if filename.lower().endswith('.xlsx'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                entries = z.infolist()
                if len(entries) > 2000 or sum(e.file_size for e in entries) > 25 * 1024 * 1024:
                    raise AppError('IMPORT_TOO_LARGE')
                ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                shared = []
                if 'xl/sharedStrings.xml' in z.namelist():
                    tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
                    shared = [''.join(t.text or '' for t in s.findall('.//x:t', ns)) for s in tree.findall('x:si', ns)]
                # The first workbook sheet may not physically be named sheet1.xml.
                workbook = ET.fromstring(z.read('xl/workbook.xml'))
                rel_id = workbook.find('x:sheets/x:sheet', ns).attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
                rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
                relation = next(x for x in rels if x.attrib.get('Id') == rel_id)
                target = relation.attrib['Target'].lstrip('/')
                path = target if target.startswith('xl/') else 'xl/' + target
                if '..' in Path(path).parts or path not in z.namelist():
                    raise ValueError
                root = ET.fromstring(z.read(path))
                rows = []
                for row in root.findall('.//x:sheetData/x:row', ns):
                    values = {}
                    for cell in row.findall('x:c', ns):
                        letters = ''.join(ch for ch in cell.attrib.get('r', '') if ch.isalpha())
                        col = 0
                        for ch in letters.upper():
                            col = col * 26 + ord(ch) - 64
                        if not 1 <= col <= 100:
                            continue
                        typ = cell.attrib.get('t')
                        node = cell.find('x:v', ns)
                        value = node.text if node is not None and node.text is not None else ''
                        if typ == 's':
                            value = shared[int(value)]
                        elif typ == 'inlineStr':
                            value = ''.join(t.text or '' for t in cell.findall('.//x:t', ns))
                        values[col - 1] = value
                    if values:
                        rows.append([values.get(i, '') for i in range(max(values) + 1)])
                    if len(rows) > 10002:
                        raise AppError('IMPORT_TOO_LARGE')
        except AppError:
            raise
        except Exception:
            raise AppError('INVALID_IMPORT')
    elif filename.lower().endswith('.csv'):
        try:
            try:
                decoded = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                decoded = data.decode('cp1252')
            try:
                dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.reader(io.StringIO(decoded), dialect))
            if len(rows) > 10002:
                raise AppError('IMPORT_TOO_LARGE')
        except AppError:
            raise
        except Exception:
            raise AppError('INVALID_IMPORT')
    else:
        raise AppError('IMPORT_EXTENSION')
    rows = [r for r in rows if any(str(x).strip() for x in r)]
    if len(rows) < 2:
        raise AppError('IMPORT_EMPTY')
    return rows


def import_mapping(headers):
    mapping = {}
    for i, h in enumerate(headers):
        norm = normalise_header(h)
        for field, aliases in IMPORT_ALIASES.items():
            if norm in aliases and field not in mapping:
                mapping[field] = i
    return mapping


def import_preview(p):
    try:
        data = base64.b64decode(p.get('data', ''), validate=True)
    except Exception:
        raise AppError('INVALID_IMPORT')
    filename = textval(p.get('filename'), 200, True)
    rows = read_import(data, filename)
    return {'headers': rows[0], 'rows': rows[1:6], 'count': len(rows) - 1,
            'mapping': import_mapping(rows[0])}


def import_commit(c, p, user_id):
    try:
        data = base64.b64decode(p.get('data', ''), validate=True)
    except Exception:
        raise AppError('INVALID_IMPORT')
    rows = read_import(data, textval(p.get('filename'), 200, True))
    mapping = p.get('mapping') or import_mapping(rows[0])
    if not isinstance(mapping, dict) or 'name' not in mapping:
        raise AppError('IMPORT_NAME_REQUIRED')
    for field, idx in mapping.items():
        if field not in IMPORT_ALIASES or not isinstance(idx, int) or idx < 0 or idx >= len(rows[0]):
            raise AppError('INVALID_IMPORT')
    created = 0
    for row in rows[1:]:
        def get(k, default=''):
            idx = mapping.get(k)
            return str(row[idx]).strip() if idx is not None and idx < len(row) and str(row[idx]).strip() else default
        if not get('name'):
            continue
        mode = normalise_header(get('threshold_mode'))
        mode = 'boxes' if mode in ('boites', 'boite', 'boxes', 'box') else 'units'
        med = {'name': get('name'), 'strength': get('strength'), 'form': get('form'),
               'barcode': get('barcode'), 'pack_size': get('pack_size', '1'),
               'price_pack': get('price_pack'), 'price_unit': get('price_unit'),
               'threshold': get('threshold', '0'), 'threshold_mode': mode,
               'expiry_days': get('expiry_days', '90')}
        med_id = write_medicine(c, med, user_id)
        boxes = intval(get('boxes', '0'))
        loose = intval(get('loose', '0'))
        if boxes or loose:
            batch = get('batch')
            if not batch:
                raise AppError('IMPORT_BATCH_REQUIRED')
            expiry = get('expiry')
            # Excel stores dates as serial numbers in XLSX cells. Convert only in the date column.
            if expiry.isdigit() and 1 <= int(expiry) <= 2958465:
                expiry = (dt.date(1899, 12, 30) + dt.timedelta(days=int(expiry))).isoformat()
            do_receipt(c, {'medicine_id': med_id, 'boxes': boxes, 'loose': loose,
                           'batch': batch, 'expiry': expiry}, user_id)
        created += 1
    if not created:
        raise AppError('IMPORT_EMPTY')
    audit(c, user_id, 'import', f'{created} medicines')
    return created


class Handler(BaseHTTPRequestHandler):
    server_version = 'VetStock/1.0'
    sys_version = ''

    def log_message(self, fmt, *args):
        # Avoid retaining query strings, names and other sensitive data in logs.
        print(f'{self.client_address[0]} - {fmt % args}', file=sys.stderr)

    def security_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")

    def send_bytes(self, status, body, content_type='application/json; charset=utf-8', headers=None):
        self.send_response(status)
        self.security_headers()
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200, headers=None):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode('utf-8'), headers=headers)

    def payload(self):
        content_type = self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            raise AppError('CONTENT_TYPE', 415)
        origin = self.headers.get('Origin')
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            expected_scheme = 'https' if isinstance(self.connection, ssl.SSLSocket) else 'http'
            if parsed.scheme != expected_scheme or parsed.netloc.lower() != self.headers.get('Host', '').lower():
                raise AppError('CROSS_ORIGIN', 403)
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if not 0 < n <= MAX_BODY:
                raise ValueError
            val = json.loads(self.rfile.read(n))
            if not isinstance(val, dict):
                raise ValueError
            return val
        except (ValueError, json.JSONDecodeError):
            raise AppError('INVALID_BODY')

    def session(self, c):
        try:
            jar = cookies.SimpleCookie()
            jar.load(self.headers.get('Cookie', ''))
            sid = jar['vetstock_sid'].value if 'vetstock_sid' in jar else ''
        except cookies.CookieError:
            sid = ''
        with SESSION_LOCK:
            data = SESSIONS.get(sid)
            if data and data['expires'] < time.time():
                del SESSIONS[sid]
                data = None
            if data:
                data['expires'] = time.time() + 8 * 3600
        if not data:
            return None
        return c.execute('SELECT * FROM users WHERE id=? AND active=1', (data['user_id'],)).fetchone()

    def require(self, c, admin=False):
        u = self.session(c)
        if not u:
            raise AppError('LOGIN_REQUIRED', 401)
        if admin and u['role'] != 'admin':
            raise AppError('ADMIN_REQUIRED', 403)
        return u

    def set_session(self, user_id):
        sid = secrets.token_urlsafe(32)
        with SESSION_LOCK:
            SESSIONS[sid] = {'user_id': user_id, 'expires': time.time() + 8 * 3600}
        value = f'vetstock_sid={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'
        if isinstance(self.connection, ssl.SSLSocket):
            value += '; Secure'
        return value

    def do_GET(self):
        try:
            if not isinstance(self.connection, ssl.SSLSocket) and not ipaddress.ip_address(self.client_address[0]).is_loopback:
                body = ('<!doctype html><html lang="fr"><meta charset="utf-8"><title>VetStock — HTTPS</title>'
                        '<body style="font-family:Arial;padding:2rem;line-height:1.6"><h1>Connexion sécurisée requise</h1>'
                        '<p>Sur l’ordinateur du cabinet, lancez Configurer_HTTPS.bat puis consultez LISEZ_MOI.txt.</p>'
                        '<p>أعِد إعداد الاتصال الآمن على حاسوب العيادة.</p></body></html>').encode('utf-8')
                return self.send_bytes(403, body, 'text/html; charset=utf-8')
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path.startswith('/api/'):
                return self.get_api(path, query)
            allowed = {'/': ('index.html', 'text/html; charset=utf-8'),
                       '/index.html': ('index.html', 'text/html; charset=utf-8'),
                       '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                       '/barcode.js': ('barcode.js', 'text/javascript; charset=utf-8'),
                       '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
                       '/sw.js': ('sw.js', 'text/javascript; charset=utf-8'),
                       '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if path not in allowed:
                raise AppError('NOT_FOUND', 404)
            file, mime = allowed[path]
            self.send_bytes(200, (STATIC / file).read_bytes(), mime)
        except AppError as exc:
            self.send_json({'error': exc.code}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self.send_json({'error': 'SERVER_ERROR'}, 500)

    def get_api(self, path, query):
        with db() as c:
            if path == '/api/state':
                initialised = bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())
                user = self.session(c)
                return self.send_json({'setup': not initialised, 'user': user_public(user) if user else None,
                                       'secure': isinstance(self.connection, ssl.SSLSocket)})
            u = self.require(c)
            if path == '/api/dashboard':
                return self.send_json(dashboard(c, u))
            if path == '/api/medicines':
                return self.send_json(medicine_rows(c))
            if path == '/api/lots':
                med_id = intval(query['medicine_id'][0], 1) if query.get('medicine_id') else None
                return self.send_json(lots_for(c, med_id))
            if path == '/api/suppliers':
                return self.send_json(supplier_rows(c))
            if path == '/api/orders':
                return self.send_json(order_rows(c))
            if path == '/api/sales':
                rows = sale_rows(c)
                if u['role'] != 'admin':
                    # Workers can handle their own sales and any unpaid active sale, not a financial report.
                    rows = [s for s in rows if s['user_id'] == u['id'] or (s['status'] == 'active' and s['paid'] < s['total'])]
                return self.send_json(rows)
            if path == '/api/sale':
                sale_id = intval(query.get('id', [None])[0], 1)
                r = c.execute('''SELECT s.*,u.display_name AS seller,
                    COALESCE((SELECT SUM(amount) FROM sale_payments WHERE sale_id=s.id),0) AS paid
                    FROM sales s JOIN users u ON u.id=s.user_id WHERE s.id=?''', (sale_id,)).fetchone()
                if not r:
                    raise AppError('NOT_FOUND', 404)
                if u['role'] != 'admin' and r['user_id'] != u['id'] and not (r['status'] == 'active' and r['paid'] < r['total']):
                    raise AppError('FORBIDDEN', 403)
                result = dict(r)
                result['lines'] = [dict(x) for x in c.execute('SELECT * FROM sale_lines WHERE sale_id=?', (sale_id,))]
                result['payments'] = [dict(x) for x in c.execute('SELECT * FROM sale_payments WHERE sale_id=?', (sale_id,))]
                result['clinic'] = {k: v for k, v in settings(c).items() if k.startswith('clinic_')}
                return self.send_json(result)
            if path == '/api/settings':
                cfg = settings(c)
                if u['role'] != 'admin':
                    cfg = {k: v for k, v in cfg.items() if k.startswith('clinic_')}
                return self.send_json(cfg)
            if path == '/api/users':
                self.require(c, True)
                return self.send_json([user_public(r) for r in c.execute('SELECT * FROM users ORDER BY id')])
            if path == '/api/supplier-invoices':
                self.require(c, True)
                return self.send_json(invoice_rows(c))
            if path == '/api/supplier-invoice-file':
                self.require(c, True)
                invoice_id = intval(query.get('id', [None])[0], 1)
                attachment = c.execute('SELECT filename,content FROM supplier_invoice_files WHERE invoice_id=?', (invoice_id,)).fetchone()
                if not attachment:
                    raise AppError('NOT_FOUND', 404)
                # Use an attachment with a safe ASCII name, never render arbitrary uploads inline.
                ext = Path(attachment['filename']).suffix.lower()
                return self.send_bytes(200, attachment['content'], 'application/octet-stream',
                     {'Content-Disposition': f'attachment; filename="facture-fournisseur-{invoice_id}{ext}"'})
            if path == '/api/audit':
                self.require(c, True)
                return self.send_json([dict(r) for r in c.execute('''SELECT a.*,u.display_name AS actor FROM audit a
                    LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 300''')])
            if path == '/api/template.csv':
                self.require(c, True)
                data = ('nom;dosage;forme;code_barres;unites_par_boite;prix_boite;prix_unite;seuil;mode_seuil;jours_alerte;lot;peremption;boites;unites_ouvertes\r\n').encode('utf-8-sig')
                return self.send_bytes(200, data, 'text/csv; charset=utf-8', {'Content-Disposition': 'attachment; filename="modele_import_vetstock.csv"'})
            if path == '/api/backup-file':
                self.require(c, True)
                fd, fn = tempfile.mkstemp(prefix='vetstock-', suffix='.sqlite3', dir=DATA)
                os.close(fd)
                try:
                    out = sqlite3.connect(fn)
                    try:
                        c.backup(out)
                    finally:
                        out.close()
                    content = Path(fn).read_bytes()
                finally:
                    Path(fn).unlink(missing_ok=True)
                return self.send_bytes(200, content, 'application/octet-stream',
                     {'Content-Disposition': f'attachment; filename="VetStock-{today()}.sqlite3"'})
            raise AppError('NOT_FOUND', 404)

    def do_POST(self):
        try:
            if not isinstance(self.connection, ssl.SSLSocket) and not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise AppError('HTTPS_REQUIRED', 403)
            p = self.payload()
            path = urllib.parse.urlsplit(self.path).path
            if not path.startswith('/api/'):
                raise AppError('NOT_FOUND', 404)
            with db() as c:
                if path == '/api/setup':
                    c.execute('BEGIN IMMEDIATE')
                    if c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                        raise AppError('ALREADY_SETUP')
                    username = textval(p.get('username'), 80, True)
                    display_name = textval(p.get('display_name'), 100, True)
                    pw = passwordval(p.get('password'))
                    cur = c.execute('''INSERT INTO users(username,display_name,role,password_hash,created_at)
                                       VALUES(?,?,'admin',?,?)''', (username, display_name, hash_password(pw), now()))
                    uid = cur.lastrowid
                    audit(c, uid, 'setup', username)
                    c.commit()
                    return self.send_json({'ok': True}, headers={'Set-Cookie': self.set_session(uid)})
                if path == '/api/login':
                    ip = self.client_address[0]
                    with SESSION_LOCK:
                        attempts = [t for t in LOGIN_ATTEMPTS.get(ip, []) if t > time.time() - 300]
                        if len(attempts) >= 12:
                            raise AppError('TOO_MANY_ATTEMPTS', 429)
                    username = textval(p.get('username'), 80, True)
                    u = c.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
                    # Always run the same password hashing work, also for unknown accounts.
                    known = u['password_hash'] if u else 'pbkdf2_sha256$310000$00000000000000000000000000000000$' + '00' * 32
                    ok = check_password(str(p.get('password') or ''), known)
                    if not u or not ok:
                        with SESSION_LOCK:
                            LOGIN_ATTEMPTS[ip] = attempts + [time.time()]
                        raise AppError('INVALID_LOGIN', 401)
                    with SESSION_LOCK:
                        LOGIN_ATTEMPTS.pop(ip, None)
                    return self.send_json({'ok': True}, headers={'Set-Cookie': self.set_session(u['id'])})
                u = self.require(c)
                if path == '/api/logout':
                    try:
                        jar = cookies.SimpleCookie(); jar.load(self.headers.get('Cookie', ''))
                        sid = jar['vetstock_sid'].value
                        with SESSION_LOCK:
                            SESSIONS.pop(sid, None)
                    except Exception:
                        pass
                    return self.send_json({'ok': True}, headers={'Set-Cookie': 'vetstock_sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
                if path == '/api/backup-now':
                    if u['role'] != 'admin':
                        raise AppError('ADMIN_REQUIRED', 403)
                    folder = settings(c)['backup_dir']
                    if not folder:
                        raise AppError('BACKUP_FOLDER_MISSING')
                    result = create_backup(c, folder)
                    audit(c, u['id'], 'backup_created', result)
                    c.commit()
                    return self.send_json({'ok': True, 'path': result})
                c.execute('BEGIN IMMEDIATE')
                result = self.post_action(c, path, p, u)
                c.commit()
                return self.send_json(result if result is not None else {'ok': True})
        except sqlite3.IntegrityError:
            self.send_json({'error': 'DATA_CONFLICT'}, 400)
        except AppError as exc:
            self.send_json({'error': exc.code}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self.send_json({'error': 'SERVER_ERROR'}, 500)

    def post_action(self, c, path, p, u):
        admin_paths = {'/api/medicine', '/api/adjustment', '/api/supplier', '/api/supplier-invoice',
                       '/api/supplier-payment', '/api/sale-void', '/api/user', '/api/user-update',
                       '/api/settings', '/api/import-preview', '/api/import-commit'}
        if path in admin_paths and u['role'] != 'admin':
            raise AppError('ADMIN_REQUIRED', 403)
        uid = u['id']
        if path == '/api/medicine':
            return {'id': write_medicine(c, p, uid)}
        if path == '/api/receipt':
            return {'id': do_receipt(c, p, uid)}
        if path == '/api/adjustment':
            lot_id = intval(p.get('lot_id'), 1)
            lot = c.execute('SELECT * FROM lots WHERE id=?', (lot_id,)).fetchone()
            if not lot:
                raise AppError('NOT_FOUND', 404)
            boxes = intval(p.get('boxes'))
            loose = intval(p.get('loose'))
            reason = textval(p.get('reason'), 300, True)
            c.execute('UPDATE lots SET boxes=?,loose=? WHERE id=?', (boxes, loose, lot_id))
            audit(c, uid, 'stock_adjusted', f'lot={lot_id} {lot["boxes"]}/{lot["loose"]} -> {boxes}/{loose}; {reason}')
            return {'ok': True}
        if path == '/api/supplier':
            name = textval(p.get('name'), 160, True)
            values = (name, textval(p.get('phone'), 60), textval(p.get('email'), 160), textval(p.get('address'), 300))
            if p.get('id'):
                supplier_id = intval(p['id'], 1)
                if not c.execute('SELECT 1 FROM suppliers WHERE id=?', (supplier_id,)).fetchone():
                    raise AppError('NOT_FOUND', 404)
                c.execute('UPDATE suppliers SET name=?,phone=?,email=?,address=? WHERE id=?', values + (supplier_id,))
                audit(c, uid, 'supplier_updated', f'{supplier_id}: {name}')
            else:
                supplier_id = c.execute('INSERT INTO suppliers(name,phone,email,address) VALUES(?,?,?,?)', values).lastrowid
                audit(c, uid, 'supplier_created', f'{supplier_id}: {name}')
            return {'id': supplier_id}
        if path == '/api/order':
            supplier_id = intval(p.get('supplier_id'), 1)
            if not c.execute('SELECT 1 FROM suppliers WHERE id=?', (supplier_id,)).fetchone():
                raise AppError('NOT_FOUND', 404)
            lines = p.get('lines')
            if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
                raise AppError('NO_ORDER_LINES')
            cur = c.execute('INSERT INTO orders(supplier_id,notes,created_at) VALUES(?,?,?)',
                            (supplier_id, textval(p.get('notes'), 500), now()))
            order_id = cur.lastrowid
            for line in lines:
                med_id = intval(line.get('medicine_id'), 1)
                if not c.execute('SELECT 1 FROM medicines WHERE id=? AND active=1', (med_id,)).fetchone():
                    raise AppError('NOT_FOUND', 404)
                c.execute('INSERT INTO order_lines(order_id,medicine_id,boxes) VALUES(?,?,?)',
                          (order_id, med_id, intval(line.get('boxes'), 1)))
            audit(c, uid, 'order_created', f'order={order_id}')
            return {'id': order_id}
        if path == '/api/supplier-invoice':
            supplier_id = intval(p.get('supplier_id'), 1)
            if not c.execute('SELECT 1 FROM suppliers WHERE id=?', (supplier_id,)).fetchone():
                raise AppError('NOT_FOUND', 404)
            cur = c.execute('''INSERT INTO supplier_invoices(supplier_id,invoice_no,total,due_date,created_at)
                             VALUES(?,?,?,?,?)''', (supplier_id, textval(p.get('invoice_no'), 120, True),
                             money(p.get('total')), dateval(p.get('due_date')), now()))
            attachment = p.get('attachment')
            if attachment:
                if not isinstance(attachment, dict):
                    raise AppError('INVALID_ATTACHMENT')
                filename = textval(attachment.get('filename'), 200, True)
                try:
                    content = base64.b64decode(attachment.get('data', ''), validate=True)
                except Exception:
                    raise AppError('INVALID_ATTACHMENT')
                ext = Path(filename).suffix.lower()
                valid = ((ext == '.pdf' and content.startswith(b'%PDF-')) or
                         (ext in ('.jpg','.jpeg') and content.startswith(b'\xff\xd8\xff')) or
                         (ext == '.png' and content.startswith(b'\x89PNG\r\n\x1a\n')))
                if not valid or len(content) > 5 * 1024 * 1024:
                    raise AppError('INVALID_ATTACHMENT')
                c.execute('INSERT INTO supplier_invoice_files(invoice_id,filename,content) VALUES(?,?,?)',
                          (cur.lastrowid, Path(filename).name, content))
            audit(c, uid, 'supplier_invoice_created', f'invoice={cur.lastrowid}')
            return {'id': cur.lastrowid}
        if path == '/api/supplier-payment':
            invoice_id = intval(p.get('invoice_id'), 1)
            invoice = c.execute('''SELECT i.*,COALESCE((SELECT SUM(amount) FROM supplier_payments WHERE invoice_id=i.id),0) AS paid
                                   FROM supplier_invoices i WHERE i.id=?''', (invoice_id,)).fetchone()
            if not invoice:
                raise AppError('NOT_FOUND', 404)
            amount = money(p.get('amount'))
            if amount <= 0 or amount > invoice['total'] - invoice['paid']:
                raise AppError('PAYMENT_TOO_HIGH')
            c.execute('INSERT INTO supplier_payments(invoice_id,amount,method,user_id,created_at) VALUES(?,?,?,?,?)',
                      (invoice_id, amount, payment_method(p.get('method')), uid, now()))
            audit(c, uid, 'supplier_payment', f'invoice={invoice_id} amount_millimes={amount}')
            return {'ok': True}
        if path == '/api/sale':
            return {'id': record_sale(c, p, uid)}
        if path == '/api/sale-payment':
            sale_id = intval(p.get('sale_id'), 1)
            sale = c.execute('''SELECT s.*,COALESCE((SELECT SUM(amount) FROM sale_payments WHERE sale_id=s.id),0) AS paid
                                FROM sales s WHERE id=?''', (sale_id,)).fetchone()
            if not sale or sale['status'] != 'active':
                raise AppError('SALE_NOT_ACTIVE')
            amount = money(p.get('amount'))
            if amount <= 0 or amount > sale['total'] - sale['paid']:
                raise AppError('PAYMENT_TOO_HIGH')
            c.execute('INSERT INTO sale_payments(sale_id,amount,method,user_id,created_at) VALUES(?,?,?,?,?)',
                      (sale_id, amount, payment_method(p.get('method')), uid, now()))
            audit(c, uid, 'sale_payment', f'sale={sale_id} amount_millimes={amount}')
            return {'ok': True}
        if path == '/api/sale-void':
            void_sale(c, intval(p.get('sale_id'), 1), textval(p.get('reason'), 300, True), uid)
            return {'ok': True}
        if path == '/api/user':
            role = p.get('role', 'employee')
            if role not in ('admin', 'employee'):
                raise AppError('INVALID_FIELD')
            username = textval(p.get('username'), 80, True)
            display = textval(p.get('display_name'), 100, True)
            cur = c.execute('''INSERT INTO users(username,display_name,role,password_hash,alerts,created_at)
                               VALUES(?,?,?,?,?,?)''', (username, display, role, hash_password(passwordval(p.get('password'))),
                               1 if p.get('alerts') else 0, now()))
            audit(c, uid, 'user_created', f'{cur.lastrowid}: {username} ({role})')
            return {'id': cur.lastrowid}
        if path == '/api/user-update':
            user_id = intval(p.get('id'), 1)
            old = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not old:
                raise AppError('NOT_FOUND', 404)
            active = 1 if p.get('active') else 0
            alerts = 1 if p.get('alerts') else 0
            if old['role'] == 'admin' and not active:
                n = c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
                if n <= 1:
                    raise AppError('LAST_ADMIN')
            c.execute('UPDATE users SET active=?,alerts=? WHERE id=?', (active, alerts, user_id))
            if p.get('password'):
                c.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(passwordval(p['password'])), user_id))
                with SESSION_LOCK:
                    for sid in [k for k, v in SESSIONS.items() if v['user_id'] == user_id]:
                        SESSIONS.pop(sid, None)
            audit(c, uid, 'user_updated', f'user={user_id} active={active} alerts={alerts}')
            return {'ok': True}
        if path == '/api/change-password':
            if not check_password(str(p.get('current_password') or ''), u['password_hash']):
                raise AppError('INVALID_LOGIN', 401)
            c.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(passwordval(p.get('new_password'))), uid))
            audit(c, uid, 'password_changed')
            with SESSION_LOCK:
                for sid in [k for k, v in SESSIONS.items() if v['user_id'] == uid]:
                    SESSIONS.pop(sid, None)
            return {'ok': True}
        if path == '/api/settings':
            for key, maxlen in [('clinic_name',160),('clinic_address',300),('clinic_phone',60),
                                ('clinic_tax_id',100),('backup_dir',500)]:
                if key in p:
                    value = textval(p[key], maxlen)
                    if key == 'backup_dir' and value and not Path(value).is_dir():
                        raise AppError('BACKUP_FOLDER_MISSING')
                    c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, value))
            audit(c, uid, 'settings_updated')
            return {'ok': True}
        if path == '/api/import-preview':
            return import_preview(p)
        if path == '/api/import-commit':
            return {'count': import_commit(c, p, uid)}
        raise AppError('NOT_FOUND', 404)


class LocalOnlyServer(ThreadingHTTPServer):
    """Reject public IPv4 peers even if the host is accidentally reachable."""
    networks = tuple(ipaddress.ip_network(x) for x in
                     ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16'))

    def verify_request(self, request, client_address):
        try:
            ip = ipaddress.ip_address(client_address[0])
            return any(ip in network for network in self.networks)
        except ValueError:
            return False


def network_addresses():
    values = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith('127.') and ip not in values:
                values.append(ip)
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1', 1))  # Select a route; this does not send a packet.
            ip = s.getsockname()[0]
            if not ip.startswith('127.') and ip not in values:
                values.append(ip)
    except OSError:
        pass
    return values


def main():
    parser = argparse.ArgumentParser(description='Application locale VetStock')
    parser.add_argument('--host', default=os.environ.get('HOST', '0.0.0.0'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8745')))
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--strictPort', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--tls-cert', default=str(DATA / 'certs' / 'server.pem'))
    parser.add_argument('--tls-key', default=str(DATA / 'certs' / 'server-key.pem'))
    args = parser.parse_args()
    init_db()
    server = LocalOnlyServer((args.host, args.port), Handler)
    cert, key = Path(args.tls_cert), Path(args.tls_key)
    secure = cert.is_file() and key.is_file()
    if secure:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(cert), str(key))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    scheme = 'https' if secure else 'http'
    port = server.server_address[1]
    print('\nVetStock — serveur local du cabinet', flush=True)
    print(f'Ordinateur : {scheme}://localhost:{port}', flush=True)
    for address in network_addresses():
        print(f'Téléphone sur le même Wi-Fi : {scheme}://{address}:{port}', flush=True)
    if not secure:
        print('ATTENTION : HTTP non chiffré. Configurez HTTPS pour les appareils du réseau ; consultez le guide.', flush=True)
    print('Laissez cette fenêtre ouverte. Ctrl+C pour arrêter.\n', flush=True)
    threading.Thread(target=backup_loop, daemon=True).start()
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f'{scheme}://localhost:{port}')).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

