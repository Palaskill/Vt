#!/usr/bin/env python3
"""Business-critical regression checks. Run with python -m unittest discover -s tests."""
import csv
import datetime as dt
import http.cookiejar
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import server as app


class Isolated(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        app.DATA=Path(self.temp.name)
        app.DB=app.DATA/'vetstock.sqlite3'
        app.SESSIONS.clear()
        app.LOGIN_ATTEMPTS.clear()
        app.init_db()
        with app.db() as c:
            cur=c.execute("INSERT INTO users(username,display_name,role,password_hash,created_at) VALUES(?,?,?,?,?)",
                          ('admin','Admin','admin',app.hash_password('motdepasse-solide'),app.now()))
            self.uid=cur.lastrowid
    def tearDown(self): self.temp.cleanup()
    def create_med(self,**kw):
        data={'name':'Comprimés test','pack_size':20,'price_pack':'10.000','price_unit':'0.600',
              'threshold':10,'threshold_mode':'units','expiry_days':90}
        data.update(kw)
        with app.db() as c: return app.write_medicine(c,data,self.uid)
    def receipt(self,mid,days,boxes=1,loose=0,batch='L1'):
        expiry=(dt.date.today()+dt.timedelta(days=days)).isoformat()
        with app.db() as c:
            return app.do_receipt(c,{'medicine_id':mid,'expiry':expiry,'boxes':boxes,'loose':loose,'batch':batch},self.uid)
    def sell(self,mid,kind,qty,price,**kw):
        data={'lines':[{'medicine_id':mid,'kind':kind,'qty':qty,'expected_price':price}],
              'discount':'0','payment':'0','method':'cash'}
        data.update(kw)
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE')
            return app.record_sale(c,data,self.uid)


class InventoryTests(Isolated):
    def test_fefo_unit_sale_preserves_closed_and_open_counts(self):
        mid=self.create_med()
        early=self.receipt(mid,30,boxes=2,batch='near')
        later=self.receipt(mid,180,boxes=2,batch='later')
        expired=self.receipt(mid,-1,boxes=4,batch='expired')
        self.sell(mid,'unit',3,600)
        with app.db() as c:
            state={r['id']:(r['boxes'],r['loose']) for r in app.lots_for(c,mid)}
            self.assertEqual(state[early],(1,17))
            self.assertEqual(state[later],(2,0))
            self.assertEqual(state[expired],(4,0))
            m=app.medicine_rows(c)[0]
            self.assertEqual((m['total_units'],m['expired_units']),(77,80))
    def test_explicit_lot_override_and_expired_rejected(self):
        mid=self.create_med()
        early=self.receipt(mid,30,batch='early')
        later=self.receipt(mid,180,batch='later')
        old=self.receipt(mid,-1,batch='old')
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE')
            app.record_sale(c,{'lines':[{'medicine_id':mid,'kind':'box','qty':1,'lot_id':later,'expected_price':10000}],
                               'discount':'0','payment':'0','method':'cash'},self.uid)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT boxes FROM lots WHERE id=?',(early,)).fetchone()[0],1)
            self.assertEqual(c.execute('SELECT boxes FROM lots WHERE id=?',(later,)).fetchone()[0],0)
        with self.assertRaises(app.AppError) as err:
            with app.db() as c:
                c.execute('BEGIN IMMEDIATE')
                app.record_sale(c,{'lines':[{'medicine_id':mid,'kind':'box','qty':1,'lot_id':old,'expected_price':10000}],
                                   'discount':'0','payment':'0','method':'cash'},self.uid)
        self.assertEqual(err.exception.code,'NOT_ENOUGH_STOCK')
    def test_out_of_stock_rolls_back_sale_and_all_prior_allocations(self):
        mid=self.create_med(); lot=self.receipt(mid,50,boxes=1)
        data={'lines':[{'medicine_id':mid,'kind':'unit','qty':5,'expected_price':600},
                       {'medicine_id':mid,'kind':'unit','qty':100,'expected_price':600}],
              'discount':'0','payment':'0','method':'cash'}
        with self.assertRaises(app.AppError):
            with app.db() as c:
                c.execute('BEGIN IMMEDIATE'); app.record_sale(c,data,self.uid)
        with app.db() as c:
            r=c.execute('SELECT boxes,loose FROM lots WHERE id=?',(lot,)).fetchone()
            self.assertEqual(tuple(r),(1,0));self.assertEqual(c.execute('SELECT COUNT(*) FROM sales').fetchone()[0],0)
    def test_void_restores_units_without_inventing_sealed_boxes(self):
        mid=self.create_med();lot=self.receipt(mid,60,boxes=1)
        sale=self.sell(mid,'unit',3,600)
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE');app.void_sale(c,sale,'erreur',self.uid)
        with app.db() as c:
            r=c.execute('SELECT boxes,loose FROM lots WHERE id=?',(lot,)).fetchone()
            self.assertEqual(tuple(r),(0,20))
            self.assertEqual(c.execute('SELECT status FROM sales WHERE id=?',(sale,)).fetchone()[0],'void')
    def test_price_change_and_missing_expiry_are_blocked(self):
        mid=self.create_med()
        with self.assertRaises(app.AppError) as e:
            with app.db() as c:app.do_receipt(c,{'medicine_id':mid,'boxes':1,'batch':'x','expiry':''},self.uid)
        self.assertEqual(e.exception.code,'INVALID_DATE')
        self.receipt(mid,20)
        with self.assertRaises(app.AppError) as e:self.sell(mid,'box',1,9999)
        self.assertEqual(e.exception.code,'PRICE_CHANGED')
    def test_money_is_exact_in_millimes(self):
        self.assertEqual(app.money('2.500'),2500);self.assertEqual(app.money('0,001'),1)
        with self.assertRaises(app.AppError):app.money('0.0009')
    def test_only_private_networks_can_reach_the_server(self):
        srv=object.__new__(app.LocalOnlyServer)
        self.assertTrue(srv.verify_request(None,('192.168.1.20',1234)))
        self.assertTrue(srv.verify_request(None,('127.0.0.1',1234)))
        self.assertFalse(srv.verify_request(None,('8.8.8.8',1234)))
    def test_partial_deliveries_update_order(self):
        mid=self.create_med()
        with app.db() as c:
            sid=c.execute("INSERT INTO suppliers(name) VALUES('Supplier')").lastrowid
            oid=c.execute("INSERT INTO orders(supplier_id,created_at) VALUES(?,?)",(sid,app.now())).lastrowid
            line=c.execute('INSERT INTO order_lines(order_id,medicine_id,boxes) VALUES(?,?,5)',(oid,mid)).lastrowid
        for count in (3,2):
            with app.db() as c:
                app.do_receipt(c,{'medicine_id':mid,'boxes':count,'loose':0,'batch':'A',
                     'expiry':(dt.date.today()+dt.timedelta(days=90)).isoformat(),'order_line_id':line},self.uid)
                status=c.execute('SELECT status FROM orders WHERE id=?',(oid,)).fetchone()[0]
                self.assertEqual(status,'open' if count==3 else 'received')
    def test_sqlite_backup_is_valid_and_complete(self):
        self.create_med();out=Path(self.temp.name)/'backup';out.mkdir()
        with app.db() as c: path=app.create_backup(c,str(out))
        backup=app.sqlite3.connect(path)
        self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(backup.execute('SELECT COUNT(*) FROM medicines').fetchone()[0],1)
        backup.close()
    def test_import_is_atomic_and_maps_csv(self):
        raw=('nom;unites_par_boite;prix_boite;prix_unite;lot;peremption;boites\n'
             'Test A;20;10.000;0.600;LOT1;2030-12-31;2\n').encode()
        p={'filename':'test.csv','data':app.base64.b64encode(raw).decode()}
        self.assertEqual(app.import_preview(p)['count'],1)
        with app.db() as c: app.import_commit(c,p,self.uid)
        with app.db() as c:self.assertEqual(app.medicine_rows(c)[0]['total_units'],40)


class HTTPTests(Isolated):
    def setUp(self):
        super().setUp()
        self.http=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)+'/api/'
        self.admin=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.worker=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();super().tearDown()
    def post(self,opener,path,data):
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=10) as r:return json.load(r)
    def get(self,opener,path):
        with opener.open(self.base+path,timeout=10) as r:return json.load(r)
    def test_employee_can_sell_but_cannot_view_reports_or_change_prices(self):
        self.post(self.admin,'login',{'username':'admin','password':'motdepasse-solide'})
        self.post(self.admin,'user',{'username':'worker','display_name':'Worker','role':'employee','password':'motdepasse-employe','alerts':False})
        med=self.post(self.admin,'medicine',{'name':'Test','pack_size':20,'price_pack':'10','price_unit':'0.6',
             'threshold':2,'threshold_mode':'units','expiry_days':30})['id']
        self.post(self.worker,'login',{'username':'worker','password':'motdepasse-employe'})
        expiry=(dt.date.today()+dt.timedelta(days=100)).isoformat()
        self.post(self.worker,'receipt',{'medicine_id':med,'batch':'B','expiry':expiry,'boxes':1,'loose':0})
        sale=self.post(self.worker,'sale',{'lines':[{'medicine_id':med,'kind':'unit','qty':3,'expected_price':600}],
                         'discount':'0','payment':'1.000','method':'cash'})['id']
        self.assertGreater(sale,0)
        self.assertNotIn('today_total',self.get(self.worker,'dashboard'))
        for path in ('supplier-invoices','users','audit'):
            with self.assertRaises(urllib.error.HTTPError) as e:self.get(self.worker,path)
            self.assertEqual(e.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post(self.worker,'medicine',{'id':med,'name':'Changed','pack_size':20})
        self.assertEqual(e.exception.code,403)
    def test_cross_origin_post_is_rejected(self):
        req=urllib.request.Request(self.base+'login',data=b'{}',headers={'Content-Type':'application/json','Origin':'https://attacker.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req,timeout=10)
        self.assertEqual(e.exception.code,403)
    def test_supplier_invoice_attachment_is_private_and_recoverable(self):
        self.post(self.admin,'login',{'username':'admin','password':'motdepasse-solide'})
        supplier=self.post(self.admin,'supplier',{'name':'VetFournisseur'})['id']
        data=b'%PDF-1.4\n% synthetic test document\n%%EOF'
        invoice=self.post(self.admin,'supplier-invoice',{'supplier_id':supplier,'invoice_no':'F01','total':'12.345',
             'attachment':{'filename':'facture.pdf','data':app.base64.b64encode(data).decode()}})['id']
        result=self.get(self.admin,'supplier-invoices')
        self.assertEqual(result[0]['has_file'],1)
        with self.admin.open(self.base+'supplier-invoice-file?id='+str(invoice)) as response:
            self.assertEqual(response.read(),data)
            self.assertIn('attachment',response.headers['Content-Disposition'])
        self.post(self.admin,'user',{'username':'worker','display_name':'Worker','role':'employee','password':'motdepasse-employe','alerts':False})
        self.post(self.worker,'login',{'username':'worker','password':'motdepasse-employe'})
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.worker.open(self.base+'supplier-invoice-file?id='+str(invoice))
        self.assertEqual(err.exception.code,403)


if __name__=='__main__':unittest.main()
