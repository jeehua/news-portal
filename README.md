# jeeseek — 知几察微 v3（部署到 Zeabur Free）

**当前部署目标**：Zeabur Free（$0/月，零扣费风险）

⚠️ **不支持 jeeseek.top 自定义域名**（Zeabur Free 限制），用 `jeeseek.zeabur.app` 子域名

详细部署步骤见 [ZEABUR_DEPLOY.md](./ZEABUR_DEPLOY.md)

## 历史部署（已废弃）

- ~~fly.io 256MB~~ → ❌ 9-1 晚上扣费 $0.08，9-2 早停机
- ~~AWS 18.138.236.151~~ → ❌ 已停

## 快速部署（Zeabur Free）

1. 花总去 https://zeabur.com 注册（GitHub OAuth，5 分钟）
2. 花总把本地代码 push 到 GitHub repo `1425338838-byte/jeeseek-fly`
3. Zeabur dashboard → Create Project → Add Service → GitHub → 选 repo → Deploy
4. 在 Configuration 里设环境变量（见 ZEABUR_DEPLOY.md）
5. 部署完成后访问 `https://jeeseek.zeabur.app`（自动分配子域名）

## 关键文件

- `Dockerfile` - Zeabur 自动识别（python:3.11-slim）
- `ZEABUR_DEPLOY.md` - 详细部署文档
- `server.py` - 主应用（已读 PORT 环境变量）
- `requirements.txt` - Python 依赖
- `translator.py` - 腾讯云翻译（只读环境变量）
- `ai_analyzer.py` - AI 报告生成
- `ai_radar.py` - AI 研判（256MB 模式注释掉）
- `keywords.py` - 关键词聚合
- `market_data.py` - 市场数据
- `static/` - 前端静态文件
- `.env` - **不 commit 到 GitHub**（含 secrets）

## Zeabur Free 限制

| 项 | 限制 |
|---|---|
| 月费 | $0 |
| 信用卡 | 不需要 |
| 自定义域名 | ❌ 不支持（用 zeabur.app 子域名）|
| Spin down | ✅ 会休眠（类似 Render） |
| 日志保留 | 48h |
| 数据持久化 | ❌ 临时盘（重启 SQLite 清空） |
| 流量上限 | 隐性 |

## 备案绕过原理

- Zeabur 境外节点（亚洲或欧美），**不在中国大陆**，**不需要工信部备案**
- DNS 通过 Cloudflare 解析，不受国内 DNS 污染/劫持
- HTTPS 自动证书

## 关键时间线

- 2026-09-02：决定迁移到 Zeabur Free（fly.io 扣费问题）
- 2026-09-02 早：fly.io 机器已停（scale count 0）
- 2026-09-02 中：代码改 Zeabur 兼容（PORT=8080）
- 2026-09-02：等花总注册 Zeabur + push GitHub