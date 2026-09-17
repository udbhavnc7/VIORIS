"""Live end-to-end test of the driving conversation flow over WebSocket."""
import asyncio
import json
import sys
import websockets

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXZfNWI1YjgwYjZhNCIsInR5cGUiOiJkZXZpY2UiLCJpYXQiOjE3ODk2NjA2NTQsImV4cCI6MTgyMTE5NjY1NH0.VI7k-LCcaLAbYClmlOD08_tkRoy7pEevRHbzfxdPVa0"
WS_URL = f"ws://127.0.0.1:8420/ws/device?type=phone&token={TOKEN}"


def log(msg: str):
    print(msg, flush=True)


async def test_live_driving_flow():
    log(f"Connecting to {WS_URL}...")
    async with websockets.connect(WS_URL) as ws:
        # Step 1: Wait for connected message
        raw = await ws.recv()
        msg = json.loads(raw)
        log(f"Connected msg received: {msg['type']}")
        assert msg["type"] == "connected"

        # Trigger proactive call via REST
        import urllib.request
        log("Triggering proactive call via REST POST /v1/call/trigger...")
        req = urllib.request.Request(
            "http://127.0.0.1:8420/v1/call/trigger",
            data=b'{"force": true}',
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            log(f"Triggered call: {data['call_id']}, status: {data['status']}")
            call_id = data["call_id"]

        # Step 2: Receive ring
        msg = json.loads(await ws.recv())
        log(f"Received ring event: {msg['type']} -> {msg.get('payload', {})}")
        assert msg["type"] == "ring"

        # Step 3: Accept call
        log("Accepting call via call_accept...")
        await ws.send(json.dumps({"type": "call_accept", "payload": {"call_id": call_id}}))

        # Receive call_start / greeting
        msg = json.loads(await ws.recv())
        log(f"Received after accept: {msg['type']}")
        greeting = msg["payload"].get("text", "")
        log(f"Vioris Greeting: '{greeting}'")
        assert "LUME" in greeting and "completed" in greeting and "next phase" in greeting

        # Step 4: Turn 1 - User commands: open mail, send a mail to shravan saying Hi Sceptix
        user_turn1 = "open mail, send a mail to shravan saying Hi Sceptix"
        log(f"\nUser: '{user_turn1}'")
        await ws.send(json.dumps({"type": "voice", "payload": {"call_id": call_id, "text": user_turn1}}))

        # Receive Vioris disambiguation response
        msg = json.loads(await ws.recv())
        reply1 = msg["payload"].get("text", "")
        log(f"Vioris Reply 1: '{reply1}'")
        assert "Which Shravan?" in reply1
        assert "Shravan G" in reply1
        assert "Shravan GK" in reply1

        # Step 5: Turn 2 - User clarifies: shravan gk
        user_turn2 = "shravan gk"
        log(f"\nUser: '{user_turn2}'")
        await ws.send(json.dumps({"type": "voice", "payload": {"call_id": call_id, "text": user_turn2}}))

        # Receive Vioris email confirmation
        msg = json.loads(await ws.recv())
        reply2 = msg["payload"].get("text", "")
        log(f"Vioris Reply 2: '{reply2}'")
        assert "Shravan GK" in reply2
        assert "Hi Sceptix" in reply2
        assert msg["payload"]["action"]["type"] == "send_mail"
        assert msg["payload"]["action"]["recipient"] == "Shravan GK"
        assert msg["payload"]["action"]["email"] == "shravan.gk@sceptix.com"

        # Step 6: User says bye
        user_turn3 = "no, bye"
        log(f"\nUser: '{user_turn3}'")
        await ws.send(json.dumps({"type": "voice", "payload": {"call_id": call_id, "text": user_turn3}}))

        msg = json.loads(await ws.recv())
        reply3 = msg["payload"].get("text", "")
        log(f"Vioris Final: '{reply3}'")
        assert "later" in reply3.lower()

        log("\nALL INTERACTIVE CONTACT DISAMBIGUATION & EMAIL WORKFLOW VERIFIED LIVE OVER WEBSOCKET!")


if __name__ == "__main__":
    asyncio.run(test_live_driving_flow())
