# netease163 — 网易云音乐爬虫服务

借鉴两个老项目改造:
- **[163yinyue](https://github.com/d9g/163yinyue)** — 12 个爬虫模块 + sqlalchemy 存储
- **[NetCloud](https://github.com/Lyrichu/NetCloud)** — 登录模块 + 统一日志 + Helper 工具

底层使用 **[pyncm](https://github.com/greats3an/pyncm)** 库处理网易云 2020+ 加密接口（避免自己维护加密/风控对抗）。

## ✨ 核心特性

### 🌐 跟时间做朋友（v2 爬虫策略）

**问题**：早期爬虫"翻来覆去那 10 个歌手"——榜单 TopN 每日不变，关键词池 60 个太少。

**方案**：
- **优先级队列**：按 `(last_crawled_at, hot_score)` 排序，**老的优先重爬，新歌补充**
- **每天 100 首目标分配**：
  - 50% 重新更新（7 天前爬过的）—— 评论/点赞数变化
  - 30% 新歌（榜单 offset 滚动 + 关键词池扩展）
  - 20% 评论增量（已知热门歌的最新评论）
- **凌晨 02:00 全量热度统计**
- **跟时间做朋友**：每天 100 首覆盖"上个月没爬/上个月爬过但有新评论"的歌

### 🤖 LLM 评论质量分析（v3）

**痛点**：网易云评论里，70% 是"顶""好听""想家了"这种口水，但 5% 的高质量评论才真正是"神评论"。

**方案**：
- 批量提交给 LLM（25 条/批），按 0-5 星评分
- **0-1 星**：口水（"顶"/"好听"/单字）
- **2-3 星**：中等（表达感受但无深度）
- **4-5 星**：高质量（有故事/情感深度/文采）
- 自动回写 `comments.ai_score` / `ai_label` / `ai_reason`
- **30 分钟自动调度**，每次分析 500 条评论

### 🎨 首页排行

3 个端点 + 1 个管理端点：
- `/api/v1/rankings/hot-songs` — 热门歌曲（按评论数/点赞数）
- `/api/v1/rankings/hot-comments` — 神评论（按点赞数）
- `/api/v1/rankings/trending` — 24h 趋势（按评论增量）
- `POST /api/v1/rankings/run-hot-stats` — 手动触发全量热度统计

## 🛠️ 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | FastAPI + SQLAlchemy 2.0 + pyncm |
| 前端 | 单页 WebUI（HTML + 原生 JS，无框架） |
| 数据库 | SQLite（单文件） |
| LLM | Anthropic 兼容 API（Claude Sonnet） |
| 调度 | systemd service + 自定义 asyncio 循环 |

## 📦 部署

### systemd 服务

两个 unit 文件：
- `netease163.service` — 主 API 服务（FastAPI 9700 端口）
- `netease163-scheduler.service` — 自动调度（评论分析 + 爬虫轮询）

```bash
systemctl daemon-reload
systemctl enable --now netease163.service netease163-scheduler.service
```

### 配置文件

`.env`（**不要提交**）：
```
ANTHROPIC_API_KEY=<your-key>
```

`ANTHROPIC_BASE_URL` 默认走 `https://api.minimaxi.com/anthropic` 兼容端点，可改为其他兼容 Anthropic 协议的端点。

### 目录结构

```
netease163/
├── netease163/
│   ├── api/          # FastAPI 路由
│   ├── ai/           # LLM 评论质量分析
│   ├── spiders/      # 6 个爬虫模块 (song/comment/lyric/toplist/...)
│   ├── random_crawler/  # 跟时间做朋友 爬虫策略
│   ├── storage/      # SQLAlchemy 模型 + DB
│   └── utils/        # 日志/Helper 工具
├── webui/dist/       # 单页 WebUI 静态文件
├── scripts/          # 调度器脚本
├── data/             # SQLite DB + 日志
└── docs/             # 文档 + 截图
```

## 📊 自动调度策略

### 评论分析（30 分钟轮询）

| 配置项 | 值 | 理由 |
|--------|-----|------|
| 间隔 | 30 分钟 | 避开 API 频率限制 |
| 批量 | 25 条/批 | 单 prompt token 控制在 4k |
| 每轮评论数 | 500 条 | 20 批 + 间隔 ≈ 30s 完成 |
| 全天分析量 | ~24,000 条 | 48 次 × 500 |
| LLM 调用频次 | ~50 次/天 | 1 次/批 + 元数据 |

**风控安全**：单次 LLM 调用 ≈ 21 次 API（20 批 prompt + 1 metadata），30 分钟间隔 = 0.7 req/min，**远低于网易云 API 单 IP 1000 req/min 的限制**。

### 爬虫调度（90 分钟轮询）

| 配置项 | 值 | 理由 |
|--------|-----|------|
| 间隔 | 90 分钟 | 16 轮/天分散请求 |
| 每轮目标 | 7 首 | 100 首/天 ÷ 14 轮（留 buffer） |
| 跟时间做朋友分配 | 5 重爬 + 3 新歌 + 2 评论 | 50%/30%/20% |

## 📈 数据模型（9 张表）

- `songs` — 歌曲元数据（id/name/artists/album/pic_url）
- `comments` — 评论（含 `ai_score`/`ai_label`/`ai_reason` AI 评分）
- `lyrics` — 歌词
- `playlists` / `playlist_songs` — 歌单
- `artists` / `albums` — 歌手/专辑
- `crawl_priority` — 爬虫优先级队列
- `song_crawl_status` — 单曲爬取状态（last_crawled_at/crawl_count）
- `song_hot_stats` — 每日热度统计
- `keywords` — 关键词池（自扩展）
- `crawl_log` — 爬取日志

## 🎯 截图

### 桌面端

![WebUI Desktop](docs/screenshots/webui-desktop.png)

### 移动端

![WebUI Mobile](docs/screenshots/webui-mobile.png)

## 🤝 贡献

基于以下开源项目：
- [163yinyue](https://github.com/d9g/163yinyue) (MIT)
- [NetCloud](https://github.com/Lyrichu/NetCloud) (MIT)
- [pyncm](https://github.com/greats3an/pyncm) (MIT)

## 📄 License

MIT