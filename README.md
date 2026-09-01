# jeeseek.top on fly.io

把 jeeseek 部署到 fly.io 免费层（新加坡节点），绕过腾讯云备案拦截。

## 部署步骤

### 1. 安装 flyctl CLI（花总本地或我代操作）
```bash
# Windows (PowerShell)
iwr https://fly.io/install.ps1 -useb | iex

# 登录
flyctl auth login
```

### 2. 创建 app
```bash
flyctl apps create jeeseek-fly --region sin
flyctl volumes create jeeseek_data --size 1 --region sin
```

### 3. 设置环境变量
```bash
flyctl secrets set \
  AGNES_API_KEY="sk-..." \
  TENCENT_TMT_SECRET_ID="..." \
  TENCENT_TMT_SECRET_KEY="..." \
  TAVILY_API_KEY="..." \
  FETCH_INTERVAL=30
```

### 4. 部署
```bash
flyctl deploy --remote-only
```

### 5. 绑定域名
```bash
flyctl certs create jeeseek.top
flyctl certs create www.jeeseek.top
```

### 6. DNS 切换（DNSPod）
- 删 A 记录 `123.207.232.102`
- 加 A 记录 → fly.io 给的 IP（`flyctl info` 查看）

## 关键配置

- **Region**: sin (Singapore) - 距离国内 50-80ms
- **Memory**: 256MB (free tier max)
- **Cold start**: ~5s（首次部署）
- **Free quota**: 3 shared-cpu-1x VMs, 160GB egress/month

## 文件说明

- `fly.toml` - fly.io 主配置
- `Dockerfile` - 镜像构建
- `fly.volumes.toml` - 数据持久化卷
- `requirements.txt` - Python 依赖
- `server.py` - 应用主入口
- `static/` - 前端静态文件

## 注意事项

1. **环境变量**：从 E:\openclaw\本地知识库\jeeseek_latest\.env 拷贝
2. **数据采集**：RSS 抓取需要外网，fly.io 新加坡节点默认有外网
3. **HTTPS**：fly.io 自动用 Let's Encrypt 签证书
4. **数据持久化**：SQLite db 必须挂载到 volume，否则重启丢数据

## 当前状态

- ✅ 代码准备好（E:\openclaw\本地知识库\jeeseek_fly\）
- ⏳ 等待花总：fly.io 账号（已注册）
- ⏳ 等待花总：flyctl CLI 安装
- ⏳ 等待花总：执行部署命令

## 备案绕过原理

- fly.io 新加坡节点，**不在中国大陆**，**不需要工信部备案**
- DNS A 记录直接指向 fly.io IP
- 域名解析在境外，不受国内 DNS 污染/劫持
- fly.io 自带 Cloudflare CDN 加速
