/**
 * 상담 음성 기록 · AI 요약 패널 (사용자 앱 화상 화면 + 약사 콘솔 공용).
 * 서버: /consultations/{id}/session|session/claim|session/consent|audio|transcript|session/end|summary(/publish|/retry)
 *
 * - 양측이 직접 "음성 기록·AI 요약"에 동의해야 녹음 업로드가 가능하다.
 * - 양측 동의가 끝나면 녹음이 자동으로 시작된다(일시정지 가능). 각자 기기 마이크만 녹음하고(Daily 통화 녹화 아님),
 *   20초마다 조각으로 잘라 자동 업로드 → 서버 워커가 전사. 조각의 시작 시각은 "양측 동의 시점부터 경과 시간"(서버 기준)이라
 *   두 사람의 조각이 시간순으로 섞여 하나의 대화록이 된다. 말소리가 거의 없는 조각은 올리지 않는다(비용·환각 방지).
 * - 전사 실패 시 같은 조각을 재전송(같은 client_id라 중복 저장 없음). 업로드 전 마지막 조각(최대 20초)은 페이지를 떠나면 사라진다.
 * - 약사만: 상담 연결(담당 배정), 전사 원문 보기, 상담 종료, 요약 초안 6항목 검토·수정 후 공개, 실패 시 재시도.
 *
 * 사용: MoyakRecording.mount(containerEl, { consultationId, actorId, role: "user" | "pharmacist", onRecordingChange? })
 *       반환값 .stop()은 녹음 중인 마지막 조각을 올리고 멈춘다(통화에서 나갈 때 호출).
 */
