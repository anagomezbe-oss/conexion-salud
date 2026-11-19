const form=document.querySelector('#bootstrapForm');
const feedback=document.querySelector('#feedback');
form.addEventListener('submit',async event=>{
  event.preventDefault();
  feedback.className='';feedback.textContent='Creando cuenta…';
  const button=form.querySelector('button');button.disabled=true;
  const values=Object.fromEntries(new FormData(form));
  try{
    const response=await fetch('/api/users',{method:'POST',headers:{'Content-Type':'application/json','X-Bootstrap-Secret':values.secret},body:JSON.stringify({name:values.name,email:values.email,password:values.password})});
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(result.error||'No se pudo crear la cuenta.');
    form.reset();feedback.className='ok';feedback.textContent='Acceso creado. Ahora retira BOOTSTRAP_ADMIN_SECRET de Vercel e inicia sesión como administrador.';
  }catch(error){feedback.textContent=error.message||'No se pudo crear la cuenta.';}
  finally{button.disabled=false;}
});
