"""行情模块 v4 - 用腾讯/新浪接口（最稳）

接口:
- K线: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh510300,day,,,5,qfq
- 实时: https://qt.gtimg.cn/q=sh510300
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger('jeeseek.market')


TOPIC_TICKERS = {
    'AI算力': {
        'etf': [('sh588200', '588200'), ('sh159819', '159819')],
        'keywords': ['AI', '算力', 'GPU', '服务器', '数据中心', '云计算']
    },
    '半导体': {
        'etf': [('sz159995', '159995'), ('sh512480', '512480')],
        'keywords': ['半导体', '芯片', '晶圆', '存储', '光刻机', '国产替代']
    },
    '新能源': {
        'etf': [('sh515030', '515030'), ('sh515790', '515790')],
        'keywords': ['新能源', '锂电池', '光伏', '储能', '宁德', '隆基']
    },
    '海外利率': {
        'etf': [('sh510300', '510300')],
        'keywords': ['美联储', '加息', '降息', '美元', '美债', 'USDCNY']
    },
    '中概互联': {
        'etf': [('sh513050', '513050'), ('sh159741', '159741')],
        'keywords': ['中概', '互联网', '腾讯', '阿里', '美团', '拼多多']
    },
    '消费': {
        'etf': [('sz159928', '159928'), ('sh512690', '512690')],
        'keywords': ['消费', '白酒', '茅台', '五粮液', '食品饮料']
    },
    '医药': {
        'etf': [('sh512010', '512010'), ('sz159992', '159992')],
        'keywords': ['医药', '创新药', 'CRO', '恒瑞', '医疗器械']
    },
}

FALLBACK_ETFS = [('sh510300', '510300'), ('sh510500', '510500')]


def _parse_kline_data(raw_data: List, code: str) -> List[Dict]:
    """解析腾讯 K线数据: [["2026-08-24","open","close","high","low","volume"], ...]"""
    quotes = []
    for row in raw_data:
        if len(row) < 6:
            continue
        quotes.append({
            'date': row[0],
            'open': float(row[1]),
            'close': float(row[2]),
            'high': float(row[3]),
            'low': float(row[4]),
            'volume': float(row[5]),
            'pct_chg': 0,  # 自己算
        })
    # 计算涨跌幅
    for i in range(1, len(quotes)):
        prev_close = quotes[i - 1]['close']
        cur_close = quotes[i]['close']
        if prev_close > 0:
            quotes[i]['pct_chg'] = (cur_close - prev_close) / prev_close * 100
    return quotes


async def _fetch_single_kline(client: httpx.AsyncClient, full_secid: str, days: int) -> Optional[List[Dict]]:
    """单个股票 K 线"""
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={full_secid},day,,,{days},qfq'
    try:
        resp = await client.get(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://gu.qq.com/',
            'Accept': '*/*',
        })
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get('code') != 0:
            return None
        # 单股票时 data 是 dict
        stock_data = data.get('data', {})
        if isinstance(stock_data, dict):
            item = stock_data.get(full_secid)
            if not item:
                return None
            klines = item.get('qfqday') or item.get('day') or []
            return _parse_kline_data(klines, full_secid) if klines else None
        return None
    except Exception as e:
        log.debug(f'{full_secid} 失败: {e}')
        return None


async def fetch_etf_quotes(codes: List[tuple], days: int = 5) -> Dict[str, List[Dict]]:
    """用腾讯接口拉 K 线（每个 ETF 单独请求,避免多股参数失效）

    codes: [(full_secid, display_code), ...]  例如 [('sh510300', '510300'), ...]
    """
    result = {}

    if not codes:
        return result

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            tasks = [_fetch_single_kline(client, full_secid, days) for full_secid, _ in codes]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            for (full_secid, display_code), resp in zip(codes, responses):
                if isinstance(resp, Exception):
                    log.warning(f'{display_code} 异常: {resp}')
                    continue
                if resp:
                    result[display_code] = resp
                    log.info(f'✓ {display_code}: {len(resp)} 条 K 线')

    except Exception as e:
        log.warning(f'腾讯K线并发失败: {e}')

    return result


def match_topic_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    text_lower = text.lower()
    for topic, info in TOPIC_TICKERS.items():
        for kw in info.get('keywords', []):
            if kw.lower() in text_lower or kw in text:
                return topic
    return None


def detect_divergence(quotes: List[Dict], news_direction: str) -> Dict[str, Any]:
    if not quotes or len(quotes) < 2:
        return {
            'divergence_type': '数据不足',
            'news_direction': news_direction,
            'price_reaction': '—',
            'volume_signal': '—',
            'ai_status': 'incomplete',
            'ai_status_msg': '行情数据不足,无法对比',
        }

    recent = quotes[-1]
    prev = quotes[-2]

    pct_chg = recent.get('pct_chg', 0) or 0
    volume_recent = recent.get('volume', 0) or 0
    volume_prev = prev.get('volume', 0) or 0

    if abs(pct_chg) >= 1.5:
        price_reaction = '强'
    elif abs(pct_chg) >= 0.5:
        price_reaction = '中'
    else:
        price_reaction = '弱'

    volume_signal = '—'
    if volume_prev > 0:
        vol_ratio = volume_recent / volume_prev
        if vol_ratio >= 1.3:
            volume_signal = '放量'
        elif vol_ratio <= 0.7:
            volume_signal = '缩量'
        else:
            volume_signal = '持平'

    divergence_type = '无背离'
    if news_direction == '正面' and pct_chg <= 0.3:
        divergence_type = '利好钝化'
    elif news_direction == '负面' and pct_chg >= -0.3:
        divergence_type = '利空钝化'
    elif news_direction == '正面' and pct_chg >= 1.5 and volume_signal == '放量':
        divergence_type = '无背离'
    elif news_direction == '中性' and pct_chg >= 1.0:
        divergence_type = '资金先行'

    return {
        'divergence_type': divergence_type,
        'news_direction': news_direction,
        'price_reaction': price_reaction,
        'volume_signal': volume_signal,
        'recent_pct_chg': round(pct_chg, 2),
        'ai_status': 'complete',
        'ai_status_msg': '已纳入对比',
    }


def _classify_news_direction(titles: List[str]) -> str:
    pos_words = ['利好', '上涨', '增长', '突破', '新高', '上调', '改善', '受益', '订单', '超预期']
    neg_words = ['利空', '下跌', '下滑', '下调', '恶化', '亏损', '减少', '降级', '暴跌']

    pos, neg = 0, 0
    for t in titles:
        if any(w in t for w in pos_words):
            pos += 1
        if any(w in t for w in neg_words):
            neg += 1

    if pos > neg:
        return '正面'
    if neg > pos:
        return '负面'
    return '中性'


async def build_price_context(domestic_news: List[Dict], overseas_news: List[Dict]) -> Dict[str, Any]:
    all_news = list(domestic_news) + list(overseas_news)

    active_topics = set()
    for item in all_news[:50]:
        title = item.get('title', '')
        topic = match_topic_from_text(title)
        if topic:
            active_topics.add(topic)

    log.info(f'[market] 活跃主题: {active_topics}')

    # 收集要拉的 ETF
    codes_to_fetch = list(FALLBACK_ETFS)
    topic_code_map = {}
    for topic in active_topics:
        info = TOPIC_TICKERS.get(topic)
        if not info:
            continue
        for full_secid, code in info.get('etf', []):
            codes_to_fetch.append((full_secid, code))
            topic_code_map.setdefault(topic, []).append((full_secid, code))

    # 去重保序
    seen = set()
    unique_codes = []
    for item in codes_to_fetch:
        if item[1] not in seen:
            seen.add(item[1])
            unique_codes.append(item)

    quotes_map = await fetch_etf_quotes(unique_codes, days=5)

    topics_with_data = {}
    for topic, code_pairs in topic_code_map.items():
        for full_secid, code in code_pairs:
            if code in quotes_map and quotes_map[code]:
                data = quotes_map[code]
                recent_pct = data[-1].get('pct_chg', 0) if data else 0
                volume_signal = (
                    '放量' if len(data) >= 2 and data[-1].get('volume', 0) > data[-2].get('volume', 0) * 1.2 else
                    '缩量' if len(data) >= 2 and data[-1].get('volume', 0) < data[-2].get('volume', 0) * 0.8 else
                    '持平'
                )
                topics_with_data[topic] = {
                    'code': code,
                    'quotes': data,
                    'recent_pct_chg': round(recent_pct, 2),
                    'volume_signal': volume_signal,
                    'source': 'tencent_ifzq',
                }
                break

    divergence_detected = []
    for topic, tdata in topics_with_data.items():
        topic_news = [n for n in all_news if match_topic_from_text(n.get('title', '')) == topic]
        if not topic_news:
            continue
        direction = _classify_news_direction([n.get('title', '') for n in topic_news[:5]])
        div = detect_divergence(tdata['quotes'], direction)
        div['topic'] = topic
        div['etf_code'] = tdata['code']
        divergence_detected.append(div)

    data_status = 'complete' if topics_with_data else 'unavailable'

    return {
        'topics_with_data': topics_with_data,
        'index_summary': {},
        'divergence_detected': divergence_detected,
        'data_status': data_status,
        'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def format_price_context_for_prompt(context: Dict[str, Any]) -> str:
    lines = []

    data_status = context.get('data_status', 'unavailable')
    if data_status == 'unavailable':
        return '\n## 价格行情数据\n当前价格数据不可用,请基于纯新闻信息分析,价格确认字段标记为"待验证"。\n'

    topics = context.get('topics_with_data', {})
    if topics:
        lines.append('\n## 主题相关 ETF 行情(最近 5 日,数据源:腾讯财经)')
        for topic, tdata in topics.items():
            recent_close = tdata['quotes'][-1]['close']
            lines.append(
                f"- {topic} (ETF {tdata['code']}): "
                f"最新收盘 {recent_close:.3f}, 涨跌 {tdata['recent_pct_chg']}%, 成交量 {tdata['volume_signal']}"
            )

    divs = context.get('divergence_detected', [])
    real_divs = [d for d in divs if d.get('divergence_type') in
                 ('利好钝化', '利空钝化', '资金先行', '资金背离')]
    if real_divs:
        lines.append('\n## 自动检测到的新闻-价格背离')
        for d in real_divs:
            lines.append(
                f"- {d['topic']}: {d['divergence_type']} | "
                f"新闻方向 {d['news_direction']} | "
                f"价格反应 {d['price_reaction']} | "
                f"成交量 {d['volume_signal']} | "
                f"最新涨跌 {d.get('recent_pct_chg', 0)}%"
            )

    return '\n'.join(lines) + '\n'
