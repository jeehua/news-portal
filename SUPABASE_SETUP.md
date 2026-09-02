# Supabase 注册 + 配置（Render Hobby 必备）

## 1. 注册

1. 打开 https://supabase.com
2. 点 **Start your project**（GitHub OAuth 最快，5 分钟）

## 2. 创建项目

1. 登录后 dashboard 点 **New Project**
2. 填：
   - **Name**: `jeeseek-db`（或任意名字）
   - **Database Password**: **自己设一个强密码**（**重要！后面要用**）
   - **Region**: **Singapore**（**离国内最近**，aws-ap-southeast-1）
3. 点 **Create new project**，等 1-2 分钟初始化

## 3. 拿 DATABASE_URL

项目创建完成后：

1. 进项目 → 左侧 **Settings** ⚙️
2. 点 **Database**
3. 滚到 **Connection string** section
5. 选 **URI** tab
6. 拷贝完整连接串，**把 [YOUR-PASSWORD] 换成你刚才设的密码**

完整 URL 格式：
```
postgresql://postgres.[project-ref]:YOUR_PASSWORD@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
```

或者**直接连接的格式**：
```
postgresql://postgres:YOUR_PASSWORD@db.[project-ref].supabase.co:5432/postgres
```

**两种格式都行**，pooler 是 PgBouncer 池化版（推荐生产）。

## 4. 给研算子

把完整的 DATABASE_URL 发我，我加到 Render 环境变量：
- key: `DATABASE_URL`
- value: 上面拷贝的完整 URL

## 5. 验证

Render 部署完成后，Supabase dashboard → **Table Editor** 会看到 jeeseek 创建的 3 张表：
- `visit_logs`（访问日志）
- `news_clicks`（新闻点击）
- `source_stats`（数据源统计）

## Supabase 免费额度

- **Postgres 数据库**：500MB（够 jeeseek 用一辈子）
- **Storage**：1GB
- **带宽**：5GB/月（足够）
- **API 请求**：无限（够用）

## ⚠️ 重要

1. **强密码**：Database Password 必须记住，丢了没法重置数据库
2. **不要 commit DATABASE_URL 到 GitHub**：.env 已被 .gitignore 排除，但 Render secrets 里也要小心
3. **Region 一定选 Singapore**：supabase.co（其它 region 国内访问慢）

## 完成后告诉研算子

只需给我 DATABASE_URL 一行，我处理剩下的。

---

**快捷链接**：
- 注册：https://supabase.com
- 文档：https://supabase.com/docs
- Python 客户端（备用）：https://supabase.com/docs/reference/python/introduction