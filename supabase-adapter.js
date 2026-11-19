const roleLabels={punto_salud:'Punto Salud',proveedor:'Proveedor',eps:'EPS',admin:'Administrador'};
const fail=error=>{if(error)throw new Error(error.message||String(error));};

export async function createSupabaseAdapter(url,key){
  const {createClient}=await import('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.91.0/+esm');
  const db=createClient(url,key,{auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true},realtime:{params:{eventsPerSecond:10}}});
  let profile=null,rooms=[],channels=new Map(),activeRoom=null,userChannel=null;
  const roomChannels=new Map();
  const orgName=new Map();

  const profileFor=async()=>{
    const {data:{user:authUser},error}=await db.auth.getUser();fail(error);
    if(!authUser)return null;
    const {data,error:pe}=await db.from('profiles').select('*').eq('id',authUser.id).single();fail(pe);
    if(!data?.active){await db.auth.signOut();throw new Error('Tu cuenta está desactivada.');}
    profile={...data,entity:orgName.get(data.entity_id)||data.entity_id};
    return profile;
  };
  const rows=async(table,select='*')=>{const {data,error}=await db.from(table).select(select);fail(error);return data||[]};
  const orgs=async()=>{const list=await rows('organizations');orgName.clear();for(const x of list)orgName.set(x.id,x.name);return list};
  const messageView=(m,reads=[])=>({...m,read:reads.some(r=>r.last_message_id>=m.id),read_by:reads.filter(r=>r.last_message_id>=m.id).length,entity:orgName.get(m.entity_id)||m.entity_id});
  const profileView=p=>({...p,entity:orgName.get(p.entity_id)||p.entity_id});

  async function loadData(){
    await orgs();
    const me=await profileFor();if(!me)throw new Error('Inicia sesión para continuar.');
    const [inventory,orders,items,events,alerts,alertReads,payments,chatRooms,members,messages,reads,users]=await Promise.all([
      rows('inventory'),rows('orders'),rows('order_items'),rows('order_events'),rows('alerts'),rows('alert_reads'),rows('payments'),rows('chat_rooms'),rows('chat_room_members'),rows('chat_messages'),rows('chat_reads'),
      me.role==='admin'?rows('profiles'):Promise.resolve([])
    ]);
    const userReads=reads.filter(r=>r.user_id===me.id);
    const msgReads=reads.filter(r=>r.user_id!==me.id);
    const byRoom=new Map();for(const m of members){if(!byRoom.has(m.room_id))byRoom.set(m.room_id,[]);byRoom.get(m.room_id).push(m)}
    const roomMessages=messages.map(m=>messageView(m,msgReads));
    for(const m of roomMessages){if(m.attachment_path){const {data,error}=await db.storage.from('chat-attachments').createSignedUrl(m.attachment_path,3600);if(!error)m.attachment_url=data.signedUrl;}}
    const mappedRooms=chatRooms.map(r=>{
      const own=(byRoom.get(r.room_id)||[]),last=roomMessages.filter(m=>m.room_id===r.room_id).at(-1)||null;
      const lastRead=userReads.find(x=>x.room_id===r.room_id)?.last_message_id||0;
      return {...r,entities:[...new Set(own.map(m=>orgName.get(m.entity_id)||m.entity_id))],unread:roomMessages.filter(m=>m.room_id===r.room_id&&m.sender_id!==me.id&&m.id>lastRead).length,last_message:last};
    }).sort((a,b)=>new Date(b.last_message?.at||b.created_at)-new Date(a.last_message?.at||a.created_at));
    const orderRows=orders.map(o=>({...o,requester:orgName.get(o.requester_entity_id)||o.requester_entity_id,provider:orgName.get(o.provider_entity_id)||o.provider_entity_id,eps:orgName.get(o.eps_entity_id)||o.eps_entity_id,created:o.created_at,items:items.filter(i=>i.order_id===o.id),events:events.filter(e=>e.order_id===o.id).map(e=>({...e,at:e.at,actor:e.actor}))}));
    const ownAlertReads=new Set(alertReads.filter(x=>x.user_id===me.id).map(x=>x.alert_id));
    return {user:{...me},inventory:inventory.map(i=>({...i,supplier:orgName.get(i.supplier_entity_id)||i.supplier_entity_id})),orders:orderRows,alerts:alerts.map(a=>({...a,entity:orgName.get(a.entity_id)||a.entity_id,created:a.created_at,read:ownAlertReads.has(a.id)})),payments:payments.map(p=>({...p,supplier:orgName.get(p.supplier_entity_id)||p.supplier_entity_id})),chatRooms:mappedRooms,chatMessages:roomMessages,users:users.map(profileView)};
  }

  async function api(path,opt={}){
    const method=(opt.method||'GET').toUpperCase();let body={};
    if(opt.body){try{body=typeof opt.body==='string'?JSON.parse(opt.body):opt.body}catch{body={}}}
    if(path==='/api/login'&&method==='POST'){
      const {data,error}=await db.auth.signInWithPassword({email:body.email,password:body.password});fail(error);
      await orgs();const me=await profileFor();if(!me){await db.auth.signOut();throw new Error('El usuario no tiene un perfil empresarial activo.');}return {user:me};
    }
    if(path==='/api/logout'){for(const c of channels.values())db.removeChannel(c);channels.clear();roomChannels.clear();if(userChannel)db.removeChannel(userChannel);userChannel=null;const {error}=await db.auth.signOut();fail(error);profile=null;return {ok:true};}
    if(path==='/api/me'){const me=await profileFor();return {user:me};}
    if(path==='/api/data')return loadData();
    if(path==='/api/orders'&&method==='POST'){
      const item=(body.items||[])[0],medicine=await db.from('inventory').select('name').eq('id',item?.id).single();fail(medicine.error);
      const {data,error}=await db.rpc('create_order',{p_provider_entity_id:[...orgName.entries()].find(x=>x[1]===body.provider)?.[0],p_eps_entity_id:[...orgName.entries()].find(x=>x[1]===body.eps)?.[0],p_eta:body.eta||null,p_notes:body.notes||'',p_items:[{medicine:medicine.data.name,qty:Number(item.qty)}]});fail(error);return {id:data.id,code:data.code};
    }
    const orderPath=path.match(/^\/api\/orders\/(\d+)$/);
    if(orderPath&&method==='POST'){
      const {data,error}=await db.rpc('update_order_status',{p_order_id:Number(orderPath[1]),p_status:body.status,p_eta:body.eta||null,p_notes:body.notes||''});fail(error);return {order:data};
    }
    if(path==='/api/inventory'&&method==='POST'){
      if(body.id){const before=await db.from('inventory').select('qty,name').eq('id',body.id).single();fail(before.error);const {data,error}=await db.from('inventory').update({qty:Number(body.qty),minimum:Number(body.minimum),updated_at:new Date().toISOString()}).eq('id',body.id).select().single();fail(error);const delta=Number(body.qty)-Number(before.data.qty);if(delta)fail((await db.from('inventory_movements').insert({inventory_id:body.id,medicine:before.data.name,delta,note:'Ajuste de existencias',actor_id:profile.id,actor:profile.name})).error);return data;}
      const {data,error}=await db.from('inventory').insert({name:body.name,code:body.code,presentation:body.presentation,qty:Number(body.qty),minimum:Number(body.minimum),supplier_entity_id:profile.entity_id}).select().single();fail(error);return data;
    }
    const alertPath=path.match(/^\/api\/alerts\/(\d+)$/);
    if(alertPath&&method==='POST'){const {error}=await db.from('alert_reads').upsert({alert_id:Number(alertPath[1]),user_id:profile.id},{onConflict:'alert_id,user_id'});fail(error);return {ok:true};}
    if(path==='/api/chat/read'&&method==='POST'){
      const {error}=await db.from('chat_reads').upsert({room_id:body.room,user_id:profile.id,last_message_id:Number(body.message_id),at:new Date().toISOString()},{onConflict:'room_id,user_id'});fail(error);
      await sendBroadcast(body.room,'read',{room:body.room,message_id:Number(body.message_id)});return {ok:true};
    }
    if(path==='/api/chat/rooms'&&method==='POST'){
      const entity=[...orgName.entries()].find(x=>x[1]===body.recipient)?.[0];if(!entity)throw new Error('La entidad seleccionada no existe.');
      const {data,error}=await db.rpc('create_direct_room',{p_entity:entity});fail(error);return {room:{room_id:data,title:body.recipient,kind:'direct'}};
    }
    const paymentPath=path.match(/^\/api\/payments\/(\d+)$/);
    if(paymentPath&&method==='POST'){const updates={status:body.status,paid_at:body.status==='Pagado'?new Date().toISOString():null};const {error}=await db.from('payments').update(updates).eq('id',Number(paymentPath[1]));fail(error);return {ok:true};}
    const userPath=path.match(/^\/api\/users\/([0-9a-f-]+)$/i);
    if(userPath&&method==='POST'){const target=(window.__appData?.users||[]).find(x=>x.id===userPath[1]);if(!target)throw new Error('No se encontró el usuario.');const {error}=await db.from('profiles').update({active:!target.active}).eq('id',target.id);fail(error);return {ok:true};}
    if(path==='/api/users'&&method==='GET')return {users:(await loadData()).users};
    if(path==='/api/users'&&method==='POST'){
      const {data:{session}}=await db.auth.getSession();if(!session?.access_token)throw new Error('Inicia sesión para continuar.');
      const response=await fetch('/api/users',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+session.access_token},body:JSON.stringify(body)});const result=await response.json().catch(()=>({}));if(!response.ok)throw new Error(result.error||'No se pudo crear el usuario.');return result;
    }
    throw new Error('Esta operación todavía no está disponible en el entorno de nube.');
  }

  async function sendBroadcast(room,event,payload){const c=channels.get(room);if(c)await c.send({type:'broadcast',event,payload});}
  async function subscribeChat(roomsList,currentRoom,handlers){
    activeRoom=currentRoom;rooms=roomsList||[];
    if(!userChannel&&profile){userChannel=db.channel('user:'+profile.id,{config:{private:true,broadcast:{self:false,ack:true}}});userChannel.on('broadcast',{event:'room_created'},async({payload})=>{try{const fresh=await loadData();rooms=fresh.chatRooms||[];handlers.roomAdded?.(fresh,payload);await subscribeRoomChannels(handlers);}catch(error){console.error(error);}});userChannel.subscribe();}
    for(const c of channels.values())await db.removeChannel(c);channels.clear();roomChannels.clear();
    await subscribeRoomChannels(handlers);
  }
  async function subscribeRoomChannels(handlers){
    for(const room of rooms){
      if(channels.has(room.room_id))continue;
      const channel=db.channel('room:'+room.room_id,{config:{private:true,presence:{key:profile.id},broadcast:{self:false,ack:true}}});
      channel.on('postgres_changes',{event:'INSERT',schema:'public',table:'chat_messages',filter:`room_id=eq.${room.room_id}`},payload=>signedMessage(payload.new).then(handlers.message).catch(()=>handlers.message(messageView(payload.new,[]))));
      channel.on('broadcast',{event:'typing'},({payload})=>handlers.typing(room.room_id,payload));
      channel.on('broadcast',{event:'read'},({payload})=>handlers.read(room.room_id,payload));
      channel.on('presence',{event:'sync'},()=>{if(room.room_id===activeRoom){const state=channel.presenceState();handlers.presence(Object.values(state).flat());}});
      channel.subscribe(async status=>{if(status==='SUBSCRIBED'&&room.room_id===activeRoom){await channel.track({user_id:profile.id,name:profile.name,entity:profile.entity});handlers.connected(true);}});
      channels.set(room.room_id,channel);roomChannels.set(room.room_id,channel);
    }
  }
  async function setActiveRoom(room){activeRoom=room;for(const [id,c] of channels){if(id===room){await c.track({user_id:profile.id,name:profile.name,entity:profile.entity});}else await c.untrack();}}
  async function signedMessage(raw){const m=messageView(raw,[]);if(m.attachment_path){const {data,error}=await db.storage.from('chat-attachments').createSignedUrl(m.attachment_path,3600);if(!error)m.attachment_url=data.signedUrl;}return m;}
  async function sendMessage(payload,file){
    const roomId=payload.room||activeRoom;
    const existing=await db.from('chat_messages').select('*').eq('room_id',roomId).eq('client_id',payload.client_id).maybeSingle();fail(existing.error);if(existing.data)return signedMessage(existing.data);
    let attachment={};
    let uploadedPath=null;
    if(file){const safeName=file.name.replace(/[^\p{L}\p{N}._-]/gu,'_');uploadedPath=`${roomId}/${crypto.randomUUID()}-${safeName}`;const {error}=await db.storage.from('chat-attachments').upload(uploadedPath,file,{contentType:file.type||'application/octet-stream',upsert:false});fail(error);attachment={attachment_name:file.name,attachment_type:file.type||'application/octet-stream',attachment_path:uploadedPath};}
    const {data,error}=await db.from('chat_messages').insert({room_id:roomId,client_id:payload.client_id,sender_id:profile.id,sender:profile.name,entity_id:profile.entity_id,body:payload.body||'',ref_type:payload.ref_type||null,ref_id:payload.ref_id||null,...attachment}).select().single();
    if(error?.code==='23505'){if(uploadedPath)await db.storage.from('chat-attachments').remove([uploadedPath]);const again=await db.from('chat_messages').select('*').eq('room_id',roomId).eq('client_id',payload.client_id).single();fail(again.error);return signedMessage(again.data);}fail(error);return signedMessage(data);
  }
  async function setTyping(active){if(activeRoom)await sendBroadcast(activeRoom,'typing',{name:profile.name,entity:profile.entity,active:Boolean(active)});}
  return {api,subscribeChat,setActiveRoom,sendMessage,setTyping,close:async()=>{for(const c of channels.values())await db.removeChannel(c);channels.clear();if(userChannel)await db.removeChannel(userChannel);userChannel=null;},getClient:()=>db};
}
