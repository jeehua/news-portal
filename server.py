#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知几察微 v3 - server.py
- 照搬 trend.902187.xyz 的数据形态(source_type/tier1/rank/time/url)
- 抓取全并行(asyncio.gather),单次从 100s 降到 <10s
- FETCH_INTERVAL 30s(之前 60s)
- 国内 10 源 + 海外 14 源 = 24 源全开
"""

import os, sys, json, asyncio, logging, sqlite3, glob, time, re, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator, List, Dict, Any
import feedparser
import socket
from concurrent.futures import ThreadPoolExecutor

# 腾讯云机器翻译(实时翻译海外资讯)
try:
    from translator import translate_title as _translate_title, get_cache_stats as _translate_stats
except Exception:
    def _translate_title(t): return t
    def _translate_stats(): return {'cached': 0, 'max': 0, 'enabled': False}

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
import pytz

# ──────────── 配置 ────────────
TZ_CN = pytz.timezone('Asia/Shanghai')
LISTEN_HOST = os.getenv('LISTEN_HOST', '0.0.0.0')
LISTEN_PORT = int(os.getenv('LISTEN_PORT', '8001'))
FETCH_INTERVAL = int(os.getenv('FETCH_INTERVAL', '30'))  # 60 → 30

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'

# ──────────── 国内源(参考 trend)────────────
# ──────────── 国内源(精简版)────────────
DOMESTIC_SOURCES = [
    {'id': 'cls-hot',         'name': '财联社',        'type': 'rsshub',    'urls': ['https://hub.slarker.me/cls/telegraph', 'https://rsshub.rssforever.com/cls/telegraph']},
    {'id': 'cls-telegraph',   'name': '财联社电报',    'type': 'rsshub',    'urls': ['https://hub.slarker.me/cls/telegraph', 'https://rsshub.rssforever.com/cls/telegraph']},
    {'id': 'jin10',           'name': '金十数据',      'type': 'jsonp',     'urls': ['https://www.jin10.com/flash_newest.js']},
    {'id': 'gelonghui',       'name': '格隆汇',        'type': 'rsshub',    'urls': ['https://hub.slarker.me/gelonghui/live', 'https://rss.injahow.cn/gelonghui/live']},
    {'id': 'wallstreetcn',    'name': '华尔街见闻',    'type': 'json_api',  'urls': ['https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=50']},
    {'id': 'wallstreetcn-hot','name': '华尔街见闻',    'type': 'json_api',  'urls': ['https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=50']},
    {'id': 'sina-finance',    'name': '新浪财经',      'type': 'json_api',  'urls': ['https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=50']},
    {'id': 'sina-7x24',       'name': '新浪7x24',      'type': 'json_api',  'urls': ['https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=50']},
    {'id': 'caixin',          'name': '财新网',        'type': 'rsshub',    'urls': ['https://hub.slarker.me/caixin/article', 'https://rsshub.rssforever.com/caixin/article']},
    {'id': 'yicai',           'name': '第一财经',      'type': 'rsshub',    'urls': ['https://hub.slarker.me/yicai/headline', 'https://rsshub.rssforever.com/yicai/headline']},
]

# ──────────── 海外源(精简版:8 个独立 chip)────────────
OVERSEAS_SOURCES = [
    # 官方 RSS 源(能接的 8 个)
    {'id': 'bbc-world',          'name': 'BBC全球',       'type': 'rss',     'urls': ['https://feeds.bbci.co.uk/news/world/rss.xml']},
    {'id': 'bbc-business',       'name': 'BBC商业',       'type': 'rss',     'urls': ['https://feeds.bbci.co.uk/news/business/rss.xml', 'https://feeds.bbci.co.uk/news/world/rss.xml']},
    {'id': 'nyt-world',          'name': '纽约时报',      'type': 'rss',     'urls': ['https://rss.nytimes.com/services/xml/rss/nyt/World.xml']},
    {'id': 'wsj-us-business',    'name': '华尔街日报',    'type': 'rss',     'urls': ['https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness']},
    {'id': 'yahoo-finance',      'name': '雅虎财经',      'type': 'rss',     'urls': ['https://finance.yahoo.com/news/rssindex']},
    {'id': 'ft-international',   'name': 'FT国际版',      'type': 'rss',     'urls': ['https://www.ft.com/rss/home/international']},
    {'id': 'ftchinese',          'name': 'FT中文网',      'type': 'rss',     'urls': ['https://www.ftchinese.com/rss/news', 'https://hub.slarker.me/ft/chinese']},
    {'id': 'zaobao-realtime',    'name': '联合早报',      'type': 'rss',     'urls': ['https://hub.slarker.me/zaobao/realtime/china', 'https://www.zaobao.com/rss/realtime.xml']},
    {'id': 'trendforce-news',    'name': 'TrendForce',    'type': 'rss',     'urls': ['https://insights.trendforce.com/feed', 'https://insights.trendforce.com/feed.xml', 'https://www.trendforce.com/news/feed_v2/']},
    # 备用：insights.trendforce.com/feed?format=rss / .rss / www.trendforce.com/news/feed_v2/
    {'id': 'bloomberg-bbg',       'name': '彭博社',          'type': 'buzzing', 'urls': ['https://bbg.buzzing.cc/feed.json']},
    {'id': 'reuters-rtr',         'name': '路透社',          'type': 'buzzing', 'urls': ['https://reuters.buzzing.cc/feed.json']},
]

LOG_PATH = '/var/log/jeeseek/server.log'

# ──────────── 日志 ────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger('jeeseek')

# ──────────── HTTP 工具 ────────────
def _http_get(url: str, timeout: int = 12, headers: dict = None) -> str:
    h = {'User-Agent': UA, 'Accept': 'application/json, text/html, */*'}
    if headers: h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception as e:
        raise

def _http_get_with_referer(url: str, referer: str, timeout: int = 12) -> str:
    """jin10 等反爬需要的带 Referer 抓取"""
    h = {'User-Agent': UA, 'Accept': '*/*', 'Referer': referer}
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception as e:
        raise

def _http_json(url: str, timeout: int = 12) -> dict:
    return json.loads(_http_get(url, timeout=timeout, headers={'Accept': 'application/json'}))

def _parse_iso(s: str) -> int:
    """同时支持 ISO 8601 (2026-07-01T08:26:39+00:00) 和 RFC 2822 (Wed, 01 Jul 2026 08:26:39 +0000)"""
    if not s: return int(time.time())
    # 尝试 RFC 2822 (email.utils.parsedate_to_datetime)
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return int(dt.timestamp())
    except Exception:
        pass
    # 尝试 ISO 8601
    try:
        s2 = re.sub(r'\.\d+(?=[+-Z])', '', s)
        dt = datetime.fromisoformat(s2.replace('Z', '+00:00'))
        return int(dt.timestamp())
    except Exception:
        return int(time.time())

def _format_time(ts: int) -> str:
    """08-27 07:30 风格"""
    if not ts: return ''
    try:
        d = datetime.fromtimestamp(ts, TZ_CN)
        return d.strftime('%m-%d %H:%M')
    except Exception:
        return ''

# ──────────── 源抓取函数 ────────────
def _fetch_rss(url: str, source_id: str, source_name: str, source_type: str, limit: int = 20) -> List[dict]:
    """抓取单个 RSS feed"""
    items = []
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(8)
    try:
        feed = feedparser.parse(url, agent=UA)
        entries = feed.entries or []
        if not entries:
            log.warning(f'{source_id} {url} 返回 0 条')
            return items
        for e in entries[:limit]:
            title = (e.get('title') or '').strip()
            if not title: continue
            pub_ts = _parse_iso(e.get('published', '') or e.get('updated', ''))
            # 实时翻译(腾讯云 TMT)-- 中英文都走,中文 no-op
            title_cn = _translate_title(title) if source_type == 'overseas' else title
            items.append({
                'id': f'{source_type}-{source_id}-{int(time.time()*1000)}-{hash(title) & 0xfffffff:x}',
                'title': title_cn,
                'title_original': title,
                'pub_ts': pub_ts,
                'source_id': source_id,
                'source_name': source_name,
                'source_type': source_type,
                'time': _format_time(pub_ts),
                'url': e.get('link', ''),
                'content': (e.get('summary') or '')[:500],
            })
        log.info(f'{source_id} ✓ {len(items)} 条')
    except Exception as e:
        log.warning(f'{source_id} FAIL: {e}')
    finally:
        socket.setdefaulttimeout(old_timeout)
    return items

def _fetch_rsshub(*urls, source_id: str, source_name: str, source_type: str, limit: int = 20) -> List[dict]:
    """支持多个镜像 fallback"""
    items = []
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(8)  # 给 feedparser.parse 设置全局超时
    try:
        for url in urls:
            if items: break  # 已经拿到数据
            try:
                feed = feedparser.parse(url, agent=UA)
                entries = feed.entries or []
                if not entries:
                    log.warning(f'{source_id} {url} 返回 0 条')
                    continue
                for e in entries[:limit]:
                    title = (e.get('title') or '').strip()
                    if not title: continue
                    pub_ts = _parse_iso(e.get('published','') or e.get('updated',''))
                    # 实时翻译(腾讯云 TMT)-- 中英文都走,中文 no-op
                    title_cn = _translate_title(title) if source_type == 'overseas' else title
                    items.append({
                        'id': f'{source_type}-{source_id}-{int(time.time()*1000)}-{hash(title) & 0xfffffff:x}',
                        'title': title_cn,           # 显示用译文
                        'title_original': title,     # 保留原文(前端可切换)
                        'pub_ts': pub_ts,
                        'source_id': source_id,
                        'source_name': source_name,
                        'source_type': source_type,
                        'time': _format_time(pub_ts),
                        'url': e.get('link', ''),
                        'content': (e.get('summary') or '')[:500],
                    })
                if items:
                    log.info(f'{source_id} ✓ {url.split("//")[1].split("/")[0]} 拿到 {len(items)} 条')
            except Exception as e:
                log.warning(f'{source_id} {url} FAIL: {e}')
    finally:
        socket.setdefaulttimeout(old_timeout)
    return items

def _fetch_10jqka_news() -> List[dict]:
    """同花顺要闻抓取（GBK 页面，只取首页 - CDN 对分页 403）
    2026-08-27 花总拍：补上同花顺源
    """
    items = []
    seen_titles = set()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'http://news.10jqka.com.cn/',
    }
    # 只取首页（分页 403 Forbidden） - 但首页含 30 条足够
    url = 'http://news.10jqka.com.cn/today_list/'
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(10)
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            charset = r.headers.get_content_charset() or 'gbk'
            data = raw.decode(charset, errors='replace')
        # 提取 <a href="*.shtml">...</a> - 严格只取短标题（避免重复正文）
        matches = re.findall(r'<a[^>]+href="([^"]+\.shtml)"[^>]*>(.*?)</a>', data, re.DOTALL)
        now_ts = int(time.time())
        seen_urls = set()
        for url2, content2 in matches:
            title = re.sub(r'<[^>]+>', '', content2)
            title = re.sub(r'\s+', '', title)
            if not title:
                continue
            if len(title) < 8 or len(title) > 50:
                continue  # 只取短标题
            if not re.search(r'[\u4e00-\u9fa5]', title):
                continue
            if url2 in seen_urls:
                continue  # 同一 URL 只取第一次
            seen_urls.add(url2)
            if title in seen_titles:
                continue
            seen_titles.add(title)
            # 从 URL 路径提取日期：/20260827/c679342755.shtml
            full_url = url2 if url2.startswith('http') else 'http://news.10jqka.com.cn' + url2
            m_date = re.search(r'/(\d{8})/c(\d+)\.shtml', full_url)
            if m_date:
                try:
                    cid = int(m_date.group(2))
                    # 简单方案：pub_ts 全部设为 now_ts - 同一个 fetch 内部的微小偏移
                    # ID 越大越新，但 ID 不会跨日跨度大（都在一天内），所以相对顺序不重要
                    # 都设为 1-2 小时前，避免堆在一个时间
                    offset_min = 30 + (cid % 90)  # 30-119 分钟前
                    pub_ts = now_ts - offset_min * 60
                except Exception:
                    pub_ts = now_ts - 1800
            else:
                pub_ts = now_ts - 3600
            items.append({
                'id': f'domestic-10jqka-{int(time.time()*1000)}-{hash(title) & 0xfffffff:x}',
                'title': title,
                'pub_ts': pub_ts,
                'source_id': '10jqka-news',
                'source_name': '同花顺',
                'source_type': 'domestic',
                'time': _format_time(pub_ts),
                'url': full_url,
                'content': '',
                'tier1': False,
            })
            if len(items) >= 30:
                break
    except Exception as e:
        log.warning(f'10jqka {url} FAIL: {e}')
    finally:
        socket.setdefaulttimeout(old_timeout)
    log.info(f'10jqka-news ✓ 拿到 {len(items)} 条')
    return items


def _fetch_jin10() -> List[dict]:
    items = []
    try:
        data = _http_get_with_referer('https://www.jin10.com/flash_newest.js', 'https://www.jin10.com/', timeout=10)
        json_str = data.strip()
        if json_str.startswith('var '):
            json_str = json_str.split('=', 1)[1].rstrip(';')
        elif json_str.endswith(')'):
            json_str = json_str.strip('();')
        j = json.loads(json_str)
        content_list = j.get('data', []) if isinstance(j, dict) else j
        for a in (content_list or [])[:30]:
            content = re.sub(r'<[^>]+>', '', (a.get('content') or a.get('title') or '')).strip()
            if not content: continue
            ts_raw = a.get('time', a.get('ctime', 0))
            if isinstance(ts_raw, str) and ts_raw.isdigit():
                ts = int(ts_raw)
            elif isinstance(ts_raw, (int, float)):
                ts = int(ts_raw)
            else:
                ts = int(time.time())
            items.append({
                'id': f'cn-jin10-{int(time.time()*1000)}-{hash(content) & 0xfffffff:x}',
                'title': content[:120],
                'pub_ts': ts if ts > 1e9 else int(time.time()),
                'source_id': 'jin10',
                'source_name': '金十数据',
                'source_type': 'cn',
                'time': _format_time(ts if ts > 1e9 else int(time.time())),
                'url': a.get('link', ''),
                'content': content,
            })
    except Exception as e:
        log.warning(f'jin10: {e}')
    return items

def _fetch_wallstreetcn() -> List[dict]:
    items = []
    try:
        data = _http_json('https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=50', timeout=12)
        for a in data.get('data', {}).get('items', [])[:40]:
            content = (a.get('content') or a.get('title') or '').strip()
            if not content: continue
            ts_raw = a.get('display_time', 0)
            ts = int(ts_raw) if isinstance(ts_raw, (int, float)) else int(time.time())
            content_clean = re.sub(r'<[^>]+>', '', content)
            items.append({
                'id': f'cn-wallstreetcn-{a.get("id", content[:20])}',
                'title': content_clean[:120],
                'pub_ts': ts if ts > 1e9 else int(time.time()),
                'source_id': 'wallstreetcn',
                'source_name': '华尔街见闻',
                'source_type': 'cn',
                'time': _format_time(ts if ts > 1e9 else int(time.time())),
                'url': a.get('uri', 'https://wallstreetcn.com/'),
                'content': content_clean,
            })
    except Exception as e:
        log.warning(f'wallstreetcn: {e}')
    return items

def _fetch_sina_finance() -> List[dict]:
    items = []
    try:
        data = _http_json('https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=50', timeout=12)
        for a in data.get('result', {}).get('data', [])[:40]:
            title = (a.get('title') or '').strip()
            if not title: continue
            ts_raw = a.get('ctime', 0)
            if isinstance(ts_raw, str) and ts_raw.isdigit():
                ts = int(ts_raw)
            elif isinstance(ts_raw, (int, float)):
                ts = int(ts_raw)
            else:
                ts = int(time.time())
            items.append({
                'id': f'cn-sina-finance-{a.get("id", title[:20])}',
                'title': title,
                'pub_ts': ts if ts > 1e9 else int(time.time()),
                'source_id': 'sina-finance',
                'source_name': '新浪财经',
                'source_type': 'cn',
                'time': _format_time(ts if ts > 1e9 else int(time.time())),
                'url': a.get('url',''),
                'content': a.get('intro',''),
            })
    except Exception as e:
        log.warning(f'sina-finance: {e}')
    return items

def _fetch_buzzing(url: str, source_id: str, source_name: str, filter_str: str = None) -> List[dict]:
    """buzzing.cc JSON Feed:300 条带中文翻译"""
    items = []
    try:
        data = _http_json(url, timeout=15)
        now_ts = int(time.time())
        cutoff = now_ts - 86400 * 7  # 168h = 7 天 (花总拍 2026-08-28)
        for it in data.get('items', [])[:60]:
            # 用 _original_published 而不是 date_published(后者是 buzz 翻译入库时间)
            pub_str = it.get('_original_published') or it.get('date_published', '')
            pub_ts = _parse_iso(pub_str) or now_ts
            if pub_ts < cutoff: continue
            title_en = it.get('title') or ''
            tr = it.get('_translations') or {}
            zh = tr.get('zh-Hans') or tr.get('zh-Hant') or {}
            zh_title = zh.get('title') if isinstance(zh, dict) else ''
            display_title = (zh_title or title_en)[:120]
            content_text = it.get('content_text') or it.get('summary') or ''
            # filter 逻辑(filter_str 用于 bloomberg-latest 等衍生名)
            if filter_str and source_id != 'wsj-markets':
                url_str = it.get('url','')
                if filter_str.lower() not in url_str.lower() and filter_str.lower() not in (it.get('title','') or '').lower():
                    continue
            items.append({
                'id': f'os-{source_id}-{it.get("id", pub_ts)}',
                'title': display_title,
                'pub_ts': pub_ts,
                'source_id': source_id,
                'source_name': source_name,
                'source_type': 'overseas',
                'time': _format_time(pub_ts),
                'url': it.get('url', ''),
                'content': content_text[:500],
            })
    except Exception as e:
        log.warning(f'{source_id} buzzing: {e}')
    return items

def _fetch_zaobao() -> List[dict]:
    items = []
    try:
        html = _http_get('https://www.zaobao.com/realtime/china', timeout=12)
        for m in re.finditer(r'<a[^>]+href="(https://www\.zaobao\.com/realtime/[^"]+)"[^>]*>([^<]{6,80})</a>[\s\S]{0,300}?(\d{1,2}:\d{2})', html):
            url, title, tm = m.group(1), m.group(2).strip(), m.group(3)
            if title:
                # 时间解析(仅有 HH:MM,按今天解析)
                try:
                    h, m_ = tm.split(':')
                    now = datetime.now(TZ_CN)
                    pub_ts = int(now.replace(hour=int(h), minute=int(m_), second=0, microsecond=0).timestamp())
                except Exception:
                    pub_ts = int(time.time())
                items.append({
                    'id': f'os-zaobao-{int(time.time()*1000)}-{hash(title) & 0xfffffff:x}',
                    'title': title,
                    'pub_ts': pub_ts,
                    'source_id': 'zaobao-realtime',
                    'source_name': '联合早报',
                    'source_type': 'overseas',
                    'time': _format_time(pub_ts),
                    'url': url,
                    'content': '',
                })
                if sum(1 for x in items if x['source_id']=='zaobao-realtime') >= 20:
                    break
    except Exception as e:
        log.warning(f'zaobao: {e}')
    return items

# ──────────── 并行抓取 ────────────
EXECUTOR = ThreadPoolExecutor(max_workers=30)

# 同一 buzzing feed 给不同 source_id 分发(按 hash 轮转)
BUZZING_POOL = {
    'bloomberg': ['https://bbg.buzzing.cc/lite/feed.json'],
    'reuters':   ['https://reutersnew.buzzing.cc/feed.json'],
    'bbc':       ['https://bbc.buzzing.cc/feed.json'],
    'nyt':       ['https://nytimes.buzzing.cc/feed.json'],
    'wsj':       ['https://wsj.buzzing.cc/feed.json'],
}
BUZZING_IDS = {
    'bloomberg': ['bloomberg-markets', 'bloomberg-business', 'bloomberg-latest'],
    'reuters':   ['reuters-latest', 'reuters-china'],
    'bbc':       ['bbc-business', 'bbc-china'],
}
BUZZING_NAMES = {
    'bloomberg': {'bloomberg-markets': '彭博', 'bloomberg-business': '彭博商业', 'bloomberg-latest': '彭博科技'},
    'reuters':   {'reuters-latest': '路透全球', 'reuters-china': '路透中文'},
    'bbc':       {'bbc-business': 'BBC商业', 'bbc-china': 'BBC中文'},
}

def _fetch_buzzing_distributed(pool_key: str):
    """抓一次 buzzing,按 hash 把条目分配给多个 source_id"""
    url = BUZZING_POOL[pool_key][0]
    raw_items = _fetch_buzzing_raw(url)
    if pool_key not in BUZZING_IDS:
        # 单 source_id(NYT / WSJ)
        return [(BUZZING_IDS.get(pool_key, [pool_key])[0], raw_items)]
    ids = BUZZING_IDS[pool_key]
    names_map = BUZZING_NAMES.get(pool_key, {})
    results = []
    # 按 round-robin 分配
    buckets = {sid: [] for sid in ids}
    for idx, it in enumerate(raw_items):
        sid = ids[idx % len(ids)]
        name = names_map.get(sid, sid)
        # 复制并改 source_id / source_name
        it2 = dict(it)
        it2['source_id'] = sid
        it2['source_name'] = name
        buckets[sid].append(it2)
    for sid in ids:
        results.append((sid, buckets[sid]))
    return results

def _fetch_buzzing_raw(url: str, per_feed_limit: int = 30) -> list:
    """抓一次 buzzing feed.json,返回原始 items 列表(feed 内去重 + 截断)"""
    items = []
    try:
        data = _http_json(url, timeout=15)
        now_ts = int(time.time())
        cutoff = now_ts - 86400 * 7  # 168h = 7 天 (花总拍 2026-08-28)
        seen_titles = set()
        for it in data.get('items', [])[:100]:
            pub_str = it.get('date_published') or it.get('_original_published', '')
            pub_ts = _parse_iso(pub_str) or now_ts
            if pub_ts < cutoff: continue
            title_en = it.get('title') or ''
            tr = it.get('_translations') or {}
            zh = tr.get('zh-Hans') or tr.get('zh-Hant') or {}
            zh_title = zh.get('title') if isinstance(zh, dict) else ''
            display_title = (zh_title or title_en)[:120]
            # feed 内去重(同一个 feed 重复标题跳过)
            norm = re.sub(r'[\s\W_]+', '', display_title[:30])
            if norm in seen_titles: continue
            seen_titles.add(norm)
            content_text = it.get('content_text') or it.get('summary') or ''
            items.append({
                'id': f'os-{url.split("//")[1].split(".")[0]}-{it.get("id", pub_ts)}',
                'title': display_title,
                'pub_ts': pub_ts,
                'source_id': '',  # 后面 _fetch_buzzing_distributed 会改
                'source_name': '',
                'source_type': 'overseas',
                'time': _format_time(pub_ts),
                'url': it.get('url', ''),
                'content': content_text[:500],
            })
            if len(items) >= per_feed_limit:
                break
    except Exception as e:
        log.warning(f'buzzing {url}: {e}')
    return items

def fetch_all_parallel() -> tuple:
    """并行抓国内 + 海外。返回 (domestic, overseas)"""
    tasks = []

    # 国内(10 源)
    tasks.append(('dom', 'cls-telegraph', '财联社电报', 'cn', lambda: _fetch_rsshub('https://hub.slarker.me/cls/telegraph', 'https://rsshub.rssforever.com/cls/telegraph', source_id='cls-telegraph', source_name='财联社电报', source_type='cn')))
    tasks.append(('dom', 'cls-hot', '财联社', 'cn', lambda: _fetch_rsshub('https://hub.slarker.me/cls/telegraph', 'https://rsshub.rssforever.com/cls/telegraph', source_id='cls-hot', source_name='财联社', source_type='cn')))
    tasks.append(('dom', 'jin10', '金十数据', 'cn', _fetch_jin10))
    tasks.append(('dom', 'gelonghui', '格隆汇', 'cn', lambda: _fetch_rsshub('https://hub.slarker.me/gelonghui/live', 'https://rss.injahow.cn/gelonghui/live', source_id='gelonghui', source_name='格隆汇', source_type='cn')))
    tasks.append(('dom', 'wallstreetcn', '华尔街见闻', 'cn', _fetch_wallstreetcn))
    tasks.append(('dom', 'wallstreetcn-hot', '华尔街见闻', 'cn', _fetch_wallstreetcn))  # 共享
    tasks.append(('dom', 'sina-finance', '新浪财经', 'cn', _fetch_sina_finance))
    tasks.append(('dom', 'sina-7x24', '新浪7x24', 'cn', _fetch_sina_finance))  # 共享
    tasks.append(('dom', 'caixin', '财新网', 'cn', lambda: _fetch_rsshub('https://hub.slarker.me/caixin/article', 'https://rsshub.rssforever.com/caixin/article', source_id='caixin', source_name='财新网', source_type='cn')))
    tasks.append(('dom', 'yicai', '第一财经', 'cn', lambda: _fetch_rsshub('https://hub.slarker.me/yicai/headline', 'https://rsshub.rssforever.com/yicai/headline', source_id='yicai', source_name='第一财经', source_type='cn')))
    # 同花顺要闻（GBK 页面，2026-08-27 花总拍：页面里有 c\d+\.shtml 链接）
    tasks.append(('dom', '10jqka-news', '同花顺', 'cn', _fetch_10jqka_news))

    # 海外:官方 RSS 源(8个能接的)
    tasks.append(('os', 'bbc-world', 'BBC全球', 'overseas', lambda: _fetch_rsshub('https://feeds.bbci.co.uk/news/world/rss.xml', source_id='bbc-world', source_name='BBC全球', source_type='overseas')))
    tasks.append(('os', 'bbc-business', 'BBC商业', 'overseas', lambda: _fetch_rsshub('https://feeds.bbci.co.uk/news/business/rss.xml', 'https://feeds.bbci.co.uk/news/world/rss.xml', source_id='bbc-business', source_name='BBC商业', source_type='overseas')))
    tasks.append(('os', 'nyt-world', '纽约时报', 'overseas', lambda: _fetch_rsshub('https://rss.nytimes.com/services/xml/rss/nyt/World.xml', source_id='nyt-world', source_name='纽约时报', source_type='overseas')))
    tasks.append(('os', 'wsj-us-business', '华尔街日报', 'overseas', lambda: _fetch_rsshub('https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness', source_id='wsj-us-business', source_name='华尔街日报', source_type='overseas')))
    tasks.append(('os', 'yahoo-finance', '雅虎财经', 'overseas', lambda: _fetch_rsshub('https://finance.yahoo.com/news/rssindex', source_id='yahoo-finance', source_name='雅虎财经', source_type='overseas')))
    tasks.append(('os', 'ft-international', 'FT国际版', 'overseas', lambda: _fetch_rsshub('https://www.ft.com/rss/home/international', source_id='ft-international', source_name='FT国际版', source_type='overseas')))
    tasks.append(('os', 'trendforce-news', 'TrendForce', 'overseas', lambda: _fetch_rss('https://insights.trendforce.com/feed', source_id='trendforce-news', source_name='TrendForce', source_type='overseas')))
    tasks.append(('os', 'ftchinese', 'FT中文网', 'overseas', lambda: _fetch_rsshub('https://www.ftchinese.com/rss/news', 'https://hub.slarker.me/ft/chinese', source_id='ftchinese', source_name='FT中文网', source_type='overseas')))
    tasks.append(('os', 'zaobao-realtime', '联合早报', 'overseas', lambda: _fetch_rsshub('https://hub.slarker.me/zaobao/realtime/china', 'https://www.zaobao.com/rss/realtime.xml', source_id='zaobao-realtime', source_name='联合早报', source_type='overseas')))
    # 彭博 + 路透(buzzing.cc JSON feed,中文翻译)
    tasks.append(('os', 'bloomberg-bbg', '彭博社', 'overseas', lambda: _fetch_buzzing('https://bbg.buzzing.cc/feed.json', 'bloomberg-bbg', '彭博社')))
    tasks.append(('os', 'reuters-rtr', '路透社', 'overseas', lambda: _fetch_buzzing('https://reuters.buzzing.cc/feed.json', 'reuters-rtr', '路透社')))

    # 并行提交
    futures = []
    for kind, sid, sname, stype, fn in tasks:
        f = EXECUTOR.submit(fn)
        futures.append((kind, sid, sname, stype, f))

    domestic = []
    overseas = []
    for kind, sid, sname, stype, f in futures:
        try:
            items = f.result(timeout=25)
            if kind == 'dom':
                domestic.extend(items)
            elif kind == 'os':
                # 单 source_id 的,为所有 item 填上 source_id/name
                for it in items:
                    if not it.get('source_id'):
                        it['source_id'] = sid
                        it['source_name'] = sname
                overseas.extend(items)
            elif kind == 'os_dist':
                # 多 source_id 的分布式结果 [(sid, [items])]
                for sub_sid, sub_items in items:
                    overseas.extend(sub_items)
        except Exception as e:
            log.warning(f'{sid} timeout/err: {e}')

    # tier1 标记(同一标题在 2 个以上不同源出现 → tier1)
    from collections import Counter
    title_count = Counter()
    for it in domestic + overseas:
        # 简化标题用于匹配
        norm = re.sub(r'[\s\W_]+', '', it['title'][:30])
        title_count[norm] += 1

    for it in domestic + overseas:
        norm = re.sub(r'[\s\W_]+', '', it['title'][:30])
        it['tier1'] = title_count[norm] >= 2

    # 去重（保留最新一份）+ 按时间倒序
    now_ts = int(time.time())
    cutoff_7d = 86400 * 7  # 168h = 7 天 (花总拍 2026-08-28)
    
    seen = set()
    unique_dom = []
    for it in sorted(domestic, key=lambda x: x['pub_ts'], reverse=True):
        norm = re.sub(r'[\s\W_]+', '', it['title'][:30])
        if norm in seen: continue
        seen.add(norm)
        delta = now_ts - it['pub_ts']
        if delta > cutoff_7d:
            continue
        unique_dom.append(it)
    unique_dom = unique_dom[:300]

    seen2 = set()
    unique_os = []
    for it in sorted(overseas, key=lambda x: x['pub_ts'], reverse=True):
        norm = re.sub(r'[\s\W_]+', '', it['title'][:30])
        if norm in seen2: continue
        seen2.add(norm)
        delta = now_ts - it['pub_ts']
        # 统一 7 天 cutoff（花总拍 2026-08-28）
        if delta > cutoff_7d:
            continue
        unique_os.append(it)
    unique_os = unique_os[:300]
    
    return unique_dom, unique_os

# ──────────── 机构纪要 ────────────
ZSXQ_API_KEY = '43fe493b82bdf7cd841c027f52824301'
ZSXQ_GROUP_ID = '15552822254242'

def _fetch_zsxq() -> List[dict]:
    items = []
    try:
        url = f'https://mcp.zsxq.com/topic/mcp?api_key={ZSXQ_API_KEY}'
        def call(body):
            data = json.dumps(body).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream',
                'User-Agent': UA,
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                for line in r.read().decode('utf-8').split('\n'):
                    if line.startswith('data: '):
                        return json.loads(line[6:])
        call({'jsonrpc':'2.0','id':0,'method':'initialize','params':{
            'protocolVersion':'2024-11-05','capabilities':{},
            'clientInfo':{'name':'jeeseek','version':'3.0'}
        }})
        # 2026-08-27 改：分页拉取所有纪要（每页 30 条，用 end_time 分页）
        all_topics = []
        end_time = None
        for page in range(20):  # 最多 20 页=600 条
            args = {'group_id': ZSXQ_GROUP_ID, 'limit': 30, 'scope': 'all'}
            if end_time:
                # 用上一页最后一个 topic 的 create_time 作为分页锚点
                args['end_time'] = end_time
            r = call({'jsonrpc':'2.0','id':page+1,'method':'tools/call','params':{
                'name':'get_group_topics',
                'arguments': args
            }})
            inner = json.loads(r['result']['content'][0]['text'])
            if not inner.get('success'):
                break
            topics = inner.get('topics_brief', [])
            if not topics:
                break
            all_topics.extend(topics)
            # 用最后一个 topic 的 create_time 作为下一页分页游标
            if topics:
                end_time = topics[-1].get('create_time', '')
            # 如果 has_more=False 或者拿到少于 30 条，说明已到末尾
            if not inner.get('has_more') or len(topics) < 30:
                break
        log.info(f'zsxq 拉取成功: {len(all_topics)} 条')
        topics = all_topics
        for t in topics:
            title = t.get('title','').strip() or t.get('content','')[:80]
            content = t.get('content','').strip()
            create_time = t.get('create_time','')
            pub_ts = _parse_iso(create_time)
            topic_id = t.get('topic_id','')
            items.append({
                'id': f'minutes-{topic_id}',
                'title': title,
                'pub_ts': pub_ts,
                'source_id': 'zsxq',
                'source_name': '机构纪要',
                'source_type': 'minutes',
                'time': _format_time(pub_ts),
                'url': f'https://wx.zsxq.com/detailed/{topic_id}',
                'content': content,
            })
        log.info(f'zsxq 拉取成功: {len(items)} 条')
    except Exception as e:
        log.warning(f'zsxq: {e}')
    return items

def fetch_minutes_parallel() -> List[dict]:
    """机构纪要：不加 cutoff，全部显示（2026-08-27 花总拍）"""
    f = EXECUTOR.submit(_fetch_zsxq)
    try:
        items = f.result(timeout=90)  # zsxq 拉取可能很慢，2026-09-01 实测偶尔 40-90s
    except Exception as e:
        log.warning(f'minutes timeout/err: {type(e).__name__}: {e}')
        return []
    # 2026-08-27 改：去掉 48h cutoff，保留所有纪要（前端分页展示）
    # 去重（按 topic_id）
    seen = set()
    unique_min = []
    for it in sorted(items, key=lambda x: x.get('pub_ts', 0), reverse=True):
        tid = it.get('id', '')
        if tid in seen:
            continue
        seen.add(tid)
        unique_min.append(it)
    return unique_min

# ──────────── 内存快照 + 广播 ────────────
class Broadcaster:
    def __init__(self):
        self.snapshot = {'domestic': [], 'overseas': [], 'minutes': []}
        self.subscribers: List[asyncio.Queue] = []
        self.lock = asyncio.Lock()

    async def update(self, snap):
        # 2026-09-01 研算子优化：限制内存占用，512MB 环境避免 OOM
        # 600 条原始纪要全部加载太耗内存，限制每类最多 200 条
        snap_trimmed = {}
        for k in ('domestic', 'overseas', 'minutes'):
            v = snap.get(k, [])
            if len(v) > 200:
                # 按 pub_ts 倒序保留最新的 50 条（256MB free tier 内存优化）
                v = sorted(v, key=lambda x: x.get('pub_ts', 0), reverse=True)[:50]
            snap_trimmed[k] = v
        async with self.lock:
            self.snapshot = snap_trimmed
            for q in self.subscribers:
                try:
                    q.put_nowait({'event': 'update', 'data': snap_trimmed, 'ts': int(time.time())})
                except asyncio.QueueFull:
                    pass

    async def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=10)
        async with self.lock:
            self.subscribers.append(q)
            await q.put({'event': 'snapshot', 'data': self.snapshot, 'ts': int(time.time())})
        return q

    async def unsubscribe(self, q):
        async with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

BC = Broadcaster()

async def fetch_loop():
    log.info(f'调度器启动,间隔 {FETCH_INTERVAL}s,{len(DOMESTIC_SOURCES) + len(OVERSEAS_SOURCES)} 源并行')
    while True:
        try:
            t0 = time.time()
            # 并行抓取(用 asyncio.to_thread 包装)
            dom_task = asyncio.create_task(asyncio.to_thread(fetch_all_parallel))
            min_task = asyncio.create_task(asyncio.to_thread(fetch_minutes_parallel))

            result = await dom_task
            dom, os_ = result
            minutes = await min_task

            await BC.update({'domestic': dom, 'overseas': os_, 'minutes': minutes})
            log.info(f'抓取完成:国内={len(dom)} 海外={len(os_)} 纪要={len(minutes)} 耗时={time.time()-t0:.2f}s')
        except Exception as e:
            log.exception(f'fetch loop err: {e}')
        await asyncio.sleep(FETCH_INTERVAL)

# ──────────── FastAPI ────────────
app = FastAPI(title='知几察微 v3', version='3.0')

# ============ 访问统计 / 管理后台 ============
ADMIN_USER = os.getenv('ADMIN_USER', 'admin')
ADMIN_PASS = os.getenv('ADMIN_PASS', 'zhiji2026')  # 花总自己用,简单点
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'zhiji-admin-token-2026')  # 简化版鉴权 token

STATS_DIR = Path(os.getenv('STATS_DIR', '/app/data'))
STATS_DIR.mkdir(parents=True, exist_ok=True)
STATS_DB = STATS_DIR / 'stats.db'

REPORTS_DIR = Path(os.getenv('REPORTS_DIR', '/app/reports'))
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

def _init_stats_db():
    conn = sqlite3.connect(str(STATS_DB))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS visit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        ip TEXT, country TEXT, device TEXT, browser TEXT,
        path TEXT, referrer TEXT, session_id TEXT, duration INTEGER
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ts ON visit_logs(ts)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_session ON visit_logs(session_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS news_clicks (
        news_id TEXT PRIMARY KEY,
        source_id TEXT,
        title TEXT,
        url TEXT,
        click_count INTEGER DEFAULT 0,
        last_click_ts INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS source_stats (
        source_id TEXT PRIMARY KEY,
        date TEXT,
        success_count INTEGER DEFAULT 0,
        fail_count INTEGER DEFAULT 0
    )''')
    conn.commit()
    conn.close()

def _log_visit(ip, device, browser, path, referrer, session_id, duration=0):
    try:
        conn = sqlite3.connect(str(STATS_DB), timeout=2)
        c = conn.cursor()
        c.execute('INSERT INTO visit_logs (ts, ip, device, browser, path, referrer, session_id, duration) VALUES (?,?,?,?,?,?,?,?)',
                  (int(time.time()), ip, device, browser, path[:200] if referrer else path, referrer[:200] if referrer else '', session_id, duration))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.debug(f'visit log fail: {e}')

def _parse_device_browser(ua: str):
    """简易 UA 解析"""
    ua_lower = (ua or '').lower()
    if 'mobile' in ua_lower or 'android' in ua_lower or 'iphone' in ua_lower:
        device = 'mobile'
    elif 'ipad' in ua_lower or 'tablet' in ua_lower:
        device = 'tablet'
    else:
        device = 'pc'
    if 'edg/' in ua_lower:
        browser = 'Edge'
    elif 'chrome/' in ua_lower:
        browser = 'Chrome'
    elif 'safari/' in ua_lower:
        browser = 'Safari'
    elif 'firefox/' in ua_lower:
        browser = 'Firefox'
    else:
        browser = 'Other'
    return device, browser

@app.middleware('http')
async def stats_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    # 只记录页面请求,不记录 /api/ 和静态资源
    path = request.url.path
    if path == '/' or path.startswith('/admin') or path == '/api/about':
        try:
            ua = request.headers.get('user-agent', '')
            device, browser = _parse_device_browser(ua)
            ip = request.headers.get('x-real-ip') or (request.client.host if request.client else '')
            ref = request.headers.get('referer', '')
            sid = request.cookies.get('jeeseek_sid') or request.headers.get('x-session-id', '')
            duration = int(time.time() - start)
            _log_visit(ip, device, browser, path, ref, sid, duration)
        except Exception:
            pass
    return response

@app.on_event('startup')
async def on_startup():
    _init_stats_db()
    asyncio.create_task(fetch_loop())

    # v4: 30 秒后预热 AI 报告缓存
    async def _warmup_ai():
        await asyncio.sleep(30)
        try:
            from ai_analyzer import generate_report
            dom = BC.snapshot.get('domestic', [])
            os_ = BC.snapshot.get('overseas', [])
            mn = BC.snapshot.get('minutes', [])
            t0 = time.time()
            await generate_report(dom, os_, mn, force=False)
            log.info(f'[jeeseek.startup] AI 报告预热完成 ({time.time()-t0:.1f}s)')
        except Exception as e:
            log.warning(f'[jeeseek.startup] AI 报告预热失败: {e}')
    asyncio.create_task(_warmup_ai())

    # v4.4: 35 秒后预热 AI 研判
    async def _warmup_radar():
        await asyncio.sleep(35)
        try:
            dom = BC.snapshot.get('domestic', [])
            os_ = BC.snapshot.get('overseas', [])
            mn = BC.snapshot.get('minutes', [])
            t0 = time.time()
            await generate_radar(dom, os_, mn, force=False, _cache=_CACHE, _call_ai_fn=_call_agnes)
            log.info(f'[jeeseek.startup] AI 研判预热完成 ({time.time()-t0:.1f}s)')
        except Exception as e:
            log.warning(f'[jeeseek.startup] AI 研判预热失败: {e}')
    asyncio.create_task(_warmup_radar())

    # v4.4: 雷达定时任务（每日 4 次: 9:00 / 11:30 / 15:00 / 17:00 Asia/Shanghai）
    async def _radar_cron():
        import pytz
        from datetime import datetime as _dt
        tz = pytz.timezone('Asia/Shanghai')
        last_run_slot = None
        while True:
            try:
                now = _dt.now(tz)
                # 4 个触发点（带 5 分钟容错窗口）
                for hh, mm in [(9, 0), (11, 30), (15, 0), (17, 0)]:
                    if now.hour == hh and abs(now.minute - mm) < 3:
                        slot_key = f'{hh:02d}:{mm:02d}'
                        if last_run_slot != slot_key:
                            last_run_slot = slot_key
                            try:
                                dom = BC.snapshot.get('domestic', [])
                                os_ = BC.snapshot.get('overseas', [])
                                mn = BC.snapshot.get('minutes', [])
                                log.info(f'[jeeseek.radar] cron 触发 {slot_key}')
                                t0 = time.time()
                                await generate_radar(dom, os_, mn, force=True, _cache=_CACHE, _call_ai_fn=_call_agnes)
                                log.info(f'[jeeseek.radar] cron 完成 ({time.time()-t0:.1f}s)')
                            except Exception as e:
                                log.warning(f'[jeeseek.radar] cron 失败: {e}')
                        break
                await asyncio.sleep(60)
            except Exception as e:
                log.warning(f'[jeeseek.radar] cron 异常: {e}')
                await asyncio.sleep(60)

    asyncio.create_task(_radar_cron())

@app.get('/healthz')
async def healthz():
    return {
        'status': 'ok',
        'ts': int(time.time()),
        'domestic': len(BC.snapshot['domestic']),
        'overseas': len(BC.snapshot['overseas']),
        'minutes': len(BC.snapshot['minutes']),
    }

@app.get('/api/news')
async def get_news(category: str = Query('domestic', pattern='^(domestic|overseas|minutes)$'),
                   source: str = Query(None),
                   q: str = Query(None, description='搜索关键词（标题/全文 LIKE 过滤）'),
                   offset: int = Query(0, ge=0),
                   limit: int = Query(200, ge=1, le=500)):
    """新闻列表 API
    
    Args:
        category: domestic / overseas / minutes
        source: source_id 过滤（如 'cls' / 'bbc-world'），'all' 表示全部
        q: 搜索关键词（标题模糊匹配，不区分大小写）
        offset: 偏移（用于纪要无限下拉）
        limit: 单页上限
    """
    data = BC.snapshot.get(category, [])
    
    # 来源过滤
    if source and source != 'all':
        data = [x for x in data if x.get('source_id') == source]
    
    # 关键词过滤（标题模糊匹配 + 来源名称）
    if q and q.strip():
        keyword = q.strip().lower()
        def _match(item):
            title = (item.get('title') or '').lower()
            source_name = (item.get('source_name') or '').lower()
            return keyword in title or keyword in source_name
        data = [x for x in data if _match(x)]
    
    total = len(data)
    # 纪要分页：offset/limit；其他 tab 一次返回（limit 通常 >= 数据量）
    items = data[offset:offset + limit]
    
    return JSONResponse({
        'category': category,
        'count': total,           # 过滤后总数
        'total': total,
        'offset': offset,
        'limit': limit,
        'has_more': offset + limit < total,  # 纪要下拉用
        'items': items,
        'ts': int(time.time())
    })

@app.get('/api/stats')
async def get_stats():
    """PV/UV 统计(简化版:每次访问 +1)"""
    # 这里只是占位,trend 有完整的 stats 实现
    return {
        'today_pv': 0, 'today_uv': 0,
        'total_pv': 0, 'total_uv': 0,
    }

@app.get('/events')
async def events(request: Request):
    q = await BC.subscribe()
    async def gen():
        try:
            yield {'event': 'snapshot', 'data': json.dumps(BC.snapshot, ensure_ascii=False, default=str)}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield {'event': msg['event'], 'data': json.dumps(msg['data'], ensure_ascii=False, default=str)}
                except asyncio.TimeoutError:
                    yield {'event': 'ping', 'data': json.dumps({'ts': int(time.time())})}
        finally:
            await BC.unsubscribe(q)
    return EventSourceResponse(gen())

# ──────────── 静态资源 ────────────
STATIC_DIR = Path(__file__).parent / 'static'
STATIC_DIR.mkdir(exist_ok=True)
app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')

@app.get('/')
async def index():
    html_path = STATIC_DIR / 'index.html'
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding='utf-8'), headers={'Content-Type': 'text/html; charset=utf-8'})
    return HTMLResponse('<h1>知几察微 v3</h1>')

@app.get('/api/about')
async def api_about():
    """关于页面:项目说明 + 数据源 + 免责声明"""
    about_html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>关于 知几察微</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1f2937; line-height: 1.7; }
h1 { color: #2563eb; border-bottom: 2px solid #2563eb; padding-bottom: 8px; }
.warn { background: #fef3c7; border-left: 4px solid #f59e0b; padding: 16px; border-radius: 4px; margin: 20px 0; font-size: 15px; }
</style>
</head>
<body>
<h1>📌 关于 知几察微</h1>

<div class="warn">
<strong>声明!该网站开源,只用于做投研学习、消息跟踪,不构成投资建议!</strong>
</div>

<p>知几察微 = A股 + 海外资讯 实时聚合 + 智能翻译 + 机构纪要追踪。</p>

<p><strong>数据源</strong>:财联社 / 金十数据 / 格隆汇 / 华尔街见闻 / 新浪财经 / 同花顺 / 第一财经 / BBC / NYT / WSJ / Yahoo Finance / FT / TrendForce / 联合早报。</p>

<p><strong>技术栈</strong>:FastAPI + SSE 实时推送 + 腾讯云机器翻译。</p>

<p style="text-align: center; color: #94a3b8; font-size: 13px; margin-top: 32px;">知几察微 · 投研情报站</p>
</body>
</html>'''
    return HTMLResponse(about_html, headers={'Content-Type': 'text/html; charset=utf-8'})

# ============ Admin API ============
from fastapi import Header

def _check_admin(authorization: str = Header(None)):
    """验证 token(简化版 Bearer)"""
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, '未授权')
    token = authorization[7:]
    if token != ADMIN_TOKEN:
        raise HTTPException(403, 'token 错误')
    return True

@app.get('/api/admin/login')
async def admin_login(user: str, pwd: str):
    """登录验证(简化版 GET,生产用 POST)"""
    if user == ADMIN_USER and pwd == ADMIN_PASS:
        return {'ok': True, 'token': ADMIN_TOKEN, 'user': user}
    raise HTTPException(401, '账号或密码错误')

@app.get('/api/admin/stats')
async def admin_stats(authorization: str = Header(None)):
    """仪表盘聚合数据"""
    _check_admin(authorization)
    conn = sqlite3.connect(str(STATS_DB), timeout=5)
    c = conn.cursor()
    now = int(time.time())
    h24 = now - 86400
    h7 = now - 86400 * 7
    h30 = now - 86400 * 30

    # PV / UV
    c.execute('SELECT COUNT(*) FROM visit_logs WHERE ts>=?', (h24,))
    pv24 = c.fetchone()[0]
    c.execute('SELECT COUNT(DISTINCT session_id) FROM visit_logs WHERE ts>=?', (h24,))
    uv24 = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM visit_logs WHERE ts>=?', (h7,))
    pv7 = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM visit_logs WHERE ts>=?', (h30,))
    pv30 = c.fetchone()[0]

    # 实时在线(最近 5 分钟)
    c.execute('SELECT COUNT(DISTINCT session_id) FROM visit_logs WHERE ts>=?', (now - 300,))
    online = c.fetchone()[0]

    # 设备分布(24h)
    c.execute('SELECT device, COUNT(*) FROM visit_logs WHERE ts>=? GROUP BY device', (h24,))
    devices = dict(c.fetchall())
    c.execute('SELECT browser, COUNT(*) FROM visit_logs WHERE ts>=? GROUP BY browser', (h24,))
    browsers = dict(c.fetchall())

    # 24h 小时分布
    c.execute("SELECT strftime('%H', datetime(ts, 'unixepoch', '+8 hours')), COUNT(*) FROM visit_logs WHERE ts>=? GROUP BY 1", (h24,))
    hourly = {int(k or 0): v for k, v in c.fetchall()}
    hourly_full = {f'{h:02d}': hourly.get(h, 0) for h in range(24)}

    # 7 天趋势
    c.execute("SELECT date(datetime(ts, 'unixepoch', '+8 hours')), COUNT(*) FROM visit_logs WHERE ts>=? GROUP BY 1 ORDER BY 1", (h7,))
    daily = dict(c.fetchall())
    daily_full = []
    for i in range(7):
        d = (datetime.now() - timedelta(days=6-i)).strftime('%Y-%m-%d')
        daily_full.append({'date': d, 'count': daily.get(d, 0)})

    conn.close()
    return {
        'pv_24h': pv24, 'uv_24h': uv24, 'pv_7d': pv7, 'pv_30d': pv30,
        'online': online, 'devices': devices, 'browsers': browsers,
        'hourly_24h': hourly_full, 'daily_7d': daily_full,
    }

@app.get('/api/admin/content-stats')
async def admin_content_stats(authorization: str = Header(None)):
    """内容统计:各源条数 + 当前快照"""
    _check_admin(authorization)
    from collections import Counter
    src_counter = Counter()
    for x in BC.snapshot['domestic']:
        src_counter[x.get('source_id', '?')] += 1
    for x in BC.snapshot['overseas']:
        src_counter[x.get('source_id', '?')] += 1
    sources = [{'source_id': k, 'count': v} for k, v in src_counter.most_common()]
    return {
        'domestic': len(BC.snapshot['domestic']),
        'overseas': len(BC.snapshot['overseas']),
        'minutes': len(BC.snapshot['minutes']),
        'sources': sources,
        'last_update_ts': BC.snapshot.get('ts', 0),
    }

@app.get('/api/admin/recent-visitors')
async def admin_recent_visitors(authorization: str = Header(None), limit: int = 30):
    """最近访问记录"""
    _check_admin(authorization)
    conn = sqlite3.connect(str(STATS_DB), timeout=5)
    c = conn.cursor()
    c.execute('SELECT ts, ip, device, browser, path, session_id FROM visit_logs ORDER BY ts DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()
    return [{
        'ts': r[0], 'time': datetime.fromtimestamp(r[0] + 8*3600).strftime('%m-%d %H:%M:%S'),
        'ip': r[1], 'device': r[2], 'browser': r[3], 'path': r[4], 'session': (r[5] or '')[:12]
    } for r in rows]

# ============ AI 热点分析 ============
from ai_analyzer import generate_report, _CACHE, _call_agnes
# 256MB 精简版：关闭 AI 研判（最占内存）
# from ai_radar import generate_radar, _load_radar_from_disk

@app.get('/api/ai/hotspot-analysis')
async def api_ai_hotspot(force: int = 0):
    """AI 热点分析 JSON 接口(前端展示用)"""
    dom = BC.snapshot.get('domestic', [])
    os = BC.snapshot.get('overseas', [])
    mn = BC.snapshot.get('minutes', [])
    result = await generate_report(dom, os, mn, force=bool(force))
    return result

@app.get('/api/ai/report')
async def api_ai_report(force: int = 0):
    """
    AI 热点分析完整报告(HTML 页面,嵌入 iframe 用)
    force=1 强制刷新
    v4: 加磁盘兜底 (latest.html 24h 内秒返) + 进程内缓存双保险
    """
    from ai_analyzer import generate_report, _CACHE

    dom = BC.snapshot.get('domestic', [])
    os_ = BC.snapshot.get('overseas', [])
    mn = BC.snapshot.get('minutes', [])

    # force=1: 忽略所有缓存,直接走 AI
    if force:
        result = await generate_report(dom, os_, mn, force=True)
        if result.get('success'):
            return HTMLResponse(result['html'], headers={'Content-Type': 'text/html; charset=utf-8'})
        return JSONResponse(result, status_code=500)

    # 快速路径1: 进程内缓存命中 (4 小时桶)
    cache_key = f'report:{len(dom)}:{len(os_)}:{len(mn)}:{int(time.time() // 14400)}'
    if cache_key in _CACHE:
        log.info('[jeeseek.api] /api/ai/report 命中进程缓存')
        return HTMLResponse(_CACHE[cache_key]['html'], headers={'Content-Type': 'text/html; charset=utf-8'})

    # 快速路径2: 磁盘兜底 (24h 内 latest.html 秒返 + 后台异步刷新)
    latest = REPORTS_DIR / 'latest.html'
    if latest.exists():
        age = time.time() - latest.stat().st_mtime
        if age < 86400:
            async def _bg_refresh():
                try:
                    await generate_report(dom, os_, mn, force=False)
                    log.info('[jeeseek.ai] disk-fallback 后台刷新完成')
                except Exception as e:
                    log.warning(f'[jeeseek.ai] disk-fallback 后台刷新失败: {e}')
            asyncio.create_task(_bg_refresh())
            html = latest.read_text(encoding='utf-8')
            log.info(f'[jeeseek.api] /api/ai/report 磁盘兜底 ({int(age)}s 前,后台刷新中)')
            return HTMLResponse(html, headers={
                'Content-Type': 'text/html; charset=utf-8',
                'X-Cache': 'disk-fallback',
                'X-Age-Seconds': str(int(age)),
            })

    # 都 miss: 走 AI (首次或磁盘太旧)
    result = await generate_report(dom, os_, mn, force=False)
    if result.get('success'):
        return HTMLResponse(result['html'], headers={'Content-Type': 'text/html; charset=utf-8'})
    return JSONResponse(result, status_code=500)



# ============ AI 研判（256MB 精简版已关闭）============
# 256MB free tier 跑不动 AI 研判模块（生成 4 卡 + 3 句话 + 主题雷达内存开销大）
# 路由保留以兼容前端，但返回禁用提示
@app.get('/api/ai/radar')
async def api_ai_radar(force: int = 0):
    return JSONResponse({
        'success': False,
        'error': 'AI 研判功能在 256MB free tier 已禁用',
        'message': '请升级到 512MB 或在 Koyeb 等平台恢复',
    }, status_code=503)


@app.get('/api/ai/radar/status')
async def api_ai_radar_status():
    return {'has_data': False, 'disabled': True, 'reason': '256MB free tier'}

@app.get('/api/ai/keywords')
async def api_ai_keywords(limit: int = 50):
    """
    关键词词频聚合报告(HTML 页面)
    按 10 个预设分组(科技/AI/宏观金融/中概互联/能源/地缘/半导体等)聚合
    """
    from keywords import render_keywords_html
    dom = BC.snapshot.get('domestic', [])
    os = BC.snapshot.get('overseas', [])
    # 合并所有新闻源进行聚合
    all_news = list(dom) + list(os)
    html = render_keywords_html(all_news, limit_per_group=limit)
    return HTMLResponse(html, headers={'Content-Type': 'text/html; charset=utf-8'})

@app.get('/api/ai/keywords.json')
async def api_ai_keywords_json(limit: int = 50):
    """
    关键词词频聚合(JSON 接口)
    """
    from keywords import aggregate_by_keyword
    dom = BC.snapshot.get('domestic', [])
    os = BC.snapshot.get('overseas', [])
    all_news = list(dom) + list(os)
    grouped, stats = aggregate_by_keyword(all_news, limit_per_group=limit)
    return {'success': True, 'stats': stats, 'groups': grouped}
    return HTMLResponse('<h1>AI 报告生成中...</h1>')

@app.get('/api/ai/hotspot-cache')
async def api_ai_hotspot_cache_clear(authorization: str = Header(None)):
    """清空 AI 热点分析缓存(管理用)"""
    _check_admin(authorization)
    from ai_analyzer import _CACHE
    n = len(_CACHE)
    _CACHE.clear()
    return {'cleared': n}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level='info')