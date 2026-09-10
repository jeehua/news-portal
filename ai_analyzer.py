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

# v5.3: 来源黑名单（不暴露任何知识星球博主名/星球名/群名）
SRC_BLOCKLIST = (
    '基业长青', '短评&信息', '可接ai', '每日上扬', 'Mr.S', 'Max',
    '群主', '星球', '主理人', '老师',
)

def _clean_source(src: str) -> str:
    """过滤博主名/星球名，空字符串或黑名单命中都返回空（前端会显示「机构纪要」）"""
    if not src:
        return ''
    s = str(src).strip()
    if not s:
        return ''
    if any(b in s for b in SRC_BLOCKLIST):
        return ''
    # 过滤纯"博主昵称"形式（不含机构/媒体关键词的短字符串）
    if len(s) <= 8 and not any(k in s for k in ('机构', '纪要', '财经', '证券', '新闻', '社', '研', '资讯', '报道')):
        return ''
    return s

# === 配置 ===
def _load_agnes_key():
    key = os.getenv('AGNES_API_KEY')
    if key:
        return key
    # Render Secret Files 挂载到 /etc/secrets/
    for path in ['/etc/secrets/jeeseek.env', '/home/ubuntu/trendradar/config/ai_key.txt', '/var/www/jeeseek/api/.agnes_key']:
        if Path(path).exists():
            for line in Path(path).read_text(encoding='utf-8', errors='ignore').split('\n'):
                line = line.strip()
                if line.startswith('AGNES_API_KEY='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    return ''

AGNES_API_KEY = _load_agnes_key()
AGNES_BASE = os.getenv('AGNES_API_BASE', 'https://apihub.agnes-ai.com/v1')
AGNES_MODEL = os.getenv('AGNES_MODEL', 'agnes-2.0-flash')
AGNES_TIMEOUT = int(os.getenv('AGNES_TIMEOUT', '180'))
AGNES_MAX_TOKENS = int(os.getenv('AGNES_MAX_TOKENS', '12000'))
AGNES_MAX_TOKENS_DEPTH = int(os.getenv('AGNES_MAX_TOKENS_DEPTH', '16000'))  # depth 模式需要更多 token（5-8 赛道 × 12 要闻）

# === Prompt 路径（v3 为主，v1 fallback） ===
PROMPT_FILES = [
    Path(__file__).parent / 'ai_hotspot_prompt_v3.txt',  # 豆包版（首选）
    Path(__file__).parent / 'ai_hotspot_prompt.txt',     # v1 兜底
]

# v4：行业跟踪报告模式（按子赛道切分，类似花总示例）
DEPTH_PROMPT_FILES = [
    # v5 优先（板块→子赛道→纪要三层结构，金红主题）
    Path(__file__).parent / 'ai_depth_prompt_v5.txt',
    # v4 fallback（按 8 大赛道平铺，绿色主题）
    Path(__file__).parent / 'ai_depth_prompt_v4.txt',
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
# 数据类 v4：行业跟踪报告（子赛道切分）
# ──────────────────────────────
@dataclass
class SubSectorReport:
    """v5: 子赛道报告（板块下的二级细分）"""
    name: str = ""
    tagline: str = ""
    highlights: List[str] = field(default_factory=list)
    key_minutes: List[Dict] = field(default_factory=list)  # [{source, time, summary}]
    key_data: List[str] = field(default_factory=list)
    related_companies: List[str] = field(default_factory=list)
    catalyst_risk: str = ""


@dataclass
class SectorReport:
    """v5: 板块报告（包含多个子赛道）"""
    name: str = ""
    tagline: str = ""
    sub_sectors: List[SubSectorReport] = field(default_factory=list)
    # 兼容 v4：板块级汇总（可选，v5 可不用）
    highlights: List[str] = field(default_factory=list)
    key_data: List[str] = field(default_factory=list)
    related_companies: List[str] = field(default_factory=list)
    catalyst_risk: str = ""


@dataclass
class DepthReport:
    """v4: 行业跟踪报告 - 按子赛道切分"""
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


# ────────────────────────────
# v4.2: 赛道关键词预分类（让纪要文本带上赛道标签，让 AI 知道归到哪）
# ────────────────────────────
SECTOR_KEYWORDS = {
    'AI 算力链': [
        '光模块', '光通信', 'CPO', 'PCB', 'GPU', 'HBM', 'DDR5', '服务器', '数据中心', '液冷', '电源',
        'NV', 'NVIDIA', 'AMD', 'GOOGL', 'TPU', 'TSMC', '台积电', '联电', '中际旭创', '新易盛', '天孚通信',
        '胜宏科技', '沪电股份', '生益科技', '深南电路', '广合科技', 'Surfax', 'Credo', '博通', 'Broadcom',
        'AVGO', 'Marvell', 'MRVL', '台股', 'ASIC', 'GB200', 'GB300', 'B200', 'B300', 'Vera Rubin',
        'AI 算力', '算力', 'AI 服务器', 'AI 基础设施', '领创新', '伊之密', '明族', '智同科技', '中航',
        '算力中心', '互联网数据中心', 'IDC', '智算', '算力网络', '液冷服务器', '英伟达', '戴尔',
        '超微', 'Supermicro', 'SanDisk', '西部数据', '固态', 'NAND', 'DRAM', '存储芯片',
        '江波龙', '兆易创新', '龙芯', '铜缆', '高速铜', 'SpeePHY', 'PCIe', '半导体设备', 'ASML', 'LRCX', 'AMAT',
        'KLA', 'TEL', '东京电子', '光互联', 'COUPE', '玻璃基板'
    ],
    '端侧 AI': [
        'AI 手机', 'AI PC', 'AI 眼镜', 'AR 眼镜', '字节', '豆包', 'DeepSeek', 'Kimi', '清华\n智谱', 'MiniMax',
        '腾讯', 'WorkBuddy', '联光', '豆包手机', '面壁', '智谱', '手机助手', 'Agent', '智能体', 'AI 助手',
        '联想', '苹米', 'Apple Intelligence', 'Galaxy AI', 'Gemini Nano', '穿戴', 'AR/VR', '智能眼镜',
        'AR 头', '元宇宙', 'NPC AI', 'AI 应用', 'AIGC', '多模态', 'LLM 推理', '本地推理', '边计算',
        'SoC 芯片', '联发科', 'MTK', '高通', 'Qualcomm', 'ARM', '手机芯片', 'AI 应用', 'AINPC', 'PC AI'
    ],
    '新能源链': [
        '光伏', '锂电', '锂电池', '钕电池', '储能', '充电桩', '新能源汽车', '太阳能', '逆变器',
        '隆基', '通威', '中环', '阳光电源', '锦浪', '固德威', '宁德', 'CATL', '比亚迪', 'BYD',
        '亿纬锂能', '欣旺达', '枥川股份', '赣锋锂业', '天齐锂业', '升志股份', '唭酒',
        '多晶硅', '硅片', '电池片', '组件', '风电', '海上风电', '陆上风电', '明阳智能', '金风科技',
        '氢能', '质子交换膜', 'PEM', '氢燃料', '电堆', '充电柱', '氢能车', '重卡', '绅宝',
        '甘冶', '欧晶科技', '仪电', '中环装备', '迈为股份', '捷佳伟创', '光伏设备'
    ],
    '半导体周期': [
        '半导体设备', '半导体材料', '刻蚀机', '光刻机', '检测设备', '封测', '完测', 'EDA',
        '北方华创', '中微公司', '拓荆', '华海涪科', 'CMP 抛光', '上机数控', '长川科技',
        '沪硅产业', '立昂微', '影东股份', '合晶', '华为哈勃', '卓郎微', '思瑞浦', '明烯微',
        '明芳微', '士兰微', '扬杰科技', '华微电子', '捷捷微', '充思股份', '中恶集成',
        '长江存储', '长熹存储', '合肥长熹', 'CxMT', '耶雅方香', '韦尔股份', '豪威',
        '集创北方', '中革微', '全志科技', '瑞芯微', '梦芯股份'
    ],
    '新消费/AI 应用': [
        '游戏', '游戏公司', '腾讯游戏', '网易', '米哈游', '莉莉丝', '游戏版号', '腾讯云游戏',
        'AI 教育', '教育', '作业帮', '猿辅导', '中公教育', '高途',
        '电商', '拼多多', '告多多', '小红书', 'B站', 'BILIBILI', '快手',
        'AI 广告', 'AI 营销', 'AI 客服', 'AI 提产', '腾讯殷东', '今朝头条',
        'AI 应用', '智能客服', 'AI 创造', 'AIGC', '周文商业', '周文版权', 'AI 业务'
    ],
    '高端制造': [
        '机器人', '人形机器人', '关节机器人', '机器人减速器', '丝杠', '谐波减速器',
        '谐波', '智同科技', '伊之密', '东茂集团', '中航高机', '象山传动', '汇川技术',
        '博众精工', '绿的谐波', '双环传动', '中大力德', '拓普集团', '三花智控',
        '数控机床', '加工中心', '海天精工', '创盛智能', '国盛智科',
        '传感器', 'TWS', '麦克韦尔', '奇蛊微', '士兰微',
        '航发动力', '航发控制', '中国远火', '十所', '民生机械'
    ],
    '生物医药': [
        '创新药', '创新药企业', 'PD-1', 'PD-L1', '以香药', '典以美', '肿瘤药',
        '恒瑞医药', '百济神州', '百济神洲', '信达生物', '君实生物', '复宏汉霖',
        '药明康德', '药明生物', '药明合联', 'CXO', 'CRO', 'CMO', '临床试验',
        '迈瑞医疗', '联影医疗', '鱼跃医疗', '达安基因', '迈克生物',
        '合成生物', '川宁生物', '金河生物',
        '药明', '药石', '梴芝药业', '仁东医药', '人工心脏', '脑机接口', '脑机',
        '脑机接口', '介入器材'
    ],
    '周期与宏观': [
        '黄金', '白银', '黄金股', '山东黄金', '紫金矿业', '中金黄金', '赤峰黄金',
        '原油', '油价', 'WTI', '布伦特', '中石油', '中石化', '中海油',
        '天然气', 'LNG', '煤炭', '焦煤', '焦炭', '陕北煤业', '中国神华',
        '钢铁', '镀锌板', '冷轧', '热轧', '宝钢', '鞍山钢铁', '首钢',
        '航运', '干散货', '集装箱', '中远海运', '招商轮船', '油运',
        '汇率', '人民币', '美元', '美元指数', '美债', '10年期', '加息', '降息',
        '美联储', 'FOMC', '鲍威尔', 'CPI', 'PPI', '非农', 'PCE',
        '房价', '房地产', '万科', '保利', '中交地产', '碧桂园'
    ],
}


def _classify_minutes_sector(title: str, content: str = '') -> List[str]:
    """根据关键词返回所属赛道列表（多标签）"""
    text = (title + ' ' + (content or ''))[:2000]  # 限长防慢
    matched = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw and kw in text:
                matched.append(sector)
                break  # 一个赛道命中一次就够
    return matched if matched else ['周期与宏观']  # 兜底：宏观众赛道


def _format_minutes_by_sector(minutes_list: List[Dict], limit_per_sector: int = 15) -> str:
    """v4.2: 纪要按赛道预分类后输出（每条纪要带赛道标签，AI 看到就知道归到哪）

    v5 兼容版：保留作为 fallback，v5 主路径用 _format_minutes_by_sub_sector
    """
    if not minutes_list:
        return '（无机构纪要数据）'

    # Step 1: 给每条纪要打赛道标签
    categorized = []  # (sector_str, item)
    for m in minutes_list:
        title = m.get('title', '').replace('\n', ' ').strip()
        content = m.get('content', '').replace('\n', ' ').strip()
        time_str = m.get('time', '')[:16]
        source = m.get('source_name') or m.get('source_id', '?')
        sectors = _classify_minutes_sector(title, content)
        sector_tag = '|'.join(sectors)  # 如「AI 算力链|端侧 AI」
        # 拼装成一条预分类纪要
        item_text = f'【{sector_tag}】[{time_str}] [{source}] {title}'
        if content:
            content_preview = content[:600].strip()
            item_text += f'\n  > {content_preview}'
        categorized.append((sectors[0] if sectors else '周期与宏观', item_text))

    # Step 2: 按赛道分组，按时间倒序（原始顺序已是近→远），每组取前 N
    from collections import defaultdict
    grouped = defaultdict(list)
    for sector, text in categorized:
        grouped[sector].append(text)

    # Step 3: 输出（按 8 赛道顺序）
    sector_order = [
        'AI 算力链', '端侧 AI', '新能源链', '半导体周期',
        '新消费/AI 应用', '高端制造', '生物医药', '周期与宏观'
    ]
    lines = []
    total_count = 0
    for sector in sector_order:
        items = grouped.get(sector, [])[:limit_per_sector]
        if not items:
            continue
        lines.append(f'\n【赛道: {sector}】 ({len(items)} 条)')
        for i, txt in enumerate(items, 1):
            lines.append(f'  {i}. {txt}')
            total_count += 1
            if total_count >= 120:  # 总上限防爆 token
                break
        if total_count >= 120:
            lines.append('  ... (以下省略)')
            break

    return '\n'.join(lines) if lines else '（无机构纪要数据）'


# ───────────────────────────────────────────────
# v5: 板块 → 子赛道 二级分类词典
# ───────────────────────────────────────────────
SECTOR_HIERARCHY = {
    'AI 算力链': {
        'sub_sectors': {
            '液冷': ['液冷', '冷板', '浸没', '英维克', '曙光数创', '飞龙', '散热', '热管理', '冷板式', '浸没式', '液冷服务器', '飞龙股份'],
            'PCB/IC载板': ['PCB', '深南电路', '沪电股份', '胜宏科技', '广合科技', 'CCL', '玻璃基板', 'IC 载板', '覆铜板', 'TGV'],
            '光模块/CPO': ['光模块', '光通信', 'CPO', '中际旭创', '新易盛', '天孚通信', 'Credo', 'COUPE', '硅光子', '800G', '1.6T', '光互联', '硅光'],
            'GPU/算力': ['GPU', 'HBM', '英伟达', 'NV', 'AMD', '博通', 'Broadcom', 'Marvell', 'AI 算力', '智算', '算力中心', '数据中心', 'Vera Rubin', 'GB200', 'GB300', 'B200', 'B300'],
            '服务器/电源': ['服务器', '电源', 'AI 服务器', '机柜', 'PSU'],
            '存储芯片': ['SanDisk', '西部数据', '固态', 'NAND', 'DRAM', '存储芯片', '江波龙', '兆易创新', 'DDR5'],
            '半导体设备': ['半导体设备', 'ASML', 'LRCX', 'AMAT', 'KLA', 'TEL', '东京电子', '北方华创', '中微', '刻蚀机', '光刻机', '拓荆', '华海清科'],
        }
    },
    '端侧 AI': {
        'sub_sectors': {
            'AI 手机': ['AI 手机', '豆包手机', '字节手机', '苹果', 'Apple Intelligence', '华为', 'OPPO', 'Vivo', '小米', 'Galaxy AI', 'Gemini Nano'],
            'AI PC/眼镜': ['AI PC', 'AI 眼镜', 'AR 眼镜', '联想', '苹果 PC', '穿戴', 'AR/VR', '智能眼镜', 'AR 头', '元宇宙'],
            'Agent/应用': ['Agent', '智能体', '豆包', 'DeepSeek', 'Kimi', '智谱', '腾讯混元', 'LLM', '大模型', 'AI 应用', 'AIGC', '多模态', 'AI 助手', 'AINPC', 'NPC AI'],
            'SoC 芯片': ['SoC 芯片', '联发科', 'MTK', '高通', 'Qualcomm', 'ARM', '手机芯片', 'PC 芯片'],
        }
    },
    '新能源链': {
        'sub_sectors': {
            '光伏': ['光伏', '太阳能', '隆基', '通威', '中环', '多晶硅', '硅片', '电池片', '组件', '逆变器', '迈为', '捷佳伟创', '光伏设备'],
            '锂电/储能': ['锂电', '锂电池', '钕电池', '储能', '充电桩', '宁德', 'CATL', '比亚迪', 'BYD', '亿纬锂能', '欣旺达', '赣锋', '天齐'],
            '风电/氢能': ['风电', '海上风电', '陆上风电', '明阳智能', '金风科技', '氢能', '质子交换膜', 'PEM', '氢燃料', '电堆'],
            '新能源车': ['新能源汽车', '整车', '理想', '蔚来', '小鹏', '问界', '智能驾驶', '自动驾驶', 'Tesla', '特斯拉'],
        }
    },
    '半导体周期': {
        'sub_sectors': {
            '设备/材料': ['半导体设备', '半导体材料', '刻蚀机', '光刻机', '检测设备', '北方华创', '中微公司', '拓荆', '华海清科', 'CMP 抛光', '上海新阳', 'EDA'],
            '封测': ['封测', '完测', '长川科技', '精测电子', '华峰测控'],
            '国产存储': ['长江存储', '长鑫存储', '合肥长鑫', 'CxMT', '兆易创新', '北京君正'],
            'IC 设计': ['韦尔股份', '豪威', '集创北方', '中革微', '全志科技', '瑞芯微', '梦芯股份', '卓胜微', '思瑞浦'],
        }
    },
    '新消费/AI 应用': {
        'sub_sectors': {
            '游戏': ['游戏', '腾讯游戏', '网易', '米哈游', '莉莉丝', '游戏版号', '腾讯云游戏', 'AI NPC', 'AIGC'],
            '电商/广告': ['电商', '拼多多', '小红书', 'B站', 'BILIBILI', '快手', 'AI 广告', 'AI 营销', '抖音电商'],
            '教育/SaaS': ['AI 教育', '教育', '作业帮', '猿辅导', '中公教育', '高途', 'SaaS', 'AI 客服', '智能客服'],
        }
    },
    '高端制造': {
        'sub_sectors': {
            '人形机器人': ['人形机器人', '机器人减速器', '丝杠', '谐波减速器', '谐波', '智同科技', '绿的谐波', '双环传动', '中大力德', '拓普集团', '三花智控', '关节机器人'],
            '数控机床/工业母机': ['数控机床', '加工中心', '海天精工', '创盛智能', '国盛智科', '工业母机'],
            '传感器/军工': ['传感器', '航发动力', '航发控制', '中国远火', '十所', '民生机械', '军工', '无人机'],
        }
    },
    '生物医药': {
        'sub_sectors': {
            '创新药': ['创新药', 'PD-1', 'PD-L1', 'ADC', 'GLP-1', '肿瘤药', '恒瑞医药', '百济神州', '信达生物', '君实生物', '复宏汉霖'],
            'CXO/CRO': ['药明康德', '药明生物', '药明合联', 'CXO', 'CRO', 'CMO', '临床试验', '药石', '康龙化成'],
            '医疗器械/合成生物': ['迈瑞医疗', '联影医疗', '鱼跃医疗', '合成生物', '川宁生物', '脑机接口', '介入器材', '人工心脏'],
        }
    },
    '周期与宏观': {
        'sub_sectors': {
            '贵金属/有色': ['黄金', '白银', '黄金股', '山东黄金', '紫金矿业', '中金黄金', '赤峰黄金', '有色', '铜', '铝'],
            '原油/煤炭': ['原油', '油价', 'WTI', '布伦特', '中石油', '中石化', '中海油', '天然气', 'LNG', '煤炭', '焦煤', '焦炭', '陕北煤业', '中国神华'],
            '钢铁/航运': ['钢铁', '镀锌板', '冷轧', '热轧', '宝钢', '鞍山钢铁', '首钢', '航运', '干散货', '集装箱', '中远海运', '招商轮船'],
            '宏观/汇率/利率': ['汇率', '人民币', '美元', '美元指数', '美债', '10年期', '加息', '降息', '美联储', 'FOMC', '鲍威尔', 'CPI', 'PPI', '非农', 'PCE'],
            '房地产': ['房价', '房地产', '万科', '保利', '中交地产', '碧桂园'],
        }
    },
}


def _compute_sector_frequency(minutes: List[Dict], domestic: List[Dict], overseas: List[Dict]) -> Dict[str, int]:
    """v5.4: 计算 8 板块的热度分值（板块热度榜）

    计分规则：
      - 纪要命中板块：每条 +3 分（每个二级分类计一次，跨板块累加）
      - 国内/海外新闻命中板块：每条 +1 分（板块去重，多子赛道只计 1 分）

    返回：按频次降序的 dict，例如
        {'AI 算力链': 87, '端侧 AI': 42, '周期与宏观': 18, ...}
    """
    freq = {b: 0 for b in SECTOR_HIERARCHY.keys()}

    # 1. 纪要：每条按二级分类累加 ×3
    for m in minutes or []:
        title = m.get('title', '')
        content = m.get('content', '')
        pairs = _classify_minutes_sub_sectors(title, content)
        seen_boards = set()
        for p in pairs:
            board = p.get('board', '')
            if board in freq and board not in seen_boards:
                freq[board] += 3
                seen_boards.add(board)

    # 2. 新闻：每条命中板块 +1（板块级去重）
    for n in list(domestic or []) + list(overseas or []):
        title = n.get('title', '')
        content = n.get('content', '') or n.get('summary', '') or n.get('text', '')
        text = (title + ' ' + content)[:2000]
        if not text.strip():
            continue
        for board, info in SECTOR_HIERARCHY.items():
            matched = False
            for keywords in info['sub_sectors'].values():
                for kw in keywords:
                    if kw and kw in text:
                        matched = True
                        break
                if matched:
                    break
            if matched:
                freq[board] += 1

    # 3. 降序排序
    sorted_freq = dict(sorted(freq.items(), key=lambda x: x[1], reverse=True))
    log.info(f'[jeeseek.ai] 板块热度榜: ' + ' | '.join(
        f'{b}={n}' for b, n in list(sorted_freq.items())[:8]
    ))
    return sorted_freq


def _format_sector_frequency_for_prompt(freq: Dict[str, int]) -> str:
    """v5.4: 把板块热度榜格式化成 prompt 友好的文本

    格式：'1.AI 算力链（87 分） 2.端侧 AI（42 分） 3.周期与宏观（18 分） ...'
    """
    if not freq:
        return '（无数据）'
    lines = []
    for i, (board, score) in enumerate(freq.items(), 1):
        lines.append(f'{i}.{board}（{score} 分）')
    return ' '.join(lines)


def _classify_minutes_sub_sectors(title: str, content: str = '') -> List[Dict[str, str]]:
    """v5: 返回纪要的二级分类 [(板块, 子赛道), ...]，可多标签

    Returns: [{'board': 'AI 算力链', 'sub': '液冷'}, ...]
    """
    text = (title + ' ' + (content or ''))[:2000]
    matches = []
    seen_pairs = set()
    for board, info in SECTOR_HIERARCHY.items():
        for sub_name, keywords in info['sub_sectors'].items():
            for kw in keywords:
                if kw and kw in text:
                    pair = (board, sub_name)
                    if pair not in seen_pairs:
                        matches.append({'board': board, 'sub': sub_name})
                        seen_pairs.add(pair)
                    break  # 一个子赛道命中一次就够
    return matches if matches else [{'board': '周期与宏观', 'sub': '宏观/汇率/利率'}]


def _format_minutes_by_sub_sector(minutes_list: List[Dict], limit_per_sub: int = 8) -> str:
    """v5: 纪要按 板块 → 子赛道 二级分组后输出

    每条纪要带【板块 / 子赛道】标签，AI 看到就知道归到哪。
    """
    if not minutes_list:
        return '（无机构纪要数据）'

    from collections import defaultdict
    grouped = defaultdict(lambda: defaultdict(list))  # {board: {sub: [item_text]}}

    for m in minutes_list:
        title = m.get('title', '').replace('\n', ' ').strip()
        content = m.get('content', '').replace('\n', ' ').strip()
        time_str = m.get('time', '')[:16]
        source = m.get('source_name') or m.get('source_id', '?')
        matches = _classify_minutes_sub_sectors(title, content)
        for m_pair in matches:
            board = m_pair['board']
            sub = m_pair['sub']
            item_text = f'【{board}/{sub}】[{time_str}] [{source}] {title}'
            if content:
                content_preview = content[:500].strip()
                item_text += f'\n  > {content_preview}'
            grouped[board][sub].append(item_text)

    # 按 8 个板块顺序输出
    board_order = [
        'AI 算力链', '端侧 AI', '新能源链', '半导体周期',
        '新消费/AI 应用', '高端制造', '生物医药', '周期与宏观'
    ]
    lines = []
    total_count = 0
    MAX_TOTAL = 120  # 总上限防爆 token

    for board in board_order:
        subs = grouped.get(board, {})
        if not subs:
            continue
        lines.append(f'\n## 板块：{board}（{sum(len(v) for v in subs.values())} 条纪要）')
        # 子赛道按纪要数量倒序
        for sub_name in sorted(subs.keys(), key=lambda k: len(subs[k]), reverse=True):
            items = subs[sub_name][:limit_per_sub]
            if not items:
                continue
            lines.append(f'\n### 子赛道：{sub_name}（{len(items)} 条）')
            for i, txt in enumerate(items, 1):
                lines.append(f'  {i}. {txt}')
                total_count += 1
                if total_count >= MAX_TOTAL:
                    lines.append('  ... (以下省略)')
                    return '\n'.join(lines)

    return '\n'.join(lines) if lines else '（无机构纪要数据）'


# ──────────────────────────────
# Agnes API 调用
# ──────────────────────────────
def _call_agnes(system: str, user: str, max_tokens: int = None, timeout: int = None) -> str:
    # 动态读取 env var（避免 import 时为空的问题）
    key = os.getenv('AGNES_API_KEY') or _load_agnes_key()
    if not key:
        raise RuntimeError('AGNES_API_KEY is empty or missing')
    client = openai.OpenAI(
        api_key=key,
        base_url=os.getenv('AGNES_API_BASE', 'https://apihub.agnes-ai.com/v1'),
        timeout=timeout or AGNES_TIMEOUT,
    )
    resp = client.chat.completions.create(
        model=AGNES_MODEL,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        max_tokens=max_tokens or AGNES_MAX_TOKENS,
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
# v4: 行业跟踪报告渲染（按子赛道切分）
# ──────────────────────────────
def _to_depth_result(data: dict, raw: str, stats: dict, model: str, ts: int, sector_freq: Dict[str, int] = None) -> DepthReport:
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
    result.version = 'v5' if any(s.get('sub_sectors') for s in (data.get('sectors') or []) if isinstance(s, dict)) else 'v4'
    result.title = str(data.get('title', '产业链日报'))

    # sectors (v5: 含 sub_sectors；v4: 仅板块级字段)
    for s in data.get('sectors', []) or []:
        if isinstance(s, dict):
            sec = SectorReport(
                name=str(s.get('name', '')),
                tagline=str(s.get('tagline', '')),
                highlights=[str(x) for x in (s.get('highlights', []) or []) if x],
                key_data=[str(x) for x in (s.get('key_data', []) or []) if x],
                related_companies=[str(x) for x in (s.get('related_companies', []) or []) if x],
                catalyst_risk=str(s.get('catalyst_risk', '')),
            )
            # v5: 解析 sub_sectors（含 key_minutes）
            for sub in (s.get('sub_sectors', []) or []):
                if isinstance(sub, dict):
                    key_minutes = []
                    for km in (sub.get('key_minutes', []) or []):
                        if isinstance(km, dict):
                            key_minutes.append({
                                'source': str(km.get('source', '')),
                                'time': str(km.get('time', '')),
                                'summary': str(km.get('summary', '')).strip(),
                            })
                        elif isinstance(km, str) and km.strip():
                            # 兼容: 字符串列表 "▶ [source·time] summary"
                            key_minutes.append({'source': '', 'time': '', 'summary': km.strip()})
                    sec.sub_sectors.append(SubSectorReport(
                        name=str(sub.get('name', '')),
                        tagline=str(sub.get('tagline', '')),
                        highlights=[str(x) for x in (sub.get('highlights', []) or []) if x],
                        key_minutes=key_minutes,
                        key_data=[str(x) for x in (sub.get('key_data', []) or []) if x],
                        related_companies=[str(x) for x in (sub.get('related_companies', []) or []) if x],
                        catalyst_risk=str(sub.get('catalyst_risk', '')),
                    ))
            result.sectors.append(sec)

    # overall
    overall = data.get('overall', {}) or {}
    result.main_lines = [str(x) for x in (overall.get('main_lines', []) or []) if x]
    catalysts = overall.get('catalysts', {}) or {}
    result.catalysts_new = [str(x) for x in (catalysts.get('new', []) or []) if x]
    result.catalysts_cooling = [str(x) for x in (catalysts.get('cooling', []) or []) if x]
    result.risks = [str(x) for x in (overall.get('risks', []) or []) if x]

    # v5.4 兜底：按板块热度榜排序（防止 AI 没听指令乱排）
    if sector_freq and result.sectors:
        freq_order = list(sector_freq.keys())
        def _sort_key(sec):
            try:
                return freq_order.index(sec.name)
            except ValueError:
                return len(freq_order)  # AI 新增的板块（不在 8 板块里）排最后
        result.sectors.sort(key=_sort_key)
        log.info(f'[jeeseek.ai] v5.4 兜底排序完成: ' + ' > '.join(s.name for s in result.sectors))

    return result


def _render_depth_html(result: DepthReport) -> str:
    """v5: 产业链日报 - 板块→子赛道→纪要 三层嵌套（金红喜庆主题）"""
    title = result.title or '产业链日报'
    has_subs = any(s.sub_sectors for s in result.sectors)

    # === 渲染板块（一级）===
    boards_html = []
    for i, s in enumerate(result.sectors, 1):
        if s.sub_sectors:
            # v5: 嵌套渲染子赛道
            subs_html_parts = []
            for j, sub in enumerate(s.sub_sectors, 1):
                highlights_html = ''.join(f'<li>{h}</li>' for h in sub.highlights) if sub.highlights else '<li>—</li>'
                data_html = ''.join(f'<li>{d}</li>' for d in sub.key_data) if sub.key_data else '<li>—</li>'
                companies_html = ''.join(f'<li>{c}</li>' for c in sub.related_companies) if sub.related_companies else '<li>—</li>'

                # key_minutes 可折叠
                minutes_block = ""
                if sub.key_minutes:
                    minutes_items = []
                    for km in sub.key_minutes:
                        src = km.get('source', '')
                        time = km.get('time', '')
                        summary = km.get('summary', '')
                        # v5.3: 硬过滤博主名/星球名（花总拍：不要暴露来源）
                        # 即使 AI 输出基业长青等名字，也强制替换为空走「机构纪要」分支
                        src = _clean_source(src)
                        meta = '[机构纪要' if not src else f'[{src}'
                        if time:
                            meta += f'·{time}' if src else f'·{time}'
                        meta += ']'
                        minutes_items.append(f'<li><span class="km-meta">{meta}</span> <span class="km-summary">{summary}</span></li>')
                    minutes_html = ''.join(minutes_items)
                    minutes_block = (
                        '\n<div class="sector-block key-minutes-block">'
                        '\n  <div class="block-label">纪要提炼（' + str(len(sub.key_minutes)) + ' 条）</div>'
                        '\n  <details class="key-minutes-details" open>'
                        '\n    <summary>展开/折叠纪要原文摘要</summary>'
                        '\n    <ol class="key-minutes-list">' + minutes_html + '</ol>'
                        '\n  </details>'
                        '\n</div>'
                    )

                sub_tagline = f' <span class="sub-tagline">· {sub.tagline}</span>' if sub.tagline else ''
                subs_html_parts.append(f"""
<div class="sub-sector">
  <div class="sub-header">
    <span class="sub-num">{i}.{j}</span>
    <span class="sub-name">{sub.name}</span>{sub_tagline}
  </div>
  <div class="sub-body">
    <div class="sector-block">
      <div class="block-label">【核心要闻】</div>
      <ol class="block-list">{highlights_html}</ol>
    </div>
    {minutes_block}
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
      <div class="block-text">{sub.catalyst_risk or '—'}</div>
    </div>
  </div>
</div>""")

            board_tagline = f' <span class="board-tagline">· {s.tagline}</span>' if s.tagline else ''
            boards_html.append(f"""
<div class="board">
  <div class="board-header">
    <span class="board-num">{i}</span>
    <span class="board-name">{s.name}</span>{board_tagline}
    <span class="board-count">· {len(s.sub_sectors)} 个子赛道</span>
  </div>
  <div class="board-body">__BOARD_SUBS_PLACEHOLDER__</div>
</div>""".replace('__BOARD_SUBS_PLACEHOLDER__', ''.join(subs_html_parts)))
        else:
            # v4 fallback: 老渲染
            highlights_html = ''.join(f'<li>{h}</li>' for h in s.highlights) if s.highlights else '<li>—</li>'
            data_html = ''.join(f'<li>{d}</li>' for d in s.key_data) if s.key_data else '<li>—</li>'
            companies_html = ''.join(f'<li>{c}</li>' for c in s.related_companies) if s.related_companies else '<li>—</li>'
            tagline_html = f' <span class="board-tagline">· {s.tagline}</span>' if s.tagline else ''
            boards_html.append(f"""
<div class="board">
  <div class="board-header">
    <span class="board-num">{i}</span>
    <span class="board-name">{s.name}</span>{tagline_html}
  </div>
  <div class="board-body">
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
</div>""")

    # === 当周总结渲染 ===
    main_lines_html = ''.join(f'<li>{x}</li>' for x in result.main_lines) if result.main_lines else '<li>—</li>'
    catalysts_new_html = ''.join(f'<li>{x}</li>' for x in result.catalysts_new) if result.catalysts_new else '<li>—</li>'
    catalysts_cooling_html = ''.join(f'<li>{x}</li>' for x in result.catalysts_cooling) if result.catalysts_cooling else '<li>—</li>'
    risks_html = ''.join(f'<li>{x}</li>' for x in result.risks) if result.risks else '<li>—</li>'

    stats_line = f'国内: {result.domestic_count}条 | 海外: {result.overseas_count}条 | 纪要: {result.minutes_count}条 | 总计: {result.total_news}条'
    version_badge = 'v5 三层嵌套' if has_subs else 'v4 单层'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - 知几察微</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; background: #fef2f2; color: #1f2937; line-height: 1.7; padding: 20px; }}
.container {{ max-width: 980px; margin: 0 auto; }}
.header {{ background: linear-gradient(135deg, #DC2626 0%, #991B1B 100%); color: white; padding: 28px 32px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 16px rgba(220, 38, 38, 0.25); position: relative; overflow: hidden; }}
.header::before {{ content: ""; position: absolute; top: -50%; right: -20%; width: 400px; height: 400px; background: radial-gradient(circle, rgba(245, 158, 11, 0.18) 0%, transparent 60%); border-radius: 50%; }}
.header h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 8px; position: relative; }}
.header .meta {{ font-size: 13px; opacity: 0.92; position: relative; }}
.header .badge {{ display: inline-block; background: linear-gradient(90deg, #F59E0B, #D97706); color: white; padding: 3px 12px; border-radius: 14px; font-size: 12px; font-weight: 600; margin-left: 8px; box-shadow: 0 2px 6px rgba(245, 158, 11, 0.4); }}
.stats {{ background: linear-gradient(90deg, #FEF2F2, #FEE2E2); color: #991B1B; padding: 12px 20px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; border: 1px solid #FECACA; }}

/* 板块（一级）*/
.board {{ background: white; border-radius: 12px; margin-bottom: 18px; overflow: hidden; box-shadow: 0 2px 8px rgba(220, 38, 38, 0.08); border: 1px solid #FECACA; }}
.board-header {{ background: linear-gradient(90deg, #DC2626 0%, #B91C1C 100%); padding: 14px 22px; font-size: 18px; color: white; font-weight: 700; display: flex; align-items: center; gap: 12px; box-shadow: inset 0 -2px 8px rgba(0,0,0,0.1); }}
.board-num {{ background: rgba(255,255,255,0.25); color: white; width: 30px; height: 30px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 15px; font-weight: 700; border: 2px solid rgba(255,255,255,0.3); flex-shrink: 0; }}
.board-name {{ flex-shrink: 0; }}
.board-tagline {{ font-size: 13px; font-weight: 400; opacity: 0.92; }}
.board-count {{ font-size: 12px; font-weight: 400; opacity: 0.85; margin-left: auto; }}
.board-body {{ padding: 4px 0; }}

/* 子赛道（二级）*/
.sub-sector {{ padding: 18px 24px; border-bottom: 1px solid #F3F4F6; }}
.sub-sector:last-child {{ border-bottom: none; }}
.sub-header {{ font-size: 15px; color: #991B1B; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
.sub-num {{ background: linear-gradient(135deg, #F59E0B, #D97706); color: white; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 700; min-width: 32px; text-align: center; flex-shrink: 0; }}
.sub-name {{ color: #991B1B; }}
.sub-tagline {{ font-size: 13px; color: #B91C1C; font-weight: 400; }}
.sub-body {{ padding-left: 4px; }}

.sector-block {{ margin-bottom: 12px; }}
.sector-block:last-child {{ margin-bottom: 0; }}
.block-label {{ font-size: 13px; color: #B91C1C; font-weight: 600; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }}
.block-list {{ margin-left: 0; padding-left: 22px; color: #374151; font-size: 14px; }}
.block-list li {{ margin-bottom: 4px; line-height: 1.65; }}
.companies {{ list-style: none; padding-left: 0; display: flex; flex-wrap: wrap; gap: 6px; }}
.companies li {{ background: linear-gradient(90deg, #FEF2F2, #FEE2E2); color: #991B1B; padding: 3px 10px; border-radius: 12px; font-size: 13px; margin-bottom: 0; border: 1px solid #FECACA; }}

/* 纪要提炼（可折叠）*/
.key-minutes-block {{ background: linear-gradient(90deg, #FFFBEB 0%, #FEF3C7 100%); border-radius: 8px; padding: 10px 14px; border-left: 3px solid #F59E0B; }}
.key-minutes-details summary {{ cursor: pointer; font-size: 13px; color: #B45309; font-weight: 600; padding: 4px 0; user-select: none; }}
.key-minutes-details summary::marker {{ color: #F59E0B; }}
.key-minutes-details[open] summary {{ margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px dashed #FCD34D; }}
.key-minutes-list {{ padding-left: 20px; margin: 0; color: #78350F; font-size: 13px; }}
.key-minutes-list li {{ margin-bottom: 6px; line-height: 1.6; padding: 4px 0; }}
.km-meta {{ background: #DC2626; color: white; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; margin-right: 6px; }}
.km-summary {{ color: #422006; }}

.catalyst-risk .block-text {{ background: linear-gradient(90deg, #FEF2F2, #FECACA); padding: 8px 12px; border-radius: 6px; color: #7F1D1D; font-size: 13px; line-height: 1.6; border-left: 3px solid #DC2626; }}

/* 当周总结 */
.overall {{ background: linear-gradient(135deg, #7F1D1D 0%, #450A0A 100%); color: #FEE2E2; padding: 24px 28px; border-radius: 12px; margin-bottom: 16px; box-shadow: 0 4px 16px rgba(127, 29, 29, 0.3); }}
.overall h2 {{ font-size: 18px; color: #FCD34D; margin-bottom: 14px; font-weight: 700; }}
.overall-block {{ margin-bottom: 16px; }}
.overall-block:last-child {{ margin-bottom: 0; }}
.overall-block .label {{ color: #FCD34D; font-size: 14px; font-weight: 600; margin-bottom: 6px; }}
.overall-block ol, .overall-block ul {{ padding-left: 22px; font-size: 13px; color: #FEE2E2; }}
.overall-block li {{ margin-bottom: 3px; }}

footer {{ text-align: center; color: #B45309; font-size: 12px; margin-top: 30px; padding: 16px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{title}<span class="badge">{version_badge}</span></h1>
    <div class="meta">生成时间: {result.time_str}</div>
  </div>
  <div class="stats">数据概览: {stats_line}</div>
  __JOINED_BOARDS_PLACEHOLDER__
  <div class="overall">
    <h2>当周总结</h2>
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
  <footer>知几察微 · jeeseek.top · {version_badge} · 金红喜庆主题</footer>
</div>
</body>
</html>"""

    return html.replace('__JOINED_BOARDS_PLACEHOLDER__', ''.join(boards_html))
# ──────────────────────────────
# 主入口：生成报告（自动选择 v1 或 v3 路径）
# ──────────────────────────────
async def generate_report(
    domestic: List[Dict],
    overseas: List[Dict],
    minutes: List[Dict],
    force: bool = False,
    mode: str = 'macro'  # 'macro' = v3 宏观信号 (默认) | 'depth' = v4 行业跟踪
) -> Dict[str, Any]:
    """生成 AI 热点分析报告
    mode:
      - 'macro' (默认)：v3 宏观信号模式，TOP赛道 + 4 类信号
      - 'depth'：v4 行业跟踪模式，按子赛道切分
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
    dom_limit = 40 if mode == 'macro' else 80  # depth 增加国内要闻采样
    os_limit = 40 if mode == 'macro' else 60  # depth 增加海外采样
    min_limit = 20 if mode == 'macro' else 100  # depth 优先消化纪要

    # 格式化数据
    # v4.2: depth 模式纪要按赛道预分类后输出（带【赛道】标签 + content 预览）
    if mode == 'depth':
        minutes_text = _format_minutes_by_sector(minutes, limit_per_sector=15)
    else:
        minutes_text = _format_news(minutes, limit=min_limit)
    domestic_text = _format_news(domestic, limit=dom_limit)
    overseas_text = _format_news(overseas, limit=os_limit)
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

    # v5.4: 计算板块热度榜（仅 depth 模式需要，macro 模式不适用）
    sector_freq = None
    if mode == 'depth':
        try:
            sector_freq = _compute_sector_frequency(minutes, domestic, overseas)
            user_p = user_p.replace('{sector_frequency}', _format_sector_frequency_for_prompt(sector_freq))
        except Exception as e:
            log.warning(f'[jeeseek.ai] 板块热度计算失败: {e}')
            user_p = user_p.replace('{sector_frequency}', '（热度计算失败，按 prompt 默认顺序）')
    else:
        user_p = user_p.replace('{sector_frequency}', '（仅 depth 模式使用）')

    log.info(f'[jeeseek.ai] 生成报告 (mode={mode}): 国内{len(domestic)} 海外{len(overseas)} 纪要{len(minutes)}')

    # 调用 AI（depth 模式增加超时，因为 token 更多）
    loop = asyncio.get_event_loop()
    timeout = AGNES_TIMEOUT + (60 if mode == 'depth' else 10)
    try:
        # depth 模式使用更多 max_tokens（5-8 赛道 × 12 要闻 ≈ 16K）
        max_tokens = AGNES_MAX_TOKENS_DEPTH if mode == 'depth' else AGNES_MAX_TOKENS
        raw = await asyncio.wait_for(
            loop.run_in_executor(None, _call_agnes, system_p, user_p, max_tokens, timeout),
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
            result = _to_depth_result(data, raw, stats, '智能分析', ts, sector_freq=sector_freq)
            html = _render_depth_html(result)
            log.info(f'[jeeseek.ai] v4 depth 渲染完成，象赛道 {len(result.sectors)} 条')
        else:
            # 兑底：渲染失败页面，原始内容存 raw
            result = DepthReport(
                raw_response=raw,
                success=True,
                title='深度报告解析失败',
                error=parse_error or 'JSON 字段不匹配',
                model='智能分析',
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
        result = _to_batch_result(data, raw, stats, '智能分析', ts)
        html = _render_batch_html(result)
        log.info(f'[jeeseek.ai] v3 渲染完成，TOP 赛道 {len(result.track_rankings)} 条')
    elif is_v1:
        result = _to_v1_result(data, raw, stats, '智能分析', ts)
        html = _render_v1_html(result)
        log.info(f'[jeeseek.ai] v1 渲染完成（兼容模式）')
    else:
        # 兜底：放到 batch_result 的 signals 里
        result = BatchAnalysisResult(
            raw_response=raw,
            success=True,
            error=parse_error or 'JSON 字段不匹配',
            model='智能分析',
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
