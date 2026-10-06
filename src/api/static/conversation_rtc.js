const rtcContainer=document.createElement('div');rtcContainer.id='rtc';rtcContainer.hidden=true;
rtcContainer.innerHTML='<div class="rtc-stage"><video id="remote-video" autoplay playsinline controls></video><video id="local-video" autoplay playsinline muted></video><span id="rtc-status" class="rtc-caption" role="status">통화 참여를 눌러주세요</span></div><div class="rtc-controls"><button id="rtc-join" class="primary">통화 참여</button><button id="rtc-mic" disabled>마이크 끄기</button><button id="rtc-camera" disabled>카메라 끄기</button><button id="rtc-leave" disabled>통화 나가기</button></div>';
$('video-panel').append(rtcContainer);
let rtcPeer=null,rtcSocket=null,rtcStream=null,rtcJoining=false,rtcEpoch=0,rtcCandidates=[];
function rtcHangup(){
  rtcEpoch++;rtcJoining=false;
  if(rtcSocket){rtcSocket.onclose=null;rtcSocket.close();rtcSocket=null;}
  if(rtcPeer){rtcPeer.close();rtcPeer=null;}
  if(rtcStream){rtcStream.getTracks().forEach(t=>t.stop());rtcStream=null;}
  $('local-video').srcObject=null;$('remote-video').srcObject=null;rtcCandidates=[];
  $('rtc-join').disabled=ended;$('rtc-mic').disabled=true;$('rtc-camera').disabled=true;$('rtc-leave').disabled=true;
  $('rtc-status').textContent=ended?'상담 종료':'통화에서 나왔습니다. 다시 참여할 수 있습니다.';
}
function makePeer(epoch){
  if(rtcPeer)rtcPeer.close();rtcCandidates=[];
  const pc=new RTCPeerConnection({iceServers:[]});rtcPeer=pc;
  for(const track of rtcStream.getTracks())pc.addTrack(track,rtcStream);
  pc.onicecandidate=e=>{if(epoch===rtcEpoch&&e.candidate&&rtcSocket?.readyState===WebSocket.OPEN)rtcSocket.send(JSON.stringify({type:'candidate',candidate:e.candidate.toJSON()}));};
  pc.ontrack=e=>{if(epoch!==rtcEpoch)return;$('remote-video').srcObject=e.streams[0];$('remote-video').play().catch(()=>notice('상대 영상의 재생 버튼을 눌러 소리를 켜주세요.'));};
  pc.onconnectionstatechange=()=>{if(epoch!==rtcEpoch)return;const text={connected:'영상통화 연결됨 · 옆에서 채팅하세요',connecting:'상대방과 연결 중',disconnected:'연결이 잠시 끊겼습니다',failed:'연결 실패 · 나간 뒤 다시 참여해주세요'};if(text[pc.connectionState])$('rtc-status').textContent=text[pc.connectionState];};
  return pc;
}
async function rtcJoin(){
  if(!state||ended)throw Error('진행 중인 상담에서 참여해주세요.');
  if(rtcJoining||rtcStream)return;
  rtcJoining=true;const epoch=++rtcEpoch,role=currentRole,cid=state.id;$('rtc-join').disabled=true;
  try{
    const stream=await navigator.mediaDevices.getUserMedia({video:true,audio:true});
    if(epoch!==rtcEpoch){stream.getTracks().forEach(t=>t.stop());return;}
    rtcStream=stream;$('local-video').srcObject=stream;
    $('rtc-mic').disabled=false;$('rtc-camera').disabled=false;$('rtc-leave').disabled=false;
    $('rtc-mic').textContent='마이크 끄기';$('rtc-camera').textContent='카메라 끄기';makePeer(epoch);
    const ws=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/demo/'+cid+'/signal');rtcSocket=ws;
    ws.onopen=()=>ws.send(JSON.stringify({token:state[role+'_token']}));
    let chain=Promise.resolve();
    ws.onmessage=event=>{chain=chain.then(async()=>{
      if(epoch!==rtcEpoch)return;const data=JSON.parse(event.data);
      if(data.type==='waiting')$('rtc-status').textContent='상대방이 통화에 참여하기를 기다립니다';
      if(data.type==='peer-left'){$('remote-video').srcObject=null;makePeer(epoch);$('rtc-status').textContent='상대방이 나갔습니다 · 다시 참여하면 연결됩니다';}
      if(data.type==='peer-ready'&&role==='user'){await rtcPeer.setLocalDescription(await rtcPeer.createOffer());ws.send(JSON.stringify({type:'offer',description:rtcPeer.localDescription}));}
      if(data.type==='offer'||data.type==='answer'){
        await rtcPeer.setRemoteDescription(data.description);
        for(const candidate of rtcCandidates)await rtcPeer.addIceCandidate(candidate);rtcCandidates=[];
        if(data.type==='offer'){await rtcPeer.setLocalDescription(await rtcPeer.createAnswer());ws.send(JSON.stringify({type:'answer',description:rtcPeer.localDescription}));}
      }
      if(data.type==='candidate'){if(rtcPeer.remoteDescription)await rtcPeer.addIceCandidate(data.candidate);else rtcCandidates.push(data.candidate);}
    }).catch(e=>{rtcHangup();notice('영상 연결에 실패했습니다. 다시 참여해주세요. '+e.message,true);});};
    ws.onclose=()=>{if(epoch===rtcEpoch){rtcHangup();notice('통화 연결이 종료되었습니다. 같은 역할로 중복 참여했거나 인증이 만료되었을 수 있습니다.',true);}};
    $('rtc-status').textContent='통화방에 연결 중';
  }catch(e){rtcHangup();throw Error('카메라와 마이크 사용을 허용해주세요. '+e.message);}
  finally{rtcJoining=false;}
}
$('rtc-join').onclick=run(rtcJoin);$('rtc-leave').onclick=rtcHangup;
$('rtc-mic').onclick=()=>{const track=rtcStream?.getAudioTracks()[0];if(track){track.enabled=!track.enabled;$('rtc-mic').textContent=track.enabled?'마이크 끄기':'마이크 켜기';}};
$('rtc-camera').onclick=()=>{const track=rtcStream?.getVideoTracks()[0];if(track){track.enabled=!track.enabled;$('rtc-camera').textContent=track.enabled?'카메라 끄기':'카메라 켜기';}};
window.addEventListener('pagehide',rtcHangup);
run(async()=>{const saved=sessionStorage.getItem('moyak-demo-session');if(saved){await activate(JSON.parse(saved));notice('같은 상담방에 연결했습니다. 통화 참여를 누르고 옆 채팅창을 사용하세요.');}})();
