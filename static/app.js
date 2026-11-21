let token = localStorage.getItem('token') || '';
let role = '';
let currentUserId = null;
const $ = id => document.getElementById(id);
const headers = () => ({'Content-Type':'application/json', ...(token ? {'Authorization':'Bearer '+token} : {})});
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function api(url, opts={}) {
  let r;
  try { r = await fetch(url, {...opts, headers:{...headers(), ...(opts.headers||{})}}); }
  catch (_) { throw Error('Не удалось связаться с сервером. Убедитесь, что сайт запущен вместе с backend (FastAPI), а не только через GitHub Pages.'); }
  const d = await r.json().catch(()=>({}));
  if (!r.ok) throw Error(d.detail || 'Ошибка запроса');
  return d;
}
function msg(t) { $('message').textContent=t; $('message').style.display='block'; clearTimeout(msg.timer); msg.timer=setTimeout(()=>$('message').style.display='none',3500); }
function updateNavigation() {
  document.querySelectorAll('[data-role]').forEach(b=>{
    const allowed=b.dataset.role.split(',');
    b.classList.toggle('hidden', !allowed.includes(role));
  });
}
function setUser(name) {
  $('userbox').textContent='';
  const span=document.createElement('span'); span.textContent=`${name} (${role}) `;
  const button=document.createElement('button'); button.textContent='Выйти'; button.onclick=logout;
  $('userbox').append(span,button);
  $('auth').classList.add('hidden'); updateNavigation();
}
async function login() {
  try {
    const d=await api('/api/login',{method:'POST',body:JSON.stringify({email:$('email').value,password:$('password').value})});
    token=d.token; role=d.role; localStorage.setItem('token',token);
    const u=await api('/api/me'); currentUserId=u.id; setUser(d.name);
    show('catalog'); await loadCatalog(); msg('Вход выполнен');
  } catch(e) { msg(e.message); }
}
async function register() {
  try { await api('/api/register',{method:'POST',body:JSON.stringify({name:$('regname').value,email:$('regemail').value,password:$('regpass').value})}); msg('Регистрация успешна. Теперь войдите.'); }
  catch(e) { msg(e.message); }
}
async function logout() {
  await api('/api/logout',{method:'POST'}).catch(()=>{});
  token=''; role=''; currentUserId=null; localStorage.removeItem('token');
  $('userbox').textContent='Вы не вошли'; $('auth').classList.remove('hidden'); updateNavigation(); show('catalog'); loadCatalog();
}
function show(id) {
  const allowed={catalog:['client','manager','admin'],account:['client','manager','admin'],manage:['manager','admin'],admin:['admin']};
  if (id!=='catalog' && id!=='auth' && (!token || !(allowed[id]||[]).includes(role))) { msg('У вас нет доступа к этому разделу'); return; }
  document.querySelectorAll('main section').forEach(s=>s.classList.add('hidden'));
  $(id).classList.remove('hidden');
  if(id==='account') loadMy(); if(id==='manage') loadManage(); if(id==='admin') loadAdmin();
}
async function loadCatalog() {
  try {
    const q=new URLSearchParams({search:$('search').value,category:$('category').value});
    const data=await api('/api/equipment?'+q);
    $('items').innerHTML=data.map(x=>`<article class="item"><h3>${esc(x.name)}</h3><p>${esc(x.description)}</p><p>Категория: ${esc(x.category)}</p><b>${esc(x.price)} ₽ / сутки</b><p>Доступно единиц: ${esc(x.quantity)}</p><button onclick="book(${Number(x.id)})">Забронировать</button></article>`).join('')||'Ничего не найдено';
  } catch(e) { $('items').textContent=e.message; }
}
let selectedEquipmentId = null;
function localDateValue(d=new Date()) {
  const y=d.getFullYear(), m=String(d.getMonth()+1).padStart(2,'0'), day=String(d.getDate()).padStart(2,'0');
  return `${y}-${m}-${day}`;
}
function book(id) {
  if(!token){msg('Сначала войдите в систему, чтобы оформить бронирование');return;}
  selectedEquipmentId=Number(id);
  const card=document.querySelector(`.item button[onclick="book(${selectedEquipmentId})"]`)?.closest('.item');
  $('bookingItemName').textContent=card?.querySelector('h3')?.textContent || 'Выбранное оборудование';
  const today=localDateValue();
  $('bookingStart').min=today; $('bookingEnd').min=today;
  $('bookingStart').value=today; $('bookingEnd').value=today;
  $('bookingModal').classList.remove('hidden');
}
function closeBooking() {
  $('bookingModal').classList.add('hidden');
  selectedEquipmentId=null;
  $('confirmBooking').disabled=false;
  $('confirmBooking').textContent='Подтвердить бронь';
}
async function submitBooking() {
  if(!selectedEquipmentId){msg('Сначала выберите оборудование');return;}
  const start=$('bookingStart').value, end=$('bookingEnd').value;
  if(!start || !end){msg('Укажите обе даты');return;}
  if(end<start){msg('Дата окончания не может быть раньше даты начала');return;}
  const button=$('confirmBooking'); button.disabled=true; button.textContent='Отправка...';
  try {
    const d=await api('/api/bookings',{method:'POST',body:JSON.stringify({equipment_id:selectedEquipmentId,start_date:start,end_date:end})});
    closeBooking(); msg(`Бронирование №${d.booking_id} создано. Стоимость: ${d.total} ₽`);
    if(!$('account').classList.contains('hidden')) await loadMy();
  } catch(e) {
    button.disabled=false; button.textContent='Подтвердить бронь';
    msg(e.message || 'Не удалось отправить бронирование. Проверьте подключение к серверу.');
  }
}
async function loadMy(){
  try { const a=await api('/api/my-bookings'); $('mybookings').innerHTML=a.map(b=>`<div class="entry">${esc(b.equipment_name)}: ${esc(b.start_date)} — ${esc(b.end_date)}; ${esc(b.total)} ₽; статус: <b>${esc(b.status)}</b>; штраф: ${esc(b.fine)} ₽</div>`).join('')||'Бронирований пока нет'; }
  catch(e){$('mybookings').textContent=e.message;}
}
async function loadManage(){
  try {
    const a=await api('/api/manage/bookings');
    $('managebookings').innerHTML=a.map(b=>`<div class="entry">№${Number(b.id)} ${esc(b.equipment_name)}, клиент: ${esc(b.client_name)}, ${esc(b.start_date)} — ${esc(b.end_date)}, статус: ${esc(b.status)} ${b.status==='booked'?`<button onclick="issue(${Number(b.id)})">Выдать</button>`:''} ${['issued','overdue'].includes(b.status)?`<button onclick="ret(${Number(b.id)})">Принять возврат</button>`:''}</div>`).join('')||'Бронирований нет';
    const s=await api('/api/stats');
    $('stats').innerHTML=`Всего бронирований: ${esc(s.bookings)}; активных: ${esc(s.active)}; выручка с завершённых аренд: ${esc(s.revenue)} ₽<div class="bars">`+s.categories.map(c=>`<div class="barrow"><span>${esc(c.category)}</span><div class="bar" style="width:${Math.max(5,Math.min(100,Number(c.count)*12))}%"></div><b>${esc(c.count)}</b></div>`).join('')+'</div>';
  } catch(e){$('managebookings').textContent=e.message;}
}
async function issue(id){try{const d=await api(`/api/manage/bookings/${id}/issue`,{method:'POST'});msg(d.message);loadManage();}catch(e){msg(e.message);}}
async function ret(id){
  const condition=prompt('Состояние при возврате','Исправно'); if(condition===null)return;
  const raw=prompt('Штраф за повреждение, руб.','0'); if(raw===null)return;
  const damage=Number(raw); if(!Number.isFinite(damage)||damage<0){msg('Штраф должен быть неотрицательным числом');return;}
  try{const d=await api(`/api/manage/bookings/${id}/return`,{method:'POST',body:JSON.stringify({condition_in:condition,damage_fine:damage})});msg(`${d.message}. Штраф: ${d.fine} ₽`);loadManage();}catch(e){msg(e.message);}
}
async function addItem(){
  try{await api('/api/admin/equipment',{method:'POST',body:JSON.stringify({name:$('newname').value,category:$('newcat').value,description:'',price:Number($('newprice').value),quantity:Number($('newqty').value),status:'available'})});msg('Оборудование добавлено');loadAdmin();}
  catch(e){msg(e.message);}
}
async function loadAdmin(){
  try{
    const a=await api('/api/admin/equipment');
    $('adminitems').innerHTML=a.map(x=>`<div class="entry">${esc(x.name)} — ${esc(x.category)}, ${esc(x.price)} ₽/сутки, количество: ${esc(x.quantity)}, состояние: ${esc(x.status)} <button onclick="setRepair(${Number(x.id)})">В ремонт</button><button onclick="writeoff(${Number(x.id)})">Списать</button></div>`).join('')||'Оборудования пока нет';
    const u=await api('/api/admin/users');
    $('users').innerHTML=u.map(x=>{
      const protectedUser=x.role==='admin'||x.id===currentUserId;
      const select=protectedUser?`<span>Роль защищена</span>`:`<select id="role${Number(x.id)}"><option value="client" ${x.role==='client'?'selected':''}>Клиент</option><option value="manager" ${x.role==='manager'?'selected':''}>Менеджер</option></select><button onclick="changeRole(${Number(x.id)})">Сохранить роль</button>`;
      return `<div class="entry">${esc(x.name)} — ${esc(x.email)} (${esc(x.role)}) ${select}</div>`;
    }).join('');
  }catch(e){$('adminitems').textContent=e.message;}
}
async function setRepair(id){try{const d=await api(`/api/admin/equipment/${id}/repair`,{method:'POST'});msg(d.message);loadAdmin();}catch(e){msg(e.message);}}
async function writeoff(id){if(!confirm('Списать оборудование?'))return;try{const d=await api(`/api/admin/equipment/${id}/writeoff`,{method:'POST'});msg(d.message);loadAdmin();}catch(e){msg(e.message);}}
async function changeRole(id){
  const chosen=$('role'+id); if(!chosen)return;
  if(!confirm(`Изменить роль пользователя на «${chosen.options[chosen.selectedIndex].text}»?`))return;
  try{const d=await api(`/api/admin/users/${id}/role`,{method:'PUT',body:JSON.stringify({role:chosen.value})});msg(d.message);loadAdmin();}
  catch(e){msg(e.message);}
}
(async()=>{
  updateNavigation();
  if(token){try{const u=await api('/api/me');role=u.role;currentUserId=u.id;setUser(u.name);}catch(e){token='';localStorage.removeItem('token');}}
  await loadCatalog();
})();
