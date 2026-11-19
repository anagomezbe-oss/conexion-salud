import importlib.util
import json
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('conexion_app', ROOT / 'app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class ApiFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        app.DB = Path(cls.temp.name) / 'test.db'
        app.Handler.sessions = {}
        app.init()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def client(self, email, password='demo1234'):
        c=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call(c,'/api/login',{'email':email,'password':password})
        return c

    def call(self, c, path, body=None, method=None, expected=200):
        req=Request(self.base+path, data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type':'application/json'}, method=method or ('POST' if body is not None else 'GET'))
        try:
            with c.open(req) as r:
                result=r.read()
                self.assertEqual(r.status,expected)
        except HTTPError as e:
            result=e.read()
            self.assertEqual(e.code,expected, result.decode())
            e.close()
        try: return json.loads(result)
        except (ValueError, TypeError): return result

    def test_permissions_and_end_to_end_order_flow(self):
        vendor=self.client('proveedor@conexionsalud.demo')
        health=self.client('salud@conexionsalud.demo')
        eps=self.client('eps@conexionsalud.demo')
        vdata=self.call(vendor,'/api/data')
        self.assertTrue(all(o['provider']=='MediSupply' for o in vdata['orders']))
        self.assertTrue(all(i['supplier']=='MediSupply' for i in vdata['inventory']))
        self.call(vendor,'/api/export',expected=403)
        self.call(eps,'/api/orders/1',{'status':'Entregado'},expected=403)
        created=self.call(health,'/api/orders',{'provider':'MediSupply','eps':'EPS Bienestar','eta':'2026-12-01','items':[{'id':1,'qty':12}]},expected=201)
        fresh=self.call(vendor,'/api/data')
        order=next(o for o in fresh['orders'] if o['code']==created['code'])
        oid=order['id']
        for status in ('Confirmado','Retrasado','En preparación','Despachado','En tránsito'):
            self.call(vendor,f'/api/orders/{oid}',{'status':status,'notes':'Prueba de flujo'})
        self.call(vendor,f'/api/orders/{oid}',{'status':'Entregado'},expected=403)
        hdata=self.call(health,'/api/data')
        self.assertTrue(any(a['title']=='Pedido actualizado' and created['code'] in a['description'] for a in hdata['alerts']))
        self.call(health,f'/api/orders/{oid}',{'status':'Entregado','notes':'Recepción confirmada'})
        final=self.call(health,'/api/data')
        order=next(o for o in final['orders'] if o['id']==oid)
        self.assertEqual(order['status'],'Entregado')
        self.assertEqual(order['events'][-1]['actor'],'Laura Gómez')
        self.call(health,'/api/alerts/'+str(final['alerts'][0]['id']),{})
        self.call(health,'/api/messages',{'body':'Mensaje de prueba','recipient':'MediSupply','conversation':'MediSupply'},expected=201)
        self.assertTrue(any(m['body']=='Mensaje de prueba' for m in self.call(vendor,'/api/data')['messages']))
        payment=final['payments'][0]
        self.call(health,f"/api/payments/{payment['id']}",{'status':'En revisión'})
        self.assertTrue(any(p['id']==payment['id'] and p['status']=='En revisión' for p in self.call(health,'/api/data')['payments']))

    def test_inventory_validation_and_authentication(self):
        vendor=self.client('proveedor@conexionsalud.demo')
        eps=self.client('eps@conexionsalud.demo')
        self.call(vendor,'/api/inventory',{'id':1,'qty':-1,'minimum':5},expected=400)
        self.call(eps,'/api/inventory',{'id':1,'qty':1,'minimum':1},expected=403)
        bad=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call(bad,'/api/login',{'email':'salud@conexionsalud.demo','password':'incorrecta'},expected=401)

if __name__=='__main__': unittest.main(verbosity=2)
