// 知几察微 v3.7 — jeeseek.top 前端
// 2026-08-27：加搜索 + 纪要无限下拉 + 关键词高亮 + cache-control 已禁用（nginx 不缓存）

const STATE = {
  domestic: { items: [], filter: 'all', lastIds: new Set(), search: '' },
  overseas: { items: [], filter: 'all', lastIds: new Set(), search: '' },
  minutes: {
    items: [], rendered: 0, selectedId: null, autoFollow: true,
    search: '', hasMore: true, loading: false,
  },
};

const PAGE_SIZE = 30;        // 纪要每页 30 条
const SEARCH_DEBOUNCE = 350; // ms

const $ = (id) => document.getElementById(id);

function escHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[c]);
}
// 别名：v4 AI 报告模块统一用 escapeHtml（保持命名一致）
const escapeHtml = escHtml;

// 标题中搜索词高亮
function highlightTitle(title, keyword) {
  if (!keyword) return escHtml(title);
  const safeTitle = escHtml(title);
  const safeKw = escHtml(keyword).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return safeTitle.replace(new RegExp(safeKw, 'gi'), m => `<mark style="background:#fef08a;padding:0 2px;border-radius:2px;">${m}</mark>`);
}

// ──── chip 切换 ────
['domestic-filters', 'overseas-filters'].forEach(fid => {
  const cat = fid.split('-')[0];  // domestic / overseas
  $(fid).addEventListener('click', e => {
    const btn = e.target.closest('.source-filter-btn');
    if (!btn) return;
    $(fid).querySelectorAll('.source-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    STATE[cat].filter = btn.dataset.source;
    _resetListContainer(cat);
    renderList(cat);
  });
});

// ──── 列表渲染（国内/海外） ────
function _resetListContainer(cat) {
  const listEl = $(`${cat}-feed`);
  listEl.innerHTML = '';
  listEl.dataset.rendered = '0';
}

function renderList(cat) {
  const s = STATE[cat];
  let filtered = s.filter === 'all' ? s.items : s.items.filter(it => it.source_id === s.filter);
  if (s.search) {
    const kw = s.search.toLowerCase();
    filtered = filtered.filter(it => (it.title || '').toLowerCase().includes(kw));
  }
  $(`${cat}-count`).textContent = `(${filtered.length}/${s.items.length})`;

  const listEl = $(`${cat}-feed`);
  if (filtered.length === 0) {
    const msg = s.search ? `没找到 "${s.search}" 相关新闻` : '暂无新闻';
    listEl.innerHTML = `<div class="empty-state">${msg}</div>`;
    listEl.dataset.rendered = '0';
    return;
  }

  const rendered = Number(listEl.dataset.rendered || 0);
  if (rendered >= filtered.length) return;

  const newOnes = filtered.slice(rendered);
  const html = newOnes.map((it) => {
    const isNew = !s.lastIds.has(it.id);
    s.lastIds.add(it.id);
    const tier1Class = it.tier1 ? ' tier1' : '';
    const tier1Badge = it.tier1 ? '<span class="tier1-badge">重要</span>' : '';
    const sourceClass = cat === 'domestic' ? 'source-domestic' : 'source-overseas';
    const url = it.url || '#';
    const time = it.time || '';
    return `<a href="${escHtml(url)}" target="_blank" rel="noopener" class="news-item${tier1Class}${isNew ? ' new' : ''}">
      <div class="news-title">${tier1Badge}${highlightTitle(it.title, s.search)}</div>
      <div class="news-meta"><span class="source-tag ${sourceClass}">${escHtml(it.source_name)}</span><span>${escHtml(time)}</span></div>
    </a>`;
  }).join('');

  listEl.insertAdjacentHTML('beforeend', html);
  listEl.dataset.rendered = String(filtered.length);
}

// ──── 机构纪要 ────
function _resetMinutes() {
  STATE.minutes.items = [];
  STATE.minutes.rendered = 0;
  STATE.minutes.hasMore = true;
  $('institutional-items').innerHTML = '';
  $('minutes-load-status').textContent = '下拉加载更多 ↓';
  $('minutes-load-status').className = 'load-status';
}

async function loadMinutesPage(reset = false) {
  if (STATE.minutes.loading) return;
  if (!reset && !STATE.minutes.hasMore) return;
  
  STATE.minutes.loading = true;
  const statusEl = $('minutes-load-status');
  if (statusEl) {
    statusEl.textContent = '加载中…';
    statusEl.classList.add('loading');
  }
  
  try {
    const offset = reset ? 0 : STATE.minutes.rendered;
    const params = new URLSearchParams({
      category: 'minutes',
      offset: String(offset),
      limit: String(PAGE_SIZE),
    });
    if (STATE.minutes.search) params.set('q', STATE.minutes.search);
    
    const res = await fetch('/api/news?' + params);
    const data = await res.json();
    
    if (reset) {
      STATE.minutes.items = data.items;
      STATE.minutes.rendered = 0;
      $('institutional-items').innerHTML = '';
      STATE.minutes.autoFollow = true; // 搜索时也跟随最新
    } else {
      STATE.minutes.items = STATE.minutes.items.concat(data.items);
    }
    
    STATE.minutes.hasMore = data.has_more;
    renderMinutes();
    
    if (statusEl) {
      if (STATE.minutes.hasMore) {
        statusEl.textContent = `已加载 ${STATE.minutes.rendered}/${data.total} · 下拉加载更多 ↓`;
        statusEl.className = 'load-status';
      } else {
        statusEl.textContent = `已加载全部 ${data.total} 条`;
        statusEl.className = 'load-status end';
      }
    }
  } catch (e) {
    console.error('loadMinutes failed:', e);
    if (statusEl) {
      statusEl.textContent = '加载失败，重试中…';
      statusEl.className = 'load-status';
    }
  } finally {
    STATE.minutes.loading = false;
  }
}

function renderMinutes() {
  const s = STATE.minutes;
  const listEl = $('institutional-items');
  
  // 增量渲染
  const to = s.rendered + (STATE.minutes._pendingDelta || 0);
  if (s.items.length > s.rendered) {
    const fresh = s.items.slice(s.rendered);
    fresh.forEach(it => {
      const div = document.createElement('div');
      div.className = 'inst-item';
      div.dataset.id = it.id;
      div.innerHTML = `
        <div class="inst-item-title">${highlightTitle(it.title, s.search)}</div>
        <div class="inst-item-time">${escHtml(it.time || '')}</div>
      `;
      div.onclick = () => selectMinute(div, it);
      listEl.appendChild(div);
    });
    s.rendered = s.items.length;
  }
  
  $('institutional-count').textContent = `(${s.items.length})`;
  
  // 自动跟随最新（选最后一条）
  if (s.autoFollow && s.items.length > 0) {
    const lastItem = listEl.querySelector('.inst-item:last-child');
    if (lastItem) {
      const id = lastItem.dataset.id;
      s.selectedId = id;
      listEl.querySelectorAll('.inst-item').forEach(el => el.classList.toggle('active', el.dataset.id === id));
      renderMinuteDetail(id);
    }
  }
}

function selectMinute(el, item) {
  STATE.minutes.autoFollow = false;
  STATE.minutes.selectedId = item.id;
  $('institutional-items').querySelectorAll('.inst-item').forEach(i => i.classList.remove('active'));
  el.classList.add('active');
  renderMinuteDetail(item.id);
}

function renderMinuteDetail(id) {
  const it = STATE.minutes.items.find(i => i.id === id);
  if (!it) return;
  const summary = it.content || it.title || '（暂无正文）';
  const safeText = escHtml(summary);
  const paragraphs = safeText.split(/\n+/).filter(p => p.trim()).map(p => `<p>${p.trim()}</p>`).join('');
  $('institutional-content-area').innerHTML = `
    <h2>${highlightTitle(it.title, STATE.minutes.search)}</h2>
    <div class="content-body">${paragraphs || '<p>暂无内容</p>'}</div>
  `;
}

// 滚动监听 - 无限下拉
let minutesSentinel = null;
function _setupMinutesSentinel() {
  const listEl = $('institutional-items');
  if (!listEl) return;
  
  // 创建 sentinel 元素
  if (!minutesSentinel) {
    minutesSentinel = document.createElement('div');
    minutesSentinel.id = 'minutes-sentinel';
    minutesSentinel.style.height = '1px';
  }
  if (minutesSentinel.parentNode !== listEl) {
    listEl.appendChild(minutesSentinel);
  }
  
  if (!_minutesObserver) {
    _minutesObserver = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) {
        loadMinutesPage(false);
      }
    }, { root: listEl, rootMargin: '50px' });
    _minutesObserver.observe(minutesSentinel);
  }
}
let _minutesObserver = null;

