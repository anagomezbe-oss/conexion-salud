from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import sqlite3, json, secrets, hashlib, hmac, os, datetime, base64, struct, threading, time
from http.cookies import SimpleCookie

ROOT = Path(__file__).parent
DB = Path(os.environ.get('CS_DB', ROOT / 'conexion_salud.db'))
ROLES = ('punto_salud','proveedor','eps','admin')
NOW = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
WS_LOCK=threading.RLock(); WS_CLIENTS={}; WS_PRESENCE={}; WS_TYPING={}
def password_hash(value):
    salt=secrets.token_bytes(16);return salt.hex()+'$'+hashlib.pbkdf2_hmac('sha256',value.encode(),salt,240000).hex()
def password_check(value,stored):
    try:
        salt,expected=stored.split('$',1);actual=hashlib.pbkdf2_hmac('sha256',value.encode(),bytes.fromhex(salt),240000).hex();return hmac.compare_digest(actual,expected)
    except ValueError:return False

def connect():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def init():
    c=connect(); c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, password TEXT, role TEXT, entity TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS inventory(id INTEGER PRIMARY KEY, name TEXT, code TEXT UNIQUE, presentation TEXT, supplier TEXT, qty INTEGER CHECK(qty>=0), minimum INTEGER CHECK(minimum>=0), updated TEXT);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, code TEXT UNIQUE, requester TEXT, provider TEXT, eps TEXT, status TEXT, eta TEXT, notes TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE, medicine TEXT, qty INTEGER CHECK(qty>0));
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), status TEXT, note TEXT, actor TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, kind TEXT, priority TEXT, title TEXT, description TEXT, entity TEXT, created TEXT, read INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, supplier TEXT, invoice TEXT, issued TEXT, due TEXT, amount INTEGER, status TEXT, paid TEXT);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, sender TEXT, recipient TEXT, body TEXT, at TEXT, conversation TEXT);
    CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY, medicine TEXT, delta INTEGER, note TEXT, actor TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS chat_rooms(room_id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, entities TEXT NOT NULL, ref_type TEXT, ref_id TEXT, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS chat_messages(id INTEGER PRIMARY KEY, room_id TEXT NOT NULL REFERENCES chat_rooms(room_id), client_id TEXT UNIQUE, sender TEXT NOT NULL, entity TEXT NOT NULL, body TEXT NOT NULL, at TEXT NOT NULL, ref_type TEXT, ref_id TEXT, attachment_name TEXT, attachment_type TEXT, attachment_data TEXT);
    CREATE TABLE IF NOT EXISTS chat_reads(room_id TEXT NOT NULL, user_email TEXT NOT NULL, message_id INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL, PRIMARY KEY(room_id,user_email));
    ''')
    cols={r['name'] for r in c.execute('PRAGMA table_info(chat_messages)')}
    if 'client_id' not in cols:c.execute('ALTER TABLE chat_messages ADD COLUMN client_id TEXT')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_client_id ON chat_messages(client_id)')
    if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
        # Demonstration passwords are intentionally local-only and documented in README.
        for n,e,r,ent in [('Laura Gómez','salud@conexionsalud.demo','punto_salud','Punto Salud'),('Carlos Ruiz','proveedor@conexionsalud.demo','proveedor','MediSupply'),('Ana Torres','eps@conexionsalud.demo','eps','EPS Bienestar'),('Administración','admin@conexionsalud.demo','admin','Conexión Salud')]:
            c.execute('INSERT INTO users(name,email,password,role,entity) VALUES(?,?,?,?,?)',(n,e,password_hash('demo1234'),r,ent))
        meds=[('Acetaminofén 500 mg','MED-001','Caja x 100 tabletas','MediSupply',240,80),('Amoxicilina 500 mg','MED-002','Caja x 50 cápsulas','FarmaRed',34,40),('Losartán 50 mg','MED-003','Caja x 30 tabletas','MediSupply',0,30),('Metformina 850 mg','MED-004','Caja x 60 tabletas','FarmaRed',125,40),('Omeprazol 20 mg','MED-005','Caja x 30 cápsulas','BioFarma',18,25),('Loratadina 10 mg','MED-006','Caja x 100 tabletas','BioFarma',90,25)]
        for m in meds:c.execute('INSERT INTO inventory(name,code,presentation,supplier,qty,minimum,updated) VALUES(?,?,?,?,?,?,?)',(*m,NOW()))
        orders=[('CS-2026-0142','Punto Salud','MediSupply','EPS Bienestar','En tránsito','2026-11-21','Despacho confirmado'),('CS-2026-0141','Punto Salud','FarmaRed','EPS Bienestar','Retrasado','2026-11-20','Demora logística en centro de distribución'),('CS-2026-0140','Punto Salud','BioFarma','EPS Nueva Vida','Entregado','2026-11-16','Entrega recibida correctamente'),('CS-2026-0139','Punto Salud','MediSupply','EPS Bienestar','Pendiente','2026-11-24','Pedido prioritario')]
        for o in orders:
            cur=c.execute('INSERT INTO orders(code,requester,provider,eps,status,eta,notes,created) VALUES(?,?,?,?,?,?,?,?)',(*o,NOW())); oid=cur.lastrowid
            c.execute('INSERT INTO order_items(order_id,medicine,qty) VALUES(?,?,?)',(oid,'Acetaminofén 500 mg',60))
            c.execute('INSERT INTO events(order_id,status,note,actor,at) VALUES(?,?,?,?,?)',(oid,'Pendiente','Pedido registrado',o[0],NOW()))
            if o[4]!='Pendiente':c.execute('INSERT INTO events(order_id,status,note,actor,at) VALUES(?,?,?,?,?)',(oid,o[4],o[5],o[0],NOW()))
        for a in [('Inventario','Crítica','Medicamento agotado','Losartán 50 mg llegó a cero unidades.','MediSupply'),('Inventario','Media','Stock bajo','Amoxicilina 500 mg está por debajo del mínimo.','FarmaRed'),('Pedido','Alta','Pedido con retraso','CS-2026-0141 · FarmaRed reportó demora logística.','FarmaRed')]: c.execute('INSERT INTO alerts(kind,priority,title,description,entity,created) VALUES(?,?,?,?,?,?)',(*a,NOW()))
        for p in [('MediSupply','FAC-2026-081','2026-11-01','2026-11-30',4850000,'Pendiente'),('FarmaRed','FAC-2026-077','2026-10-20','2026-11-15',2130000,'Vencido'),('BioFarma','FAC-2026-069','2026-10-12','2026-11-12',1760000,'Pagado')]:c.execute('INSERT INTO payments(supplier,invoice,issued,due,amount,status,paid) VALUES(?,?,?,?,?,?,?)',(*p,NOW() if p[-1]=='Pagado' else None))
        c.execute('INSERT INTO messages(sender,recipient,body,at,conversation) VALUES(?,?,?,?,?)',('Carlos Ruiz','Punto Salud','Confirmamos el despacho del pedido CS-2026-0142. En tránsito hacia el punto de entrega.','2026-11-18T10:20:00+00:00','MediSupply'))
    if not c.execute('SELECT 1 FROM chat_rooms LIMIT 1').fetchone():
        c.execute('INSERT INTO chat_rooms(room_id,kind,title,entities,created) VALUES(?,?,?,?,?)',('sala-general','group','Sala general','["Punto Salud","EPS Bienestar","MediSupply","FarmaRed","BioFarma","EPS Nueva Vida"]',NOW()))
    if not c.execute('SELECT 1 FROM chat_messages LIMIT 1').fetchone():
        c.execute('INSERT INTO chat_messages(room_id,sender,entity,body,at) VALUES(?,?,?,?,?)',('sala-general','Carlos Ruiz','MediSupply','Bienvenidos al canal de coordinación. Usemos este espacio para novedades operativas entre las entidades.','2026-11-18T10:20:00+00:00'))
    c.commit();c.close()

def rowdict(r):return dict(r) if r else None
class Handler(BaseHTTPRequestHandler):
    sessions={}
    protocol_version='HTTP/1.1'
    def log_message(self,*a):pass
    def send(self,code,data,ctype='application/json; charset=utf-8'):
        raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' https://cdn.jsdelivr.net; connect-src 'self' https://*.supabase.co wss://*.supabase.co ws://127.0.0.1:*; img-src 'self' data: blob:");self.end_headers();self.wfile.write(raw)
    def body(self):
        n=int(self.headers.get('Content-Length',0));return json.loads(self.rfile.read(n) or b'{}')
    def user(self):
        cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''));sid=cookie.get('sid')
        return self.sessions.get(sid.value) if sid else None
    @staticmethod
    def can_access_room(user,room):return bool(user and room and (user['role']=='admin' or user['entity'] in json.loads(room['entities'])))
    def ws_send(self,obj):
        raw=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode('utf-8');n=len(raw)
        if n<126:head=bytes([0x81,n])
        elif n<65536:head=bytes([0x81,126])+struct.pack('!H',n)
        else:head=bytes([0x81,127])+struct.pack('!Q',n)
        try:
            with self.ws_lock:self.wfile.write(head+raw);self.wfile.flush()
            return True
        except (OSError,ValueError):return False
    def ws_frame(self):
        first=self.rfile.read(2)
        if len(first)<2:return None,None
        b1,b2=first;opcode=b1&15;length=b2&127;masked=bool(b2&128)
        if length==126:
            x=self.rfile.read(2)
            if len(x)!=2:return None,None
            length=struct.unpack('!H',x)[0]
        elif length==127:
            x=self.rfile.read(8)
            if len(x)!=8:return None,None
            length=struct.unpack('!Q',x)[0]
        if length>2_500_000 or not masked:return None,None
        mask=self.rfile.read(4);payload=self.rfile.read(length)
        if len(payload)!=length:return None,None
        if masked:payload=bytes(v^mask[i%4] for i,v in enumerate(payload))
        return opcode,payload
    @classmethod
    def broadcast(cls,room_id,payload,exclude=None):
        with WS_LOCK:clients=list(WS_CLIENTS.get(room_id,[]))
        for cl in clients:
            if cl is not exclude:cl.ws_send(payload)
    @classmethod
    def notify_room_participants(cls,entities,payload,exclude_room=None,exclude=None):
        with WS_LOCK:clients=list({id(cl):cl for group in WS_CLIENTS.values() for cl in group}.values())
        for cl in clients:
            if cl is not exclude and cl.ws_room!=exclude_room and (cl.ws_user['role']=='admin' or cl.ws_user['entity'] in entities):cl.ws_send(payload)
    @classmethod
    def presence(cls,room_id):
        with WS_LOCK:
            seen={}
            for cl in WS_CLIENTS.get(room_id,[]):seen[cl.ws_user['email']]={'name':cl.ws_user['name'],'entity':cl.ws_user['entity'],'role':cl.ws_user['role']}
            typing=[{'name':item[0].ws_user['name'],'entity':item[0].ws_user['entity']} for item in WS_TYPING.get(room_id,{}).values() if time.time()-item[1]<5]
        cls.broadcast(room_id,{'type':'presence','users':list(seen.values()),'typing':typing})
    def websocket(self):
        u=self.user()
        if not u:return self.send(401,{'error':'Inicia sesión para continuar.'})
        if self.headers.get('Upgrade','').lower()!='websocket' or not self.headers.get('Sec-WebSocket-Key'):return self.send(400,{'error':'Handshake WebSocket inválido.'})
        origin=self.headers.get('Origin','');host=self.headers.get('Host','')
        if origin and urlparse(origin).netloc!=host:return self.send(403,{'error':'Origen no autorizado.'})
        accept=base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101,'Switching Protocols');self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers();self.close_connection=True
        self.ws_user=u;self.ws_lock=threading.Lock();self.ws_room=None
        try:
            while True:
                opcode,payload=self.ws_frame()
                if opcode is None or opcode==8:break
                if opcode==9:
                    self.wfile.write(bytes([0x8a,len(payload)])+payload);self.wfile.flush();continue
                if opcode!=1:continue
                try:msg=json.loads(payload.decode('utf-8'))
                except (ValueError,UnicodeDecodeError):self.ws_send({'type':'error','error':'Mensaje inválido.'});continue
                action=msg.get('type')
                if action=='join':
                    room_id=str(msg.get('room',''))
                    c=connect();room=c.execute('SELECT * FROM chat_rooms WHERE room_id=?',(room_id,)).fetchone();c.close()
                    if not room or not self.can_access_room(u,room):self.ws_send({'type':'error','error':'No tienes acceso a esta conversación.'});continue
                    old=self.ws_room
                    if old:
                        with WS_LOCK:
                            WS_CLIENTS[old]=[x for x in WS_CLIENTS.get(old,[]) if x is not self]
                            WS_TYPING.get(old,{}).pop(u['email'],None)
                    self.ws_room=room_id
                    with WS_LOCK:WS_CLIENTS.setdefault(room_id,[]).append(self)
                    self.ws_send({'type':'joined','room':room_id});self.presence(room_id)
                    if old:self.presence(old)
                    continue
                room_id=self.ws_room
                if not room_id:self.ws_send({'type':'error','error':'Selecciona una conversación primero.'});continue
                if action=='heartbeat':self.ws_send({'type':'heartbeat','at':NOW()});continue
                if action=='typing':
                    with WS_LOCK:
                        if msg.get('active',True):WS_TYPING.setdefault(room_id,{})[u['email']]=(self,time.time())
                        else:WS_TYPING.get(room_id,{}).pop(u['email'],None)
                    self.presence(room_id);continue
                if action=='read':
                    try:mid=max(0,int(msg.get('message_id',0)))
                    except (ValueError,TypeError):self.ws_send({'type':'error','error':'Confirmación de lectura inválida.'});continue
                    c=connect();mid=min(mid,c.execute('SELECT COALESCE(MAX(id),0) FROM chat_messages WHERE room_id=?',(room_id,)).fetchone()[0]);c.execute('INSERT INTO chat_reads(room_id,user_email,message_id,at) VALUES(?,?,?,?) ON CONFLICT(room_id,user_email) DO UPDATE SET message_id=MAX(message_id,excluded.message_id),at=excluded.at',(room_id,u['email'],mid,NOW()));c.commit();c.close();self.broadcast(room_id,{'type':'read','room':room_id,'email':u['email'],'name':u['name'],'message_id':mid},self);continue
                if action=='message':
                    body=str(msg.get('body','')).strip();attachment=msg.get('attachment') or {};client_id=str(msg.get('client_id') or secrets.token_urlsafe(18))[:100]
                    if not body and not attachment:self.ws_send({'type':'error','client_id':client_id,'error':'Escribe un mensaje o adjunta un archivo.'});continue
                    if len(body)>5000:self.ws_send({'type':'error','client_id':client_id,'error':'El mensaje supera los 5.000 caracteres.'});continue
                    raw=attachment.get('data','');
                    if raw and len(raw)>2_800_000:self.ws_send({'type':'error','client_id':client_id,'error':'El archivo supera el límite de 2 MB.'});continue
                    ref_type=msg.get('ref_type');ref_id=str(msg.get('ref_id') or '')[:80]
                    if ref_type and ref_type not in ('pedido','inventario','entrega','alerta'):self.ws_send({'type':'error','client_id':client_id,'error':'Referencia de operación no válida.'});continue
                    c=connect();room=c.execute('SELECT * FROM chat_rooms WHERE room_id=?',(room_id,)).fetchone()
                    if not self.can_access_room(u,room):c.close();self.ws_send({'type':'error','client_id':client_id,'error':'Acceso denegado.'});continue
                    if ref_type=='pedido' and not c.execute('SELECT 1 FROM orders WHERE code=?',(ref_id,)).fetchone():c.close();self.ws_send({'type':'error','client_id':client_id,'error':'No se encuentra el pedido relacionado.'});continue
                    if ref_type=='entrega' and not c.execute("SELECT 1 FROM orders WHERE code=? AND status IN ('Despachado','En tránsito','Entregado','Retrasado')",(ref_id,)).fetchone():c.close();self.ws_send({'type':'error','client_id':client_id,'error':'No se encuentra la entrega relacionada.'});continue
                    if ref_type=='inventario' and not c.execute('SELECT 1 FROM inventory WHERE name=? OR code=?',(ref_id,ref_id)).fetchone():c.close();self.ws_send({'type':'error','client_id':client_id,'error':'No se encuentra el medicamento relacionado.'});continue
                    if ref_type=='alerta' and not c.execute('SELECT 1 FROM alerts WHERE title=? OR CAST(id AS TEXT)=?',(ref_id,ref_id)).fetchone():c.close();self.ws_send({'type':'error','client_id':client_id,'error':'No se encuentra la alerta relacionada.'});continue
                    prior=c.execute('SELECT * FROM chat_messages WHERE client_id=?',(client_id,)).fetchone()
                    if prior:
                        if prior['room_id']!=room_id or prior['sender']!=u['name']:
                            c.close();self.ws_send({'type':'error','client_id':client_id,'error':'Identificador de mensaje duplicado.'});continue
                        row=rowdict(c.execute('SELECT id,room_id,client_id,sender,entity,body,at,ref_type,ref_id,attachment_name,attachment_type FROM chat_messages WHERE id=?',(prior['id'],)).fetchone());c.close();self.ws_send({'type':'ack','client_id':client_id,'message':row});continue
                    c.execute('INSERT OR IGNORE INTO chat_messages(room_id,client_id,sender,entity,body,at,ref_type,ref_id,attachment_name,attachment_type,attachment_data) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(room_id,client_id,u['name'],u['entity'],body,NOW(),ref_type,ref_id or None,attachment.get('name'),attachment.get('type'),raw or None));c.commit()
                    row=rowdict(c.execute('SELECT id,room_id,client_id,sender,entity,body,at,ref_type,ref_id,attachment_name,attachment_type FROM chat_messages WHERE client_id=?',(client_id,)).fetchone());c.close()
                    self.ws_send({'type':'ack','client_id':client_id,'message':row})
                    self.broadcast(room_id,{'type':'message','message':row},self)
                    self.notify_room_participants(json.loads(room['entities']),{'type':'message','message':row},exclude_room=room_id,exclude=self)
                    continue
                self.ws_send({'type':'error','error':'Evento no reconocido.'})
        except (OSError,ValueError,ConnectionError):pass
        finally:
            if self.ws_room:
                with WS_LOCK:
                    WS_CLIENTS[self.ws_room]=[x for x in WS_CLIENTS.get(self.ws_room,[]) if x is not self]
                    WS_TYPING.get(self.ws_room,{}).pop(u['email'],None)
                self.presence(self.ws_room)
    def api(self,method,path):
        parsed=urlparse(path);path=parsed.path;query=parse_qs(parsed.query)
        u=self.user(); c=connect()
        try:
            if path=='/api/login' and method=='POST':
                d=self.body(); r=c.execute('SELECT * FROM users WHERE email=? AND active=1',(d.get('email','').lower(),)).fetchone()
                if not r or not password_check(d.get('password',''),r['password']):return self.send(401,{'error':'Correo o contraseña incorrectos.'})
                sid=secrets.token_urlsafe(32);self.sessions[sid]=rowdict(r);self.send(200,{'user':{k:r[k] for k in ('name','email','role','entity')}},headers=None) if False else None
                self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Set-Cookie',f'sid={sid}; HttpOnly; SameSite=Strict; Path=/');raw=json.dumps({'user':{k:r[k] for k in ('name','email','role','entity')}},ensure_ascii=False).encode();self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
            if path=='/api/logout':
                cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''));sid=cookie.get('sid')
                if sid:self.sessions.pop(sid.value,None)
                self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Set-Cookie','sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0');self.send_header('Content-Length','11');self.end_headers();self.wfile.write(b'{"ok":true}');return
            if path=='/api/me':
                return self.send(200,{'user':{k:u[k] for k in ('name','email','role','entity')}} if u else {'user':None})
            if not u:return self.send(401,{'error':'Inicia sesión para continuar.'})
            role=u['role']; admin=role=='admin'
            d=self.body() if method=='POST' else {}
            def deny():return self.send(403,{'error':'Tu perfil no tiene permiso para esta operación.'})
            if method=='GET' and path=='/api/data':
                where='';args=[]
                if role=='proveedor':where=' WHERE provider=?';args=[u['entity']]
                elif role=='eps':where=' WHERE eps=?';args=[u['entity']]
                orders=[rowdict(x) for x in c.execute('SELECT * FROM orders'+where+' ORDER BY id DESC',args)]
                for o in orders:o['items']=[rowdict(x) for x in c.execute('SELECT medicine,qty FROM order_items WHERE order_id=?',(o['id'],))]
                invq='SELECT * FROM inventory ORDER BY name'; invargs=[]
                if role=='proveedor':invq='SELECT * FROM inventory WHERE supplier=? ORDER BY name';invargs=[u['entity']]
                inv=[rowdict(x) for x in c.execute(invq,invargs)]
                alertsq='SELECT * FROM alerts ORDER BY id DESC'; alertargs=[]
                if role=='punto_salud':alertsq='SELECT * FROM alerts ORDER BY id DESC'
                elif not admin:alertsq='SELECT * FROM alerts WHERE entity IN (?,?) ORDER BY id DESC';alertargs=[u['entity'],'Punto Salud']
                alerts=[rowdict(x) for x in c.execute(alertsq,alertargs)]
                msgq='SELECT * FROM messages WHERE sender=? OR recipient=? ORDER BY id';msgargs=[u['name'],u['entity']]
                if role=='admin':msgq='SELECT * FROM messages ORDER BY id';msgargs=[]
                messages=[rowdict(x) for x in c.execute(msgq,msgargs)]
                room_rows=c.execute('SELECT * FROM chat_rooms ORDER BY created DESC').fetchall()
                rooms=[]
                for rr in room_rows:
                    if admin or u['entity'] in json.loads(rr['entities']):
                        room=rowdict(rr);room['entities']=json.loads(room['entities']);room['unread']=c.execute('SELECT COUNT(*) FROM chat_messages m WHERE m.room_id=? AND m.id>COALESCE((SELECT message_id FROM chat_reads WHERE room_id=? AND user_email=?),0) AND m.sender<>?',(room['room_id'],room['room_id'],u['email'],u['name'])).fetchone()[0];room['last_message']=rowdict(c.execute('SELECT id,sender,body,at FROM chat_messages WHERE room_id=? ORDER BY id DESC LIMIT 1',(room['room_id'],)).fetchone());rooms.append(room)
                room_ids=[r['room_id'] for r in rooms]
                chat=[]
                if room_ids:
                    marks=','.join('?' for _ in room_ids)
                    chat=[rowdict(x) for x in c.execute(f'SELECT id,room_id,client_id,sender,entity,body,at,ref_type,ref_id,attachment_name,attachment_type FROM chat_messages WHERE room_id IN ({marks}) ORDER BY id',(room_ids))]
                    for cm in chat:
                        rr=next(r for r in rooms if r['room_id']==cm['room_id']);entities=rr['entities'];emarks=','.join('?' for _ in entities)
                        cm['read_by']=c.execute(f'SELECT COUNT(DISTINCT cr.user_email) FROM chat_reads cr JOIN users ru ON ru.email=cr.user_email WHERE cr.room_id=? AND cr.message_id>=? AND ru.entity IN ({emarks}) AND cr.user_email<>?',(cm['room_id'],cm['id'],*entities,u['email'])).fetchone()[0]
                payq='SELECT * FROM payments ORDER BY id DESC';payargs=[]
                if role=='proveedor':payq='SELECT * FROM payments WHERE supplier=? ORDER BY id DESC';payargs=[u['entity']]
                pays=[rowdict(x) for x in c.execute(payq,payargs)]
                for o in orders:o['events']=[rowdict(x) for x in c.execute('SELECT status,note,actor,at FROM events WHERE order_id=? ORDER BY id',(o['id'],))]
                users=[{k:r[k] for k in ('id','name','email','role','entity','active')} for r in c.execute('SELECT * FROM users')] if admin else []
                return self.send(200,{'orders':orders,'inventory':inv,'alerts':alerts,'messages':messages,'chatRooms':rooms,'chatMessages':chat,'payments':pays,'users':users,'user':{k:u[k] for k in ('name','email','role','entity')}})
            if method=='GET' and path=='/api/chat/history':
                room_id=(query.get('room') or [''])[0];room=c.execute('SELECT * FROM chat_rooms WHERE room_id=?',(room_id,)).fetchone()
                if not room:return self.send(404,{'error':'Conversación no encontrada.'})
                if not self.can_access_room(u,room):return deny()
                try:limit=max(1,min(300,int((query.get('limit') or ['150'])[0])))
                except ValueError:limit=150
                msgs=[rowdict(x) for x in c.execute('SELECT id,room_id,client_id,sender,entity,body,at,ref_type,ref_id,attachment_name,attachment_type FROM chat_messages WHERE room_id=? ORDER BY id DESC LIMIT ?',(room_id,limit))][::-1]
                return self.send(200,{'room':room_id,'messages':msgs})
            if method=='POST' and path=='/api/chat/rooms':
                recipient=d.get('recipient','').strip()
                if not recipient or recipient==u['entity'] or len(recipient)>80:return self.send(400,{'error':'Selecciona otra entidad para el chat privado.'})
                known={r['entity'] for r in c.execute('SELECT DISTINCT entity FROM users WHERE active=1')}
                if recipient not in known:return self.send(400,{'error':'Entidad no disponible.'})
                entities=sorted(set([u['entity'],recipient]));room_id='direct:'+base64.urlsafe_b64encode('|'.join(entities).encode()).decode().rstrip('=')
                c.execute('INSERT OR IGNORE INTO chat_rooms(room_id,kind,title,entities,created) VALUES(?,?,?,?,?)',(room_id,'direct',recipient,json.dumps(entities),NOW()))
                room=rowdict(c.execute('SELECT * FROM chat_rooms WHERE room_id=?',(room_id,)).fetchone());room['entities']=json.loads(room['entities']);self.notify_room_participants(entities,{'type':'room_created','room':room,'created_by':u['email']});self.send(201,{'room':room});return
            if method=='POST' and path=='/api/chat/read':
                room_id=d.get('room','');room=c.execute('SELECT * FROM chat_rooms WHERE room_id=?',(room_id,)).fetchone()
                if not room or not self.can_access_room(u,room):return deny()
                mid=max(0,int(d.get('message_id',0)));mid=min(mid,c.execute('SELECT COALESCE(MAX(id),0) FROM chat_messages WHERE room_id=?',(room_id,)).fetchone()[0]);c.execute('INSERT INTO chat_reads(room_id,user_email,message_id,at) VALUES(?,?,?,?) ON CONFLICT(room_id,user_email) DO UPDATE SET message_id=MAX(message_id,excluded.message_id),at=excluded.at',(room_id,u['email'],mid,NOW()));self.send(200,{'ok':True});return
            if method=='GET' and path.startswith('/api/chat/file/'):
                mid=int(path.rsplit('/',1)[1]);m=c.execute('SELECT * FROM chat_messages WHERE id=?',(mid,)).fetchone();room=c.execute('SELECT * FROM chat_rooms WHERE room_id=?',(m['room_id'],)).fetchone() if m else None
                if not m or not self.can_access_room(u,room):return deny()
                raw=base64.b64decode(m['attachment_data']);self.send_response(200);self.send_header('Content-Type',m['attachment_type'] or 'application/octet-stream');self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+__import__('urllib.parse').parse.quote(m['attachment_name'] or 'archivo'));self.send_header('Content-Length',str(len(raw)));self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw);return
            if method=='POST' and path=='/api/orders':
                if role not in ('punto_salud','admin'):return deny()
                items=d.get('items',[])
                if not items or not d.get('provider'):return self.send(400,{'error':'Agrega medicamentos y selecciona un proveedor.'})
                try:
                    for it in items:
                        if int(it['qty'])<1 or not c.execute('SELECT 1 FROM inventory WHERE id=?',(it['id'],)).fetchone():raise ValueError()
                except (ValueError,KeyError,TypeError):return self.send(400,{'error':'Medicamento o cantidad inválidos.'})
                code='CS-'+datetime.datetime.now().strftime('%Y')+'-'+str(int(NOW()[-8:-6]) if False else secrets.randbelow(8000)+2000)
                cur=c.execute('INSERT INTO orders(code,requester,provider,eps,status,eta,notes,created) VALUES(?,?,?,?,?,?,?,?)',(code,u['entity'],d['provider'],d.get('eps','EPS Bienestar'),'Pendiente',d.get('eta',''),d.get('notes',''),NOW()));oid=cur.lastrowid
                for it in items:
                    q=int(it['qty']);m=c.execute('SELECT name FROM inventory WHERE id=?',(it['id'],)).fetchone()
                    if q<1 or not m:return self.send(400,{'error':'Medicamento o cantidad inválidos.'})
                    c.execute('INSERT INTO order_items(order_id,medicine,qty) VALUES(?,?,?)',(oid,m['name'],q))
                c.execute('INSERT INTO events(order_id,status,note,actor,at) VALUES(?,?,?,?,?)',(oid,'Pendiente','Pedido creado',u['name'],NOW()));self.send(201,{'ok':True,'code':code});return
            if method=='POST' and path.startswith('/api/orders/'):
                oid=int(path.split('/')[3]);o=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
                if not o:return self.send(404,{'error':'Pedido no encontrado.'})
                if not admin and ((role=='proveedor' and o['provider']!=u['entity']) or (role=='eps' and o['eps']!=u['entity']) or (role=='punto_salud' and o['requester']!=u['entity'])):return deny()
                status=d.get('status');valid={'Pendiente':['Confirmado','Cancelado'],'Confirmado':['En preparación','Retrasado','Cancelado'],'En preparación':['Despachado','Retrasado'],'Despachado':['En tránsito','Entregado','Retrasado'],'En tránsito':['Entregado','Retrasado'],'Retrasado':['Confirmado','En preparación','Despachado','En tránsito','Entregado'],'Entregado':[],'Cancelado':[]}
                if status not in valid.get(o['status'],[]):return self.send(400,{'error':'Transición de estado no válida.'})
                if role=='eps':return deny()
                if role=='proveedor' and (o['provider']!=u['entity'] or status in ('Entregado','Cancelado')):return deny()
                if role=='punto_salud' and status not in ('Entregado','Cancelado'):return deny()
                c.execute('UPDATE orders SET status=?,eta=COALESCE(NULLIF(?,\'\'),eta),notes=COALESCE(NULLIF(?,\'\'),notes) WHERE id=?',(status,d.get('eta',''),d.get('notes',''),oid));c.execute('INSERT INTO events(order_id,status,note,actor,at) VALUES(?,?,?,?,?)',(oid,status,d.get('notes',''),u['name'],NOW()))
                c.execute('INSERT INTO alerts(kind,priority,title,description,entity,created) VALUES(?,?,?,?,?,?)',('Pedido','Alta' if status=='Retrasado' else 'Media','Pedido actualizado',o['code']+' · '+status,u['entity'],NOW()))
                self.send(200,{'ok':True});return
            if method=='POST' and path=='/api/inventory':
                if role not in ('proveedor','admin'):return deny()
                q=int(d.get('qty',-1));mn=int(d.get('minimum',0))
                if q<0 or mn<0:return self.send(400,{'error':'Las cantidades no pueden ser negativas.'})
                if d.get('id'):
                    existing=c.execute('SELECT * FROM inventory WHERE id=?',(d['id'],)).fetchone()
                    if not existing:return self.send(404,{'error':'Medicamento no encontrado.'})
                    if not admin and existing['supplier']!=u['entity']:return deny()
                    c.execute('UPDATE inventory SET qty=?,minimum=?,updated=? WHERE id=?',(q,mn,NOW(),d['id']))
                else:
                    if not d.get('name') or not d.get('code'):return self.send(400,{'error':'Nombre y código son obligatorios.'})
                    c.execute('INSERT INTO inventory(name,code,presentation,supplier,qty,minimum,updated) VALUES(?,?,?,?,?,?,?)',(d['name'],d['code'],d.get('presentation','Unidad'),d.get('supplier',u['entity']) if admin else u['entity'],q,mn,NOW()))
                name=d.get('name') or (c.execute('SELECT name FROM inventory WHERE id=?',(d['id'],)).fetchone() or {'name':'Medicamento'})['name']
                if q<=mn:c.execute('INSERT INTO alerts(kind,priority,title,description,entity,created) VALUES(?,?,?,?,?,?)',('Inventario','Crítica' if q==0 else 'Media','Alerta de inventario',f'{name}: {q} unidades disponibles.',u['entity'],NOW()))
                c.execute('INSERT INTO movements(medicine,delta,note,actor,at) VALUES(?,?,?,?,?)',(name,q,'Actualización de existencia',u['name'],NOW()));self.send(200,{'ok':True});return
            if method=='POST' and path.startswith('/api/alerts/'):
                aid=int(path.rsplit('/',1)[1]);a=c.execute('SELECT entity FROM alerts WHERE id=?',(aid,)).fetchone()
                if not a:return self.send(404,{'error':'Alerta no encontrada.'})
                if not admin and role!='punto_salud' and a['entity'] not in (u['entity'],'Punto Salud'):return deny()
                c.execute('UPDATE alerts SET read=1 WHERE id=?',(aid,));self.send(200,{'ok':True});return
            if method=='POST' and path=='/api/messages':
                if role=='eps':recipient='Punto Salud'
                else:recipient=d.get('recipient','Punto Salud')
                body=d.get('body','').strip()
                if not body:return self.send(400,{'error':'Escribe un mensaje antes de enviar.'})
                if role=='proveedor' and recipient not in ('Punto Salud','EPS Bienestar','EPS Nueva Vida'):return deny()
                if role=='punto_salud' and recipient not in ('MediSupply','FarmaRed','BioFarma','EPS Bienestar','EPS Nueva Vida'):return deny()
                c.execute('INSERT INTO messages(sender,recipient,body,at,conversation) VALUES(?,?,?,?,?)',(u['name'],recipient,body,NOW(),d.get('conversation','Punto Salud')));self.send(201,{'ok':True});return
            if method=='POST' and path.startswith('/api/payments/'):
                if role not in ('admin','punto_salud'):return deny()
                status=d.get('status');
                if status not in ('Pendiente','En revisión','Pagado','Vencido'):return self.send(400,{'error':'Estado de pago inválido.'})
                c.execute('UPDATE payments SET status=?,paid=? WHERE id=?',(status,NOW() if status=='Pagado' else None,int(path.rsplit('/',1)[1])));self.send(200,{'ok':True});return
            if method=='POST' and path=='/api/users':
                if not admin:return deny()
                if d.get('role') not in ROLES or not d.get('email') or not d.get('name'):return self.send(400,{'error':'Completa nombre, correo y perfil.'})
                entity=d.get('entity','Conexión Salud');c.execute('INSERT INTO users(name,email,password,role,entity) VALUES(?,?,?,?,?)',(d['name'],d['email'].lower(),password_hash(d.get('password','demo1234')),d['role'],entity))
                group=c.execute("SELECT entities FROM chat_rooms WHERE room_id='sala-general'").fetchone()
                if group:
                    entities=json.loads(group['entities'])
                    if entity not in entities:entities.append(entity);c.execute("UPDATE chat_rooms SET entities=? WHERE room_id='sala-general'",(json.dumps(entities),))
                self.send(201,{'ok':True});return
            if method=='POST' and path.startswith('/api/users/'):
                if not admin:return deny()
                uid=int(path.rsplit('/',1)[1]);c.execute('UPDATE users SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?',(uid,));self.send(200,{'ok':True});return
            if method=='GET' and path=='/api/export':
                if role not in ('admin','punto_salud'):return deny()
                rows=c.execute('SELECT code,requester,provider,eps,status,eta,created FROM orders').fetchall();import csv,io
                b=io.StringIO();w=csv.writer(b);w.writerow(rows[0].keys() if rows else ['pedido','solicitante','proveedor','eps','estado','entrega','creado']);w.writerows([tuple(x) for x in rows]);return self.send(200,b.getvalue().encode(), 'text/csv; charset=utf-8')
            return self.send(404,{'error':'Ruta no encontrada.'})
        except (ValueError,KeyError,sqlite3.IntegrityError) as ex:return self.send(400,{'error':'No se pudo guardar: revisa los datos.'})
        finally:c.commit();c.close()
    def do_GET(self):
        p=urlparse(self.path).path
        if p=='/ws/chat':return self.websocket()
        if p.startswith('/api/chat/'):return self.api('GET',self.path)
        if p=='/api/config':
            url=os.environ.get('SUPABASE_URL','');key=os.environ.get('SUPABASE_PUBLISHABLE_KEY',os.environ.get('SUPABASE_ANON_KEY',''))
            return self.send(200,{'supabaseUrl':url,'supabasePublishableKey':key,'mode':'supabase' if url and key else 'local'})
        if p.startswith('/api/'):return self.api('GET',p)
        file=ROOT/('index.html' if p=='/' else p.lstrip('/'))
        try:
            if file.resolve().parent!=ROOT.resolve() or not file.is_file():return self.send(404,b'Not found','text/plain')
            mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}.get(file.suffix,'application/octet-stream')
            self.send(200,file.read_bytes(),mime)
        except:return self.send(404,b'Not found','text/plain')
    def do_POST(self):self.api('POST',self.path)

if __name__=='__main__':
    init();port=int(os.environ.get('PORT','8000'));print(f'Conexión Salud: http://127.0.0.1:{port}');ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
