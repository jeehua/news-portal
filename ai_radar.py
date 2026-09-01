"""ai_radar_v2.py - 集成价格上下文的雷达生成

跟 v1 的区别：
1. 从 market_data 拉价格 + 背离检测
2. 把价格数据注入 prompt
3. 输出包含 price_divergence 详细字段
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger('jeeseek.radar')

RADAR_PROMPT_FILE = Path(__file__).parent / 'prompts' / 'radar_prompt.txt'
RADAR_OUTPUT_DIR = Path(os.getenv('REPORTS_DIR', '/app/reports')) / 'radar'


def _load_radar_prompt() -> Optional[str]:
    try:
        return RADAR_PROMPT_FILE.read_text(encoding='utf-8')
    except Exception as e:
        log.error(f'[jeeseek.radar] 读 prompt 失败: {e}')
        return None


def _split_system_user(prompt_text: str) -> tuple:
    marker = '# 输出格式'
    if marker in prompt_text:
        idx = prompt_text.index(marker)
        system_p = prompt_text[:idx].strip()
        user_p = prompt_text[idx:].strip()
        return system_p, user_p
    return prompt_text, ''


async def generate_radar(
    domestic: List[Dict],
    overseas: List[Dict],
    minutes: List[Dict],
    force: bool = False,
    _cache: Optional[Dict] = None,
    _call_ai_fn=None,
) -> Dict[str, Any]:
    """生成 AI 研判 JSON（集成价格上下文）"""
    from ai_analyzer import _format_news, _parse_ai_json
    from market_data import build_price_context, format_price_context_for_prompt

    cache_key = f'radar:{len(domestic)}:{len(overseas)}:{len(minutes)}:{int(time.time() // 14400)}'
    if not force and _cache is not None and cache_key in _cache:
        log.info('[jeeseek.radar] 命中缓存')
        return _cache[cache_key]

    prompt_text = _load_radar_prompt()
    if not prompt_text:
        return {'success': False, 'error': 'Radar prompt 未找到'}

    system_p, user_tpl = _split_system_user(prompt_text)
    if not user_tpl:
        return {'success': False, 'error': 'Prompt 格式错误'}

    # 1. 拉价格上下文（新增）
    log.info('[jeeseek.radar] 拉取价格上下文...')
    try:
        price_context = await asyncio.wait_for(
            build_price_context(domestic, overseas),
            timeout=30
        )
    except Exception as e:
        log.warning(f'[jeeseek.radar] 价格上下文拉取失败: {e}')
        price_context = {'data_status': 'unavailable'}

    price_text = format_price_context_for_prompt(price_context)

    # 2. 格式化新闻
    domestic_text = _format_news(domestic, limit=25)
    overseas_text = _format_news(overseas, limit=25)
    minutes_text = _format_news(minutes, limit=15)

    # 3. 填充模板
    user_p = user_tpl
    user_p = user_p.replace('{current_time}', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    user_p = user_p.replace('{domestic_count}', str(len(domestic)))
    user_p = user_p.replace('{overseas_count}', str(len(overseas)))
    user_p = user_p.replace('{minutes_count}', str(len(minutes)))
    user_p = user_p.replace('{domestic_content}', domestic_text)
    user_p = user_p.replace('{overseas_content}', overseas_text)
    user_p = user_p.replace('{minutes_content}', minutes_text)

    # 4. 附加价格上下文（在用户 prompt 末尾）
    user_p = user_p + '\n\n# 价格上下文（自动接入）\n' + price_text

    log.info(f'[jeeseek.radar] 价格状态: {price_context.get("data_status")}, 主题: {list(price_context.get("topics_with_data", {}).keys())}')

    # 5. 调 AI
    if _call_ai_fn is None:
        return {'success': False, 'error': 'AI 调用函数未注入'}

    loop = asyncio.get_event_loop()
    try:
        from ai_analyzer import AGNES_TIMEOUT
        raw = await asyncio.wait_for(
            loop.run_in_executor(None, _call_ai_fn, system_p, user_p),
            timeout=AGNES_TIMEOUT + 10
        )
    except asyncio.TimeoutError:
        log.error('[jeeseek.radar] AI 超时')
        return {'success': False, 'error': 'LLM 超时'}
    except Exception as e:
        log.error(f'[jeeseek.radar] AI 失败: {e}')
        return {'success': False, 'error': str(e)[:200]}

    # 6. 解析 JSON
    data, parse_error = _parse_ai_json(raw)
    if not data:
        return {'success': False, 'error': f'JSON 解析失败: {parse_error}', 'raw': raw[:500]}

    if 'cards' not in data:
        return {'success': False, 'error': '字段不完整', 'raw': raw[:500]}

    # 7. 注入价格数据（如果 AI 没给，自己用价格上下文补）
    if price_context.get('data_status') != 'unavailable':
        price_divs = price_context.get('divergence_detected', [])
        if price_divs and data.get('cards', {}).get('price_divergence'):
            pd_card = data['cards']['price_divergence']
            pd_card['auto_detected'] = price_divs

    ts = int(time.time())
    result = {
        'success': True,
        'data': data,
        'meta': {
            'generated_at': ts,
            'time_str': datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
            'data_stats': {
                'domestic': len(domestic),
                'overseas': len(overseas),
                'minutes': len(minutes),
            },
            'price_context': {
                'status': price_context.get('data_status'),
                'topics_with_data': list(price_context.get('topics_with_data', {}).keys()),
                'divergence_count': len([d for d in price_context.get('divergence_detected', [])
                                         if d.get('divergence_type') not in ('无背离', '数据不足')]),
            }
        }
    }

    if _cache is not None:
        _cache[cache_key] = result

    try:
        RADAR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_file = RADAR_OUTPUT_DIR / 'latest.json'
        out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        log.warning(f'[jeeseek.radar] 保存失败: {e}')

    log.info(f'[jeeseek.radar] 研判完成: price_status={price_context.get("data_status")}')
    return result


def _load_radar_from_disk() -> Optional[Dict[str, Any]]:
    try:
        out_file = RADAR_OUTPUT_DIR / 'latest.json'
        if out_file.exists():
            age = time.time() - out_file.stat().st_mtime
            if age < 86400:
                return json.loads(out_file.read_text(encoding='utf-8'))
    except Exception as e:
        log.warning(f'[jeeseek.radar] 磁盘兜底失败: {e}')
    return None