// ──── SSE ────
function connectSSE() {
  const es = new EventSource('/events');
  es.addEventListener('snapshot', e => applyUpdate(JSON.parse(e.data)));
  es.addEventListener('update', e => applyUpdate(JSON.parse(e.data)));
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); };
}

function applyUpdate(data) {
  if (data.domestic) {
    STATE.domestic.items = data.domestic;
    _resetListContainer('domestic');
    STATE.domestic.lastIds = new Set();
    renderList('domestic');
  }
  if (data.overseas) {
    STATE.overseas.items = data.overseas;
    _resetListContainer('overseas');
    STATE.overseas.lastIds = new Set();
    renderList('overseas');
  }
  if (data.minutes) {
    // 后端推送的全量分钟数 → 重置后只显示第一页（除非搜索中）
    if (!STATE.minutes.search) {
      STATE.minutes.items = data.minutes;
      STATE.minutes.rendered = Math.min(data.minutes.length, PAGE_SIZE);
      $('institutional-items').innerHTML = '';
      // 增量渲染前 PAGE_SIZE 条
      const first = data.minutes.slice(0, STATE.minutes.rendered);
      first.forEach(it => {
        const div = document.createElement('div');
        div.className = 'inst-item';
        div.dataset.id = it.id;
        div.innerHTML = `
          <div class="inst-item-title">${highlightTitle(it.title, '')}</div>
          <div class="inst-item-time">${escHtml(it.time || '')}</div>
        `;
        div.onclick = () => selectMinute(div, it);
        $('institutional-items').appendChild(div);
      });
      STATE.minutes.hasMore = data.minutes.length > PAGE_SIZE;
      $('institutional-count').textContent = `(${data.minutes.length})`;
      const statusEl = $('minutes-load-status');
      if (statusEl) {
        statusEl.textContent = STATE.minutes.hasMore ? `已加载 ${STATE.minutes.rendered}/${data.minutes.length} · 下拉加载更多 ↓` : `已加载全部 ${data.minutes.length} 条`;
        statusEl.className = STATE.minutes.hasMore ? 'load-status' : 'load-status end';
      }
      // 自动跟随（选中最新——数据的第一条，不是显示的最后一行）
      if (STATE.minutes.autoFollow && first.length > 0) {
        // 数据按时间倒序，第一条是最新的
        const latestItem = STATE.minutes.items[0];
        if (latestItem) {
          STATE.minutes.selectedId = latestItem.id;
          // 高亮最新项（如果当前可见）
          const itemEl = $('institutional-items').querySelector(`.inst-item[data-id="${latestItem.id}"]`);
          if (itemEl) itemEl.classList.add('active');
          renderMinuteDetail(latestItem.id);
        }
      }
      _setupMinutesSentinel();
    }
  }
  $('last-update').textContent = `最后更新: ${new Date().toLocaleTimeString('zh-CN')}`;
}

