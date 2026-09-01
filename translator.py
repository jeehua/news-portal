"""
腾讯云机器翻译 — 实时翻译海外资讯标题
- TC3-HMAC-SHA256 鉴权
- LRU 缓存避免重复请求
- 自动判断是否需要翻译（中文 no-op）
- 失败 fallback 原文
"""
import hashlib, hmac, json, time, urllib.request, os, threading

# ──────────── 密钥（从 .env 读） ────────────
def _load_keys():
    """从 /var/www/jeeseek/api/.env 读密钥"""
    keys = {}
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(env_path):
        return keys
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            if '=' in line:
                k, v = line.split('=', 1)
                keys[k.strip()] = v.strip()
    return keys

_K = _load_keys()
SECRET_ID = os.environ.get('TENCENT_TMT_SECRET_ID') or _K.get('TENCENT_TMT_SECRET_ID', '')
SECRET_KEY = os.environ.get('TENCENT_TMT_SECRET_KEY') or _K.get('TENCENT_TMT_SECRET_KEY', '')
REGION = os.environ.get('TENCENT_TMT_REGION') or _K.get('TENCENT_TMT_REGION', 'ap-shanghai')

ENDPOINT = 'tmt.tencentcloudapi.com'
SERVICE = 'tmt'
VERSION = '2018-03-21'
ACTION = 'TextTranslate'

# ──────────── LRU 缓存 ────────────
_cache = {}
_cache_lock = threading.Lock()
CACHE_MAX = 5000


def _is_chinese(text: str) -> bool:
    """简单判断：含中文 char > 30% 视为中文"""
    if not text: return True
    chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return chinese_count / len(text) > 0.3


def _sign_tc3(payload: dict, timestamp: int, date: str) -> str:
    """TC3-HMAC-SHA256 鉴权签名"""
    canonical_uri = '/'
    canonical_querystring = ''
    ct = 'application/json; charset=utf-8'
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    canonical_headers = f'content-type:{ct}\nhost:{ENDPOINT}\n'
    signed_headers = 'content-type;host'
    hashed_request_payload = hashlib.sha256(payload_bytes).hexdigest()
    canonical_request = f'POST\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{hashed_request_payload}'

    credential_scope = f'{date}/{SERVICE}/tc3_request'
    hashed_canonical_request = hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()
    string_to_sign = f'TC3-HMAC-SHA256\n{timestamp}\n{credential_scope}\n{hashed_canonical_request}'

    def _hmac(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

    secret_date = _hmac(f'TC3{SECRET_KEY}'.encode('utf-8'), date)
    secret_service = _hmac(secret_date, SERVICE)
    secret_signing = _hmac(secret_service, 'tc3_request')
    signature = hmac.new(secret_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    return (
        f'TC3-HMAC-SHA256 Credential={SECRET_ID}/{credential_scope}, '
        f'SignedHeaders={signed_headers}, Signature={signature}'
    ), payload_bytes, ct


def _call_api(text: str, source='en', target='zh', retries=2) -> str:
    """调腾讯云翻译 API"""
    payload = {
        'SourceText': text,
        'Source': source,
        'Target': target,
        'ProjectId': 0,
    }
    timestamp = int(time.time())
    date = time.strftime('%Y-%m-%d', time.gmtime(timestamp))
    auth, payload_bytes, ct = _sign_tc3(payload, timestamp, date)

    req = urllib.request.Request(
        f'https://{ENDPOINT}/',
        data=payload_bytes,
        headers={
            'Authorization': auth,
            'Content-Type': ct,
            'Host': ENDPOINT,
            'X-TC-Action': ACTION,
            'X-TC-Version': VERSION,
            'X-TC-Timestamp': str(timestamp),
            'X-TC-Region': REGION,
        },
        method='POST'
    )

    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = json.loads(r.read().decode('utf-8'))
            if 'Response' in resp and 'TargetText' in resp['Response']:
                return resp['Response']['TargetText']
            last_err = f'unexpected response: {resp}'
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
            time.sleep(0.3)
    return None


def translate_title(text: str) -> str:
    """
    翻译标题：
    - 中文 → 原文返回
    - 英文 → 调腾讯云翻译
    - 缓存命中 → 直接返回
    - 失败 → fallback 原文
    """
    if not text: return text
    if _is_chinese(text): return text

    with _cache_lock:
        if text in _cache:
            return _cache[text]

    translated = _call_api(text)
    if translated is None:
        return text  # 失败 fallback

    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            # 简单 LRU：清掉前 1000 条
            keys_to_delete = list(_cache.keys())[:1000]
            for k in keys_to_delete:
                _cache.pop(k, None)
        _cache[text] = translated
    return translated


def get_cache_stats() -> dict:
    """缓存统计"""
    with _cache_lock:
        return {
            'cached': len(_cache),
            'max': CACHE_MAX,
            'enabled': bool(SECRET_ID and SECRET_KEY),
        }


# ──────────── 启动检查 ────────────
if __name__ == '__main__':
    print(f'SECRET_ID 配置: {bool(SECRET_ID)}')
    print(f'SECRET_KEY 配置: {bool(SECRET_KEY)}')
    print(f'REGION: {REGION}')
    print(f'缓存状态: {get_cache_stats()}')

    # 测一条
    test = 'Apple unveils new AI chip for data centers'
    print(f'\n原文: {test}')
    print(f'译文: {translate_title(test)}')
    print(f'二次调用（缓存命中）: {translate_title(test)}')