#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知几察微 v3 - server.py
- 照搬 trend.902187.xyz 的数据形态(source_type/tier1/rank/time/url)
- 抓取全并行(asyncio.gather),单次从 100s 降到 <10s
- FETCH_INTERVAL 30s(之前 60s)
- 国内 10 源 + 海外 14 源 = 24 源全开
"""

import os, sys, json, asyncio, logging, glob, time, re, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator, List, Dict, Any
import feedparser
import socket
from concurrent.futures import ThreadPoolExecutor

# 数据库抽象层（Render 部署用 Postgres / 本地用 SQLite）
import db
import auth  # jeeseek 邀请制鉴权（见 auth.py）

# v5.3c build marker — 验证 v5.3c 部署的 server.py 真生效
print('=== JEESEEK BUILD v5.3c 2026-10-14 12:50 ===', flush=True)
print('  LISTEN_HOST:', os.getenv('LISTEN_HOST', '0.0.0.0'), flush=True)
print('  LISTEN_PORT:', os.getenv('PORT', os.getenv('LISTEN_PORT', '8080')), flush=True)
print('  DATABASE_URL exists:', bool(os.getenv('DATABASE_URL')), flush=True)

# 腾讯云机器翻译(实时翻译海外资讯)
try:
    from translator import translate_title as _translate_title, get_cache_stats as _translate_stats
except Exception:
    def _translate_title(t): return t
    def _translate_stats(): return {'cached': 0, 'max': 0, 'enabled': False}

from fastapi import FastAPI, Query, Request, HTTPException, Response
from fastapi import Cookie, Depends, status as fastapi_status
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
import pytz

# ──────────── 配置 ────────────
TZ_CN = pytz.timezone('Asia/Shanghai')
LISTEN_HOST = os.getenv('LISTEN_HOST', '0.0.0.0')
LISTEN_PORT = int(os.getenv('PORT', os.getenv('LISTEN_PORT', '8080')))
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

LOG_PATH = os.getenv('JEESEEK_LOG_PATH', '/var/log/jeeseek/server.log')
# 容器里 /var/log 可能没写权限 → fallback
try:
    Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    LOG_PATH = '/tmp/jeeseek/server.log'
    Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)

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
        # 2026-09-03 v4.4: 调试打印一条 topic 实际字段（部署后看日志）
        # 2026-09-03 v4.5: 增强打印 owner/group 单独字段（不受 500 字符限制）
        if all_topics and os.getenv('ZSXQ_DEBUG_TOPIC', '1') == '1':
            sample = all_topics[0]
            log.info(f'[zsxq sample topic] keys={list(sample.keys())} sample={str(sample)[:500]}')
            sample_owner = sample.get('owner')
            sample_group = sample.get('group')
            log.info(f'[zsxq sample owner] type={type(sample_owner).__name__} value={str(sample_owner)[:400]}')
            log.info(f'[zsxq sample group] type={type(sample_group).__name__} value={str(sample_group)[:400]}')
            os.environ['ZSXQ_DEBUG_TOPIC'] = '0'  # 只打一次
        topics = all_topics
        skipped_short = 0
        skipped_empty = 0
        skipped_type = 0
        for t in topics:
            topic_type = t.get('type', '') or t.get('talk_type', '')
            content = t.get('content','').strip()
            title = t.get('title','').strip() or t.get('content','')[:80]

            # v4.4: 过滤纯附件 topic（图片/文件/音频）
            # 1. 有明确 type 字段且不是 talk/q&a/short_text/digest
            if topic_type and topic_type not in ('talk', 'q&a', 'short_text', 'digest', ''):
                skipped_type += 1
                continue
            # 2. content 短且没有 title → 几乎肯定是纯附件
            if not content and not title:
                skipped_empty += 1
                continue
            # 3. content 太短 (< 20字) 且 title 也短 → 可能是纯图片描述/文件说明
            if len(content) < 20 and len(title) < 20:
                skipped_short += 1
                continue

            create_time = t.get('create_time','')
            pub_ts = _parse_iso(create_time)
            topic_id = t.get('topic_id','')
            # v4.5: 从 topic['owner']['name'] 拿星球所有者昵称（花总账号=基业长青）
            # topic['owner'] 是 dict {alias/name/user_id/avatar_url}，topic['group'] 是 dict {group_id/name}
            t_owner = t.get('owner') if isinstance(t.get('owner'), dict) else {}
            t_group = t.get('group') if isinstance(t.get('group'), dict) else {}
            owner_name = t_owner.get('name') or ''
            group_name = t_group.get('name') or ''
            items.append({
                'id': f'minutes-{topic_id}',
                'title': title,
                'pub_ts': pub_ts,
                'source_id': 'zsxq',
                'source_name': '机构纪要',  # 花总拍：不暴露知识星球博主名
                'owner_name': owner_name,
                'group_name': group_name,
                'group_id': str(t_group.get('group_id') or ZSXQ_GROUP_ID),
                'source_type': 'minutes',
                'time': _format_time(pub_ts),
                'url': f'https://wx.zsxq.com/detailed/{topic_id}',
                'content': content,
            })
        log.info(f'zsxq 过滤: type={skipped_type} short={skipped_short} empty={skipped_empty} → 保留 {len(items)} 条')
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
        # 2026-09-03 v5: 1000 条覆盖全天纪要（每天约 200-500 条），内存 1000*2KB≈2MB 可控
        snap_trimmed = {}
        for k in ('domestic', 'overseas', 'minutes'):
            v = snap.get(k, [])
            if len(v) > 1000:
                # 按 pub_ts 倒序保留最新的 1000 条（覆盖 2-3 天全天，足够 AI 报告采样 8-12 条）
                v = sorted(v, key=lambda x: x.get('pub_ts', 0), reverse=True)[:1000]
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

REPORTS_DIR = Path(os.getenv('REPORTS_DIR', '/app/reports'))
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# 数据库初始化（兼容 Postgres/Supabase 和本地 SQLite）
def _init_stats_db():
    """向后兼容的 init 函数，实际调用 db.init_schema()"""
    db.init_schema()

def _log_visit(ip, device, browser, path, referrer, session_id, duration=0):
    try:
        conn = db.connect()
        c = conn.cursor()
        c.execute('INSERT INTO visit_logs (ts, ip, device, browser, path, referrer, session_id, duration) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
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

class RegisterReq(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_\-]+$")
    email: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    invite_code: str = Field(default="", max_length=64)  # 首位注册可留空
    display_name: str = Field(default="", max_length=64)

class LoginReq(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)

class CreateInviteReq(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=50)
    note: str = Field(default="", max_length=128)
    expires_in_days: int = Field(default=0, ge=0, le=365)  # 0 = 永久
    code: str = Field(default="", max_length=64)  # 自定义邀请码（可选）

class UpdateInviteReq(BaseModel):
    max_uses: int = Field(default=0, ge=0, le=50)  # 0 = 不改
    note: str = Field(default="", max_length=128)  # 空串 = 保持原值 (除非显式传 __CLEAR__)
    expires_in_days: int = Field(default=-1, ge=-1, le=365)  # -1 = 不改；0 = 永久；>0 = N 天
    status: str = Field(default="", max_length=16)  # 'active' / 'revoked' / '' = 不改

# 不需要登录的路径前缀/精确路径（v5.1 全部锁）
AUTH_PUBLIC_PATHS = (
    "/api/auth/",       # 注册/登录/登出/me - 唯一对外的鉴权入口
    "/static/",         # 静态资源：login.html / register.html / logo 等
    "/favicon.ico",
    "/robots.txt",
)

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """邀请制鉴权：除白名单外，所有 /api/* 必须登录"""
    path = request.url.path
    # 白名单内直接放行
    for pub in AUTH_PUBLIC_PATHS:
        if path == pub or path.startswith(pub):
            return await call_next(request)
    # 只锁 /api/*（前端页面走 index.html 静态分发 + JS 二次校验）
    if not path.startswith("/api/"):
        return await call_next(request)
    # 检查 cookie
    token = request.cookies.get(auth.COOKIE_NAME)
    user = None
    if token:
        payload = auth.decode_token(token)
        if payload:
            jti = payload.get("jti")
            if jti and auth.is_session_valid(jti):
                uid = int(payload.get("sub", 0))
                if uid:
                    user = auth.get_user_by_id(uid)
                    if user and user.get("status") != "active":
                        user = None
    if not user:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "需要登录", "code": "AUTH_REQUIRED"},
        )
    # 把 user 挂到 request.state 给后续 handler 用
    request.state.user = user
    return await call_next(request)

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
    log.info('[jeeseek.startup] >>> on_startup 进入 (v5.2.1-debug)')
    try:
        _init_stats_db()
        log.info(f'[jeeseek.startup] _init_stats_db 完成, USE_POSTGRES={db.USE_POSTGRES}')
    except Exception as e:
        log.error(f'[jeeseek.startup] _init_stats_db 失败: {e!r}')
        import traceback
        log.error(f'[jeeseek.startup] _init_stats_db traceback: {traceback.format_exc()}')
        return  # 关键：别再走下面，避免后续崩溃
    # v4: 启动时打印 translator cache 状态
    try:
        stats = _translate_stats()
        log.info(f'[jeeseek.startup] translator 状态: enabled={stats.get("enabled")} cached={stats.get("cached")}')
    except Exception as e:
        log.warning(f'[jeeseek.startup] translator 状态读取失败: {e}')

    # v5.2.2: 启动时自动建 system 账号 + LongWay6124 邀请码
    # 设计：所有异常 log.error 不吞；不依赖 _init_stats_db 跑成功
    log.info('[jeeseek.startup] >>> 准备建 system 账号 + LongWay6124')
    try:
        # 1) 确保 system 账号存在 (用 bcrypt 在本函数内 hash, 不依赖 auth.get_user_by_username 失败)
        system_user = auth.get_user_by_username('system')
        if not system_user:
            import bcrypt as _bcrypt
            _ph = _bcrypt.hashpw(b'__system_no_login__', _bcrypt.gensalt()).decode()
            conn = db.connect()
            cur = conn.cursor()
            try:
                if db.USE_POSTGRES:
                    cur.execute(
                        "INSERT INTO users (username, email, password_hash, display_name, role, invite_quota) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (username) DO NOTHING RETURNING id",
                        ('system', 'system@jeeseek.local', _ph, 'System', 'admin', 0),
                    )
                else:
                    cur.execute(
                        "INSERT OR IGNORE INTO users (username, email, password_hash, display_name, role, invite_quota) VALUES (?, ?, ?, ?, ?, ?)",
                        ('system', 'system@jeeseek.local', _ph, 'System', 'admin', 0),
                    )
                conn.commit()
                log.info('[jeeseek.startup] system 账号 INSERT 执行完毕')
            except Exception as e:
                log.error(f'[jeeseek.startup] system 账号 INSERT 失败: {e!r}')
                import traceback
                log.error(f'[jeeseek.startup] INSERT traceback: {traceback.format_exc()}')
                conn.rollback()
            finally:
                conn.close()
        else:
            log.info(f'[jeeseek.startup] system 账号已存在 (id={system_user["id"]})')

        # 2) 重读 system 账号, 建 LongWay6124
        system_user = auth.get_user_by_username('system')
        if not system_user:
            log.error('[jeeseek.startup] 重读 system 账号后仍为 None — 无法建 LongWay6124')
        else:
            log.info(f'[jeeseek.startup] system 账号 id={system_user["id"]}, 调用 create_invite_code')
            try:
                code = auth.create_invite_code(
                    created_by=system_user['id'],
                    code_value='LongWay6124',
                    max_uses=999,
                    note='long way 6124',
                )
                if code:
                    log.info(f'[jeeseek.startup] 自动建码 LongWay6124 成功 (uid={system_user["id"]})')
                else:
                    log.info('[jeeseek.startup] LongWay6124 已存在 (create_invite_code 返回 None = UNIQUE 冲突被吞)')
            except Exception as e:
                log.error(f'[jeeseek.startup] LongWay6124 创建失败: {e!r}')
                import traceback
                log.error(f'[jeeseek.startup] create_invite_code traceback: {traceback.format_exc()}')
    except Exception as e:
        log.error(f'[jeeseek.startup] 自动建码总逻辑失败: {e!r}')
        import traceback
        log.error(f'[jeeseek.startup] traceback: {traceback.format_exc()}')

    log.info('[jeeseek.startup] <<< on_startup 即将创建 fetch_loop task')
    try:
        asyncio.create_task(fetch_loop())
        log.info('[jeeseek.startup] fetch_loop task 创建成功')
    except Exception as e:
        log.error(f'[jeeseek.startup] fetch_loop 创建失败: {e!r}')

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

    # v4.4: AI 研判已禁用（Render Free 512MB 跑不动，用户拍板）
    # 不再启动 _warmup_radar 和 _radar_cron 任务，节省 CPU/内存
    log.info('[jeeseek.startup] AI 研判已禁用（Render Free 512MB 限制）')

@app.get('/healthz')
async def healthz():
    return {
        'status': 'ok',
        'ts': int(time.time()),
        'domestic': len(BC.snapshot['domestic']),
        'overseas': len(BC.snapshot['overseas']),
        'minutes': len(BC.snapshot['minutes']),
    }

@app.get('/api/debug/translate')
async def debug_translate(text: str = 'Apple unveils new AI chip'):
    """调试翻译:返回 translator 状态 + 测试翻译"""
    try:
        stats = _translate_stats()
        translated = _translate_title(text)
        return {
            'stats': stats,
            'input': text,
            'output': translated,
            'is_chinese': any('\u4e00' <= c <= '\u9fff' for c in translated)
        }
    except Exception as e:
        return {'error': str(e), 'type': type(e).__name__}

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
            content = (item.get('content') or '').lower() if category == 'minutes' else ''
            return keyword in title or keyword in source_name or keyword in content
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


def _is_logged_in(request: Request) -> bool:
    """检查请求是否带有效 token (不抛异常)"""
    try:
        token = request.cookies.get(auth.COOKIE_NAME)
        if not token:
            return False
        # verify_token 返回 jti dict 或抛错
        data = auth.verify_token(token)
        return bool(data)
    except Exception:
        return False


@app.get('/')
async def index(request: Request):
    # 未登录 → 返 login.html；已登录 → 返 index.html
    if not _is_logged_in(request):
        login_path = STATIC_DIR / 'login.html'
        if login_path.exists():
            return HTMLResponse(login_path.read_text(encoding='utf-8'), headers={'Content-Type': 'text/html; charset=utf-8'})
        return HTMLResponse('<h1>请先登录</h1>')
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
    conn = db.connect()
    c = conn.cursor()
    now = int(time.time())
    h24 = now - 86400
    h7 = now - 86400 * 7
    h30 = now - 86400 * 30

    # PV / UV（Postgres 用 %s，SQLite 也能用 %s 风格如果用 sqlite3 — 但保持 ? 让两边都用 db.connect）
    # psycopg2 用 %s, sqlite3 也支持 %s 在新版但为兼容用 qmark ?
    # 用 ? 占位符（sqlite3 原生，psycopg2 需换 %s）— 我们全部统一改 %s 走 psycopg2 + sqlite3 兼容
    c.execute('SELECT COUNT(*) FROM visit_logs WHERE ts>=%s', (h24,))
    pv24 = c.fetchone()[0]
    c.execute('SELECT COUNT(DISTINCT session_id) FROM visit_logs WHERE ts>=%s', (h24,))
    uv24 = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM visit_logs WHERE ts>=%s', (h7,))
    pv7 = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM visit_logs WHERE ts>=%s', (h30,))
    pv30 = c.fetchone()[0]

    # 实时在线(最近 5 分钟)
    c.execute('SELECT COUNT(DISTINCT session_id) FROM visit_logs WHERE ts>=%s', (now - 300,))
    online = c.fetchone()[0]

    # 设备分布(24h)
    c.execute('SELECT device, COUNT(*) FROM visit_logs WHERE ts>=%s GROUP BY device', (h24,))
    devices = dict(c.fetchall())
    c.execute('SELECT browser, COUNT(*) FROM visit_logs WHERE ts>=%s GROUP BY browser', (h24,))
    browsers = dict(c.fetchall())

    # 24h 小时分布（Postgres: 用 to_char，SQLite: 用 db.hour_in_cn）
    if db.USE_POSTGRES:
        c.execute("SELECT EXTRACT(HOUR FROM (to_timestamp(ts) AT TIME ZONE 'Asia/Shanghai'))::int, COUNT(*) FROM visit_logs WHERE ts>=%s GROUP BY 1", (h24,))
        hourly = {int(k or 0): v for k, v in c.fetchall()}
    else:
        c.execute("SELECT strftime('%H', datetime(ts, 'unixepoch', '+8 hours')), COUNT(*) FROM visit_logs WHERE ts>=%s GROUP BY 1", (h24,))
        hourly = {int(k or 0): v for k, v in c.fetchall()}
    hourly_full = {f'{h:02d}': hourly.get(h, 0) for h in range(24)}

    # 7 天趋势
    if db.USE_POSTGRES:
        c.execute("SELECT to_char(to_timestamp(ts) AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD'), COUNT(*) FROM visit_logs WHERE ts>=%s GROUP BY 1 ORDER BY 1", (h7,))
        daily = dict(c.fetchall())
    else:
        c.execute("SELECT date(datetime(ts, 'unixepoch', '+8 hours')), COUNT(*) FROM visit_logs WHERE ts>=%s GROUP BY 1 ORDER BY 1", (h7,))
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
    conn = db.connect()
    c = conn.cursor()
    c.execute('SELECT ts, ip, device, browser, path, session_id FROM visit_logs ORDER BY ts DESC LIMIT %s', (limit,))
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
async def api_ai_report(force: int = 0, mode: str = 'macro'):
    """
    AI 热点分析完整报告(HTML 页面,嵌入 iframe 用)
    force=1 强制刷新
    mode='macro' (默认) → v3 宏观信号 (TOP 赛道 + 4 类信号)
    mode='depth' → v4 行业跟踪 (按子赛道切分,适合产业链跟踪)
    v4: 加磁盘兜底 (latest.html 24h 内秒返) + 进程内缓存双保险
    """
    from ai_analyzer import generate_report, _CACHE

    if mode not in ('macro', 'depth'):
        mode = 'macro'

    dom = BC.snapshot.get('domestic', [])
    os_ = BC.snapshot.get('overseas', [])
    mn = BC.snapshot.get('minutes', [])

    # force=1: 忽略所有缓存,直接走 AI
    if force:
        result = await generate_report(dom, os_, mn, force=True, mode=mode)
        if result.get('success'):
            return HTMLResponse(result['html'], headers={'Content-Type': 'text/html; charset=utf-8'})
        return JSONResponse(result, status_code=500)

    # 快速路径1: 进程内缓存命中 (4 小时桶)
    cache_key = f'report:{mode}:{len(dom)}:{len(os_)}:{len(mn)}:{int(time.time() // 14400)}'
    if cache_key in _CACHE:
        log.info(f'[jeeseek.api] /api/ai/report 命中进程缓存 (mode={mode})')
        return HTMLResponse(_CACHE[cache_key]['html'], headers={'Content-Type': 'text/html; charset=utf-8'})

    # 快速路径2: 磁盘兜底 (24h 内 latest.html 秒返 + 后台异步刷新)
    latest = REPORTS_DIR / ('latest_depth.html' if mode == 'depth' else 'latest.html')
    if latest.exists():
        age = time.time() - latest.stat().st_mtime
        if age < 86400:
            async def _bg_refresh():
                try:
                    await generate_report(dom, os_, mn, force=False, mode=mode)
                    log.info(f'[jeeseek.ai] disk-fallback 后台刷新完成 (mode={mode})')
                except Exception as e:
                    log.warning(f'[jeeseek.ai] disk-fallback 后台刷新失败 (mode={mode}): {e}')
            asyncio.create_task(_bg_refresh())
            html = latest.read_text(encoding='utf-8')
            log.info(f'[jeeseek.api] /api/ai/report 磁盘兜底 (mode={mode}, {int(age)}s 前,后台刷新中)')
            return HTMLResponse(html, headers={
                'Content-Type': 'text/html; charset=utf-8',
                'X-Cache': 'disk-fallback',
                'X-Age-Seconds': str(int(age)),
                'X-Mode': mode,
            })

    # 都 miss: 走 AI (首次或磁盘太旧)
    result = await generate_report(dom, os_, mn, force=False, mode=mode)
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


@app.get('/api/minutes/search')
async def api_minutes_search(
    q: str = Query('', description='搜索关键词（公司名/项目/事件/产品）'),
    sector: str = Query('', description='赛道筛选（AI 算力链/端侧 AI/...）'),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200)
):
    """
    机构纪要专用搜索 API（v4.3 跟踪模式配套）
    - q: 关键词，匹配 title + content + source_name（全文模糊）
    - sector: 赛道筛选（8 个固定赛道之一）
    - 返回带赛道预分类的纪要列表
    """
    from ai_analyzer import _classify_minutes_sector

    all_minutes = BC.snapshot.get('minutes', [])
    keyword = q.strip().lower() if q else ''

    results = []
    for m in all_minutes:
        title = m.get('title', '')
        content = m.get('content', '')
        source_name = m.get('source_name', '')

        # 赛道预分类
        sectors = _classify_minutes_sector(title, content)

        # 赛道过滤
        if sector and sector not in sectors:
            continue

        # 关键词过滤（标题或正文）
        if keyword:
            text = (title + ' ' + content + ' ' + source_name).lower()
            if keyword not in text:
                continue

        # 截取 content 预览（前 500 字）
        item = {
            'id': m.get('id', ''),
            'title': title,
            'pub_ts': m.get('pub_ts', 0),
            'time': m.get('time', ''),
            'source_name': source_name,
            'source_id': m.get('source_id', ''),
            'url': m.get('url', ''),
            # v4.5.1: 透传 owner_name / group_name / group_id（让前端能按群筛）
            'owner_name': m.get('owner_name', ''),
            'group_name': m.get('group_name', ''),
            'group_id': m.get('group_id', ''),
            'sectors': sectors,
            'primary_sector': sectors[0] if sectors else '周期与宏观',
            'content_preview': content[:500] if content else '',
            'content_length': len(content),
        }
        results.append(item)

    total = len(results)
    items = results[offset:offset + limit]

    return JSONResponse({
        'category': 'minutes',
        'q': q,
        'sector': sector,
        'count': total,
        'total': total,
        'offset': offset,
        'limit': limit,
        'has_more': offset + limit < total,
        'items': items,
        'ts': int(time.time())
    })

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

# ════════════════════════════════════════════════════════
# 邀请制鉴权路由（v5 新增）
# ════════════════════════════════════════════════════════

@app.post("/api/auth/register")
async def api_register(req: RegisterReq, response: Response):
    # 1. 第一个注册的人自动成为 admin，不需要邀请码
    is_first = auth.count_users() == 0

    # 2. 校验邀请码（非首注册）
    invite = None
    if not is_first:
        invite = auth.get_invite_code(req.invite_code)
        if not invite:
            raise HTTPException(400, "邀请码不存在")
        if invite["status"] != "active":
            raise HTTPException(400, f"邀请码已{invite['status']}")
        if invite["expires_at"] and invite["expires_at"] < int(time.time()):
            raise HTTPException(400, "邀请码已过期")
        if invite["use_count"] >= invite["max_uses"]:
            raise HTTPException(400, "邀请码已用完")

    # 3. 唯一性检查
    if auth.get_user_by_username(req.username):
        raise HTTPException(400, "用户名已被占用")
    if auth.get_user_by_email(req.email):
        raise HTTPException(400, "邮箱已被注册")

    # 4. 写库
    role = "admin" if is_first else "user"
    pw_hash = auth.hash_password(req.password)
    display = req.display_name.strip() or req.username
    invited_by = None if is_first else invite["created_by"]
    now = int(time.time())
    conn = db.connect()
    cur = conn.cursor()
    try:
        if db.USE_POSTGRES:
            cur.execute(
                """INSERT INTO users (username,email,password_hash,display_name,role,invite_quota,invited_by,created_at,status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'active') RETURNING id""",
                (req.username, req.email.lower(), pw_hash, display, role, 3, invited_by, now),
            )
            uid = cur.fetchone()[0]
        else:
            cur.execute(
                """INSERT INTO users (username,email,password_hash,display_name,role,invite_quota,invited_by,created_at,status)
                   VALUES (?,?,?,?,?,?,?,?,'active')""",
                (req.username, req.email.lower(), pw_hash, display, role, 3, invited_by, now),
            )
            uid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    # 5. 消耗邀请码
    if not is_first:
        auth.consume_invite_code(req.invite_code, uid)

    # 6. 自动登录
    token = auth.create_token(uid, req.username, role)
    auth.set_auth_cookie(response, token)
    log.info(f"[auth] 新用户注册: uid={uid} username={req.username} role={role} first={is_first}")
    return {
        "ok": True,
        "user": {
            "id": uid, "username": req.username, "display_name": display,
            "role": role, "invite_quota": 3, "is_first": is_first,
        },
        "token": token,  # 便于前端 localStorage 备份（不强依赖）
    }


@app.post("/api/auth/login")
async def api_login(req: LoginReq, response: Response):
    import traceback
    try:
        user = auth.get_user_by_username(req.username)
    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"[auth.login] get_user_by_username FAILED: {e!r}\n{tb}")
        raise HTTPException(500, "登录服务暂不可用")
    if not user or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    if user["status"] != "active":
        raise HTTPException(403, f"账号已{user['status']}")
    try:
        auth.update_last_login(user["id"])
    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"[auth.login] update_last_login FAILED: {e!r}\n{tb}")
        # 登录成功但更新 last_login 失败 — 仍放行，不阻用户
    token = auth.create_token(user["id"], user["username"], user["role"])
    auth.set_auth_cookie(response, token)
    log.info(f"[auth] 登录: uid={user['id']} username={user['username']}")
    return {
        "ok": True,
        "user": {
            "id": user["id"], "username": user["username"],
            "display_name": user["display_name"], "role": user["role"],
            "invite_quota": user["invite_quota"],
        },
    }


@app.post("/api/auth/logout")
async def api_logout(request: Request, response: Response):
    # 吊销当前 session
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        payload = auth.decode_token(token)
        if payload and payload.get("jti"):
            auth.revoke_session(payload["jti"])
    auth.clear_auth_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
async def api_me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return {"ok": False, "authenticated": False}
    return {
        "ok": True,
        "authenticated": True,
        "user": {
            "id": user["id"], "username": user["username"],
            "display_name": user["display_name"], "role": user["role"],
            "invite_quota": user["invite_quota"],
        },
    }


@app.post("/api/invite/create")
async def api_invite_create(req: CreateInviteReq, request: Request):
    user = request.state.user
    if user["invite_quota"] <= 0:
        raise HTTPException(400, f"邀请名额已用完（剩余 {user['invite_quota']}）")
    # 实际消耗一个名额
    conn = db.connect()
    cur = conn.cursor()
    try:
        if db.USE_POSTGRES:
            cur.execute("UPDATE users SET invite_quota=invite_quota-1 WHERE id=%s", (user["id"],))
        else:
            cur.execute("UPDATE users SET invite_quota=invite_quota-1 WHERE id=?", (user["id"],))
        conn.commit()
    finally:
        conn.close()
    code = auth.create_invite_code(
        created_by=user["id"],
        max_uses=req.max_uses,
        note=req.note,
        expires_in_days=req.expires_in_days if req.expires_in_days > 0 else None,
        code_value=req.code.strip() if req.code and req.code.strip() else "",
    )
    if not code:
        # 回滚 invite_quota
        conn2 = db.connect()
        cur2 = conn2.cursor()
        try:
            if db.USE_POSTGRES:
                cur2.execute("UPDATE users SET invite_quota=invite_quota+1 WHERE id=%s", (user["id"],))
            else:
                cur2.execute("UPDATE users SET invite_quota=invite_quota+1 WHERE id=?", (user["id"],))
            conn2.commit()
        finally:
            conn2.close()
        raise HTTPException(400, f"邀请码 '{req.code}' 已存在，请换个名字")
    log.info(f"[invite] uid={user['id']} 创建邀请码 {code} (max_uses={req.max_uses}, custom={bool(req.code)})")
    return {"ok": True, "code": code, "remaining_quota": user["invite_quota"] - 1}


@app.put("/api/invite/{code}")
async def api_invite_update(code: str, req: UpdateInviteReq, request: Request):
    """更新邀请码参数：max_uses / note / expires_in_days / status"""
    user = request.state.user
    existing = auth.get_invite_code(code)
    if not existing:
        raise HTTPException(404, "邀请码不存在")
    if existing["created_by"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403, "只能修改自己创建的邀请码")
    # 把 sentinel 值转成 None
    new_max = req.max_uses if req.max_uses > 0 else None
    new_note = req.note if req.note != "" else None  # 空串 = 不改；__CLEAR__ = 清空
    if req.note == "__CLEAR__":
        new_note = ""
    new_expires = req.expires_in_days if req.expires_in_days >= 0 else None
    new_status = req.status if req.status else None
    ok = auth.update_invite_code(
        code,
        max_uses=new_max,
        note=new_note,
        expires_in_days=new_expires,
        status=new_status,
    )
    if not ok:
        raise HTTPException(500, "更新失败")
    return {"ok": True, "code": code}


@app.delete("/api/invite/{code}")
async def api_invite_delete(code: str, request: Request):
    """软删除邀请码（status 改为 revoked，保留历史）"""
    user = request.state.user
    existing = auth.get_invite_code(code)
    if not existing:
        raise HTTPException(404, "邀请码不存在")
    if existing["created_by"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403, "只能删除自己创建的邀请码")
    ok = auth.delete_invite_code(code)
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True, "code": code, "status": "revoked"}


@app.get("/api/invite/my-codes")
async def api_invite_my_codes(request: Request):
    user = request.state.user
    codes = auth.list_my_invite_codes(user["id"])
    return {"ok": True, "codes": codes, "remaining_quota": user["invite_quota"]}


@app.get("/api/invite/my-invites")
async def api_invite_my_invites(request: Request):
    user = request.state.user
    invited = auth.list_users_i_invited(user["id"])
    return {"ok": True, "invited": invited, "remaining_quota": user["invite_quota"]}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level='info')