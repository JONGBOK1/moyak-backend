/**
 * 상담 음성 기록 · AI 요약 패널 (사용자 앱 화상 화면 + 약사 콘솔 공용).
 * 서버: /consultations/{id}/session|session/claim|session/consent|audio|transcript|session/end|summary(/publish|/retry)
 *
 * - 양측이 직접 "음성 기록·AI 요약"에 동의해야 녹음 업로드가 가능하다.
 * - 각자 기기 마이크만 녹음한다(Daily 통화 전체를 녹화하지 않음). 녹음을 멈추면 자동 업로드 → 서버 워커가 전사.
 * - 전사 실패 시 같은 파일로 재전송(같은 client_id라 중복 저장 없음). 업로드 전 녹음은 페이지를 떠나면 사라진다.
 * - 약사만: 상담 연결(담당 배정), 전사 원문 보기, 상담 종료, 요약 초안 6항목 검토·수정 후 공개, 실패 시 재시도.
 *
 * 사용: MoyakRecording.mount(containerEl, { consultationId, actorId, role: "user" | "pharmacist" })
 */
(function () {
  const LABELS = {
    symptoms: "호소한 증상", discussion: "상담 내용", medication_guidance: "복약 안내",
    precautions: "주의사항", follow_up: "후속 안내", needs_verification: "확인이 필요한 내용",
  };
  const AUDIO_STATUS = { queued: "전사 대기", processing: "전사 중", completed: "전사 완료", failed: "전사 실패" };

  function injectStyle() {
    if (document.getElementById("moyak-rec-style")) return;
    const s = document.createElement("style");
    s.id = "moyak-rec-style";
    s.textContent = `
      .mrec { font-size: 13px; color: #2b2b2b; display: flex; flex-direction: column; gap: 8px; }
      .mrec .row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
      .mrec button { border: none; border-radius: 10px; padding: 8px 12px; font-weight: 700; cursor: pointer; background: #176b5b; color: #fff; font-size: 13px; }
      .mrec button.alt { background: #fff; color: #176b5b; border: 1.5px solid #176b5b; }
      .mrec button.rec { background: #e53935; }
      .mrec button:disabled { opacity: .45; cursor: default; }
      .mrec .pill { border-radius: 99px; padding: 3px 9px; font-size: 11px; font-weight: 700; background: #f1ece2; color: #6b5d50; }
      .mrec .pill.ok { background: #e3f4ec; color: #176b5b; }
      .mrec .note { color: #7d6d6a; font-size: 12px; line-height: 1.5; }
      .mrec .err { color: #c0392b; font-size: 12px; }
      .mrec .files div { display: flex; justify-content: space-between; gap: 8px; padding: 3px 0; border-bottom: 1px solid #f1ece2; }
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
    const pageStart = Date.now();
    let recorder = null, chunks = [], recStart = 0, timer = null, stopped = false;
    let lastSummaryStatus = null;

    const root = document.createElement("div");
    root.className = "mrec";
    container.appendChild(root);

    function headers(extra) {
      const id = typeof opts.actorId === "function" ? opts.actorId() : opts.actorId;
      return { "X-Moyak-User-Id": encodeURIComponent(id || ""), "X-Moyak-Role": role, ...(extra || {}) };
    }
    async function call(method, path, body, extraHeaders) {
      const id = typeof opts.actorId === "function" ? opts.actorId() : opts.actorId;
      if (!id) { const e = new Error(isPharm ? "약사 ID를 먼저 입력해주세요." : "사용자 정보가 없어요."); e.status = 0; throw e; }
      const res = await fetch(base + path, {
        method, headers: headers(body && !(body instanceof Blob) ? { "Content-Type": "application/json", ...extraHeaders } : extraHeaders),
        body: body instanceof Blob ? body : body ? JSON.stringify(body) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { const e = new Error(data.detail || `요청 실패 (${res.status})`); e.status = res.status; throw e; }
      return data;
    }
    const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

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

    async function startRec() {
      showErr();
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recorder = new MediaRecorder(stream);
        chunks = [];
        recStart = Date.now() - pageStart;
        recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
        recorder.onstop = async () => {
          stream.getTracks().forEach((t) => t.stop());
          const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
          const clientId = "rec_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
          blobs[clientId] = { blob, startMs: recStart };
          recorder = null;
          try { await upload(clientId); } catch (e) { showErr(e); }
          refresh();
        };
        recorder.start();
        const btn = root.querySelector('[data-a="rec"]'); // 녹음 중엔 다시 그리지 않으므로 버튼만 직접 바꾼다
        if (btn) {
          btn.textContent = "■ 녹음 중지 · 업로드";
          btn.classList.add("rec");
          btn.nextElementSibling.textContent = "녹음 중… 말씀이 끝나면 중지를 눌러주세요";
        }
      } catch (e) {
        showErr(new Error("마이크를 사용할 수 없어요: " + e.message));
      }
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
      const recording = !!recorder;
      let html = `<div class="row">
          <span class="pill ${room.user_consent_at ? "ok" : ""}">환자 동의 ${room.user_consent_at ? "✓" : "대기"}</span>
          <span class="pill ${room.pharmacist_consent_at ? "ok" : ""}">약사 동의 ${room.pharmacist_consent_at ? "✓" : "대기"}</span></div>`;
      if (!room.ended_at && !mine) {
        html += `<div class="consent-box">상담 중 <b>내 기기 마이크로 녹음한 음성</b>을 글자로 바꾸고(음성 전사), 채팅·사전 챗봇 대화와 함께
          <b>AI가 상담 요약</b>을 만드는 데 동의합니다. 요약은 약사가 검토한 뒤 공개되며, 원본 음성은 전사 후 삭제됩니다.
          <div class="row" style="margin-top:6px"><button data-a="consent">동의합니다</button></div></div>`;
      }
      if (!room.ended_at && both) {
        html += `<div class="row"><button data-a="rec" class="${recording ? "rec" : ""}">${recording ? "■ 녹음 중지 · 업로드" : "● 녹음 시작"}</button>
          <span class="note">${recording ? "녹음 중… 말씀이 끝나면 중지를 눌러주세요" : "내 마이크만 녹음돼요"}</span></div>`;
      } else if (!room.ended_at && mine && !both) {
        html += `<div class="note">상대방이 동의하면 녹음할 수 있어요.</div>`;
      }
      if (files.length) {
        html += `<div class="files">` + files.filter((f) => isPharm || f.sender_role === role).map((f) =>
          `<div><span>${f.sender_role === "pharmacist" ? "약사" : "환자"} 녹음 #${f.id}</span>
           <span>${AUDIO_STATUS[f.status] || f.status}${f.status === "failed" && blobs[f.client_id] ? ` <button class="alt" data-a="reup" data-c="${esc(f.client_id)}">다시 보내기</button>` : ""}</span></div>`).join("") + `</div>`;
        if (files.some((f) => f.status === "failed" && !blobs[f.client_id]) && !isPharm)
          html += `<div class="note">전사에 실패한 녹음은 이 화면을 떠나 원본이 없어요. 필요하면 다시 녹음해주세요.</div>`;
      }
      if (isPharm) {
        if (transcript && transcript.length) {
          html += `<div class="sum-label">전사 원문</div><div class="transcript">${transcript.map((t) =>
            `[${t.sender_role === "pharmacist" ? "약사" : "환자"}] ${esc(t.text || "(" + (AUDIO_STATUS[t.status] || t.status) + ")")}`).join("\n")}</div>`;
        }
        if (!room.ended_at) {
          html += `<div class="row"><button class="alt" data-a="end">상담 종료 (요약 생성)</button></div>
            <div class="note">승인/거절을 먼저 확정하고, 모든 녹음의 전사가 끝난 뒤 종료할 수 있어요. 동의가 없으면 AI 요약 없이 종료돼요.</div>`;
        }
      }
      html += renderSummary(summary, room);
      body.innerHTML = html;
    }

    function renderSummary(summary, room) {
      if (!room.ended_at || !summary) return "";
      const st = summary.status;
      if (st === "not_requested") return `<div class="note">동의가 없어 AI 요약 없이 종료됐어요.</div>`;
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

    let everConnected = false;
    async function refresh() {
      if (stopped) return;
      // 카드가 화면에 붙기 전에 마운트될 수 있다 — 한 번이라도 붙었다가 사라졌을 때만 멈춘다
      if (!root.isConnected) {
        if (everConnected) { stopped = true; clearInterval(timer); }
        return;
      }
      everConnected = true;
      // 약사가 요약 초안을 고치는 중이면 다시 그려서 입력을 지우지 않는다
      if (lastSummaryStatus === "pending_review" && isPharm && root.querySelector("textarea")) return;
      let room = null, files = [], summary = null, transcript = null;
      try {
        room = await call("GET", "/session");
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
      lastSummaryStatus = summary && summary.status;
      if (!recorder || !root.querySelector('[data-a="rec"]')) render({ room, files, summary, transcript });
      if (summary && summary.status === "published") { stopped = true; clearInterval(timer); }
    }

    root.addEventListener("click", async (e) => {
      const a = e.target.closest("[data-a]");
      if (!a) return;
      showErr();
      a.disabled = true;
      try {
        const act = a.dataset.a;
        if (act === "claim") await call("POST", "/session/claim");
        if (act === "consent") await call("POST", "/session/consent");
        if (act === "rec") { if (recorder) recorder.stop(); else await startRec(); }
        if (act === "reup") { await upload(a.dataset.c); }
        if (act === "end") {
          if (!window.confirm || window.confirm("상담을 종료할까요? 종료하면 채팅·녹음을 더 할 수 없고 요약 초안이 만들어져요.")) await call("POST", "/session/end");
        }
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
        a.disabled = false;
        if (a.dataset.a !== "rec") refresh();
      }
    });

    setTimeout(refresh, 0); // 화면에 붙은 뒤 첫 조회
    timer = setInterval(refresh, 3000);
    return { refresh, stop() { stopped = true; clearInterval(timer); if (recorder) recorder.stop(); } };
  }

  window.MoyakRecording = { mount };
})();
