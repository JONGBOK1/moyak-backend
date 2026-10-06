const $ = id => document.getElementById(id);
// Share the proven consultation flow with the Flutter Web screen.
if(new URLSearchParams(location.search).get('embedded')==='1'){
  document.querySelector('body>header').hidden=true;
  const style=document.createElement('style');
  style.textContent=':root{background:#fffffbe8;color:#252525}main{padding:16px}button.primary,.role-picker button[aria-pressed=true]{background:#f1c744;border-color:#f1c744;color:#252525}.bubble.mine{background:#fff0b4}.call-layout.chat-closed{grid-template-columns:1fr}.call-layout.chat-closed #participants{display:none}';
  document.head.append(style);
}
const chatToggle=document.createElement('button');
chatToggle.type='button';chatToggle.textContent='채팅 닫기';
chatToggle.setAttribute('aria-label','채팅 창 열기 또는 닫기');
chatToggle.setAttribute('aria-expanded','true');
chatToggle.setAttribute('aria-controls','participants');
chatToggle.onclick=()=>{
  const closed=document.querySelector('.call-layout').classList.toggle('chat-closed');
  $('participants').hidden=closed;
  chatToggle.textContent=closed?'채팅 열기':'채팅 닫기';
  chatToggle.setAttribute('aria-expanded',String(!closed));
};
document.querySelector('#video-panel .cardhead').append(chatToggle);
const roles = {user:'사용자', pharmacist:'약사'};
const labels = {symptoms:'호소한 증상',discussion:'상담 내용',medication_guidance:'복약 안내',precautions:'주의사항',follow_up:'후속 안내',needs_verification:'확인할 내용'};
let state=null, sockets=[], generation=0, polling=false, ended=false, draftShown=false, activeRecorder=null, uploadBusy=false, actionBusy=false;
const seen={user:new Set(),pharmacist:new Set()}, pendingAudio=new Map();
let currentRole=new URLSearchParams(location.search).get('role')==='pharmacist'?'pharmacist':'user';
function selectRole(role){
  if(activeRecorder)throw Error('녹음을 마친 뒤 역할을 변경해주세요.');
  if(role!==currentRole&&typeof rtcHangup==='function')rtcHangup();
  currentRole=role;
  for(const name of Object.keys(roles)){$('participant-'+name).hidden=name!==role;$('role-'+name).setAttribute('aria-pressed',String(name===role));}
  $('peer').textContent=(role==='user'?'약사':'사용자')+' 화면 열기';
}
function remember(){sessionStorage.setItem('moyak-demo-session',JSON.stringify(state));}
function showVideo(url){$('call').src=url;$('call').hidden=false;$('video-empty').hidden=true;}
async function openVideo(){
  if(!state)throw Error('먼저 영상상담을 시작해주세요.');
  if(!state.video_available){$('rtc').hidden=false;$('video-empty').hidden=true;$('call').hidden=true;notice('양쪽 화면에서 통화 참여를 누르면 영상과 음성이 연결됩니다. 오른쪽에서 채팅도 함께 사용하세요.');return;}
  const result=await request('/demo/'+state.id+'/video',currentRole,'POST');
  state.room_url=result.room_url;remember();showVideo(result.room_url);
  $('peer').disabled=false;
  notice('영상 안에서 통화에 참여하세요. 오른쪽 채팅은 통화를 끊지 않고 사용할 수 있습니다.');
}
function notice(message,error=false){$('notice').textContent=message;$('notice').className='notice'+(error?' error':'');}
function run(fn){return async()=>{try{await fn();}catch(e){notice(e.message,true);}};}
async function request(path,role='user',method='GET',body){
  const headers={};if(state)headers.Authorization='Bearer '+state[role+'_token'];
  if(body!==undefined)headers['Content-Type']='application/json';
  const response=await fetch(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'입력 또는 요청을 확인해주세요.');return data;
}
const path=suffix=>'/consultations/'+state.id+suffix;
for(const [role,title] of Object.entries(roles)){
  const card=document.createElement('article');card.className='card';card.id='participant-'+role;
  card.innerHTML=`<div class="cardhead"><h2>${title} 화면</h2><span id="connection-${role}" class="status">연결 전</span></div><label class="consent"><input id="consent-${role}" type="checkbox" disabled>음성 기록과 채팅·음성의 AI 요약 처리에 동의합니다.</label><div id="chat-${role}" class="chat" aria-live="polite"><div class="empty">상담 메시지가 여기에 표시됩니다.</div></div><form id="form-${role}"><input id="text-${role}" maxlength="4000" placeholder="${title} 메시지 입력" aria-label="${title} 메시지" disabled><button id="send-${role}" disabled>전송</button></form><div class="row record"><button id="record-${role}" disabled>마이크로 음성 기록</button><span id="audio-${role}" class="muted">양측 동의 후 사용 가능</span></div>`;
  $('participants').append(card);
  $('form-'+role).onsubmit=async e=>{e.preventDefault();await run(async()=>{
    const input=$('text-'+role),text=input.value.trim();if(!text)return;
    await request(path('/messages'),role,'POST',{client_id:crypto.randomUUID(),text});input.value='';
  })();};
  $('consent-'+role).onchange=run(async()=>{const input=$('consent-'+role);input.disabled=true;
    try{await request(path('/session/consent'),role,'POST');await refresh();}
    catch(e){input.checked=false;input.disabled=false;throw e;}
  });
  $('record-'+role).onclick=run(()=>record(role));
}
for(const role of Object.keys(roles))$('role-'+role).onclick=run(()=>selectRole(role));
selectRole(currentRole);
function append(role,message){
  if(seen[role].has(message.id))return;seen[role].add(message.id);
  const container=$('chat-'+role);container.querySelector('.empty')?.remove();
  const node=document.createElement('div');node.className='bubble'+(message.sender_role===role?' mine':'');
  const author=document.createElement('small');author.textContent=roles[message.sender_role];
  node.append(author,document.createTextNode(message.text));container.append(node);container.scrollTop=container.scrollHeight;
}
function connect(role,version){
  if(version!==generation)return;
  const ws=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+path('/messages/ws'));sockets.push(ws);
  ws.onopen=()=>{ws.send(JSON.stringify({token:state[role+'_token'],after:Math.max(0,...seen[role])}));$('connection-'+role).textContent='실시간 연결됨';};
  ws.onmessage=e=>{const data=JSON.parse(e.data);if(data.type==='messages')data.items.forEach(m=>append(role,m));};
  ws.onclose=e=>{if(version!==generation)return;$('connection-'+role).textContent=e.code===1008?'인증 만료 · 새 시연 필요':'재연결 중';if(e.code!==1008)setTimeout(()=>connect(role,version),2000);};
}
async function start(){
  if(activeRecorder||uploadBusy)throw Error('녹음과 업로드를 마친 뒤 새 시연을 시작해주세요.');
  $('start').disabled=true;
  try{
    const response=await fetch('/demo/start',{method:'POST',headers:{'X-Moyak-Demo':'1'}});const data=await response.json();
    if(!response.ok)throw Error(data.detail||'시연을 시작하지 못했습니다.');
    await activate(data);
    await openVideo();
  }finally{$('start').disabled=false;}
}
async function activate(data){
    if(typeof rtcHangup==='function')rtcHangup();
    generation++;sockets.forEach(s=>s.close());sockets=[];state=data;state.startedAt ||= Date.now();ended=false;draftShown=false;pendingAudio.clear();remember();
    $('session-id').textContent='상담 '+state.id.slice(0,8);$('call').removeAttribute('src');$('call').hidden=true;$('video-empty').hidden=false;$('video-empty').textContent='영상에 연결한 뒤 카메라·마이크를 허용하고 참여해주세요.';
    $('draft').textContent='초안 생성 대기 중';$('published').textContent='약사 확인 전에는 공개되지 않습니다.';$('transcript').textContent='아직 음성 기록이 없습니다.';
    for(const role of Object.keys(roles)){seen[role].clear();$('chat-'+role).replaceChildren();$('consent-'+role).checked=false;$('consent-'+role).disabled=false;$('text-'+role).disabled=false;$('send-'+role).disabled=false;$('text-'+role).value='';connect(role,generation);}
    $('sample').disabled=false;$('video').disabled=false;$('rtc').hidden=!!state.video_available;
    $('peer').disabled=false;
    if(state.room_url)showVideo(state.room_url);
    else if(!state.video_available){$('rtc').hidden=false;$('video-empty').hidden=true;}
    selectRole(currentRole);
    await refresh();
}
async function sample(){
  $('sample').disabled=true;
  try{for(const [role,text] of [['user','[가상 시연] 오늘 아침부터 머리가 아파서 상담을 요청했어요.'],['pharmacist','언제 시작했는지와 다른 불편한 증상이 있는지 말씀해주세요.'],['user','아침 9시쯤 시작했고, 약은 아직 먹지 않았어요.'],['pharmacist','말씀하신 내용을 확인했습니다. 이 시연에서는 약을 처방하거나 구매 승인하지 않고 상담 기록만 남기겠습니다.']])await request(path('/messages'),role,'POST',{client_id:crypto.randomUUID(),text});notice('가상의 예시 대화를 넣었습니다. 직접 메시지를 추가하거나 상담을 종료해보세요.');}
  finally{$('sample').disabled=ended;}
}
function renderDraft(content){$('draft').replaceChildren();for(const [key,label] of Object.entries(labels)){const el=document.createElement('label');el.textContent=label;el.htmlFor='field-'+key;const input=document.createElement('textarea');input.id='field-'+key;input.value=(content[key]||[]).join('\n');$('draft').append(el,input);}}
function renderPublished(content){$('published').replaceChildren();for(const [key,label] of Object.entries(labels)){const heading=document.createElement('h3');heading.textContent=label;const text=document.createElement('div');text.textContent=content[key]?.join('\n')||'기록된 내용 없음';$('published').append(heading,text);}}
async function refresh(){
  if(!state||polling)return;polling=true;const version=generation;
  try{
    const [room,audio,summary,userSummary]=await Promise.all([request(path('/session')),request(path('/audio')),request(path('/summary'),'pharmacist'),request(path('/summary'),'user')]);
    if(version!==generation)return;ended=!!room.ended_at;
    if(ended&&typeof rtcHangup==='function'&&(typeof rtcStream!=='undefined'&&rtcStream))rtcHangup();
    $('rtc-join').disabled=ended||(typeof rtcStream!=='undefined'&&!!rtcStream)||(typeof rtcJoining!=='undefined'&&rtcJoining);
    if(ended&&$('call').hasAttribute('src')){$('call').removeAttribute('src');$('call').hidden=true;$('video-empty').hidden=false;$('video-empty').textContent='상담이 종료되었습니다. 채팅 기록은 계속 확인할 수 있습니다.';}
    $('peer').disabled=ended;
    const both=!!room.user_consent_at&&!!room.pharmacist_consent_at;
    for(const role of Object.keys(roles)){
      const given=!!room[role+'_consent_at'];$('consent-'+role).checked=given;$('consent-'+role).disabled=given||ended;
      $('text-'+role).disabled=ended;$('send-'+role).disabled=ended;
      const own=audio.filter(a=>a.sender_role===role),failed=own.filter(a=>a.status==='failed');
      $('audio-'+role).textContent=activeRecorder?.role===role?'녹음 중 · 종료 버튼을 눌러 업로드':!both?'양측 동의 후 사용 가능':`${own.filter(a=>a.status==='completed').length}개 전사 완료 / ${own.length}개 업로드${failed.length?' · 실패 파일 재전송 필요':''}`;
      $('record-'+role).disabled=!both||ended||uploadBusy||!!(activeRecorder&&activeRecorder.role!==role);
      for(const row of own.filter(a=>a.status==='completed'))pendingAudio.delete(row.client_id);
      let retry=$('audio-retry-'+role);if(!retry){retry=document.createElement('button');retry.id='audio-retry-'+role;$('record-'+role).parentElement.append(retry);}
      retry.onclick=run(async()=>{for(const row of failed){const saved=pendingAudio.get(row.client_id);if(!saved)throw Error('원음이 현재 탭에 없습니다. 새 시연을 시작해주세요.');await uploadAudio(saved);}await refresh();});
      retry.hidden=!failed.length;retry.textContent='실패한 음성 다시 전송';
    }
    $('finish').disabled=ended||!both||!state.ai_available||!!activeRecorder||uploadBusy||actionBusy||audio.some(a=>a.status!=='completed');
    $('sample').disabled=ended||actionBusy;$('video').disabled=ended;
    const names={not_requested:ended?'요약 없이 종료':'상담 진행 중',queued:'AI 요약 대기 중',processing:'AI 요약 생성 중',pending_review:'약사 확인 대기',published:'사용자에게 공개 완료',failed:'생성 실패 · 설정 확인 후 재시도'};
    $('summary-state').textContent=names[summary.status];$('retry').hidden=summary.status!=='failed';
    $('publish').disabled=summary.status!=='pending_review';
    if(summary.content&&!draftShown){renderDraft(summary.content);draftShown=true;}
    if(userSummary.content)renderPublished(userSummary.content);
    if(summary.status==='published')$('draft').querySelectorAll('textarea').forEach(el=>el.disabled=true);
    if(audio.some(a=>a.status==='completed')){const rows=await request(path('/transcript'),'pharmacist');if(version===generation)$('transcript').textContent=rows.map(a=>roles[a.sender_role]+': '+(a.text||'전사 처리 중')).join('\n\n');}
  }finally{polling=false;}
}
async function uploadAudio(item){
  const response=await fetch(path('/audio')+'?client_id='+item.id+'&start_ms='+item.start,{method:'POST',headers:{Authorization:'Bearer '+state[item.role+'_token'],'Content-Type':item.blob.type},body:item.blob});
  if(!response.ok){const result=await response.json();throw Error(result.detail||'업로드 실패');}
}
async function record(role){
  if(activeRecorder){activeRecorder.recorder.stop();return;}
  if(!navigator.mediaDevices||!window.MediaRecorder)throw Error('이 브라우저는 녹음을 지원하지 않습니다. Chrome 또는 Edge를 사용해주세요.');
  const stream=await navigator.mediaDevices.getUserMedia({audio:true});
  const mime=['audio/webm;codecs=opus','audio/mp4'].find(t=>MediaRecorder.isTypeSupported(t));
  if(!mime){stream.getTracks().forEach(t=>t.stop());throw Error('지원하는 녹음 형식이 없습니다.');}
  const recorder=new MediaRecorder(stream,{mimeType:mime}),chunks=[];const id=crypto.randomUUID(),started=Date.now();
  activeRecorder={role,recorder};$('record-'+role).textContent='녹음 종료 · 전사하기';$('start').disabled=true;
  recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
  recorder.onstop=async()=>{stream.getTracks().forEach(t=>t.stop());activeRecorder=null;uploadBusy=true;
    $('record-'+role).textContent='마이크로 음성 기록';
    const item={id,role,start:Math.max(0,started-(state.startedAt||started)),blob:new Blob(chunks,{type:mime})};pendingAudio.set(id,item);
    try{await uploadAudio(item);notice('음성을 업로드했습니다. 전사가 끝나면 약사 화면에서 원문을 볼 수 있습니다.');}
    catch(e){notice(e.message+' 새 시연 또는 재녹음으로 다시 시도해주세요.',true);}
    finally{uploadBusy=false;$('start').disabled=false;await refresh();}
  };
  recorder.start();await refresh();
}
$('start').onclick=run(start);
$('sample').onclick=run(sample);
$('video').onclick=run(openVideo);
$('peer').onclick=run(()=>{remember();const role=currentRole==='user'?'pharmacist':'user';const peer=window.open('/?role='+role,'moyak-peer-'+state.id+'-'+role);if(!peer)throw Error('팝업이 차단되었습니다. 이 사이트의 팝업을 허용해주세요.');});
$('finish').onclick=run(async()=>{
  actionBusy=true;$('finish').disabled=true;
  try{const existing=await request('/consultations/'+state.id,'pharmacist');
    if(existing.status==='pending')await request('/consultations/'+state.id+'/decision','pharmacist','POST',{pharmacist_id:state.pharmacist_id,approve:false,reason:'시연 상담 종료 · 약품 구매 승인 없음'});
    await request(path('/session/end'),'pharmacist','POST');notice('상담을 종료했습니다. 실제 AI 요약을 생성하고 있습니다. 완료되면 약사 초안이 나타납니다.');
  }finally{actionBusy=false;await refresh();}
});
$('retry').onclick=run(async()=>{await request(path('/summary/retry'),'pharmacist','POST');await refresh();});
$('publish').onclick=run(async()=>{const content={};for(const key of Object.keys(labels))content[key]=$('field-'+key).value.split('\n').map(t=>t.trim()).filter(Boolean);await request(path('/summary/publish'),'pharmacist','POST',content);notice('약사 확인이 완료되어 사용자 화면에 요약을 공개했습니다.');await refresh();});
setInterval(()=>refresh().catch(e=>notice(e.message,true)),1500);
window.addEventListener('beforeunload',e=>{if(activeRecorder||uploadBusy){e.preventDefault();e.returnValue='';}});
