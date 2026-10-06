"""Manual integration check against a local Edge CDP session (port 9223).

Uses only fictional demo messages; --generate invokes the configured real AI API.
"""
import argparse
import asyncio
import json
import time
from urllib.request import urlopen

import websockets


async def main(generate, mock_video=False, local_rtc=False):
    pages = json.load(urlopen("http://127.0.0.1:9223/json"))
    page = next(p for p in pages if p["type"] == "page" and p["url"] in {"http://127.0.0.1:8001/", "http://127.0.0.1:8001"})
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
        sequence = 0
        async def evaluate(expression):
            nonlocal sequence
            sequence += 1
            await ws.send(json.dumps({"id": sequence, "method": "Runtime.evaluate", "params": {
                "expression": expression, "awaitPromise": True, "returnByValue": True, "userGesture": True,
            }}))
            while True:
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if reply.get("id") != sequence:
                    continue
                if "exceptionDetails" in reply.get("result", {}):
                    raise RuntimeError("Browser JavaScript error: " + str(reply["result"]["exceptionDetails"]))
                return reply["result"]["result"].get("value")

        async def until(expression, seconds=12):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if await evaluate(expression):
                    return
                await asyncio.sleep(0.5)
            raise RuntimeError("Browser condition timeout: " + expression)

        await evaluate("setTimeout(() => location.reload(), 10)")
        await asyncio.sleep(2)
        await until("typeof start === 'function'")
        if mock_video:
            # UI-only substitute: no claim that a media connection was established.
            await evaluate("""window.fetch = ((original) => async (...args) => {
                const url=String(args[0]);
                if (/^\\/demo\\/[^/]+\\/video$/.test(url)) return new Response(JSON.stringify({room_url:'about:blank#video-test'}),{status:200});
                const response=await original(...args);
                if(url==='/demo/start'){const body=await response.json();body.video_available=true;return new Response(JSON.stringify(body),{status:response.status});}
                return response;
            })(window.fetch.bind(window));""")
        previous = await evaluate("state?.id || ''")
        await evaluate("document.getElementById('start').click()")
        await until("!!state && state.id !== " + json.dumps(previous) + " && !document.getElementById('start').disabled && document.getElementById('connection-user').textContent === '실시간 연결됨'", seconds=25)
        if not local_rtc:
            await until("!!state.room_url", seconds=25)
            assert await evaluate("!document.getElementById('call').hidden && document.getElementById('call').src === state.room_url")
        await evaluate("document.getElementById('peer').click()")
        await until("window.open('', 'moyak-peer-'+state.id+'-pharmacist').document.getElementById('connection-pharmacist')?.textContent === '실시간 연결됨'", seconds=20)
        assert await evaluate("window.open('', 'moyak-peer-'+state.id+'-pharmacist').document.getElementById('call').src === document.getElementById('call').src")
        if local_rtc:
            await until("typeof window.open('', 'moyak-peer-'+state.id+'-pharmacist').rtcJoin === 'function'")
            fake_media = """navigator.mediaDevices.getUserMedia=async()=>{
                const canvas=document.createElement('canvas');canvas.width=320;canvas.height=240;
                const context=canvas.getContext('2d');setInterval(()=>{context.fillStyle='green';context.fillRect(0,0,320,240);context.fillStyle='white';context.fillText(String(Date.now()),20,60);},100);
                const stream=canvas.captureStream(10);const audio=new AudioContext();audio.resume();
                const oscillator=audio.createOscillator(),destination=audio.createMediaStreamDestination();oscillator.connect(destination);oscillator.start();
                destination.stream.getTracks().forEach(t=>stream.addTrack(t));return stream;
            };"""
            await evaluate(fake_media)
            await evaluate("window.open('', 'moyak-peer-'+state.id+'-pharmacist').eval(" + json.dumps(fake_media) + ")")
            await evaluate("rtcJoin()")
            await evaluate("window.open('', 'moyak-peer-'+state.id+'-pharmacist').eval('rtcJoin()')")
            await until("rtcPeer?.connectionState === 'connected'", seconds=30)
            await until("(async()=>{const stats=await rtcPeer.getStats();return [...stats.values()].some(s=>s.type==='inbound-rtp'&&s.kind==='video'&&s.framesDecoded>0);})()", seconds=20)
            print("PASS: real WebRTC transport and decoded synthetic video (physical camera/microphone not tested)", flush=True)
        await evaluate("(()=>{const p=window.open('', 'moyak-peer-'+state.id+'-pharmacist');p.document.getElementById('text-pharmacist').value='영상통화 화면에서 보내는 메시지';p.document.getElementById('form-pharmacist').requestSubmit();})()")
        await until("document.getElementById('chat-user').textContent.includes('영상통화 화면에서 보내는 메시지')")
        assert await evaluate("!document.getElementById('participant-user').hidden && document.getElementById('participant-pharmacist').hidden")
        if local_rtc:
            assert await evaluate("rtcPeer.connectionState === 'connected'")
        else:
            assert await evaluate("document.getElementById('call').src === state.room_url")
        assert await evaluate("(()=>{const v=document.getElementById('video-panel').getBoundingClientRect(),c=document.getElementById('participants').getBoundingClientRect();return innerWidth<=750 || (v.right<=c.left && Math.abs(v.top-c.top)<2);})()")
        print("PASS: side-by-side video/chat and peer message without leaving the call", flush=True)
        await evaluate("document.getElementById('consent-user').click(); document.getElementById('consent-pharmacist').click()")
        await until("document.getElementById('consent-user').checked && document.getElementById('consent-pharmacist').checked && !document.getElementById('finish').disabled")
        await evaluate("document.getElementById('sample').click()")
        await until("document.querySelectorAll('#chat-user .bubble').length === 5 && document.querySelectorAll('#chat-pharmacist .bubble').length === 5")
        print("PASS: start, both consents, realtime chat in both panels", flush=True)
        if generate:
            await evaluate("document.getElementById('finish').click()")
            await until("['약사 확인 대기','생성 실패 · 설정 확인 후 재시도'].includes(document.getElementById('summary-state').textContent)", seconds=120)
            status = await evaluate("document.getElementById('summary-state').textContent")
            if status != "약사 확인 대기":
                raise RuntimeError("Real AI summary failed; inspect API configuration")
            assert await evaluate("document.querySelectorAll('#draft textarea').length === 6 && document.querySelectorAll('#published h3').length === 0")
            await evaluate("document.getElementById('publish').click()")
            await until("document.querySelectorAll('#published h3').length === 6")
            print("PASS: real AI draft, hidden before review, pharmacist publish, user summary", flush=True)
        if local_rtc:
            await evaluate("rtcHangup();window.open('', 'moyak-peer-'+state.id+'-pharmacist').close()")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--mock-video", action="store_true")
    parser.add_argument("--local-rtc", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.generate, args.mock_video, args.local_rtc))
