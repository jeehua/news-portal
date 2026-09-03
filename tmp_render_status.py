import urllib.request, urllib.parse, json, sys
sys.stdout.reconfigure(encoding='utf-8')
KEY = 'rnd_2ZcyymCyDQVIIm0xIaXipJKPWvdc'
SERVICE_ID = 'srv-dabs0c8jo6nc738obd00'
BASE = 'https://api.render.com/v1'
req = urllib.request.Request(f'{BASE}/services/{SERVICE_ID}/events?limit=2', headers={'Authorization': f'Bearer {KEY}', 'Accept': 'application/json'})
data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode('utf-8'))
for ev in data:
    e = ev['event']
    print(f"{e['timestamp']} | {e['type']} | {json.dumps(e.get('details', {}), ensure_ascii=False)[:200]}")
print()
req2 = urllib.request.Request(f'{BASE}/services/{SERVICE_ID}', headers={'Authorization': f'Bearer {KEY}'})
svc = json.loads(urllib.request.urlopen(req2, timeout=15).read().decode('utf-8'))
print(f"Render service: {svc.get('name')} | status={svc.get('status')} | live_url={svc.get('liveUrl', 'N/A')}")
