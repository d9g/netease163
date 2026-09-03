# netease163 — 网易云音乐爬虫服务

**项目路径**：`/root/netease163/`
**未来域名**：`http://163.d9g.com.cn/` (待老杨配置 nginx/DNS)

借鉴两个老项目改造:
- **163yinyue** (d9g/163yinyue) — 12 个爬虫模块 + sqlalchemy 存储
- **NetCloud** (Lyrichu/NetCloud) — 登录模块 + 统一日志 + Helper 工具

底层使用 **pyncm** 库处理网易云 2020+ 加密接口 (避免自己维护加密/风控对抗)。

## 功能

### 12 个爬虫 (借鉴 163yinyue)
- 🎵 评论 (普通 + 热门)
- 📝 歌词
- 🎤 歌手信息
- 📋 歌单 (含分类)
- 📻 电台 (djradio)
- 🏆 排行榜
- 🔍 搜索 (歌曲/歌手/歌单/专辑)
- 💿 专辑

### 服务能力 (新)
- 🌐 **FastAPI HTTP 服务** (端口 9700) — REST API
- 💾 **SQLite 存储** (默认) / MySQL (可选)
- 🔐 **模拟登录** (借鉴 NetCloud) — 手机/邮箱/二维码
- 🛠️ **CLI** (typer) — 命令行入口

## 快速开始

```bash
# 1. 激活 venv (SOUL 铁律: 不放 /tmp)
source /root/.netease163-venv/bin/activate

# 2. CLI 模式
python -m netease163.cli lyric --id 1062642
python -m netease163.cli comment --id 1062642 --limit 10
python -m netease163.cli search --q "林俊杰"

# 4. 启动服务
python -m netease163.api.app
# 访问 http://127.0.0.1:9700/docs  (Swagger UI)
```

## API 示例

```bash
# 歌词
curl http://127.0.0.1:9700/api/v1/lyric/1062642

# 评论
curl "http://127.0.0.1:9700/api/v1/comment/1062642?limit=10"

# 搜索
curl "http://127.0.0.1:9700/api/v1/search?q=林俊杰&limit=3"

# 歌曲详情
curl http://127.0.0.1:9700/api/v1/song/1062642

# 歌单详情
curl http://127.0.0.1:9700/api/v1/playlist/2438161637
```

## 技术栈

| 组件 | 选型 | 借鉴 |
|---|---|---|
| 网易云 API | **pyncm** (含加密) | — |
| HTTP 客户端 | requests / httpx | — |
| CLI | typer | argparse (163yinyue) |
| 数据库 | SQLAlchemy + SQLite/MySQL | 163yinyue |
| Web | FastAPI | 新增 |
| 日志 | loguru | NetCloud Helper.get_logger |
| 登录 | pyncm.login | NetCloud NetCloudLogin (简化) |

## 跟原项目区别

| 项 | 163yinyue (原) | NetCloud (原) | netease163 (本项目) |
|---|---|---|---|
| 网易云 API | requests + 自写加密 | requests + pycrypto | **pyncm (内建加密)** |
| 部署 | 纯 CLI | 库 + demo | **CLI + FastAPI** |
| 存储 | MySQL 强制 | 本地文件 | **SQLite (默认) + MySQL** |
| 登录 | ❌ | ✅ | ✅ (pyncm 简化) |
| 依赖 | 3 个 | 7+ 个 | 8 个 (轻量) |

## 借鉴声明

- 163yinyue: © d9g (老杨自己), GPL/自由使用
- NetCloud: © lyrichu, GPL/自由使用
- pyncm: © gnuacgn@github, MIT

本项目仅作内部学习/工具用, 不对外发布。