// ──── 手动刷新 ────
async function refreshAll() {
  // 分别 try/catch，防一个错全部 fail
  try {
    const domRes = await fetch('/api/news?category=domestic&limit=500&t=' + Date.now());
    if (!domRes.ok) throw new Error('domestic status ' + domRes.status);
    const dom = await domRes.json();
    if (dom.items && Array.isArray(dom.items)) {
      applyUpdate({ domestic: dom.items });
    }
  } catch (e) {
    console.warn('refreshAll domestic failed:', e.message);
  }
  try {
    const osRes = await fetch('/api/news?category=overseas&limit=500&t=' + Date.now());
    if (!osRes.ok) throw new Error('overseas status ' + osRes.status);
    const os = await osRes.json();
    if (os.items && Array.isArray(os.items)) {
      applyUpdate({ overseas: os.items });
    }
  } catch (e) {
    console.warn('refreshAll overseas failed:', e.message);
  }
  // 纪要仅首次加载
  if (!STATE.minutes.items || STATE.minutes.items.length === 0) {
    loadMinutesPage(true);
  }
}

// ──── 拖动分隔条 ────
const resizer = $('resizer');
const leftCol = $('left-column');
const rightCol = $('right-column');
let isResizing = false;
resizer.addEventListener('mousedown', (e) => {
  isResizing = true;
  resizer.classList.add('resizing');
  document.body.style.cursor = 'col-resize';
  e.preventDefault();
});
document.addEventListener('mousemove', (e) => {
  if (!isResizing) return;
  const rect = $('feed-page').getBoundingClientRect();
  const newLeftWidth = ((e.clientX - rect.left) / rect.width) * 100;
  const clamped = Math.max(20, Math.min(80, newLeftWidth));
  leftCol.style.flex = `${clamped}`;
  rightCol.style.flex = `${100 - clamped}`;
});
document.addEventListener('mouseup', () => {
  if (isResizing) {
    isResizing = false;
    resizer.classList.remove('resizing');
    document.body.style.cursor = '';
  }
});

