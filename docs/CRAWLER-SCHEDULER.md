# 爬虫调度器 v2 - 2026-09-07 升级

## 设计目标
- 早高峰 (10-12) 间隔 5-15s, 每小时 20-35 首
- 凌晨 (0-6) 间隔 15-35s, 每小时 4-6 首
- LLM 优先级: ai_score>=4 AND liked_count>=50 的歌优先重爬评论
- 预留并发参数 (默认 MAX_WORKERS=1)
- 风控降速: 连续 3 次 429 → 间隔 × 1.5, 连续 20 次成功 → × 0.95

## 7 段 profile (按小时)

| 时段 | profile | MIN/MAX | 每轮 | cron | 预估入歌/h |
|---|---|---|---|---|---|
| 0-6 深夜 | deep_night | 15-35s | 2 | `*/30 0-6` | 4-6 |
| 7-9 早盘前 | pre_market | 12-28s | 3 | `*/20 7-9` | 7-12 |
| 10-11 早盘 | morning_peak | 5-15s | 4 | `*/15 10-11` | 20-35 |
| 12 午休 | noon | 10-22s | 3 | `*/20 12` | 8-14 |
| 13-14 午后 | afternoon | 8-18s | 3 | `*/20 13-14` | 12-20 |
| 15-23 晚+夜 | evening_night | 12-25s | 3 | `*/25 15-23` | 8-15 |

## LLM 优先级 (T2)
- 查询: `comments.ai_score >= 4 AND comments.liked_count >= 50`
- 每轮选 2 首 (ROUND_LLM_PRIORITY=2)
- 排序: max(ai_score) DESC, max(liked_count) DESC
- 9/7 实测: 找到 10+ 首高价值歌, 最高 max_ai=5/max_liked=640088

## 风控降速 (T4)
- 连续 3 次失败 → MIN/MAX 各 × 1.5/1.3 (上限 12-32s)
- 连续 20 次成功 → MIN/MAX 各 × 0.95 (下限 = TIME_PROFILES 原值)
- 跨 profile 自动重置计数器
- 跨天自动重置计数器

## 预留并发 (T1)
- MAX_WORKERS=1 (默认, 串行, 安全)
- 改 =2 启用并发骨架 `_fetch_workers_concurrent`
- 改 >=3 必须配 Redis/DB 锁防重复

## 故障排查

### 服务不跑轮
- `journalctl -u netease163.service | grep "跑一轮"` 看上次触发
- `curl http://127.0.0.1:9700/api/v1/stats/crawler` 看 today_count

### 风控触发降速
- `journalctl -u netease163.service | grep "风控降速"` 看触发次数
- 触发后等 20 次成功自动恢复

### 调度锁冲突
- `cat /root/netease163/data/.spider.lock` 看锁状态
- fcntl.flock 进程退出自动释放, 不需要手动

## 修改影响
- 改 TIME_PROFILES → 重启服务生效
- 改 cron 段 → 重启服务生效
- 改 MAX_WORKERS → 重启服务生效
- 改 LLM 门槛 → 重启服务生效
