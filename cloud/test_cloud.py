"""Cloud security and inventory regression tests, using disposable local fixtures."""
import datetime as dt
import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import server as app
from cloud import postgres
from cloud import server as cloud


class AdapterTests(unittest.TestCase):
    def test_binding_and_settings_upsert(self):
        sql, returning = postgres.translate("SELECT '?' AS value, ? AS bound, 'it''s?' AS literal")
        self.assertEqual(sql, "SELECT '?' AS value, %s AS bound, 'it''s?' AS literal")
        self.assertFalse(returning)
        sql, returning = postgres.translate('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)')
        self.assertIn('ON CONFLICT (key) DO UPDATE', sql)
        self.assertFalse(returning)
        sql, returning = postgres.translate('INSERT INTO medicines(name) VALUES(?)')
        self.assertTrue(returning)
        self.assertTrue(sql.endswith('RETURNING id'))

    def test_numeric_aggregates_and_bytes(self):
        row = postgres.Row([('amount', postgres._convert(Decimal('123400'))),
                            ('data', postgres._convert(memoryview(b'pdf')))])
        self.assertEqual(row[0], 123400)
        self.assertEqual(row['data'], b'pdf')


class CloudHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_data, cls.old_db, cls.old_db_factory = app.DATA, app.DB, app.db
        app.DATA = Path(cls.tmp.name)
        app.DB = app.DATA / 'vetstock.sqlite3'
        class ClosingConnection(sqlite3.Connection):
            def __exit__(self, *args):
                try: return super().__exit__(*args)
                finally: self.close()
        def open_db():
            c = sqlite3.connect(app.DB, timeout=15, factory=ClosingConnection)
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA foreign_keys=ON')
            return c
        app.db = open_db
        app.init_db()
        with app.db() as c:
            c.executescript('''CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at INTEGER NOT NULL);
              CREATE TABLE login_attempts(username_hash TEXT PRIMARY KEY,attempts INTEGER NOT NULL,window_start INTEGER NOT NULL);''')
        with patch.dict(os.environ, {'VETSTOCK_ADMIN_USERNAME':'admin', 'VETSTOCK_ADMIN_PASSWORD':'mot-de-passe-test'}):
            cloud.bootstrap()
        cls.http = app.ThreadingHTTPServer(('127.0.0.1', 0), cloud.Handler)
        cls.host = '127.0.0.1:' + str(cls.http.server_port)
        cls.origin = 'http://' + cls.host
        cls.origin_patch = patch('cloud.server.expected_origin', return_value=cls.origin)
        cls.origin_patch.start()
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()
        cls.origin_patch.stop()
        app.DATA, app.DB, app.db = cls.old_data, cls.old_db, cls.old_db_factory
        cls.tmp.cleanup()

    def request(self, path, data=None, cookie='', origin=None):
        connection = http.client.HTTPConnection(self.host, timeout=10)
        headers = {'Host':self.host}
        if data is not None:
            headers.update({'Content-Type':'application/json', 'Origin':origin or self.origin})
        if cookie:
            headers['Cookie'] = cookie
        connection.request('POST' if data is not None else 'GET', '/api/'+path,
                           json.dumps(data) if data is not None else None, headers)
        response = connection.getresponse()
        body = json.loads(response.read())
        result = (response.status, body, response.getheader('Set-Cookie'))
        connection.close()
        return result

    def login(self, username, password):
        status, _, header = self.request('login', {'username':username,'password':password})
        self.assertEqual(status, 200)
        self.assertIn('; Secure;', header)
        self.assertIn('; HttpOnly;', header)
        return header.split(';', 1)[0]

    def test_cloud_flow_roles_fefo_and_revocation(self):
        self.assertEqual(self.request('setup', {'username':'attacker','password':'0123456789'})[0], 404)
        self.assertEqual(self.request('login', {'username':'admin','password':'x'}, origin='https://attacker.invalid')[0], 403)
        admin = self.login('ADMIN', 'mot-de-passe-test')
        st, user, _ = self.request('user', {'username':'employe','display_name':'Employé',
            'role':'employee','password':'employe-password','alerts':True}, admin)
        self.assertEqual(st, 200)
        worker_id = user['id']
        st, med, _ = self.request('medicine', {'name':'Test','pack_size':20,'price_pack':'10',
            'price_unit':'0.6','threshold':2,'threshold_mode':'units','expiry_days':30}, admin)
        self.assertEqual(st, 200)
        worker = self.login('employe', 'employe-password')
        self.assertEqual(self.request('medicine', {'name':'No'}, worker)[0], 403)
        self.assertEqual(self.request('users', cookie=worker)[0], 403)
        expiry = (dt.date.today()+dt.timedelta(days=100)).isoformat()
        st, lot, _ = self.request('receipt', {'medicine_id':med['id'],'batch':'L1','expiry':expiry,
                                              'boxes':1,'loose':0}, worker)
        self.assertEqual(st, 200)
        st, sale, _ = self.request('sale', {'lines':[{'medicine_id':med['id'],'kind':'unit','qty':3,
            'expected_price':600}],'discount':'0','payment':'0','method':'cash'}, worker)
        self.assertEqual(st, 200)
        st, lots, _ = self.request('lots', cookie=admin)
        self.assertEqual((lots[0]['boxes'],lots[0]['loose']), (0,17))
        self.assertNotIn('today_total', self.request('dashboard', cookie=worker)[1])
        self.assertEqual(self.request('user-update', {'id':worker_id,'active':False,'alerts':True}, admin)[0], 200)
        self.assertEqual(self.request('lots', cookie=worker)[0], 401)

    def test_failed_logins_are_persistent_and_limited(self):
        for _ in range(12):
            self.assertEqual(self.request('login', {'username':'absent','password':'wrong'})[0], 401)
        self.assertEqual(self.request('login', {'username':'absent','password':'wrong'})[0], 429)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT attempts FROM login_attempts WHERE username_hash=?',
                                       (cloud.token_hash('absent'),)).fetchone()[0], 12)

    def test_design_assets_are_served_by_cloud(self):
        connection = http.client.HTTPConnection(self.host, timeout=10)
        try:
            connection.request('GET', '/', headers={'Host': self.host})
            response = connection.getresponse()
            page = response.read()
            self.assertEqual(response.status, 200)
            self.assertIn("script-src 'self'", response.getheader('Content-Security-Policy'))
            for name in ('design.css', 'design.js', 'gsap.min.js', 'lenis.min.js', 'react-spotlight.js'):
                with self.subTest(asset=name):
                    self.assertIn(('/' + name).encode(), page)
                    connection.request('GET', '/' + name, headers={'Host': self.host})
                    asset = connection.getresponse()
                    self.assertEqual(asset.status, 200)
                    self.assertGreater(len(asset.read()), 100)
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