// ──── Tab 切换 ────
function switchTab(tab) {
  console.log('[switchTab] called with:', tab, 'event:', event?.target?.textContent);
  const feedPage = $('feed-page');
  const reportPage = $('report-page');
  const keywordsPage = $('keywords-page');
  const radarPage = $('radar-page');
  const tabs = document.querySelectorAll('.main-tab');
  
  if (!feedPage || !reportPage || !keywordsPage) {
    console.error('[switchTab] missing page element', { feedPage, reportPage, keywordsPage });
    return;
  }
  
  // 高亮当前 tab（不使用 event.target 以免报错）
  tabs.forEach((t, i) => {
    t.classList.remove('active');
    const t1 = t.textContent.includes('推送') || t.textContent.includes('📰');
    const t2 = t.textContent.includes('关键词') || t.textContent.includes('🏷️');
    const t3 = t.textContent.includes('报告') || t.textContent.includes('📊');
    const t4 = t.textContent.includes('研判') || t.textContent.includes('🎯');
    if ((tab === 'feed' && t1) || (tab === 'keywords' && t2) || (tab === 'report' && t3) || (tab === 'radar' && t4)) {
      t.classList.add('active');
    }
  });
  
  // 隐藏所有
  feedPage.style.display = 'none';
  reportPage.style.display = 'none';
  keywordsPage.style.display = 'none';
  if (radarPage) radarPage.style.display = 'none';
  
  if (tab === 'report' || tab === 'keywords') {
    const targetPage = tab === 'report' ? reportPage : keywordsPage;
    const iframeId = tab === 'report' ? 'report-iframe' : 'keywords-iframe';
    targetPage.style.display = 'block';
    targetPage.style.height = 'calc(100vh - 120px)';  // 确保高度
    
    const iframe = $(iframeId);
    if (iframe) {
      // 给个 loading 提示，避免用户觉得卡死
      targetPage.innerHTML = '<div style="padding:60px;text-align:center;color:#64748b;font-size:14px;">⏳ 加载中…</div>' + 
        `<iframe id="${iframeId}" src="${tab === 'report' ? '/api/ai/report?t=' : '/api/ai/keywords?t='}${Date.now()}" 
                 style="width:100%;height:calc(100vh - 120px);border:none;display:none;" 
                 onload="this.style.display='block'; this.parentElement.querySelector('div').style.display='none'; console.log('[iframe] ${tab} loaded');"></iframe>`;
      console.log('[switchTab] ' + tab + ' iframe loading...');
    } else {
      console.error('[switchTab] no iframe:', iframeId);
    }
  } else if (tab === 'radar') {
    if (radarPage) {
      radarPage.style.display = 'block';
      loadRadar();
    }
  } else {
    feedPage.style.display = 'block';
  }
}

// ──── AI 研判 ────
async function loadRadar(force = 0) {
  console.log('[loadRadar] force=', force);
  const summaryEl = $('radar-summary-text');
  if (summaryEl) summaryEl.innerHTML = '加载中...';

  try {
    const url = force ? '/api/ai/radar?force=1' : '/api/ai/radar';
    console.log('[loadRadar] fetching', url);
    const resp = await fetch(url);
    console.log('[loadRadar] status:', resp.status);
    const data = await resp.json();
    console.log('[loadRadar] data:', data.success, data.data ? 'has data' : 'no data');

    if (!data.success || !data.data) {
      if (summaryEl) summaryEl.innerHTML = '⚠️ 分析失败，点击右上角刷新按钮重试';
      console.warn('[loadRadar] failed: success=' + data.success);
      return;
    }

    renderRadar(data.data, data.meta);
  } catch (err) {
    console.error('[loadRadar] failed:', err);
    if (summaryEl) summaryEl.innerHTML = '⚠️ 加载失败：' + (err.message || 'unknown');
  }
}

