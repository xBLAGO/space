'use strict';
const $ = id => document.getElementById(id);
let csrf = '', state, plan, activeDelivery, fingerprint, searching = false, readSequence = 0;
function message(text) { $('message').textContent = text; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache:'no-store'} : {
    method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
    body:JSON.stringify(body), cache:'no-store'
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Локальная операция недоступна');
  return value;
}
function action(name, data={}) {
  return api('/api/action', {scenario:$('scenario').value, action:name, data});
}
function invalidate() {
  const previous = plan; plan = null;
  if ($('send')) $('send').disabled = true;
  if ($('preview-text')) $('preview-text').textContent = 'Превью сброшено. Требуется новое подтверждение.';
  if (previous) action('invalidate', {nonce:previous.nonce}).catch(()=>{});
}
async function refresh() {
  const selected = $('scenario').value, sequence = ++readSequence;
  const nextState = await api('/api/state?scenario='+selected);
  if (sequence !== readSequence || selected !== $('scenario').value) return;
  state = nextState; csrf = state.csrf;
  if (state.role !== 'B') await api('/api/view?scenario='+selected);
  if (sequence !== readSequence || selected !== $('scenario').value) return;
  const d = state.data;
  if (state.role === 'A') {
    $('request').textContent = d.request.text; $('status').textContent = d.status;
    $('allow').disabled = !d.can_allow; $('revoke').disabled = !d.can_revoke;
    $('confirm').disabled = !['ACCEPTED','AWAITING_CONFIRMATION'].includes(d.request.state);
    const offers = $('offers'); offers.replaceChildren();
    if (!d.request.offers.length) offers.textContent = 'Предложений пока нет';
    for (const offer of d.request.offers) {
      const p = document.createElement('p'); p.textContent = offer.participant+': '+offer.note; offers.append(p);
      if (offer.status === 'OFFERED') {
        const b = document.createElement('button'); b.textContent = 'Принять предложение';
        b.onclick = ()=>run('accept',{offer_id:offer.id}); offers.append(b);
      }
    }
  } else if (state.role === 'B') {
    const next = JSON.stringify([d.request.version,d.approved_text,d.permission]);
    if (fingerprint !== undefined && fingerprint !== next) invalidate();
    fingerprint = next; $('expiry-info').textContent = d.permission_expires_at ? 'Полномочия действуют не позднее: '+new Date(d.permission_expires_at*1000).toLocaleString() : 'Нужно разрешение автора'; $('request').textContent = d.approved_text || d.request.text;
    $('match').disabled = !d.permission; $('search').disabled = !d.permission;
    $('preview').disabled = !searching || !d.permission;
  } else {
    const inbox = $('inbox'); inbox.replaceChildren();
    if (!d.inbox.length) inbox.textContent = 'Входящих приглашений нет';
    for (const card of d.inbox) {
      const p = document.createElement('p'); p.textContent = card.label;
      const b = document.createElement('button'); b.textContent = 'Открыть'; b.dataset.delivery = card.id;
      b.onclick = async ()=> {
        try {
          const result = await action('open',{id:card.id}); activeDelivery = card.id;
          $('task').textContent = result.task; $('referrer').textContent = 'referrer: '+result.referrer;
          $('receipt').textContent = 'Локальная квитанция: '+result.receipt; $('opened').hidden = false;
          message('Приглашение открыто локально. Это не согласие на контакт или помощь.');
        } catch(e) { $('opened').hidden = true; message(e.message); }
      };
      p.append(b); inbox.append(p);
    }
  }
}
async function run(name, data={}) {
  try { const result = await action(name,data); message(result.message || (result.revoked ? 'Разрешение отозвано' : 'Подтверждено локально')); await refresh(); return result; }
  catch(e) { message(e.message); }
}
if ($('login')) {
  $('login').onclick = async ()=> {
    try { await api('/api/login',{username:$('username').value,password:$('password').value});
      $('login').disabled = true; $('enter').hidden = false; message('Вход выполнен. Откройте собственный профиль.'); }
    catch(e) { message(e.message); }
  };
  $('enter').onclick = ()=>location.assign('/');
} else {
  $('refresh').onclick = ()=>refresh().catch(e=>message(e.message));
  $('scenario').onchange = ()=> {
    invalidate(); activeDelivery = null; searching = false; fingerprint = undefined;
    if ($('opened')) $('opened').hidden = true;
    if ($('matches')) $('matches').textContent = 'Поиск не запускался';
    message(''); refresh().catch(e=>message(e.message));
  };
  if ($('allow')) {
    $('allow').onclick = ()=>run('allow'); $('revoke').onclick = ()=>run('revoke');
    $('edit').onclick = ()=>run('edit',{text:$('edit-text').value}); $('confirm').onclick = ()=>run('confirm');
  }
  if ($('match')) {
    $('match').onclick = ()=>run('match');
    $('search').onclick = async ()=> {
      try { const result = await action('search'); $('matches').textContent = JSON.stringify(result,null,2); searching = true; await refresh(); message('Результат доступен только мне'); }
      catch(e) { message(e.message); }
    };
    $('recipient').onchange = invalidate;
    $('preview').onclick = async ()=> {
      try { plan = await action('preview',{recipient:$('recipient').value});
        $('preview-text').textContent = 'Адресат: '+plan.recipient+'\nТочный текст: '+plan.task+'\nПолучатель увидит referrer: '+plan.referrer+'\nСрок INVITE_ONCE: '+new Date(plan.expires_at*1000).toLocaleString()+'\nЛичная заметка НЕ передаётся';
        $('send').disabled = false; message('Проверьте адресата, точный текст и раскрытие рекомендации');
      } catch(e) { invalidate(); message(e.message); }
    };
    $('send').onclick = async ()=> {
      const reviewed = plan;
      if (!reviewed) return;
      $('send').disabled = true;
      try { await action('send',{nonce:reviewed.nonce,recipient:$('recipient').value,text:reviewed.task}); plan = null; message('Превью направлено в локальную очередь. Внешней доставки нет.'); }
      catch(e) { invalidate(); message(e.message); }
    };
  }
  if ($('contact')) {
    $('contact').onclick = async ()=> { const result = await run('contact',{id:activeDelivery}); if (result) $('offer-area').hidden = false; };
    $('decline').onclick = async ()=> { const result = await run('decline',{id:activeDelivery}); if (result) { $('opened').hidden = true; message('Вы отказались. Автор не получает имя или статус отказа.'); } };
    $('offer').onclick = ()=>run('offer',{id:activeDelivery});
  }
  refresh().catch(e=>message(e.message));
  // Reads only: no automatic matching, granting, publication or delivery.
  setInterval(()=>refresh().catch(()=>{}), 15000);
}
