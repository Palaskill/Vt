/* Public read-only preview: synthetic data only; no real API is contacted. */
(() => {
  'use strict';
  const originalFetch=window.fetch.bind(window);
  const dateAt=offset=>{const d=new Date();d.setHours(12,0,0,0);d.setDate(d.getDate()+offset);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
  const now=()=>`${dateAt(0)}T10:30:00`;
  const user={id:1,username:'demo',display_name:'Cabinet vétérinaire',role:'admin',active:1,alerts:1};
  const medicines=[
    {id:1,name:'Produit vétérinaire A',strength:'20 comprimés',form:'Comprimés',barcode:'',pack_size:20,price_pack:18500,price_unit:1100,threshold:45,threshold_mode:'units',expiry_days:90,active:1,boxes:2,loose:4,total_units:44,expired_units:0,low:true},
    {id:2,name:'Vaccin B',strength:'1 dose',form:'Flacon',barcode:'',pack_size:1,price_pack:28000,price_unit:28000,threshold:3,threshold_mode:'boxes',expiry_days:60,active:1,boxes:2,loose:0,total_units:2,expired_units:0,low:true},
    {id:3,name:'Produit vétérinaire C',strength:'10 comprimés',form:'Comprimés',barcode:'',pack_size:10,price_pack:12000,price_unit:1500,threshold:10,threshold_mode:'units',expiry_days:90,active:1,boxes:8,loose:6,total_units:86,expired_units:0,low:false},
    {id:4,name:'Solution de soin D',strength:'100 ml',form:'Flacon',barcode:'',pack_size:1,price_pack:16500,price_unit:16500,threshold:2,threshold_mode:'boxes',expiry_days:60,active:1,boxes:6,loose:0,total_units:6,expired_units:0,low:false}
  ];
  const lots=[
    {id:1,medicine_id:1,name:medicines[0].name,pack_size:20,expiry_days:90,batch:'LOT-DEMO-A',expiry:dateAt(38),boxes:2,loose:4,received_at:now(),days_left:38},
    {id:2,medicine_id:2,name:medicines[1].name,pack_size:1,expiry_days:60,batch:'LOT-DEMO-B',expiry:dateAt(18),boxes:2,loose:0,received_at:now(),days_left:18},
    {id:3,medicine_id:3,name:medicines[2].name,pack_size:10,expiry_days:90,batch:'LOT-DEMO-C',expiry:dateAt(160),boxes:8,loose:6,received_at:now(),days_left:160},
    {id:4,medicine_id:4,name:medicines[3].name,pack_size:1,expiry_days:60,batch:'LOT-DEMO-D',expiry:dateAt(200),boxes:6,loose:0,received_at:now(),days_left:200}
  ];
  const suppliers=[{id:1,name:'Fournisseur Démo',phone:'70 000 000',email:'',address:'Tunisie'}];
  const orders=[{id:11,supplier_id:1,supplier:'Fournisseur Démo',status:'open',notes:'Données fictives',created_at:now(),lines:[{id:1,order_id:11,medicine_id:1,medicine:medicines[0].name,boxes:10,received:4}]}];
  const sales=[{id:27,client_name:'',subtotal:37000,discount:2000,total:35000,paid:20000,status:'active',user_id:1,seller:user.display_name,created_at:now(),void_reason:''}];
  const invoices=[{id:14,supplier_id:1,supplier:'Fournisseur Démo',invoice_no:'DEMO-001',total:120000,paid:50000,due_date:dateAt(20),created_at:now(),has_file:0}];
  const settings={clinic_name:'Cabinet vétérinaire — Démo',clinic_address:'',clinic_phone:'',clinic_tax_id:'',backup_dir:'',last_backup:''};
  function dashboard(){return {medicine_count:medicines.length,low:medicines.filter(x=>x.low),expiring:lots.filter(x=>x.days_left<=x.expiry_days),alerts_enabled:true,open_orders:orders,today_total:35000,customer_due:15000,supplier_due:70000,backup_stale:false,backup_configured:false}}
  function json(value,status=200){return Promise.resolve(new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json; charset=utf-8'}}))}
  window.fetch=function(resource,options={}){
    const raw=typeof resource==='string'?resource:resource.url;
    const url=new URL(raw,window.location.href);
    if(url.origin!==window.location.origin||!url.pathname.startsWith('/api/'))return originalFetch(resource,options);
    if(String(options.method||'GET').toUpperCase()!=='GET')return json({error:'DEMO_READ_ONLY'},403);
    const path=url.pathname.slice(5);
    switch(path){
      case 'state':return json({setup:false,user,secure:true});
      case 'dashboard':return json(dashboard());
      case 'medicines':return json(medicines);
      case 'lots':return json(url.searchParams.has('medicine_id')?lots.filter(x=>x.medicine_id===Number(url.searchParams.get('medicine_id'))):lots);
      case 'suppliers':return json(suppliers);
      case 'orders':return json(orders);
      case 'sales':return json(sales);
      case 'sale':return json({...sales[0],lines:[{id:1,sale_id:27,medicine_id:1,medicine_name:medicines[0].name,kind:'box',qty:2,unit_price:18500,total:37000}],payments:[{id:1,sale_id:27,amount:20000,method:'cash',user_id:1,created_at:now()}],clinic:settings});
      case 'supplier-invoices':return json(invoices);
      case 'settings':return json(settings);
      case 'users':return json([user,{id:2,username:'assistant-demo',display_name:'Assistant · démo',role:'employee',active:1,alerts:1}]);
      case 'audit':return json([{id:1,actor:'Cabinet vétérinaire',action:'demo',details:'Données fictives',created_at:now()}]);
      default:return json({error:'DEMO_READ_ONLY'},403);
    }
  };
  function explain(){const current=document.querySelector('.demo-toast');if(current)current.remove();const note=document.createElement('div');note.className='demo-toast';note.textContent=document.documentElement.lang==='ar'?'هذه نسخة للعرض فقط. لا يتم حفظ البيانات.':'Aperçu en lecture seule : aucune donnée du cabinet n’est sauvegardée.';document.body.appendChild(note);setTimeout(()=>note.remove(),4500)}
  const blocked=new Set(['logout','scan','enable-notifications','backup-now','import','import-preview','new-user','edit-user','print-sale','print-order']);
  document.addEventListener('click',event=>{const element=event.target.closest('[data-action],a,input[type="file"]');if(!element)return;const action=element.dataset.action||'';const unsafeLink=element.matches('a')&&(element.getAttribute('href')||'').startsWith('/api/');if(blocked.has(action)||unsafeLink||element.matches('input[type="file"]')){event.preventDefault();event.stopImmediatePropagation();explain()}},true);
})();