function renderRadar(data, meta) {
  console.log('[renderRadar]', data);

  // AI 状态提示（顶部）
  const aiStatus = data.ai_status || {};
  if (aiStatus.msg && aiStatus.code !== 'complete') {
    const banner = $('radar-ai-status-banner');
    if (banner) {
      banner.textContent = '⚠️ ' + (aiStatus.msg || '');
      banner.style.display = 'block';
    }
  } else {
    const banner = $('radar-ai-status-banner');
    if (banner) banner.style.display = 'none';
  }

  // 3 句话标签化
  const summary = data.summary_tagged || {};
  const summaryEl = $('radar-summary-text');
  if (summaryEl) {
    summaryEl.innerHTML = [
      summary.market_line && `<div style="margin:6px 0;"><strong style="color:#2563eb;">① 市场主线：</strong>${escapeHtml(summary.market_line)}</div>`,
      summary.expectation_change && `<div style="margin:6px 0;"><strong style="color:#7c3aed;">② 预期变化：</strong>${escapeHtml(summary.expectation_change)}</div>`,
      summary.tomorrow_verify && `<div style="margin:6px 0;"><strong style="color:#059669;">③ 明日验证：</strong>${escapeHtml(summary.tomorrow_verify)}</div>`,
    ].filter(Boolean).join('') || '暂无数据';
  }

  // 更新时间
  const updatedEl = $('radar-updated-at');
  if (updatedEl && meta?.time_str) {
    const pc = meta.price_context || {};
    updatedEl.innerHTML = `更新于 ${meta.time_str} · 价格数据 ${pc.status || '未知'}${pc.divergence_count > 0 ? ' · 检测到 ' + pc.divergence_count + ' 处背离' : ''}`;
  }

  // 4 张卡
  const cards = data.cards || {};
  renderMainLineCard('card-main-line', cards.main_line);
  renderExpectationCard('card-expectation-up', cards.expectation_up, 'up');
  renderExpectationCard('card-expectation-down', cards.expectation_down, 'down');
  renderPriceDivergenceCard('card-price-divergence', cards.price_divergence);

  // 主题雷达
  renderTopics(data.topic_radar || []);
}

function directionColor(direction) {
  if (direction === '偏多') return '#059669';
  if (direction === '偏空') return '#dc2626';
  return '#64748b';
}

function statusBadge(status) {
  const colorMap = {
    '刚出现': '#94a3b8', '待确认': '#f59e0b', '已确认': '#059669',
    '扩散中': '#2563eb', '过度拥挤': '#dc2626', '利好钝化': '#f97316',
    '利空钝化': '#f97316', '已失效': '#6b7280'
  };
  const color = colorMap[status] || '#64748b';
  return `<span class="radar-tag" style="background:${color}15;color:${color};border:1px solid ${color}40;">${escapeHtml(status || '—')}</span>`;
}

function signalBadge(level) {
  const colorMap = { 'A': '#2563eb', 'B': '#64748b', 'C': '#94a3b8' };
  const color = colorMap[level] || '#94a3b8';
  return `<span class="radar-tag" style="background:${color};color:white;font-weight:600;">信号 ${escapeHtml(level || '—')}</span>`;
}

