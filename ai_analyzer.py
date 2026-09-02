#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知几察微 AI 热点分析 v4
- 支持 v1 单文档长文分析（core_trends 等 6 板块）
- 支持 v3 批量统计聚合（track_rankings 等 5 板块）
- 自动检测 JSON 字段决定渲染路径
- 用 openai SDK 直接调 agnes-2.0-flash
- JSON 解析兜底：标准 json → json_repair
"""
import os
import json
import re
import time
import logging
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict

import openai

log = logging.getLogger('jeeseek.ai')

# === 配置 ===
def _load_agnes_key():
    key = os.getenv('AGNES_API_KEY')
    if key:
        return key
    for path in ['/home/ubuntu/trendradar/config/ai_key.txt', '/var/www/jeeseek/api/.agnes_key']:
        if Path(path).exists():
            return Path(path).read_text().strip()
    return ''

AGNES_API_KEY = _load_agnes_key()
AGNES_BASE = os.getenv('AGNES_API_BASE', 'https://apihub.agnes-ai.com/v1')
AGNES_MODEL = os.getenv('AGNES_MODEL', 'agnes-2.0-flash')
AGNES_TIMEOUT = int(os.getenv('AGNES_TIMEOUT', '180'))
AGNES_MAX_TOKENS = int(os.getenv('AGNES_MAX_TOKENS', '12000'))

# === Prompt 路径（v3 为主，v1 fallback） ===
PROMPT_FILES = [
    Path(__file__).parent / 'ai_hotspot_prompt_v3.txt',  # 豆包版（首选）
    Path(__file__).parent / 'ai_hotspot_prompt.txt',     # v1 兜底
]

# v4：行业深度报告模式（按子赛道切分，类似花总示例）
DEPTH_PROMPT_FILES = [
    Path(__file__).parent / 'ai_depth_prompt_v4.txt',  # 行业深度（首选）
]

# === 报告输出目录（从环境变量获取，默认 /app/reports 兼容 Fly.io/Koyeb）===
REPORTS_DIR = Path(os.getenv('REPORTS_DIR', '/app/reports'))
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# === 缓存 ===
_CACHE: Dict[str, Any] = {}


# ──────────────────────────────
# 数据类 v1 (兼容旧版)
# ──────────────────────────────
@dataclass
class AIAnalysisResult:
    """v1: 单文档长文分析 6 板块"""
    core_trends: str = ""
    sentiment_controversy: str = ""
    signals: str = ""
    rss_insights: str = ""
    outlook_strategy: str = ""
    standalone_summaries: Dict[str, str] = field(default_factory=dict)

    raw_response: str = ""
    success: bool = False
    error: str = ""
    total_news: int = 0
    domestic_count: int = 0
    overseas_count: int = 0
    minutes_count: int = 0
    model: str = ""
    time_str: str = ""
    version: str = "v1"

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────
# 数据类 v3 (豆包版)：批次聚合 + 4 类信号
# ──────────────────────────────
@dataclass
class TrackRanking:
    """TOP 赛道热度排行"""
    track: str = ""
    frequency: int = 0
    trend_change: str = ""
    bull_ratio: str = ""
    validation_ratio: str = ""
    disagreement_level: str = ""
    phase: str = ""
    core_logic_summary: str = ""


@dataclass
class SentimentStats:
    """全市场态度统计"""
    bull_count: int = 0
    neutral_count: int = 0
    cautious_count: int = 0
    bull_ratio: str = ""
    environment: str = ""
    rationale: str = ""


@dataclass
class DisagreementTopic:
    """TOP 分歧议题"""
    topic: str = ""
    consensus: str = ""
    minority: str = ""
    core_disagreement: str = ""


@dataclass
class BatchAnalysisResult:
    """v3: 批次聚合分析 + 4 类信号"""
    track_rankings: List[TrackRanking] = field(default_factory=list)
    sentiment_stats: SentimentStats = field(default_factory=SentimentStats)
    catalyst_new: List[str] = field(default_factory=list)
    catalyst_aging: List[str] = field(default_factory=list)
    disagreement_topics: List[DisagreementTopic] = field(default_factory=list)
    signals: Dict[str, str] = field(default_factory=dict)

    raw_response: str = ""
    success: bool = False
    error: str = ""
    total_news: int = 0
    domestic_count: int = 0
    overseas_count: int = 0
    minutes_count: int = 0
    model: str = ""
    time_str: str = ""
    version: str = "v3"

    def to_dict(self) -> dict:
        return {
            'track_rankings': [asdict(t) for t in self.track_rankings],
            'sentiment_stats': asdict(self.sentiment_stats),
            'catalyst_new': self.catalyst_new,
            'catalyst_aging': self.catalyst_aging,
            'disagreement_topics': [asdict(t) for t in self.disagreement_topics],
            'signals': self.signals,
            'raw_response': self.raw_response,
            'success': self.success,
            'error': self.error,
            'total_news': self.total_news,
            'domestic_count': self.domestic_count,
            'overseas_count': self.overseas_count,
            'minutes_count': self.minutes_count,
            'model': self.model,
            'time_str': self.time_str,
            'version': self.version,
        }


# ──────────────────────────────
# 数据类 v4：行业深度报告（子赛道切分）
# ──────────────────────────────
@dataclass
class SectorReport:
    """v4: 子赛道报告"""
    name: str = ""
    tagline: str = ""
    highlights: List[str] = field(default_factory=list)
    key_data: List[str] = field(default_factory=list)
    related_companies: List[str] = field(default_factory=list)
    catalyst_risk: str = ""


@dataclass
class DepthReport:
    """v4: 行业深度报告 - 按子赛道切分"""
    title: str = ""
    sectors: List[SectorReport] = field(default_factory=list)
    main_lines: List[str] = field(default_factory=list)
    catalysts_new: List[str] = field(default_factory=list)
    catalysts_cooling: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)

    raw_response: str = ""
    success: bool = False
    error: str = ""
    total_news: int = 0
    domestic_count: int = 0
    overseas_count: int = 0
    minutes_count: int = 0
    model: str = ""
    time_str: str = ""
    version: str = "v4"

    def to_dict(self) -> dict:
        return {
            'title': self.title,
            'sectors': [asdict(s) for s in self.sectors],
            'main_lines': self.main_lines,
            'catalysts_new': self.catalysts_new,
            'catalysts_cooling': self.catalysts_cooling,
            'risks': self.risks,
            'raw_response': self.raw_response,
            'success': self.success,
            'error': self.error,
            'total_news': self.total_news,
            'domestic_count': self.domestic_count,
            'overseas_count': self.overseas_count,
            'minutes_count': self.minutes_count,
            'model': self.model,
            'time_str': self.time_str,
            'version': self.version,
        }


# ──────────────────────────────
# Prompt 加载与拆分
# ──────────────────────────────
def _load_prompt() -> str:
    for f in PROMPT_FILES:
        if f.exists():
            log.info(f'[jeeseek.ai] 使用 prompt: {f.name}')
            return f.read_text(encoding='utf-8')
    log.error(f'[jeeseek.ai] 所有 prompt 都不存在')
    return ''


def _split_system_user(prompt_text: str) -> tuple:
    """按 [system]/[user] 切分 prompt"""
    system_part = ''
    user_part = ''
    current = None
    for line in prompt_text.split('\n'):
        if line.strip() == '[system]':
            current = 'sys'
            continue
        elif line.strip() == '[user]':
            current = 'user'
            continue
        if current == 'sys':
            system_part += line + '\n'
        elif current == 'user':
            user_part += line + '\n'
    return system_part.strip(), user_part.strip()


# ──────────────────────────────
# 格式化新闻列表
# ──────────────────────────────
def _format_news(news_list: List[Dict], limit: int = 50) -> str:
    lines = []
    for i, x in enumerate(news_list[:limit], 1):
        time_str = x.get('time', '')[:16]
        source = x.get('source_name') or x.get('source_id', '?')
        title = x.get('title', '').replace('\n', ' ')[:80]
        lines.append(f'{i:3d}. [{time_str}] [{source}] {title}')
    return '\n'.join(lines) if lines else '（无数据）'


# ──────────────────────────────
# Agnes API 调用
# ──────────────────────────────
def _call_agnes(system: str, user: str) -> str:
    client = openai.OpenAI(
        api_key=AGNES_API_KEY,
        base_url=AGNES_BASE,
        timeout=AGNES_TIMEOUT,
    )
    resp = client.chat.completions.create(
        model=AGNES_MODEL,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        max_tokens=AGNES_MAX_TOKENS,
        temperature=0.5,
    )
    return resp.choices[0].message.content or ''


# ──────────────────────────────
# JSON 解析（标准 + json_repair 兜底）
# ──────────────────────────────
def _parse_ai_json(raw: str) -> tuple:
    if not raw:
        return {}, '空响应'
    json_str = raw
    if '```json' in raw:
        try:
            parts = raw.split('```json', 1)
            json_str = parts[1].split('```', 1)[0].strip()
        except Exception:
            pass
    elif '```' in raw:
        try:
            json_str = raw.split('```', 1)[1].split('```', 1)[0].strip()
        except Exception:
            pass
    if '{' in json_str and json_str.find('{') < json_str.rfind('}'):
        first = json_str.find('{')
        last = json_str.rfind('}')
        json_str = json_str[first:last+1]
    json_str = json_str.strip()
    if not json_str:
        return {}, '提取的 JSON 内容为空'
    data = None
    parse_error = None
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        parse_error = e
    if data is None:
        try:
            from json_repair import repair_json
            repaired = repair_json(json_str, return_objects=True)
            if isinstance(repaired, dict):
                data = repaired
                log.info('[jeeseek.ai] json_repair 修复成功')
        except ImportError:
            log.warning('[jeeseek.ai] json_repair 未安装')
        except Exception as e:
            log.warning(f'[jeeseek.ai] json_repair 失败: {e}')
    if data is None:
        err_msg = f'JSON 解析失败: {parse_error.msg if parse_error else "未知"}'
        return {}, err_msg
    return data, ''


# ──────────────────────────────
# v3: 字段映射
# ──────────────────────────────
def _to_batch_result(data: dict, raw: str, stats: dict, model: str, ts: int) -> BatchAnalysisResult:
    result = BatchAnalysisResult(
        raw_response=raw,
        success=True,
        model=model,
        time_str=datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
        total_news=stats.get('domestic', 0) + stats.get('overseas', 0) + stats.get('minutes', 0),
        domestic_count=stats.get('domestic', 0),
        overseas_count=stats.get('overseas', 0),
        minutes_count=stats.get('minutes', 0),
    )

    # track_rankings
    for t in data.get('track_rankings', []) or []:
        if isinstance(t, dict):
            result.track_rankings.append(TrackRanking(
                track=str(t.get('track', '')),
                frequency=int(t.get('frequency', 0) or 0),
                trend_change=str(t.get('trend_change', '')),
                bull_ratio=str(t.get('bull_ratio', '')),
                validation_ratio=str(t.get('validation_ratio', '')),
                disagreement_level=str(t.get('disagreement_level', '')),
                phase=str(t.get('phase', '')),
                core_logic_summary=str(t.get('core_logic_summary', '')),
            ))

    # sentiment_stats
    ss = data.get('sentiment_stats', {}) or {}
    if isinstance(ss, dict):
        result.sentiment_stats = SentimentStats(
            bull_count=int(ss.get('bull_count', 0) or 0),
            neutral_count=int(ss.get('neutral_count', 0) or 0),
            cautious_count=int(ss.get('cautious_count', 0) or 0),
            bull_ratio=str(ss.get('bull_ratio', '')),
            environment=str(ss.get('environment', '')),
            rationale=str(ss.get('rationale', '')),
        )

    # catalyst_ranking
    cr = data.get('catalyst_ranking', {}) or {}
    if isinstance(cr, dict):
        result.catalyst_new = [str(x) for x in (cr.get('new_catalysts', []) or [])]
        result.catalyst_aging = [str(x) for x in (cr.get('aging_catalysts', []) or [])]

    # disagreement_topics
    for d in data.get('disagreement_topics', []) or []:
        if isinstance(d, dict):
            result.disagreement_topics.append(DisagreementTopic(
                topic=str(d.get('topic', '')),
                consensus=str(d.get('consensus', '')),
                minority=str(d.get('minority', '')),
                core_disagreement=str(d.get('core_disagreement', '')),
            ))

    # signals
    sig = data.get('signals', {}) or {}
    if isinstance(sig, dict):
        result.signals = {
            'signal_1_mainline': str(sig.get('signal_1_mainline', '')),
            'signal_2_industry_change': str(sig.get('signal_2_industry_change', '')),
            'signal_3_institution_focus': str(sig.get('signal_3_institution_focus', '')),
            'signal_4_market_state': str(sig.get('signal_4_market_state', '')),
        }

    return result


# ──────────────────────────────
# v1: 字段映射
# ──────────────────────────────
def _to_v1_result(data: dict, raw: str, stats: dict, model: str, ts: int) -> AIAnalysisResult:
    result = AIAnalysisResult(
        raw_response=raw,
        success=True,
        model=model,
        time_str=datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
        total_news=stats.get('domestic', 0) + stats.get('overseas', 0) + stats.get('minutes', 0),
        domestic_count=stats.get('domestic', 0),
        overseas_count=stats.get('overseas', 0),
        minutes_count=stats.get('minutes', 0),
    )
    try:
        result.core_trends = data.get('core_trends', '').strip()
        result.sentiment_controversy = data.get('sentiment_controversy', '').strip()
        result.signals_str = data.get('signals', '').strip() if hasattr(result, 'signals_str') else data.get('signals', '').strip()
        result.rss_insights = data.get('rss_insights', '').strip()
        result.outlook_strategy = data.get('outlook_strategy', '').strip()

        summaries = data.get('standalone_summaries', {})
        if isinstance(summaries, dict):
            result.standalone_summaries = {
                str(k): str(v).strip() for k, v in summaries.items() if v
            }
    except Exception as e:
        result.error = f'字段提取错误: {e}'
    return result


# ──────────────────────────────
# 列表内容格式化
# ──────────────────────────────
def _format_list_content(text: str) -> str:
    if not text:
        return ''
    text = text.strip()
    text = re.sub(r'(\d+\.)\s*【([^】]+)】([:：]?)', r'\1 \2：', text)
    text = re.sub(r'(\d+)\.([^ \d])', r'\1. \2', text)
    text = re.sub(r'(?<=[^\n])\s+(\d+\.)(?!\d)', r'\n\1', text)
    text = re.sub(r'([：:;,。；，])\s*(\d+\.)(?!\d)', r'\1\n\2', text)
    text = re.sub(r'(?<=\S)\n*(【[^】]+】)', r'\n\n\1', text)
    text = re.sub(r'(【[^】]+】)\n+([:：])', r'\1\2', text)
    text = re.sub(r'(【[^】]+】[:：]?)[ \t]*(?=[^\s:：])', r'\1\n', text)
    return text


# ──────────────────────────────
# HTML 渲染 v3 (豆包版)：4 类信号 + 赛道排行
# ──────────────────────────────
def _render_batch_html(result: BatchAnalysisResult) -> str:
    time_str = result.time_str
    version_tag = "批次聚合 v4" if result.version == "v3" else "v1"

    def sec(title: str, content: str, icon: str = '') -> str:
        if not content:
            return ''
        formatted = _format_list_content(content)
        formatted = formatted.replace('\n\n', '</p><p>')
        formatted = formatted.replace('\n', '<br>')
        return f'''<div class="section">
  <h2>{icon} {title}</h2>
  <div class="content"><p>{formatted}</p></div>
</div>'''

    # 4 个 signals
    sections_html = '\n'.join([
        sec('信号1 · 主线/风口阶段', result.signals.get('signal_1_mainline', ''), '🎯'),
        sec('信号2 · 行业景气边际变化', result.signals.get('signal_2_industry_change', ''), '📊'),
        sec('信号3 · 机构群体关心什么', result.signals.get('signal_3_institution_focus', ''), '🔍'),
        sec('信号4 · 大盘市场状态', result.signals.get('signal_4_market_state', ''), '🌐'),
    ])

    # 赛道排行表
    if result.track_rankings:
        rows = []
        for i, t in enumerate(result.track_rankings, 1):
            phase_color = {
                '发酵初期': '#3b82f6',
                '主升中段': '#10b981',
                '景气兑现后期': '#f59e0b',
                '分歧拐点': '#ef4444',
                '下行期': '#6b7280',
            }.get(t.phase, '#6b7280')
            rows.append(f'''<tr>
                <td>{i}</td>
                <td class="track-name">{t.track}</td>
                <td><b>{t.frequency}</b></td>
                <td>{t.trend_change or '—'}</td>
                <td>{t.bull_ratio or '—'}</td>
                <td>{t.validation_ratio or '—'}</td>
                <td>{t.disagreement_level or '—'}</td>
                <td><span class="phase-tag" style="background:{phase_color}">{t.phase or '—'}</span></td>
                <td class="core-logic">{t.core_logic_summary or '—'}</td>
            </tr>''')
        rankings_html = f'''<div class="section">
  <h2>🏆 赛道热度排行榜 (TOP {len(result.track_rankings)})</h2>
  <div class="rankings">
    <table>
      <thead>
        <tr>
          <th>#</th><th>赛道/逻辑</th><th>频次</th><th>环比</th>
          <th>机构乐观占比</th><th>催化类型</th><th>分歧程度</th>
          <th>趋势定性</th><th>核心叙事</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
</div>'''

    else:
        rankings_html = ''

    # 态度统计卡片
    ss = result.sentiment_stats
    if ss.bull_count or ss.cautious_count or ss.neutral_count:
        sentiment_card = f'''<div class="stats-row">
  <div class="stat-card stat-bull">
    <div class="stat-label">乐观</div>
    <div class="stat-value">{ss.bull_count}</div>
  </div>
  <div class="stat-card stat-neutral">
    <div class="stat-label">中性</div>
    <div class="stat-value">{ss.neutral_count}</div>
  </div>
  <div class="stat-card stat-cautious">
    <div class="stat-label">谨慎</div>
    <div class="stat-value">{ss.cautious_count}</div>
  </div>
  <div class="stat-card stat-env">
    <div class="stat-label">机构乐观占比</div>
    <div class="stat-value">{ss.bull_ratio or '—'}</div>
    <div class="stat-sub">{ss.environment or '—'}</div>
  </div>
</div>'''
    else:
        sentiment_card = ''

    # 催化清单
    catalyst_html = ''
    if result.catalyst_new or result.catalyst_aging:
        new_items = ''.join(f'<li>{c}</li>' for c in result.catalyst_new)
        aging_items = ''.join(f'<li>{c}</li>' for c in result.catalyst_aging)
        catalyst_html = f'''<div class="section">
  <h2>🔥 催化事件清单</h2>
  <div class="catalyst-grid">
    <div class="catalyst-col">
      <h3>新增催化（持续发酵）</h3>
      <ul>{new_items or '<li class="empty">暂无</li>'}</ul>
    </div>
    <div class="catalyst-col">
      <h3>老化催化（反复复读）</h3>
      <ul>{aging_items or '<li class="empty">暂无</li>'}</ul>
    </div>
  </div>
</div>'''

    # 分歧议题
    disagreement_html = ''
    if result.disagreement_topics:
        items = []
        for d in result.disagreement_topics:
            items.append(f'''<div class="disagreement-item">
    <div class="disagreement-topic">{d.topic}</div>
    <div class="disagreement-content">
      <div class="consensus"><b>共识方：</b>{d.consensus or '—'}</div>
      <div class="minority"><b>少数派：</b>{d.minority or '—'}</div>
      <div class="core"><b>核心分歧：</b>{d.core_disagreement or '—'}</div>
    </div>
  </div>''')
        disagreement_html = f'''<div class="section">
  <h2>⚔️ 机构分歧议题（TOP {len(result.disagreement_topics)}）</h2>
  <div class="disagreement-list">{''.join(items)}</div>
</div>'''

    stats_line = f'国内: {result.domestic_count}条 | 海外: {result.overseas_count}条 | 纪要: {result.minutes_count}条 | 总计: {result.total_news}条'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 热点分析 - 知几察微</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
    background: #f8fafc;
    color: #1f2937;
    line-height: 1.6;
    padding: 20px;
}}
.container {{ max-width: 1100px; margin: 0 auto; }}
.header {{
    background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%);
    color: white;
    padding: 24px 28px;
    border-radius: 12px;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2);
}}
.header h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 8px; }}
.header .meta {{ font-size: 13px; opacity: 0.9; }}
.header .badge {{ display: inline-block; background: rgba(255,255,255,0.2); padding: 2px 10px; border-radius: 4px; font-size: 12px; margin-left: 8px; }}
.stats {{ background: #eff6ff; color: #1e40af; padding: 12px 20px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; }}
.section {{
    background: white;
    border-radius: 10px;
    margin-bottom: 16px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}}
.section h2 {{
    background: linear-gradient(90deg, #eff6ff 0%, #ffffff 100%);
    padding: 14px 20px;
    font-size: 16px;
    color: #1e40af;
    border-left: 4px solid #2563eb;
    font-weight: 600;
}}
.section .content {{ padding: 18px 22px; color: #374151; }}
.section .content p {{ margin-bottom: 8px; }}
.section .content p:last-child {{ margin-bottom: 0; }}
.stats-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }}
.stat-card {{ background: white; padding: 18px; border-radius: 10px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
.stat-label {{ color: #6b7280; font-size: 13px; margin-bottom: 6px; }}
.stat-value {{ font-size: 28px; font-weight: 700; }}
.stat-bull .stat-value {{ color: #10b981; }}
.stat-neutral .stat-value {{ color: #6b7280; }}
.stat-cautious .stat-value {{ color: #ef4444; }}
.stat-env .stat-value {{ font-size: 22px; color: #2563eb; }}
.stat-sub {{ color: #6b7280; font-size: 12px; margin-top: 4px; }}
.rankings {{ padding: 0 22px 22px; overflow-x: auto; }}
.rankings table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
.rankings th {{ background: #f1f5f9; padding: 10px; text-align: left; color: #475569; font-weight: 600; }}
.rankings td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
.rankings .track-name {{ font-weight: 600; color: #1f2937; }}
.rankings .core-logic {{ color: #6b7280; font-size: 13px; max-width: 280px; }}
.phase-tag {{ color: white; padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
.catalyst-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; padding: 18px 22px; }}
.catalyst-col h3 {{ font-size: 14px; color: #475569; margin-bottom: 10px; }}
.catalyst-col ul {{ list-style: none; padding: 0; }}
.catalyst-col li {{ padding: 6px 10px; margin-bottom: 4px; background: #f8fafc; border-radius: 4px; font-size: 13px; }}
.catalyst-col li.empty {{ color: #9ca3af; text-align: center; }}
.disagreement-list {{ padding: 18px 22px; }}
.disagreement-item {{ padding: 14px; margin-bottom: 10px; background: #f8fafc; border-radius: 8px; border-left: 3px solid #f59e0b; }}
.disagreement-topic {{ font-weight: 600; color: #1f2937; margin-bottom: 8px; font-size: 15px; }}
.disagreement-content > div {{ margin-bottom: 4px; font-size: 13px; color: #374151; }}
.disagreement-content .core {{ color: #b45309; }}
footer {{ text-align: center; color: #94a3b8; font-size: 12px; margin-top: 30px; padding: 16px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🤖 AI 热点分析报告<span class="badge">{version_tag}</span></h1>
    <div class="meta">生成时间: {time_str}</div>
  </div>
  <div class="stats">📊 {stats_line}</div>
  {sentiment_card}
  {sections_html}
  {rankings_html}
  {catalyst_html}
  {disagreement_html}
  <footer>知算子 · jeeseek.top · AI 热点分析</footer>
</div>
</body>
</html>'''

    return html


# ──────────────────────────────
# HTML 渲染 v1 (兼容旧版)
# ──────────────────────────────
def _render_v1_html(result: AIAnalysisResult) -> str:
    time_str = result.time_str

    def section(title: str, content: str, icon: str = '') -> str:
        if not content:
            return ''
        formatted = _format_list_content(content)
        formatted = formatted.replace('\n\n', '</p><p>')
        formatted = formatted.replace('\n', '<br>')
        return f'''<div class="section">
  <h2>{icon} {title}</h2>
  <div class="content"><p>{formatted}</p></div>
</div>'''

    sections_html = '\n'.join([
        section('核心热点态势', result.core_trends, '🎯'),
        section('舆论风向争议', result.sentiment_controversy, '⚔️'),
        section('异动与弱信号', result.signals, '📡'),
        section('海外深度洞察', result.rss_insights, '🌍'),
        section('研判策略建议', result.outlook_strategy, '💡'),
    ])

    standalone_html = ''
    if result.standalone_summaries:
        items = []
        for name, summary in result.standalone_summaries.items():
            items.append(f'<div class="standalone-item"><span class="standalone-name">[{name}]</span><span class="standalone-text">{summary}</span></div>')
        standalone_html = f'''<div class="section">
  <h2>📰 独立源点速览</h2>
  <div class="standalone">{''.join(items)}</div>
</div>'''

    stats_line = f'国内: {result.domestic_count}条 | 海外: {result.overseas_count}条 | 纪要: {result.minutes_count}条 | 总计: {result.total_news}条'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 热点分析 - 知几察微</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; background: #f8fafc; color: #1f2937; line-height: 1.6; padding: 20px; }}
.container {{ max-width: 900px; margin: 0 auto; }}
.header {{ background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%); color: white; padding: 24px 28px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2); }}
.header h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 8px; }}
.header .meta {{ font-size: 13px; opacity: 0.9; }}
.section {{ background: white; border-radius: 10px; margin-bottom: 16px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
.section h2 {{ background: linear-gradient(90deg, #eff6ff 0%, #ffffff 100%); padding: 14px 20px; font-size: 16px; color: #1e40af; border-left: 4px solid #2563eb; font-weight: 600; }}
.section .content {{ padding: 18px 22px; color: #374151; }}
.section .content p {{ margin-bottom: 8px; }}
.section .content p:last-child {{ margin-bottom: 0; }}
.standalone {{ padding: 18px 22px; }}
.standalone-item {{ display: flex; align-items: flex-start; padding: 8px 0; border-bottom: 1px solid #f1f5f9; gap: 10px; }}
.standalone-item:last-child {{ border-bottom: none; }}
.standalone-name {{ flex-shrink: 0; background: #eef2ff; color: #4338ca; padding: 2px 10px; border-radius: 4px; font-size: 13px; font-weight: 600; }}
.standalone-text {{ font-size: 14px; color: #374151; line-height: 1.6; }}
footer {{ text-align: center; color: #94a3b8; font-size: 12px; margin-top: 30px; padding: 16px; }}
.stats {{ background: #eff6ff; color: #1e40af; padding: 12px 20px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🤖 AI 热点分析报告</h1>
    <div class="meta">生成时间: {time_str}</div>
  </div>
  <div class="stats">📊 {stats_line}</div>
  {sections_html}
  {standalone_html}
  <footer>知几察微 · jeeseek.top · AI 热点分析</footer>
</div>
</body>
</html>'''

    return html


# ──────────────────────────────
# v4: 行业深度报告渲染（按子赛道切分）
# ──────────────────────────────
def _to_depth_result(data: dict, raw: str, stats: dict, model: str, ts: int) -> DepthReport:
    result = DepthReport(
        raw_response=raw,
        success=True,
        model=model,
        time_str=datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
        total_news=stats.get('domestic', 0) + stats.get('overseas', 0) + stats.get('minutes', 0),
        domestic_count=stats.get('domestic', 0),
        overseas_count=stats.get('overseas', 0),
        minutes_count=stats.get('minutes', 0),
    )
    result.title = str(data.get('title', '产业链日报'))

    # sectors
    for s in data.get('sectors', []) or []:
        if isinstance(s, dict):
            result.sectors.append(SectorReport(
                name=str(s.get('name', '')),
                tagline=str(s.get('tagline', '')),
                highlights=[str(x) for x in (s.get('highlights', []) or []) if x],
                key_data=[str(x) for x in (s.get('key_data', []) or []) if x],
                related_companies=[str(x) for x in (s.get('related_companies', []) or []) if x],
                catalyst_risk=str(s.get('catalyst_risk', '')),
            ))

    # overall
    overall = data.get('overall', {}) or {}
    result.main_lines = [str(x) for x in (overall.get('main_lines', []) or []) if x]
    catalysts = overall.get('catalysts', {}) or {}
    result.catalysts_new = [str(x) for x in (catalysts.get('new', []) or []) if x]
    result.catalysts_cooling = [str(x) for x in (catalysts.get('cooling', []) or []) if x]
    result.risks = [str(x) for x in (overall.get('risks', []) or []) if x]
    return result


def _render_depth_html(result: DepthReport) -> str:
    """v4: 按子赛道渲染 HTML（类似机构日报）"""
    title = result.title or '产业链日报'

    # 象赛渲染
    sectors_html_parts = []
    for i, s in enumerate(result.sectors, 1):
        highlights_html = ''.join(f'<li>{h}</li>' for h in s.highlights) if s.highlights else '<li>—</li>'
        data_html = ''.join(f'<li>{d}</li>' for d in s.key_data) if s.key_data else '<li>—</li>'
        companies_html = ''.join(f'<li>{c}</li>' for c in s.related_companies) if s.related_companies else '<li>—</li>'
        tagline_html = f' <span style="font-size:13px;color:#64748b;font-weight:400;">· {s.tagline}</span>' if s.tagline else ''

        sectors_html_parts.append(f'''
<div class="sector">
  <div class="sector-header">
    <span class="sector-num">{i}</span>
    <span class="sector-name">{s.name}</span>{tagline_html}
  </div>
  <div class="sector-body">
    <div class="sector-block">
      <div class="block-label">【核心要闻】</div>
      <ol class="block-list">{highlights_html}</ol>
    </div>
    <div class="sector-block">
      <div class="block-label">【关键数据】</div>
      <ul class="block-list">{data_html}</ul>
    </div>
    <div class="sector-block">
      <div class="block-label">【相关公司】</div>
      <ul class="block-list companies">{companies_html}</ul>
    </div>
    <div class="sector-block catalyst-risk">
      <div class="block-label">【催化/风险】</div>
      <div class="block-text">{s.catalyst_risk or '—'}</div>
    </div>
  </div>
</div>''')

    # overall 渲染
    main_lines_html = ''.join(f'<li>{x}</li>' for x in result.main_lines) if result.main_lines else '<li>—</li>'
    catalysts_new_html = ''.join(f'<li>{x}</li>' for x in result.catalysts_new) if result.catalysts_new else '<li>—</li>'
    catalysts_cooling_html = ''.join(f'<li>{x}</li>' for x in result.catalysts_cooling) if result.catalysts_cooling else '<li>—</li>'
    risks_html = ''.join(f'<li>{x}</li>' for x in result.risks) if result.risks else '<li>—</li>'

    stats_line = f'国内: {result.domestic_count}条 | 海外: {result.overseas_count}条 | 纪要: {result.minutes_count}条 | 总计: {result.total_news}条'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - 知几察微</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; background: #f8fafc; color: #1f2937; line-height: 1.7; padding: 20px; }}
.container {{ max-width: 980px; margin: 0 auto; }}
.header {{ background: linear-gradient(135deg, #059669 0%, #047857 100%); color: white; padding: 28px 32px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(5, 150, 105, 0.2); }}
.header h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 8px; }}
.header .meta {{ font-size: 13px; opacity: 0.9; }}
.stats {{ background: #ecfdf5; color: #047857; padding: 12px 20px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; }}
.sector {{ background: white; border-radius: 10px; margin-bottom: 16px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); border-left: 4px solid #059669; }}
.sector-header {{ background: linear-gradient(90deg, #ecfdf5 0%, #ffffff 100%); padding: 14px 20px; font-size: 17px; color: #047857; font-weight: 600; border-bottom: 1px solid #f1f5f9; display: flex; align-items: center; gap: 10px; }}
.sector-num {{ background: #059669; color: white; width: 28px; height: 28px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 700; }}
.sector-body {{ padding: 16px 22px; }}
.sector-block {{ margin-bottom: 14px; }}
.sector-block:last-child {{ margin-bottom: 0; }}
.block-label {{ font-size: 13px; color: #047857; font-weight: 600; margin-bottom: 6px; }}
.block-list {{ margin-left: 0; padding-left: 22px; color: #374151; font-size: 14px; }}
.block-list li {{ margin-bottom: 4px; }}
.companies {{ list-style: none; padding-left: 0; display: flex; flex-wrap: wrap; gap: 6px; }}
.companies li {{ background: #f0fdf4; color: #047857; padding: 3px 10px; border-radius: 12px; font-size: 13px; margin-bottom: 0; }}
.catalyst-risk .block-text {{ background: #fefce8; padding: 8px 12px; border-radius: 6px; color: #713f12; font-size: 13px; line-height: 1.6; }}
.overall {{ background: #1e293b; color: #e2e8f0; padding: 24px 28px; border-radius: 10px; margin-bottom: 16px; }}
.overall h2 {{ font-size: 18px; color: #fbbf24; margin-bottom: 14px; font-weight: 700; }}
.overall-block {{ margin-bottom: 16px; }}
.overall-block:last-child {{ margin-bottom: 0; }}
.overall-block .label {{ color: #fbbf24; font-size: 14px; font-weight: 600; margin-bottom: 6px; }}
.overall-block ol, .overall-block ul {{ padding-left: 22px; font-size: 13px; color: #cbd5e1; }}
.overall-block li {{ margin-bottom: 3px; }}
footer {{ text-align: center; color: #94a3b8; font-size: 12px; margin-top: 30px; padding: 16px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📊 {title}</h1>
    <div class="meta">生成时间: {result.time_str} | 模型: {result.model}</div>
  </div>
  <div class="stats">📊 {stats_line}</div>
  {''.join(sectors_html_parts)}
  <div class="overall">
    <h2>🎯 当周总结</h2>
    <div class="overall-block">
      <div class="label">【主线】</div>
      <ol>{main_lines_html}</ol>
    </div>
    <div class="overall-block">
      <div class="label">【新增催化】</div>
      <ol>{catalysts_new_html}</ol>
    </div>
    <div class="overall-block">
      <div class="label">【冷却话题】</div>
      <ul>{catalysts_cooling_html}</ul>
    </div>
    <div class="overall-block">
      <div class="label">【风险提示】</div>
      <ol>{risks_html}</ol>
    </div>
  </div>
  <footer>知几察微 · jeeseek.top · v4 行业深度报告</footer>
</div>
</body>
</html>'''

    return html


# ──────────────────────────────
# 主入口：生成报告（自动选择 v1 或 v3 路径）
# ──────────────────────────────
async def generate_report(
    domestic: List[Dict],
    overseas: List[Dict],
    minutes: List[Dict],
    force: bool = False,
    mode: str = 'macro'  # 'macro' = v3 宏观信号 (默认) | 'depth' = v4 行业深度
) -> Dict[str, Any]:
    """生成 AI 热点分析报告
    mode:
      - 'macro' (默认)：v3 宏观信号模式，TOP赛道 + 4 类信号
      - 'depth'：v4 行业深度模式，按子赛道切分
    """
    # 不同模式独立缓存
    cache_key = f'report:{mode}:{len(domestic)}:{len(overseas)}:{len(minutes)}:{int(time.time() // 14400)}'
    if not force and cache_key in _CACHE:
        log.info(f'[jeeseek.ai] 命中缓存 (mode={mode})')
        return _CACHE[cache_key]

    # 选择 prompt 文件
    if mode == 'depth':
        prompt_text = None
        for f in DEPTH_PROMPT_FILES:
            if f.exists():
                prompt_text = f.read_text(encoding='utf-8')
                log.info(f'[jeeseek.ai] 使用 depth prompt: {f.name}')
                break
        if not prompt_text:
            prompt_text = _load_prompt()
    else:
        prompt_text = _load_prompt()
    if not prompt_text:
        return {'success': False, 'error': 'Prompt 文件未找到'}

    system_p, user_tpl = _split_system_user(prompt_text)

    # depth 模式增加纪要额度（重点是机构调研内容）
    dom_limit = 40 if mode == 'macro' else 30
    os_limit = 40 if mode == 'macro' else 30
    min_limit = 20 if mode == 'macro' else 50  # depth 优先消化纪要

    # 格式化数据
    domestic_text = _format_news(domestic, limit=dom_limit)
    overseas_text = _format_news(overseas, limit=os_limit)
    minutes_text = _format_news(minutes, limit=min_limit)
    domestic_platforms = list({x.get('source_id', '') for x in domestic if x.get('source_id')})

    # 填充模板
    user_p = user_tpl
    user_p = user_p.replace('{current_time}', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    user_p = user_p.replace('{domestic_count}', str(len(domestic)))
    user_p = user_p.replace('{domestic_platforms}', ', '.join(domestic_platforms[:8]) or '—')
    user_p = user_p.replace('{overseas_count}', str(len(overseas)))
    user_p = user_p.replace('{minutes_count}', str(len(minutes)))
    user_p = user_p.replace('{domestic_content}', domestic_text)
    user_p = user_p.replace('{overseas_content}', overseas_text)
    user_p = user_p.replace('{minutes_content}', minutes_text)

    log.info(f'[jeeseek.ai] 生成报告 (mode={mode}): 国内{len(domestic)} 海外{len(overseas)} 纪要{len(minutes)}')

    # 调用 AI（depth 模式增加超时，因为 token 更多）
    loop = asyncio.get_event_loop()
    timeout = AGNES_TIMEOUT + (30 if mode == 'depth' else 10)
    try:
        raw = await asyncio.wait_for(
            loop.run_in_executor(None, _call_agnes, system_p, user_p),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        log.error(f'[jeeseek.ai] 调用超时 (mode={mode})')
        return {'success': False, 'error': 'LLM 超时'}
    except Exception as e:
        log.error(f'[jeeseek.ai] 调用失败 (mode={mode}): {e}')
        return {'success': False, 'error': str(e)[:200]}

    # 解析 JSON
    data, parse_error = _parse_ai_json(raw)
    stats = {
        'domestic': len(domestic),
        'overseas': len(overseas),
        'minutes': len(minutes),
    }
    ts = int(time.time())

    # depth 模式独立路径
    if mode == 'depth':
        is_depth = data and 'sectors' in data and isinstance(data.get('sectors'), list)
        if is_depth:
            result = _to_depth_result(data, raw, stats, AGNES_MODEL, ts)
            html = _render_depth_html(result)
            log.info(f'[jeeseek.ai] v4 depth 渲染完成，象赛道 {len(result.sectors)} 条')
        else:
            # 兑底：渲染失败页面，原始内容存 raw
            result = DepthReport(
                raw_response=raw,
                success=True,
                title='深度报告解析失败',
                error=parse_error or 'JSON 字段不匹配',
                model=AGNES_MODEL,
                time_str=datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
                total_news=stats['domestic'] + stats['overseas'] + stats['minutes'],
                domestic_count=stats['domestic'],
                overseas_count=stats['overseas'],
                minutes_count=stats['minutes'],
            )
            result.sectors.append(SectorReport(
                name='原始响应（解析失败）',
                tagline='请刷新重试',
                highlights=[raw[:3000]],
            ))
            html = _render_depth_html(result)
            log.warning(f'[jeeseek.ai] depth 兑底渲染，error: {parse_error}')
        output = {
            'success': True,
            'html': html,
            'data': result.to_dict(),
            'error': result.error if hasattr(result, 'error') else '',
            'model': result.model,
            'time_str': result.time_str,
            'version': result.version,
            'mode': mode,
        }
        _CACHE[cache_key] = output
        try:
            report_file = REPORTS_DIR / f'latest_depth.html'
            report_file.write_text(html, encoding='utf-8')
        except Exception as e:
            log.warning(f'[jeeseek.ai] 保存 depth 报告失败: {e}')
        return output

    # 自动检测 JSON 结构 → 选择 v3 / v1 路径（macro 模式）
    is_v3 = data and 'track_rankings' in data
    is_v1 = data and 'core_trends' in data

    if is_v3:
        result = _to_batch_result(data, raw, stats, AGNES_MODEL, ts)
        html = _render_batch_html(result)
        log.info(f'[jeeseek.ai] v3 渲染完成，TOP 赛道 {len(result.track_rankings)} 条')
    elif is_v1:
        result = _to_v1_result(data, raw, stats, AGNES_MODEL, ts)
        html = _render_v1_html(result)
        log.info(f'[jeeseek.ai] v1 渲染完成（兼容模式）')
    else:
        # 兜底：放到 batch_result 的 signals 里
        result = BatchAnalysisResult(
            raw_response=raw,
            success=True,
            error=parse_error or 'JSON 字段不匹配',
            model=AGNES_MODEL,
            time_str=datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
            total_news=stats['domestic'] + stats['overseas'] + stats['minutes'],
            domestic_count=stats['domestic'],
            overseas_count=stats['overseas'],
            minutes_count=stats['minutes'],
        )
        result.signals = {
            'signal_1_mainline': raw[:1500],
            'signal_2_industry_change': '解析失败，详见信号1',
            'signal_3_institution_focus': '—',
            'signal_4_market_state': '—',
        }
        html = _render_batch_html(result)
        log.warning(f'[jeeseek.ai] 兜底渲染，error: {parse_error}')

    output = {
        'success': True,
        'html': html,
        'data': result.to_dict(),
        'error': result.error if hasattr(result, 'error') else '',
        'model': result.model,
        'time_str': result.time_str,
        'version': result.version,
        'mode': mode,
    }

    # 缓存
    _CACHE[cache_key] = output

    # 保存报告
    try:
        report_file = REPORTS_DIR / f'latest.html'
        report_file.write_text(html, encoding='utf-8')
    except Exception as e:
        log.warning(f'[jeeseek.ai] 保存报告失败: {e}')

    log.info(f'[jeeseek.ai] 报告生成完成: version={result.version}, success=True, mode={mode}')
    return output


# ──────────────────────────────
# 同步版（兼容旧代码）
# ──────────────────────────────
def generate_report_sync(domestic, overseas, minutes, force=False):
    return asyncio.run(generate_report(domestic, overseas, minutes, force=force))


# ──────────────────────────────
# 工具：清缓存（用于测试）
# ──────────────────────────────
def clear_cache():
    _CACHE.clear()
    log.info('[jeeseek.ai] 缓存已清空')
