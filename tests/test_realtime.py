import base64, importlib.util, json, os, socket, struct, tempfile, threading, time, unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('conexion_realtime',ROOT/'app.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)

class WsClient:
    def __init__(self,base,cookie):
        host,port=base.split(':')[-2:];self.host=host.strip('/');self.port=int(port.split('/')[0]);self.sock=socket.create_connection((self.host,self.port),timeout=3);self.file=self.sock.makefile('rb')
        key=base64.b64encode(os.urandom(16)).decode();path='/ws/chat'
        request=(f'GET {path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: http://{self.host}:{self.port}\r\nCookie: {cookie}\r\n\r\n').encode();self.sock.sendall(request)
        status=self.file.readline();
        while self.file.readline() not in (b'\r\n',b''):pass
        if b'101' not in status:raise AssertionError(status)
    def send(self,obj):
        raw=json.dumps(obj).encode();n=len(raw);mask=os.urandom(4)
        head=bytes([0x81,0x80|n]) if n<126 else bytes([0x81,0xfe])+struct.pack('!H',n)
        if n>=126:head=head[:2]+struct.pack('!H',n)
        payload=bytes(b^mask[i%4] for i,b in enumerate(raw));self.sock.sendall(head+mask+payload)
    def read(self,timeout=3):
        self.sock.settimeout(timeout);b1=self.file.read(1)
        if not b1:raise EOFError()
        b2=self.file.read(1);n=b2[0]&127
        if n==126:n=struct.unpack('!H',self.file.read(2))[0]
        elif n==127:n=struct.unpack('!Q',self.file.read(8))[0]
        return json.loads(self.file.read(n))
    def close(self):
        try:self.sock.sendall(bytes([0x88,0x80])+os.urandom(4))
        except OSError:pass
        self.sock.close()

class RealtimeChat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();app.DB=Path(cls.temp.name)/'chat.db';app.Handler.sessions={};app.WS_CLIENTS={};app.WS_TYPING={};app.init()
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.base='http://127.0.0.1:%d'%cls.server.server_address[1]
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def login(self,email):
        jar=CookieJar();op=build_opener(HTTPCookieProcessor(jar));req=Request(self.base+'/api/login',data=json.dumps({'email':email,'password':'demo1234'}).encode(),headers={'Content-Type':'application/json'});op.open(req).read();sid=next(c.value for c in jar if c.name=='sid');return WsClient(self.base.replace('http://',''),f'sid={sid}')
    def test_three_profiles_receive_shared_room_without_reload(self):
        clients=[self.login(x) for x in ('salud@conexionsalud.demo','eps@conexionsalud.demo','proveedor@conexionsalud.demo')]
        try:
            for c in clients:c.send({'type':'join','room':'sala-general'})
            for c in clients:
                seen=[]
                while not any(m.get('type')=='joined' for m in seen):seen.append(c.read())
            presence=None
            for _ in range(4):
                event=clients[0].read()
                if event.get('type')=='presence' and len(event.get('users',[]))==3:presence=event;break
            self.assertIsNotNone(presence,'presence should list the three active profiles')
            clients[0].send({'type':'typing','active':True})
            typing=None
            for _ in range(4):
                event=clients[1].read()
                if event.get('type')=='presence' and event.get('typing'):typing=event;break
            self.assertIsNotNone(typing,'typing indicator should reach the other sessions')
            clients[0].send({'type':'message','client_id':'shared-test-once','body':'Mensaje en vivo desde Punto Salud','ref_type':'pedido','ref_id':'CS-2026-0142'})
            received=[]
            for c in clients:
                events=[]
                while not any(m.get('type') in ('ack','message') and (m.get('message',{}).get('client_id')=='shared-test-once') for m in events):events.append(c.read())
                received.append(next(m['message'] for m in events if m.get('message',{}).get('client_id')=='shared-test-once'))
            self.assertEqual({m['body'] for m in received},{'Mensaje en vivo desde Punto Salud'})
            self.assertTrue(all(m['ref_type']=='pedido' and m['ref_id']=='CS-2026-0142' for m in received))
            clients[0].send({'type':'message','client_id':'shared-test-once','body':'Mensaje en vivo desde Punto Salud','ref_type':'pedido','ref_id':'CS-2026-0142'})
            retries=[]
            while not any(m.get('type')=='ack' and m.get('client_id')=='shared-test-once' for m in retries):retries.append(clients[0].read())
            # The server's unique client_id constraint makes a retry idempotent.
            c=app.connect();self.assertEqual(c.execute("SELECT COUNT(*) FROM chat_messages WHERE client_id='shared-test-once'").fetchone()[0],1);c.close()
            jar=CookieJar();health=build_opener(HTTPCookieProcessor(jar));login=Request(self.base+'/api/login',data=json.dumps({'email':'salud@conexionsalud.demo','password':'demo1234'}).encode(),headers={'Content-Type':'application/json'});health.open(login).read()
            req=Request(self.base+'/api/chat/rooms',data=json.dumps({'recipient':'EPS Bienestar'}).encode(),headers={'Content-Type':'application/json'});private=json.loads(health.open(req).read())['room']
            clients[2].send({'type':'join','room':private['room_id']});denied=[]
            while not any(x.get('type')=='error' for x in denied):denied.append(clients[2].read())
            self.assertIn('acceso',denied[-1]['error'].lower())
            clients[0].send({'type':'join','room':private['room_id']});events=[]
            while not any(x.get('type')=='joined' and x.get('room')==private['room_id'] for x in events):events.append(clients[0].read())
            clients[0].send({'type':'message','client_id':'private-test-once','body':'Mensaje privado entre Punto Salud y EPS'})
            sender_events=[]
            while not any(x.get('type')=='ack' and x.get('client_id')=='private-test-once' for x in sender_events):sender_events.append(clients[0].read())
            recipient_events=[]
            while not any(x.get('type')=='message' and x.get('message',{}).get('client_id')=='private-test-once' for x in recipient_events):recipient_events.append(clients[1].read())
            # The EPS session receives its private-room notification while still viewing the group room.
            clients[1].send({'type':'join','room':private['room_id']});joined=[]
            while not any(x.get('type')=='joined' and x.get('room')==private['room_id'] for x in joined):joined.append(clients[1].read())
        finally:
            for c in clients:c.close()

if __name__=='__main__':unittest.main(verbosity=2)