function renderMainLineCard(elId, card) {
  const el = $(elId);
  if (!el) return;
  if (!card || Object.keys(card).length === 0) {
    el.innerHTML = '<div class="radar-card-header">🎯 市场主线</div><div class="radar-card-judgment">⚠️ 该卡片暂无数据</div>';
    return;
  }

  const dir = card.direction || '';
  const dirColor = directionColor(dir);

  el.innerHTML = `
    <div class="radar-card-header">🎯 市场主线</div>
    <div class="radar-card-title" style="color:${dirColor};">${escapeHtml(card.title || '—')}</div>
    <div class="radar-card-tags">
      <span class="radar-tag" style="background:${dirColor};color:white;font-weight:600;">${escapeHtml(dir || '—')}</span>
      ${statusBadge(card.event_status)}
      ${signalBadge(card.signal_level)}
      <span class="radar-tag">${escapeHtml(card.time_horizon || '—')}</span>
    </div>
    <div style="margin:8px 0;display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;">
      <div>信息质量：<b>${escapeHtml(card.info_quality || '—')}</b></div>
      <div>预期差：<b>${escapeHtml(card.expectation_gap || '—')}</b></div>
      <div>价格确认：<b>${escapeHtml(card.price_confirmation || '—')}</b></div>
      <div>拥挤风险：<b>${escapeHtml(card.crowding || '—')}</b></div>
      <div>主题阶段：<b>${escapeHtml(card.topic_stage || '—')}</b></div>
      <div>盈利影响：<b>${escapeHtml(card.earnings_relevance || '—')}</b></div>
    </div>
    ${renderEvidenceList('支持证据', card.supporting_evidence, '#059669')}
    ${renderEvidenceList('反向证据', card.counter_evidence, '#dc2626')}
    ${renderEvidenceList('验证条件', card.triggers, '#2563eb')}
    ${renderEvidenceList('失效条件', card.invalidation, '#f97316')}
    <div class="radar-card-judgment">🤖 ${escapeHtml(card.ai_judgment || '分析中...')}</div>
    ${card.expires_at ? `<div style="font-size:11px;color:#94a3b8;margin-top:6px;">预计失效：${escapeHtml(card.expires_at)}</div>` : ''}
  `;
}

function renderExpectationCard(elId, card, type) {
  const el = $(elId);
  if (!el) return;
  if (!card || Object.keys(card).length === 0) {
    const icon = type === 'up' ? '📈' : '📉';
    const headerText = type === 'up' ? '预期改善' : '预期恶化';
    el.innerHTML = `<div class="radar-card-header">${icon} ${headerText}</div><div class="radar-card-judgment">⚠️ 该卡片暂无数据</div>`;
    return;
  }

  const icon = type === 'up' ? '📈' : '📉';
  const headerText = type === 'up' ? '预期改善' : '预期恶化';
  const dirColor = directionColor(card.direction || '');

  el.innerHTML = `
    <div class="radar-card-header">${icon} ${headerText}</div>
    <div class="radar-card-title" style="color:${dirColor};">${escapeHtml(card.title || '—')}</div>
    <div class="radar-card-tags">
      <span class="radar-tag" style="background:${dirColor};color:white;font-weight:600;">${escapeHtml(card.direction || '—')}</span>
      ${signalBadge(card.signal_level)}
      <span class="radar-tag">${escapeHtml(card.time_horizon || '—')}</span>
    </div>
    <div class="radar-card-judgment" style="margin-bottom:8px;">💡 ${escapeHtml(card.current_judgment || '—')}</div>
    ${renderEvidenceList('证据', card.supporting_evidence, '#059669')}
    ${renderEvidenceList('反向证据', card.counter_evidence, '#dc2626')}
    ${renderEvidenceList('验证条件', card.triggers, '#2563eb')}
    ${renderEvidenceList('失效条件', card.invalidation, '#f97316')}
    <div class="radar-card-judgment">🤖 ${escapeHtml(card.ai_judgment || '分析中...')}</div>
  `;
}

function renderPriceDivergenceCard(elId, card) {
  const el = $(elId);
  if (!el) return;
  if (!card || Object.keys(card).length === 0) {
    el.innerHTML = '<div class="radar-card-header">⚖️ 新闻-价格背离</div><div class="radar-card-judgment">⚠️ 当前价格数据不可用,无法进行背离检测</div>';
    return;
  }

  const aiStatus = card.ai_status || {};
  const statusMsg = aiStatus.msg || '已纳入对比';
  const statusColor = aiStatus.code === 'complete' ? '#059669' : '#f59e0b';

  el.innerHTML = `
    <div class="radar-card-header">⚖️ 新闻-价格背离</div>
    <div class="radar-card-title">${escapeHtml(card.title || '未检测到背离')}</div>
    <div class="radar-card-tags">
      <span class="radar-tag" style="background:#f97316;color:white;font-weight:600;">${escapeHtml(card.divergence_type || '—')}</span>
      <span class="radar-tag">新闻 ${escapeHtml(card.news_direction || '—')}</span>
      <span class="radar-tag">价格 ${escapeHtml(card.price_reaction || '—')}</span>
      <span class="radar-tag">成交量 ${escapeHtml(card.volume_signal || '—')}</span>
    </div>
    ${renderEvidenceList('新闻事件', card.supporting_evidence, '#2563eb')}
    ${card.ai_judgment ? `<div class="radar-card-judgment">🤖 ${escapeHtml(card.ai_judgment)}</div>` : ''}
    ${renderEvidenceList('观察条件', card.triggers, '#059669')}
    ${renderEvidenceList('失效条件', card.invalidation, '#dc2626')}
    <div style="font-size:11px;color:${statusColor};margin-top:6px;">${escapeHtml(statusMsg)}</div>
  `;
}

