# jeeseek.top → Zeabur Free 部署

**目标**：迁移 jeeseek 从 fly.io 到 Zeabur Free（零扣费风险）

## ⚠️ 重要前提

- ✅ **月费 $0**，**不需要信用卡**，**零扣费风险**
- ⚠️ **不支持自定义域名**（zeeseek.top 这种），用 `jeeseek.zeabur.app` 子域名
- ⚠️ **闲置休眠**（spin down），跟 Render 类似
- ⚠️ **48h 日志保留**（debug 短）
- ⚠️ **数据临时盘**（重启 SQLite 库会清空，但 news 可重新抓）
- ⚠️ **隐性流量上限**（未量化，zeabur 文档未明示）

## 部署步骤

### 1. 花总注册 Zeabur

1. 打开 https://zeabur.com
2. 点 **Sign Up**（GitHub OAuth 最快，5 分钟）
3. 登录后进入 dashboard

### 2. 创建项目 + 部署

1. 点 **Create Project** → 选区域（**Asia** 区域最近）
2. 点 **Add Service** → **GitHub**
3. 第一次授权 GitHub，选择 `1425338838-byte/jeeseek-fly` 仓库（要先把本地代码 push 上去）
4. Zeabur 自动检测 Python + Dockerfile，**点 Deploy**
5. 等待 1-3 分钟，部署完成

### 3. 配环境变量（Secrets）

进 service → **Configuration** → **Environment Variables**，添加：

```
AGNES_API_KEY=sk-SV2VDR3QqJ9GhYZbDp4KxWfLtNcMo7n8mBb1vCxYhE5jPzTaQ6l0u3sR2kF9dG-X5nLFQ
TENCENT_TMT_SECRET_ID=AKIDTy...PUPC
TENCENT_TMT_SECRET_KEY=...
TENCENT_TMT_REGION=ap-shanghai
FETCH_INTERVAL=30
LISTEN_PORT=8080
```

⚠️ **不要 commit `.env` 到 GitHub**（已经被 .gitignore 排除）

### 4. 验证

部署完成后，Zeabur 分配子域名 `https://jeeseek.zeabur.app`：

1. 浏览器打开 → 应看到 jeeseek 主页
2. 测 4 个 tab：国内/海外/纪要/AI 报告
3. 测 AI 翻译（海外 tab 标题应该中文）
4. 观察 24h 看稳定性

### 5. 跑 2-3 天观察

- 流量是否超隐性上限
- spin down 后访问是否稳定
- AI 翻译是否一直工作

## 关键配置

| 项 | 值 |
|---|---|
| Region | Asia（Zeabur 默认最近的）|
| Plan | Free ($0/mo) |
| Memory | 默认（未量化）|
| Cold start | ~30 秒（首次部署）|
| Spin down | 是（类似 Render 15 分钟）|

## 与 fly.io 的差异

| 项 | fly.io | Zeabur Free |
|---|---|---|
| 月费 | ~$1.94（256MB + CPU 计费）| **$0** |
| 自定义域名 | ✅ jeeseek.top | ❌ 子域名 only |
| Spin down | 否（256MB 一直跑）| ✅ 会休眠 |
| 数据持久化 | ✅ volumes | ❌ 临时盘 |
| 日志保留 | 完整 | 48h |

## 备份策略

⚠️ **Zeabur Free 没有持久化**，重启 = SQLite 清空：

- 新闻数据是 RSS 抓取来的，重启后**自动重新抓**
- AI 翻译 cache 重启清零，但会**重新翻译**
- AI 报告（/app/reports/）丢失 → **建议定期手动备份**到 F 盘

**定期备份脚本**（可选）：
```bash
# 在花总本地跑（假设花总有 access token）
curl -o reports_backup.zip "https://jeeseek.zeabur.app/api/reports/download"
```

## 文件说明

- `Dockerfile` - Zeabur 自动识别，python:3.11-slim 基础镜像
- `requirements.txt` - Python 依赖
- `server.py` - 应用主入口（已兼容 PORT 环境变量）
- `translator.py` - 腾讯云翻译（只读环境变量）
- `ai_analyzer.py` - AI 报告生成
- `ai_radar.py` - AI 研判（256MB 模式注释掉）
- `keywords.py` - 关键词聚合
- `market_data.py` - 市场数据
- `static/` - 前端静态文件

## 注意事项

1. **环境变量**：从 F:\openclaw\本地知识库\jeeseek-fly-deploy\.env 拷贝（删 .env 的 GitHub 暴露风险）
2. **数据采集**：RSS 抓取需要外网，Zeabur 默认有外网
3. **HTTPS**：Zeabur 自动用 Let's Encrypt 签证书（子域名）
4. **数据持久化**：**无**，重启清空，靠 RSS 重新抓

## 应急方案

如果 Zeabur Free 不稳定：
- **Plan B**：Northflank Free Tier（绑卡，256-512MB，不 spin down）
- **Plan C**：fly.io 256MB（绑虚拟卡，$1.94/月）
- **Plan D**：回退到老 AWS 服务器 18.138.236.151（已停）

## 当前状态

- ✅ 代码已准备好（Dockerfile + server.py 已改 Zeabur 兼容）
- ⏳ 等待花总：注册 Zeabur + push 到 GitHub + 配 secrets