(function () {
  const LABELS = {
    symptoms: "호소한 증상", discussion: "상담 내용", medication_guidance: "복약 안내",
    precautions: "주의사항", follow_up: "후속 안내", needs_verification: "확인이 필요한 내용",
  };
  const AUDIO_STATUS = { queued: "전사 대기", processing: "전사 중", completed: "전사 완료", failed: "전사 실패" };
  const SEGMENT_MS = 20000;   // 조각 길이
  const SILENCE_LEVEL = 0.02; // 조각 안 최대 음량(RMS)이 이보다 작으면 무음으로 보고 버림
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function injectStyle() {
    if (document.getElementById("moyak-rec-style")) return;
    const s = document.createElement("style");
    s.id = "moyak-rec-style";
    s.textContent = `
      .mrec { font-size: 13px; color: #2b2b2b; display: flex; flex-direction: column; gap: 8px; }
      .mrec .row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
      .mrec button { border: none; border-radius: 10px; padding: 8px 12px; font-weight: 700; cursor: pointer; background: #176b5b; color: #fff; font-size: 13px; }
      .mrec button.alt { background: #fff; color: #176b5b; border: 1.5px solid #176b5b; }
      .mrec button:disabled { opacity: .45; cursor: default; }
      .mrec .pill { border-radius: 99px; padding: 3px 9px; font-size: 11px; font-weight: 700; background: #f1ece2; color: #6b5d50; }
      .mrec .pill.ok { background: #e3f4ec; color: #176b5b; }
      .mrec .pill.live { background: #fdecea; color: #c62828; }
      .mrec .note { color: #7d6d6a; font-size: 12px; line-height: 1.5; }
      .mrec .err { color: #c0392b; font-size: 12px; }
      .mrec .consent-box { background: #fffdf5; border: 1px solid #f1e3b8; border-radius: 10px; padding: 10px; line-height: 1.5; }
      .mrec textarea { width: 100%; min-height: 54px; border: 1px solid #ddd; border-radius: 8px; padding: 6px; font-size: 13px; box-sizing: border-box; }
      .mrec .sum-label { font-weight: 800; color: #593d0d; margin-top: 4px; }
      .mrec .transcript { background: #fafafa; border-radius: 8px; padding: 8px; max-height: 160px; overflow-y: auto; white-space: pre-wrap; }
    `;
    document.head.appendChild(s);
  }

  function mount(container, opts) {
    injectStyle();
    const { consultationId, role } = opts;
    const isPharm = role === "pharmacist";
    const base = `/consultations/${encodeURIComponent(consultationId)}`;
    const blobs = {}; // client_id → {blob, startMs} — 실패 시 재전송용 (페이지를 떠나면 사라짐)
    let stream = null, recorder = null, segTimer = null, meter = null, timer = null, stopped = false;
    let paused = false, micError = false, anchor = null; // anchor = 이 기기 시계로 본 "양측 동의 시점"
    let lastSummaryStatus = null, lastRoom = null;
    const setRecording = (on) => opts.onRecordingChange && opts.onRecordingChange(on);

    const root = document.createElement("div");
    root.className = "mrec";
    container.appendChild(root);

    async function call(method, path, body, extraHeaders) {
      const id = typeof opts.actorId === "function" ? opts.actorId() : opts.actorId;
      if (!id) { const e = new Error(isPharm ? "약사 ID를 먼저 입력해주세요." : "사용자 정보가 없어요."); e.status = 0; throw e; }
      const headers = { "X-Moyak-User-Id": encodeURIComponent(id), "X-Moyak-Role": role, ...(extraHeaders || {}) };
      if (body && !(body instanceof Blob)) headers["Content-Type"] = "application/json";
      const res = await fetch(base + path, {
        method, headers,
        body: body instanceof Blob ? body : body ? JSON.stringify(body) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { const e = new Error(data.detail || `요청 실패 (${res.status})`); e.status = res.status; throw e; }
      return data;
    }
    const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const mmss = (ms) => {
      const s = Math.floor((ms || 0) / 1000);
      return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    };

    root.innerHTML = `
      <div class="row"><b>🎙 음성 기록 · AI 요약</b><span class="pill" data-k="conn">확인 중</span></div>
      <div data-k="body"></div>
      <div class="err" data-k="err"></div>`;
    const $ = (k) => root.querySelector(`[data-k="${k}"]`);
    const showErr = (e) => ($("err").textContent = e ? (e.message || String(e)) : "");

    async function upload(clientId) {
      const { blob, startMs } = blobs[clientId];
      const type = (blob.type || "audio/webm").split(";")[0];
      await call("POST", `/audio?client_id=${clientId}&start_ms=${startMs}`, blob, { "Content-Type": type });
    }

    // ---------- 자동 녹음: 마이크 스트림 하나를 열어두고 20초마다 새 MediaRecorder로 조각을 만든다 ----------
    async function startRec() {
      if (stream || micError) return;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (e) {
        micError = true;
        showErr(new Error("마이크를 사용할 수 없어 녹음하지 못해요: " + e.message));
        return;
      }
      try { // 조각마다 최대 음량을 재서 무음 조각은 올리지 않는다
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        ctx.createMediaStreamSource(stream).connect(analyser);
        const buf = new Float32Array(analyser.fftSize);
        meter = { ctx, peak: 0 };
        meter.id = setInterval(() => {
          analyser.getFloatTimeDomainData(buf);
          let sum = 0;
          for (const v of buf) sum += v * v;
          meter.peak = Math.max(meter.peak, Math.sqrt(sum / buf.length));
        }, 200);
      } catch (e) {
        meter = null; // 음량 측정이 안 되면 무음 판별 없이 모두 올린다
      }
      setRecording(true);
      startSegment();
      refresh();
    }

    function startSegment() {
      const rec = new MediaRecorder(stream);
      const parts = [];
      const startMs = Math.min(7200000, Math.max(0, Date.now() - (anchor ?? Date.now())));
      if (meter) meter.peak = 0;
      rec.ondataavailable = (e) => e.data.size && parts.push(e.data);
      rec.onstop = () => {
        const loud = !meter || meter.peak >= SILENCE_LEVEL;
        if (loud && parts.length) {
          const blob = new Blob(parts, { type: rec.mimeType || "audio/webm" });
          const clientId = "rec_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
          blobs[clientId] = { blob, startMs };
          rec._upload = upload(clientId).catch(showErr);
        }
        if (rec._next && stream) startSegment();
        if (rec._done) rec._done(rec._upload);
      };
      rec.start();
      recorder = rec;
      segTimer = setTimeout(() => { rec._next = true; rec.stop(); }, SEGMENT_MS);
    }

    // 녹음 중인 조각을 마무리·업로드하고 마이크를 닫는다
    function stopRec() {
      return new Promise((resolve) => {
        clearTimeout(segTimer);
        const rec = recorder;
        recorder = null;
        const close = (pending) => {
          if (stream) stream.getTracks().forEach((t) => t.stop());
          stream = null;
          if (meter) { clearInterval(meter.id); meter.ctx.close().catch(() => {}); meter = null; }
          setRecording(false);
          Promise.resolve(pending).then(resolve);
        };
        if (rec && rec.state !== "inactive") { rec._done = close; rec.stop(); } else close();
      });
    }

    // 양측 동의 + 진행 중이면 자동 녹음, 일시정지/종료면 멈춤
    function syncRecording(room) {
      const want = !!(room && !room.ended_at && room.user_consent_at && room.pharmacist_consent_at && !paused && !stopped);
      if (want && !stream) startRec();
      else if (!want && stream) stopRec();
    }

    function render(state) {
      const { room, files, summary, transcript } = state;
      const body = $("body");
      if (!room) {
        $("conn").textContent = "약사 연결 전";
        body.innerHTML = isPharm
          ? `<div class="note">이 상담을 맡으면 녹음·전사·AI 요약 기능을 쓸 수 있어요.</div>
             <div class="row"><button data-a="claim">상담 연결 (담당 약사로 배정)</button></div>`
          : `<div class="note">약사가 상담을 연결하면 녹음·AI 요약에 동의할 수 있어요.</div>`;
        return;
      }
      $("conn").textContent = room.ended_at ? "상담 종료됨" : "연결됨";
      $("conn").className = "pill" + (room.ended_at ? "" : " ok");
      const mine = isPharm ? room.pharmacist_consent_at : room.user_consent_at;
      const both = room.user_consent_at && room.pharmacist_consent_at;
      const recording = !!stream;
      let html = `<div class="row">
          <span class="pill ${room.user_consent_at ? "ok" : ""}">환자 동의 ${room.user_consent_at ? "✓" : "대기"}</span>
          <span class="pill ${room.pharmacist_consent_at ? "ok" : ""}">약사 동의 ${room.pharmacist_consent_at ? "✓" : "대기"}</span></div>`;
      if (!room.ended_at && !mine) {
        html += `<div class="consent-box">상담 중 <b>내 기기 마이크 소리를 자동으로 녹음</b>해 글자로 바꾸고(음성 전사), 채팅·사전 챗봇 대화와 함께
          <b>AI가 상담 요약</b>을 만드는 데 동의합니다. 요약은 약사가 검토한 뒤 공개되며, 원본 음성은 전사 후 삭제됩니다.
          <div class="row" style="margin-top:6px"><button data-a="consent">동의합니다</button></div></div>`;
      }
      if (!room.ended_at && both) {
        const label = recording ? "🔴 자동 녹음 중" : paused ? "⏸ 일시정지됨" : micError ? "마이크 사용 불가" : "녹음 준비 중";
        html += `<div class="row"><span class="pill ${recording ? "live" : ""}">${label}</span>
            <button class="alt" data-a="pause">${paused || micError ? "▶ 녹음 다시 시작" : "⏸ 잠깐 멈추기"}</button></div>
          <div class="note">내 마이크 소리만 20초마다 자동으로 올라가 글로 바뀌어요. 말소리가 없는 구간은 올리지 않아요.</div>`;
      } else if (!room.ended_at && mine && !both) {
        html += `<div class="note">상대방이 동의하면 녹음이 자동으로 시작돼요.</div>`;
      }
      const shown = files.filter((f) => isPharm || f.sender_role === role);
      if (shown.length) {
        const count = (st) => shown.filter((f) => f.status === st).length;
        const busy = count("queued") + count("processing");
        html += `<div class="note">녹음 조각 ${shown.length}개 · 전사 완료 ${count("completed")}` +
          (busy ? ` · 전사 중 ${busy}` : "") +
          (count("failed") ? ` · <span class="err">실패 ${count("failed")}</span>` : "") + `</div>`;
        const retry = shown.filter((f) => f.status === "failed" && blobs[f.client_id]);
        if (retry.length) html += `<div class="row"><button class="alt" data-a="reup">실패한 조각 다시 보내기 (${retry.length})</button></div>`;
      }
      if (isPharm) {
        const lines = (transcript || []).filter((t) => t.status !== "completed" || (t.text || "").trim());
        if (lines.length) {
          html += `<div class="sum-label">전사 원문 (동의 시점부터 시간순)</div><div class="transcript">${lines.map((t) =>
            `${mmss(t.start_ms)} [${t.sender_role === "pharmacist" ? "약사" : "환자"}] ${esc(t.text || "(" + (AUDIO_STATUS[t.status] || t.status) + ")")}`).join("\n")}</div>`;
        }
        if (!room.ended_at) {
          html += `<div class="row"><button class="alt" data-a="end">상담 종료 (요약 생성)</button></div>
            <div class="note">승인/거절을 먼저 확정한 뒤 종료해주세요. 마지막 녹음 조각이 글로 바뀔 때까지 잠깐 기다렸다가 종료돼요. 동의가 없으면 AI 요약 없이 종료돼요.</div>`;
        }
      }
      html += renderSummary(summary, room);
      body.innerHTML = html;
    }

    function renderSummary(summary, room) {
      if (!room.ended_at || !summary) return "";
      const st = summary.status;
      if (st === "not_requested") return `<div class="note">AI 요약 없이 종료됐어요. (녹음 동의가 없었거나 요약할 대화가 없었어요)</div>`;
      if (st === "queued" || st === "processing") return `<div class="note">📝 ${isPharm ? "요약 초안을 만드는 중…" : "약사가 상담 요약을 준비하고 있어요."}</div>`;
      if (st === "failed") return isPharm
        ? `<div class="err">요약 생성에 실패했어요.</div><div class="row"><button data-a="retry">요약 다시 만들기</button></div>`
        : `<div class="note">약사가 상담 요약을 준비하고 있어요.</div>`;
      if (st === "pending_review") {
        if (!isPharm) return `<div class="note">📝 약사가 요약을 검토하고 있어요. 검토가 끝나면 여기에 표시돼요.</div>`;
        return `<div class="sum-label">📝 요약 초안 검토 (한 줄에 한 항목, 수정 후 공개)</div>` +
          Object.entries(LABELS).map(([k, label]) =>
            `<div class="sum-label">${label}</div><textarea data-f="${k}">${esc((summary.content?.[k] || []).join("\n"))}</textarea>`).join("") +
          `<div class="row"><button data-a="publish">검토 완료 · 환자에게 공개</button></div>`;
      }
      if (st === "published") {
        const c = summary.content || {};
        return `<div class="sum-label">📝 약사가 검토한 상담 요약</div>` + Object.entries(LABELS)
          .filter(([k]) => (c[k] || []).length)
          .map(([k, label]) => `<div><b>${label}</b><br>${(c[k] || []).map((x) => "· " + esc(x)).join("<br>")}</div>`).join("");
      }
      return "";
    }

    let everConnected = false, busyAction = false;
    async function refresh() {
      if (stopped) return;
      // 카드가 화면에 붙기 전에 마운트될 수 있다 — 한 번이라도 붙었다가 사라졌을 때만 멈춘다
      if (!root.isConnected) {
        if (everConnected) { stopped = true; clearInterval(timer); stopRec(); }
        return;
      }
      everConnected = true;
      if (busyAction) return; // 종료 처리 중엔 버튼 상태를 덮어쓰지 않는다
      // 약사가 요약 초안을 고치는 중이면 다시 그려서 입력을 지우지 않는다
      if (lastSummaryStatus === "pending_review" && isPharm && root.querySelector("textarea")) return;
      let room = null, files = [], summary = null, transcript = null;
      try {
        room = await call("GET", "/session");
        if (room.recording_elapsed_ms != null) anchor = Date.now() - room.recording_elapsed_ms;
      } catch (e) {
        if (e.status !== 409 && e.status !== 403) showErr(e);
      }
      if (room) {
        try {
          files = await call("GET", "/audio");
          if (isPharm) transcript = await call("GET", "/transcript");
          if (room.ended_at) summary = await call("GET", "/summary");
        } catch (e) { showErr(e); }
      }
      if (busyAction || stopped) return;
      lastSummaryStatus = summary && summary.status;
      lastRoom = room;
      syncRecording(room);
      render({ room, files, summary, transcript });
      if (summary && summary.status === "published") { stopped = true; clearInterval(timer); }
    }

    async function endConsultation(btn) {
      if (window.confirm && !window.confirm("상담을 종료할까요? 종료하면 채팅·녹음을 더 할 수 없고 요약 초안이 만들어져요.")) return;
      btn.textContent = "마지막 녹음 정리 중…";
      paused = true;
      await stopRec(); // 마지막 조각까지 업로드
      for (let i = 0; ; i++) { // 상대방 마지막 조각이 도착·전사될 시간을 주고, 전사 중이면 최대 약 40초 기다린다
        await sleep(i === 0 ? 1500 : 2000);
        try {
          await call("POST", "/session/end");
          return;
        } catch (err) {
          if (err.status !== 409 || !/글로 바꾸는 중/.test(err.message) || i >= 20) { paused = false; throw err; }
          btn.textContent = "녹음을 글로 바꾸는 중…";
        }
      }
    }

    root.addEventListener("click", async (e) => {
      const a = e.target.closest("[data-a]");
      if (!a) return;
      showErr();
      a.disabled = true;
      const act = a.dataset.a;
      if (act === "end") busyAction = true;
      try {
        if (act === "claim") await call("POST", "/session/claim");
        if (act === "consent") await call("POST", "/session/consent");
        if (act === "pause") {
          paused = !(paused || micError); // 멈춤 ↔ 재시작 (마이크 오류 상태에서 누르면 다시 시도)
          micError = false;
          syncRecording(lastRoom);
        }
        if (act === "reup") {
          for (const f of await call("GET", "/audio")) if (f.status === "failed" && blobs[f.client_id]) await upload(f.client_id);
        }
        if (act === "end") await endConsultation(a);
        if (act === "retry") await call("POST", "/summary/retry");
        if (act === "publish") {
          const content = {};
          root.querySelectorAll("textarea[data-f]").forEach((t) => {
            content[t.dataset.f] = t.value.split("\n").map((x) => x.trim()).filter(Boolean);
          });
          await call("POST", "/summary/publish", content);
          lastSummaryStatus = "published";
          root.querySelectorAll("textarea").forEach((t) => t.remove());
        }
      } catch (err) {
        showErr(err);
      } finally {
        busyAction = false;
        a.disabled = false;
        refresh();
      }
    });

    setTimeout(refresh, 0); // 화면에 붙은 뒤 첫 조회
    timer = setInterval(refresh, 3000);
    return { refresh, stop() { stopped = true; clearInterval(timer); return stopRec(); } };
  }

  window.MoyakRecording = { mount };
})();
