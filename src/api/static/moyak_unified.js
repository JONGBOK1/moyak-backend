/* Same-origin API adapter using the backend's current ID/role contract; tokens take priority. */
(() => {
  'use strict';
  const nativeFetch = window.fetch.bind(window);
  const config = nativeFetch('/client-config').then(r => r.ok ? r.json() : {}).catch(() => ({}));
  const role = location.pathname === '/pharmacist' ? 'pharmacist' : 'user';
  const identity = () => ({
    sender_role: role,
    sender_id: role === 'pharmacist'
      ? (document.querySelector('#pharmacistId')?.value || localStorage.getItem('moyak_pharmacist_id'))
      : localStorage.getItem('moyak_user_id'),
  });
  const token = () => sessionStorage.getItem('moyak_access_token');
  const pending = new Map();
  const recordingGuards = new Map();
  window.fetch = async (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    if (url.origin !== location.origin || !/^\/(consultations|vending)(\/|$)/.test(url.pathname)) {
      return nativeFetch(input, init);
    }
    const ending = url.pathname.match(/^\/consultations\/([^/]+)\/(?:session\/)?end$/);
    if(ending && recordingGuards.get(ending[1])?.()) throw Error('녹음 중지와 업로드를 먼저 완료해주세요.');
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers).forEach((v, k) => headers.set(k, v));
    const auth = token(), who = identity();
    if (!headers.has('Authorization')) {
      if (auth) headers.set('Authorization', 'Bearer ' + auth);
      else if (who.sender_id && !location.pathname.startsWith('/kiosk/')) {
        headers.set('X-Moyak-User-Id', who.sender_id);
        headers.set('X-Moyak-Role', who.sender_role);
      }
    }
    let body = init.body, dedupe;
    if ((init.method || 'GET').toUpperCase() === 'POST' && url.pathname.endsWith('/messages') && typeof body === 'string') {
      const data = JSON.parse(body);
      dedupe = url.pathname + ':' + JSON.stringify([who.sender_id, data.content ?? data.text]);
      if (!data.client_id) {
        if (!pending.has(dedupe)) pending.set(dedupe, crypto.randomUUID());
        data.client_id = pending.get(dedupe);
      }
      body = JSON.stringify(data);
    }
    const response = await nativeFetch(input, {...init, headers, body});
    if (dedupe && response.ok) pending.delete(dedupe);
    return response;
  };
  async function api(path, body, rawType) {
    const r = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': rawType || 'application/json'},
      body: rawType ? body : JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) throw Error(typeof data.detail === 'string' ? data.detail : '요청 실패 (' + r.status + ')');
    return data;
  }
  function watchMessages(cid, render, status) {
    let socket, timer, stopped = false, cursor = 0;
    async function connect() {
      if (stopped) return;
      await config;
      if (!token() && !identity().sender_id) { status?.('사용자 ID를 입력해주세요.'); return; }
      const url = new URL('/consultations/' + cid + '/messages/ws', location.href);
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(url);
      socket.onopen = () => socket.send(JSON.stringify({after: cursor, ...(token() ? {token: token()} : identity())}));
      socket.onmessage = e => {
        const data = JSON.parse(e.data);
        if (data.type === 'messages') {
          render(data.items);
          for (const m of data.items) cursor = Math.max(cursor, m.id);
          status?.('실시간 채팅 연결됨');
        }
      };
      socket.onclose = () => { if (!stopped) timer = setTimeout(connect, 3000); };
      socket.onerror = () => status?.('채팅 재연결 중');
    }
    connect();
    const stop = () => { stopped = true; clearTimeout(timer); socket?.close(); };
    window.addEventListener('pagehide', stop, {once: true});
    return stop;
  }
  const labels = {symptoms:'증상', discussion:'상담 내용', medication_guidance:'복약 안내',
    precautions:'주의사항', follow_up:'후속 안내', needs_verification:'확인 사항'};
  const panels = new Map();
  function attachPanel(cid, target) {
    if(role==='pharmacist') {
      let root=document.getElementById('consultation-tools-root');
      if(!root){root=document.createElement('div');root.id='consultation-tools-root';document.body.append(root);}
      target=root;
    }
    if (!cid || !target || target.querySelector('[data-consult-tools]')) return;
    const box = document.createElement('section'); box.dataset.consultTools = cid;
    box.style.cssText = 'padding:12px;margin:10px 0;background:#fffdf5;border:1px solid #f1e3b8;border-radius:10px;color:#252525;font-size:13px;position:relative;z-index:2';
    const title = document.createElement('strong'); title.textContent = '음성 기록 · AI 요약 ('+cid.slice(0,8)+')'; box.append(title);
    const status = document.createElement('p'); status.textContent = '약사 연결 후 양측 동의가 필요합니다.'; box.append(status);
    const controls = document.createElement('div'); box.append(controls);
    function button(text, action) {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = text;
      b.style.cssText = 'margin:4px;padding:8px;border-radius:6px;cursor:pointer';
      b.onclick = async () => { b.disabled = true; try { await action(); } catch (e) { status.textContent=e.message; } finally { b.disabled=false; } };
      controls.append(b); return b;
    }
    const path = '/consultations/' + cid;
    if (role === 'pharmacist') button('상담 연결', async () => { await api(path+'/session/claim', {}); await refresh(); });
    button('음성 기록·AI 처리 동의', async () => {
      if (confirm('업로드한 마이크 음성과 상담 채팅·사전 챗봇 대화를 AI 전사·요약에 사용하는 데 동의하시나요?')) {
        await api(path+'/session/consent', {}); await refresh();
      }
    });
    let recorder, stream, chunks, recordingAt, clipId, uploadBody, uploading=false, ended=false, poll;
    recordingGuards.set(cid,()=>recorder?.state==='recording'||uploading||!!uploadBody);
    const clips=new Map();
    async function uploadClip() {
      if (!uploadBody || uploading) return;
      uploading=true;
      try {
        await api(path+'/audio?client_id='+clipId+'&start_ms='+recordingAt, uploadBody, uploadBody.type);
        clips.set(clipId,{blob:uploadBody,start:recordingAt});
        uploadBody=null; status.textContent='음성 업로드 완료 · 전사 대기 중';
      } finally { uploading=false; }
    }
    const recordButton = button('내 마이크 녹음', async () => {
      if (recorder?.state === 'recording') { recorder.stop(); return; }
      if (uploadBody) { await uploadClip(); return; }
      const session = await api(path+'/session');
      if (session.ended_at || !session.user_consent_at || !session.pharmacist_consent_at) throw Error('진행 중인 상담에서 양측 동의 후 녹음할 수 있습니다.');
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw Error('이 브라우저는 녹음을 지원하지 않습니다.');
      const consultation=await api(path);
      recordingAt=Math.max(0,Date.now()-Date.parse(consultation.created_at.replace(/Z$/,'')+'Z'));
      if(recordingAt>7200000)throw Error('상담 시작 후 2시간이 지나 새 음성 기록을 추가할 수 없습니다.');
      stream = await navigator.mediaDevices.getUserMedia({audio:true});
      chunks=[]; clipId=crypto.randomUUID();
      recorder=new MediaRecorder(stream);
      recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      recorder.onstop=async()=>{
        stream.getTracks().forEach(t=>t.stop());
        recordButton.textContent='내 마이크 녹음';
        uploadBody=new Blob(chunks,{type:recorder.mimeType});
        if (uploadBody.size>20*1024*1024) { uploadBody=null;status.textContent='녹음이 20 MiB를 초과했습니다. 짧게 나눠 녹음해주세요.';return; }
        try { await uploadClip(); } catch(e) {status.textContent=e.message+' · 녹음 버튼을 눌러 같은 파일을 재전송하세요.';}
      };
      recorder.start(1000); recordButton.textContent='녹음 중지·업로드';
      status.textContent='이 기기의 마이크만 녹음합니다. 상대방도 자신의 기기에서 녹음해주세요.';
    });
    button('실패한 내 음성 재전송',async()=>{
      const audio=await api(path+'/audio');let count=0;
      for(const row of audio.filter(r=>r.sender_role===role&&r.status==='failed')) {
        const clip=clips.get(row.client_id);
        if(!clip)throw Error('재전송할 원본이 이 화면에 없습니다. 아래 파일 선택으로 원본 음성을 다시 업로드해주세요.');
        await api(path+'/audio?client_id='+row.client_id+'&start_ms='+clip.start,clip.blob,clip.blob.type);count++;
      }
      status.textContent=count+'개 재전송 완료';
    });
    const recovery=document.createElement('input');recovery.type='file';recovery.accept='audio/*';
    recovery.title='새로고침 등으로 원본이 없을 때 실패 음성 원본 선택';controls.append(recovery);
    button('선택 파일로 실패 음성 복구',async()=>{
      const file=recovery.files[0];if(!file)throw Error('원본 음성 파일을 선택해주세요.');
      const rows=(await api(path+'/audio')).filter(r=>r.sender_role===role&&r.status==='failed');
      if(!rows.length)throw Error('실패한 내 음성이 없습니다.');
      const wanted=prompt('복구할 음성 번호: '+rows.map(r=>r.id).join(', '),String(rows[0].id));
      const row=rows.find(r=>String(r.id)===wanted);if(!row)return;
      const type=file.type||({'wav':'audio/wav','mp3':'audio/mpeg','m4a':'audio/mp4','webm':'audio/webm'}[file.name.split('.').pop().toLowerCase()]);
      if(!type)throw Error('지원하는 음성 파일 형식을 확인해주세요.');
      await api(path+'/audio?client_id='+row.client_id+'&start_ms='+row.start_ms,file,type);
      status.textContent='음성 복구 업로드 완료';
    });
    const result=document.createElement('div');box.append(result);
    let draftKey='';
    async function refresh() {
      if (!box.isConnected) { clearInterval(poll); return; }
      try {
        const [room, audio, summary] = await Promise.all([api(path+'/session'),api(path+'/audio'),api(path+'/summary')]);
        ended=!!room.ended_at;
        for(const row of audio.filter(r=>r.status==='completed')) clips.delete(row.client_id);
        if (recorder?.state !== 'recording' && !uploadBody) status.textContent =
          (ended?'상담 종료 · ':'')+'전사 완료 '+audio.filter(a=>a.status==='completed').length+'/'+audio.length+
          ' · 요약: '+({not_requested:'미요청',queued:'대기',processing:'생성 중',pending_review:'약사 검토 대기',published:'공개 완료',failed:'생성 실패'}[summary.status]||summary.status);
        const failed=audio.filter(a=>a.status==='failed');
        if(failed.length) status.textContent+=' · 음성 전사 실패: 크레딧/연결 확인 후 해당 음성을 재시도해주세요.';
        recordButton.disabled=ended || uploading;
        if (role==='pharmacist' && summary.status==='pending_review' && draftKey!==cid) {
          draftKey=cid; result.replaceChildren();
          const fields={};
          for(const [key,label] of Object.entries(labels)) {
            const l=document.createElement('label');l.textContent=label;
            const t=document.createElement('textarea');t.style.cssText='display:block;width:95%;margin:5px 0';
            t.value=(summary.content[key]||[]).join('\n');fields[key]=t;l.append(t);result.append(l);
          }
          const publish=document.createElement('button');publish.textContent='검토한 요약 공개';result.append(publish);
          publish.onclick=async()=>{publish.disabled=true;try {
            const content=Object.fromEntries(Object.entries(fields).map(([k,t])=>[k,t.value.split('\n').map(s=>s.trim()).filter(Boolean)]));
            await api(path+'/summary/publish',content);result.textContent='요약을 공개했습니다.';
          }catch(e){status.textContent=e.message;publish.disabled=false;}};
        } else if(summary.status==='published') {
          result.textContent=Object.entries(labels).map(([k,l])=>l+': '+(summary.content[k]||[]).join(' / ')).join('\n');
          result.style.whiteSpace='pre-wrap';
        }
      } catch(e) { if(recorder?.state!=='recording') status.textContent=e.message; }
    }
    if(role==='pharmacist') {
      button('상담 종료·요약 요청',async()=>{
        if (recorder?.state==='recording'||uploading||uploadBody) throw Error('녹음 중지와 업로드를 먼저 완료해주세요.');
        await api(path+'/session/end',{});await refresh();
      });
      button('실패한 요약 재시도',async()=>{await api(path+'/summary/retry',{});await refresh();});
      button('전사 확인',async()=>{const rows=await api(path+'/transcript');alert(rows.map(r=>r.sender_role+': '+(r.text||r.status)).join('\n'));});
    }
    target.append(box);panels.set(cid,box);refresh();poll=setInterval(refresh,3000);
    window.addEventListener('pagehide',()=>{clearInterval(poll);stream?.getTracks().forEach(t=>t.stop());},{once:true});
  }
  window.MoyakUnified={api,watchMessages,attachPanel};
  config.then(c=>{
    if(role==='pharmacist' && !localStorage.getItem('moyak_pharmacist_id')) {
      const input=document.querySelector('#pharmacistId');
      const id=input?.value || 'local-pharmacist';
      localStorage.setItem('moyak_pharmacist_id',id);
      if(input)input.value=id;
    }
  });
})();
