#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知几察微 关键词词频聚合 v2（8 大类 · 两级）
- 支持 [大类/小类] 两级分类
- 按大类聚合渲染，小类作为子标题
- 标题包含分组关键词 → 归到对应分类
- 输出 {大类: {小类: [新闻列表]}}
"""
import re
import time
from pathlib import Path
from typing import Dict, List, Tuple
from collections import Counter, defaultdict

# === 配置文件路径 ===
FREQ_FILE = Path(__file__).parent / 'frequency_words.txt'

# === 8 大类固定顺序 ===
CATEGORY_ORDER = ['成长', '价值', '周期', '政治', '地缘', '大厂', '外企', '宏观金融']

# === 内置默认配置（如果配置文件不存在） ===
DEFAULT_KEYWORD_GROUPS = {
    '成长/AI算力': ['AI', '人工智能', '英伟达', 'NVIDIA', 'AMD', '算力', '光模块', 'PCB', 'CPO', '大模型'],
    '宏观金融/央行政策': ['美联储', 'Fed', '降息', '加息', '利率', '央行', '货币政策'],
    '大厂/互联网': ['阿里', '腾讯', '百度', '京东', '美团', '拼多多', '字节'],
    '地缘/中美关系': ['中美', '中美关系', '制裁', '关税', '贸易战'],
    '周期/能源资源': ['原油', '石油', '天然气', '黄金', '白银', '有色', '煤炭', 'OPEC'],
}


def load_keyword_groups() -> Dict[str, List[str]]:
    """
    加载关键词分组配置（支持 [大类] 和 [大类/小类] 两种格式）
    返回: {完整路径: [关键词列表]}
    """
    groups = {}

    if FREQ_FILE.exists():
        try:
            content = FREQ_FILE.read_text(encoding='utf-8')
            current_group = None
            for line in content.split('\n'):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('[') and line.endswith(']'):
                    current_group = line[1:-1].strip()
                    groups[current_group] = []
                elif current_path := current_group:
                    # 支持 关键词 => 别名 语法（取关键词部分）
                    if '=>' in line:
                        keyword = line.split('=>', 1)[0].strip()
                    else:
                        keyword = line
                    # 跳过特殊前缀
                    if keyword.startswith(('+', '!', '@', '/')):
                        continue
                    groups[current_group].append(keyword)
            # 过滤空分组
            groups = {k: v for k, v in groups.items() if v}
        except Exception as e:
            print(f'Warning: 读取关键词配置失败: {e}')

    # 兜底用默认
    if not groups:
        groups = DEFAULT_KEYWORD_GROUPS

    return groups


def parse_category_path(path: str) -> Tuple[str, str]:
    """
    解析 [大类/小类] 路径
    返回: (大类, 小类)
    - '成长/AI算力' → ('成长', 'AI算力')
    - '宏观金融' → ('宏观金融', '(大类)')
    """
    if '/' in path:
        parts = path.split('/', 1)
        return parts[0].strip(), parts[1].strip()
    return path, '(大类)'


def classify_title(title: str, groups: Dict[str, List[str]]) -> List[str]:
    """
    把标题分类到多个组
    返回该标题命中的分组列表（可能多个）
    """
    matched = []
    if not title:
        return matched
    title_lower = title.lower()
    for group, keywords in groups.items():
        for kw in keywords:
            if kw.lower() in title_lower:
                matched.append(group)
                break  # 一个标题在一个分组只算一次
    return matched


def aggregate_by_keyword(news_list: List[dict], limit_per_group: int = 10) -> Tuple[Dict, dict]:
    """
    关键词词频聚合（两级）
    返回: ({大类: {小类: [新闻列表]}}, 统计信息)
    """
    groups = load_keyword_groups()

    # 按 [大类][小类] 归类新闻
    flat_grouped = defaultdict(list)  # {小类路径: [news]}
    flat_counter = Counter()

    for item in news_list:
        title = item.get('title', '')
        matched_groups = classify_title(title, groups)
        for group in matched_groups:
            flat_grouped[group].append(item)
            flat_counter[group] += 1

    # 限制每组数量
    flat_limited = {}
    for group, items in flat_grouped.items():
        flat_limited[group] = items[:limit_per_group]

    # 按大类聚合
    category_grouped = defaultdict(dict)  # {大类: {小类: [news]}}
    category_counter = defaultdict(dict)  # {大类: {小类: count}}

    for group_path, items in flat_limited.items():
        parent, sub = parse_category_path(group_path)
        category_grouped[parent][sub] = items
        category_counter[parent][sub] = flat_counter[group_path]

    # 按 8 大类固定顺序排
    sorted_categories = {}
    for cat in CATEGORY_ORDER:
        if cat in category_grouped:
            # 子类按命中数降序排
            sorted_subs = dict(sorted(
                category_grouped[cat].items(),
                key=lambda x: -category_counter[cat][x[0]]
            ))
            sorted_categories[cat] = sorted_subs

    # 其他未在 8 大类列表里的（防御性）
    for cat in category_grouped:
        if cat not in sorted_categories:
            sorted_subs = dict(sorted(
                category_grouped[cat].items(),
                key=lambda x: -category_counter[cat][x[0]]
            ))
            sorted_categories[cat] = sorted_subs

    stats = {
        'total_news': len(news_list),
        'total_categories': len(sorted_categories),
        'total_subgroups': len(flat_counter),
        'matched_news': sum(flat_counter.values()),
        'subgroup_counts': dict(flat_counter),
        'category_counts': {cat: sum(category_counter[cat].values()) for cat in sorted_categories},
        'time_str': time.strftime('%Y-%m-%d %H:%M:%S'),
    }

    return sorted_categories, stats


# 预编译正则以提速
_COMPILED_GROUPS = None
def _get_compiled_groups():
    global _COMPILED_GROUPS
    if _COMPILED_GROUPS is None:
        groups = load_keyword_groups()
        compiled = {}
        for group, keywords in groups.items():
            # 用 | 连接所有关键词（中文不区分大小写）
            pattern = '|'.join(re.escape(kw) for kw in keywords)
            compiled[group] = re.compile(pattern, re.IGNORECASE)
        _COMPILED_GROUPS = compiled
    return _COMPILED_GROUPS


def classify_title_fast(title: str) -> List[str]:
    """快速版本：用预编译正则"""
    if not title:
        return []
    compiled = _get_compiled_groups()
    matched = []
    for group, pattern in compiled.items():
        if pattern.search(title):
            matched.append(group)
    return matched


# ──────────────────────────────
# 渲染成 HTML（按 8 大类聚合）
# ──────────────────────────────
def render_keywords_html(news_list: List[dict], limit_per_group: int = 10) -> str:
    """
    渲染关键词聚合为 HTML 报告（两级结构）
    """
    categories, stats = aggregate_by_keyword(news_list, limit_per_group)

    time_str = stats['time_str']
    total_cats = stats['total_categories']
    total_subs = stats['total_subgroups']
    total_matched = stats['matched_news']

    sections_html = ''
    if categories:
        # 按 CATEGORY_ORDER 顺序渲染
        for category, sub_dict in categories.items():
            cat_count = stats['category_counts'].get(category, 0)
            subs_html = ''
            for sub, items in sub_dict.items():
                sub_count = stats['subgroup_counts'].get(f'{category}/{sub}', 0)
                items_html = ''
                for i, item in enumerate(items, 1):
                    title = item.get('title', '')
                    time_s = item.get('time', '')[:16]
                    source = item.get('source_name', '') or item.get('source_id', '')
                    url = item.get('url', '')
                    link_target = f' target="_blank"' if url else ''
                    link_href = f' href="{url}"' if url else ''
                    items_html += f'''<div class="item">
  <span class="num">{i}.</span>
  <a class="title"{link_href}{link_target}>{title}</a>
  <span class="meta">[{source}] {time_s}</span>
</div>'''

                subs_html += f'''<div class="subgroup collapsed" onclick="toggleSubgroup(this)">
  <div class="subgroup-header">
    <span class="subgroup-name">{sub}</span>
    <span style="display:flex;align-items:center;">
      <span class="subgroup-count">{sub_count}条</span>
      <span class="subgroup-arrow">▼</span>
    </span>
  </div>
  <div class="subgroup-items">{items_html}</div>
</div>'''

            sections_html += f'''<div class="category">
  <div class="category-header">
    <span class="category-name">{category}</span>
    <span class="category-count">{cat_count}条 · {len(sub_dict)} 子类</span>
  </div>
  <div class="category-items">
    <div class="subgroups-grid">{subs_html}</div>
  </div>
</div>'''
    else:
        sections_html = '<div class="empty">暂无匹配的关键词分组</div>'

    # 顶部统计卡片
    stats_cards = ''
    for cat in CATEGORY_ORDER:
        cnt = stats['category_counts'].get(cat, 0)
        stats_cards += f'''<div class="stat-card">
  <div class="stat-name">{cat}</div>
  <div class="stat-value">{cnt}</div>
</div>'''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>关键词词频聚合 - 知几察微</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
    background: #f8fafc;
    color: #1f2937;
    line-height: 1.6;
    padding: 20px;
}}
.container {{ max-width: 900px; margin: 0 auto; }}
.header {{
    background: linear-gradient(135deg, #10b981 0%, #047857 100%);
    color: white;
    padding: 24px 28px;
    border-radius: 12px;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(16, 185, 129, 0.2);
}}
.header h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 8px; }}
.header .meta {{ font-size: 13px; opacity: 0.9; }}
.stats {{
    background: #ecfdf5;
    color: #047857;
    padding: 12px 20px;
    border-radius: 8px;
    margin-bottom: 16px;
    font-size: 13px;
}}
.stat-cards {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 16px;
}}
.stat-card {{
    background: white;
    padding: 14px;
    border-radius: 8px;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}}
.stat-name {{ color: #6b7280; font-size: 12px; margin-bottom: 6px; }}
.stat-value {{ color: #047857; font-size: 22px; font-weight: 700; }}
.category {{
    background: white;
    border-radius: 12px;
    margin-bottom: 16px;
    overflow: hidden;
    box-shadow: 0 2px 6px rgba(0,0,0,0.08);
}}
.category-header {{
    background: linear-gradient(135deg, #10b981 0%, #059669 100%);
    color: white;
    padding: 14px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-weight: 600;
}}
.category-name {{ font-size: 16px; }}
.category-count {{ background: rgba(255,255,255,0.2); padding: 3px 12px; border-radius: 12px; font-size: 12px; }}
.category-items {{ padding: 14px 18px; }}
.subgroups-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-bottom: 12px;
}}
.subgroup {{
    background: #f8fafc;
    border-radius: 8px;
    overflow: hidden;
    border-left: 3px solid #10b981;
}}
.subgroup.collapsed .subgroup-items {{ display: none; }}
.subgroup-header {{
    cursor: pointer;
    user-select: none;
    display: flex;
    align-items: center;
    justify-content: space-between;
    transition: background 0.2s;
}}
.subgroup-header:hover {{ background: #ecfdf5; }}
.subgroup-arrow {{
    margin-left: 6px;
    font-size: 10px;
    color: #047857;
    transition: transform 0.2s;
}}
.subgroup.collapsed .subgroup-arrow {{ transform: rotate(-90deg); }}
.subgroup-header {{
    background: white;
    padding: 8px 14px;
    border-bottom: 1px solid #e5e7eb;
}}
.subgroup-name {{ font-size: 14px; font-weight: 600; color: #047857; }}
.subgroup-count {{ background: #d1fae5; color: #047857; padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }}
.subgroup-items {{ padding: 8px 14px; }}
.item {{
    display: flex;
    align-items: baseline;
    padding: 6px 0;
    border-bottom: 1px solid #f1f5f9;
    gap: 8px;
}}
.item:last-child {{ border-bottom: none; }}
.num {{
    flex-shrink: 0;
    color: #94a3b8;
    font-size: 12px;
    min-width: 24px;
}}
.title {{
    flex: 1;
    font-size: 14px;
    color: #1f2937;
    text-decoration: none;
    line-height: 1.5;
}}
.title:hover {{ color: #047857; text-decoration: underline; }}
.meta {{
    flex-shrink: 0;
    font-size: 11px;
    color: #94a3b8;
}}
.empty {{
    text-align: center;
    padding: 40px;
    color: #9ca3af;
    background: white;
    border-radius: 10px;
}}
footer {{ text-align: center; color: #94a3b8; font-size: 12px; margin-top: 30px; padding: 16px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🏷️ 关键词词频聚合</h1>
    <div class="meta">生成时间: {time_str} | 大类: {total_cats} 个 | 子类: {total_subs} 个</div>
  </div>
  <div class="stats">
    📊 总新闻: {stats["total_news"]} 条 | 命中关键词: {total_matched} 次
  </div>
  <div class="stat-cards">{stats_cards}</div>
  {sections_html}
  <footer>知几察微 · jeeseek.top · 关键词词频聚合 · 8 大类版</footer>
</div>

<script>
function toggleSubgroup(el) {{
  el.classList.toggle("collapsed");
}}
</script>
</body>
</html>'''

    return html
