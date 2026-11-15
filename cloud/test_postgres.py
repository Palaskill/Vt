"""Integration checks only against a disposable, local PostgreSQL test database."""
import datetime as dt
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg2
import server as app
from cloud.postgres import Connection
from cloud import server as cloud

URL = os.environ.get('VETSTOCK_TEST_DATABASE_URL', '')


@unittest.skipUnless(URL, 'VETSTOCK_TEST_DATABASE_URL is not configured')
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        info = psycopg2.extensions.parse_dsn(URL)
        if info.get('host') not in ('localhost','127.0.0.1') or info.get('dbname') != 'vetstock_test':
            raise RuntimeError('Integration tests require the local disposable vetstock_test database')
        raw = psycopg2.connect(URL)
        with raw:
            with raw.cursor() as c:
                c.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF; IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF; END $$")
                c.execute((Path(__file__).parent/'schema.sql').read_text())
        raw.close()
        with cls().db() as c:
            cls.user_id = c.execute('INSERT INTO users(username,display_name,role,password_hash,created_at) VALUES(?,?,?,?,?)',
                            ('pg_test','PG Test','admin',app.hash_password('password-test-123'),app.now())).lastrowid

    def db(self):
        return Connection(psycopg2.connect(URL))

    def test_inventory_transactions_and_private_schema(self):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            uid = self.user_id
            before = app.dashboard(c, c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())['today_total']
            med = app.write_medicine(c, {'name':'Test PG','pack_size':20,'price_pack':'10.000',
                'price_unit':'0.600','threshold':1,'threshold_mode':'units','expiry_days':90}, uid)
            lot = app.do_receipt(c, {'medicine_id':med,'boxes':1,'loose':0,'batch':'PG1',
                'expiry':(dt.date.today()+dt.timedelta(days=90)).isoformat()}, uid)
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            app.record_sale(c, {'lines':[{'medicine_id':med,'kind':'unit','qty':3,'expected_price':600}],
                'discount':'0','payment':'0','method':'cash'}, uid)
            result = c.execute('SELECT boxes,loose FROM lots WHERE id=?',(lot,)).fetchone()
            self.assertEqual((result['boxes'],result['loose']),(0,17))
            self.assertEqual(next(row for row in app.medicine_rows(c) if row['id']==med)['total_units'],17)
            self.assertEqual(app.dashboard(c, c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())['today_total'],before+1800)
            self.assertFalse(c.execute("SELECT has_schema_privilege('anon','vetstock','USAGE')").fetchone()[0])
            self.assertTrue(c.execute("SELECT relrowsecurity FROM pg_class WHERE oid='vetstock.users'::regclass").fetchone()[0])

    def test_concurrent_sales_never_oversell(self):
        with self.db() as c:
            uid = self.user_id
            med = app.write_medicine(c, {'name':'Concurrent','pack_size':1,'price_pack':'1',
                'price_unit':'1','threshold':0,'threshold_mode':'units','expiry_days':30}, uid)
            app.do_receipt(c, {'medicine_id':med,'boxes':1,'loose':0,'batch':'one',
                'expiry':(dt.date.today()+dt.timedelta(days=90)).isoformat()}, uid)
        results = []
        def sell():
            try:
                with self.db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    app.record_sale(c, {'lines':[{'medicine_id':med,'kind':'box','qty':1,'expected_price':1000}],
                        'discount':'0','payment':'0','method':'cash'}, uid)
                results.append('ok')
            except app.AppError as exc:
                results.append(exc.code)
        threads = [threading.Thread(target=sell) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertCountEqual(results, ['ok','NOT_ENOUGH_STOCK'])


if __name__ == '__main__': unittest.main()
