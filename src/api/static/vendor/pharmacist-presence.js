/**
 * 약사 화상방 입장 알림 (사용자 앱의 약사 대기 / 화상 입장 미리보기 화면에서 사용).
 *
 * 3초마다 GET /consultations/{id}/presence 를 확인해서, 약사가 화상방에 들어오는 순간
 *   - 화면 위쪽에 "약사가 화상 상담에 들어왔어요" 배너 + [지금 입장하기] 버튼
 *   - 알림음(두 음 차임) + 진동(안드로이드만 지원)
 * 으로 알려준다. 약사가 나가면 배너를 내린다.
 *
 * 브라우저 정책상 소리/진동은 사용자가 그 화면을 한 번이라도 터치한 뒤에만 울릴 수 있다
 * (터치 전이면 배너만 표시됨). 그래서 화면 첫 터치 때 오디오를 미리 깨워 둔다.
 *
 * 사용: const stop = MoyakPresence.watch({ consultationId, onJoin, onLeave, onEnter });
 */
(function () {
  var audioCtx = null;

  function unlockAudio() {
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume();
    } catch (e) {}
  }
  ["pointerdown", "touchstart", "keydown"].forEach(function (ev) {
    document.addEventListener(ev, unlockAudio, { passive: true });
  });

  function chime() {
    try {
      unlockAudio();
      if (!audioCtx || audioCtx.state !== "running") return;
      [880, 1320].forEach(function (freq, i) {
        var t = audioCtx.currentTime + i * 0.22;
        var osc = audioCtx.createOscillator();
        var gain = audioCtx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(0.35, t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(t);
        osc.stop(t + 0.4);
      });
    } catch (e) {}
    try {
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
    } catch (e) {}
  }

  function ensureStyle() {
    if (document.getElementById("moyak-presence-style")) return;
    var style = document.createElement("style");
    style.id = "moyak-presence-style";
    style.textContent =
      ".moyak-presence-banner{position:absolute;left:16px;right:16px;top:52px;z-index:60;background:#3e2723;color:#fff;" +
      "border-radius:20px;padding:14px 16px;display:flex;align-items:center;gap:12px;box-shadow:0 12px 28px rgba(62,39,35,.35);" +
      "animation:moyakDrop .35s ease-out}" +
      ".moyak-presence-banner .dot{width:10px;height:10px;border-radius:5px;background:#4cd964;flex-shrink:0;" +
      "box-shadow:0 0 0 0 rgba(76,217,100,.7);animation:moyakPulse 1.4s infinite}" +
      ".moyak-presence-banner .txt{flex:1;min-width:0}" +
      ".moyak-presence-banner .t1{font-size:14px;font-weight:800}" +
      ".moyak-presence-banner .t2{font-size:11px;color:#e8dcd4;margin-top:2px}" +
      ".moyak-presence-banner button{flex-shrink:0;border:none;border-radius:14px;background:#ffd54f;color:#3e2723;" +
      "font-size:13px;font-weight:800;padding:10px 12px;cursor:pointer}" +
      "@keyframes moyakDrop{from{transform:translateY(-16px);opacity:0}to{transform:none;opacity:1}}" +
      "@keyframes moyakPulse{70%{box-shadow:0 0 0 8px rgba(76,217,100,0)}100%{box-shadow:0 0 0 0 rgba(76,217,100,0)}}";
    document.head.appendChild(style);
  }

  function showBanner(onEnter) {
    ensureStyle();
    hideBanner();
    var root = document.body.firstElementChild || document.body; // 앱 디자인 루트 (app-scale.js가 축소하는 대상)
    var el = document.createElement("div");
    el.className = "moyak-presence-banner";
    el.id = "moyakPresenceBanner";
    el.innerHTML =
      '<div class="dot"></div><div class="txt"><div class="t1">약사가 화상 상담에 들어왔어요</div>' +
      '<div class="t2">지금 입장하면 바로 상담을 시작할 수 있어요</div></div><button type="button">지금 입장하기</button>';
    el.querySelector("button").addEventListener("click", onEnter);
    root.appendChild(el);
  }

  function hideBanner() {
    var el = document.getElementById("moyakPresenceBanner");
    if (el) el.remove();
  }

  function watch(opts) {
    var inRoom = false;
    var stopped = false;
    async function check() {
      if (stopped) return;
      try {
        var res = await fetch("/consultations/" + encodeURIComponent(opts.consultationId) + "/presence");
        if (!res.ok) return;
        var data = await res.json();
        if (!data.available || stopped) return;
        if (data.pharmacist_in_room && !inRoom) {
          inRoom = true;
          showBanner(opts.onEnter);
          chime();
          if (opts.onJoin) opts.onJoin();
        } else if (!data.pharmacist_in_room && inRoom) {
          inRoom = false;
          hideBanner();
          if (opts.onLeave) opts.onLeave();
        }
      } catch (e) {
        // 네트워크 순단은 다음 확인에서 재시도
      }
    }
    check();
    var timer = setInterval(check, 3000);
    return function stop() {
      stopped = true;
      clearInterval(timer);
      hideBanner();
    };
  }

  window.MoyakPresence = { watch: watch };
})();
