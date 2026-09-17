# Vioris — Phone ↔ Laptop Connection Setup

This guide connects your phone to your laptop over Tailscale (private, encrypted, no open ports).

## Prerequisites

1. **Tailscale installed on both devices**
   - Laptop: https://tailscale.com/download
   - Phone: App Store / Play Store → "Tailscale"

2. **Sign in with the same account** on both devices

## Step 1: Find your laptop's Tailscale IP

On your laptop, run:

```bash
tailscale ip -4
```

This gives you something like `100.64.0.1`. Save this.

## Step 2: Start Vioris Gateway

```bash
python run_gateway.py
```

The gateway starts on `0.0.0.0:8420` (accessible over Tailscale).

## Step 3: Pair your phone

On your **laptop** (browser or curl):

```bash
# Create pairing token
curl -X POST http://localhost:8420/auth/device/pair \
  -H "Content-Type: application/json" \
  -d '{"name": "my-phone"}'
```

Response:
```json
{
  "device_id": "...",
  "name": "my-phone",
  "pairing_token": "...",
  "expires_at": "..."
}
```

Copy the `pairing_token`.

## Step 4: Exchange token on phone

From your **phone** (browser over Tailscale):

```
http://100.64.0.1:8420/exchange?device_id=DEVICE_ID&token=PAIRING_TOKEN
```

Or use curl on the phone (if using Termux or similar):

```bash
curl -X POST http://100.64.0.1:8420/auth/device/exchange \
  -H "Content-Type: application/json" \
  -d '{"device_id": "DEVICE_ID", "token": "PAIRING_TOKEN"}'
```

Response:
```json
{
  "device_id": "...",
  "jwt": "...",
  "expires_at": "...",
  "token_type": "bearer"
}
```

Save the `jwt`.

## Step 5: Connect WebSocket

From your phone, open a WebSocket connection:

```
ws://100.64.0.1:8420/ws/device?token=JWT&type=phone
```

## Step 6: Test the connection

On your laptop, push a test ring to the phone:

```bash
curl -X POST http://localhost:8420/v1/push/ring \
  -H "Authorization: Bearer DEVICE_JWT" \
  -H "Content-Type: application/json" \
  -d '{"source": "test", "message": "Hello from laptop!"}'
```

Your phone should receive the ring event over WebSocket.

## Architecture

```
Phone (Tailscale)          Laptop (Tailscale)
   │                           │
   │  ws://100.64.0.1:8420     │
   ├───────────────────────────┤
   │                           │
   │   ← ring/digest/approval  │  (laptop pushes)
   │   → voice/approve/reject  │  (phone sends)
   │                           │
   │   HTTP REST API           │
   ├───────────────────────────┤
   │   GET /v1/tasks           │
   │   POST /v1/approve        │
   │   POST /v1/stop           │
   │   ...                     │
```

## Firewall

If Windows Firewall blocks it, allow port 8420:

```powershell
New-NetFirewallRule -DisplayName "Vioris Gateway" -Direction Inbound -LocalPort 8420 -Protocol TCP -Action Allow
```

## Troubleshooting

**Phone can't connect:**
- Check Tailscale is running on both devices
- Verify laptop's Tailscale IP: `tailscale ip -4`
- Check gateway is listening: `netstat -an | findstr 8420`

**WebSocket disconnects immediately:**
- Check JWT is valid (not expired, device not revoked)
- Look at gateway logs for auth errors

**No audio/call:**
- WebSocket is for signaling only
- Audio will be handled separately (future phase)