function renderEvidenceList(title, items, color) {
  if (!items || !items.length) return '';
  return `
    <details style="margin:6px 0;">
      <summary style="font-size:12px;color:${color};cursor:pointer;user-select:none;font-weight:600;">
        ${escapeHtml(title)} (${items.length})
      </summary>
      <ul style="margin:4px 0 4px 18px;padding:0;font-size:12px;color:#475569;line-height:1.6;">
        ${items.map(it => `<li>${escapeHtml(it)}</li>`).join('')}
      </ul>
    </details>
  `;
}

function renderTopics(topics) {
  const listEl = $('radar-topic-list');
  if (!listEl) {
    console.warn('[renderTopics] listEl not found');
    return;
  }

  if (!topics.length) {
    listEl.innerHTML = '<div style="color:#94a3b8;padding:20px;">暂无主题数据</div>';
    return;
  }

  listEl.innerHTML = topics.map(t => {
    const dirColor = directionColor(t.direction);
    return `
      <div class="topic-card">
        <div class="topic-card-title" style="color:${dirColor};">${escapeHtml(t.topic || '—')}</div>
        <div class="topic-card-tags">
          <span class="radar-tag" style="background:${dirColor};color:white;font-weight:600;">${escapeHtml(t.direction || '—')}</span>
          ${statusBadge(t.event_status)}
          ${signalBadge(t.signal_level)}
          <span class="radar-tag">信息 ${escapeHtml(t.info_quality || '—')}</span>
          <span class="radar-tag">预期差 ${escapeHtml(t.expectation_gap || '—')}</span>
          <span class="radar-tag">价格 ${escapeHtml(t.price_confirmation || '—')}</span>
          <span class="radar-tag">拥挤 ${escapeHtml(t.crowding || '—')}</span>
        </div>
        <div class="topic-card-comment">💬 ${escapeHtml(t.comment || '—')}</div>
        ${t.expires_at ? `<div style="font-size:11px;color:#94a3b8;margin-top:4px;">预计失效：${escapeHtml(t.expires_at)}</div>` : ''}
      </div>
    `;
  }).join('');
}


async function refreshRadar() {
  console.log('[refreshRadar] 强制刷新');
  await loadRadar(1);
}

// ──── 搜索（debounce） ────
function debounce(fn, delay) {
  let t;
  return function(...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), delay);
  };
}

function setupSearch() {
  // 国内搜索
  const domSearch = $('domestic-search');
  if (domSearch) {
    const handler = debounce((v) => {
      STATE.domestic.search = v;
      _resetListContainer('domestic');
      renderList('domestic');
    }, SEARCH_DEBOUNCE);
    domSearch.addEventListener('input', e => handler(e.target.value.trim()));
  }
  
  // 海外搜索
  const osSearch = $('overseas-search');
  if (osSearch) {
    const handler = debounce((v) => {
      STATE.overseas.search = v;
      _resetListContainer('overseas');
      renderList('overseas');
    }, SEARCH_DEBOUNCE);
    osSearch.addEventListener('input', e => handler(e.target.value.trim()));
  }
  
  // 纪要搜索（服务端过滤）
  const minSearch = $('minutes-search');
  if (minSearch) {
    const handler = debounce((v) => {
      STATE.minutes.search = v;
      loadMinutesPage(true); // reset
    }, SEARCH_DEBOUNCE);
    minSearch.addEventListener('input', e => handler(e.target.value.trim()));
  }
}

// ──── 启动 ────
setupSearch();
connectSSE();
refreshAll();
// 兜底轮询（防 SSE 断了）
setInterval(refreshAll, 60000);

// AI 研判 - 启动时预加载 (不管用户切不切 tab)
console.log('[startup] loadRadar()');
loadRadar();
