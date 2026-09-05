# netease163 审计修复计划

**审计来源**：2026-09-05 老杨发的代码审计报告

## P0（核心安全 + 部署）

- [ ] #1 加 API Key 中间件 + 默认 127.0.0.1
- [ ] #2 密码改 Pydantic body (POST)
- [ ] #3 评论去重：comment_id 唯一键 + upsert
- [ ] #4 修复失效端点 `netease163.crawler.comment`
- [ ] #5 补 requirements.txt: apscheduler, qrcode, httpx

## P1（数据正确性）

- [ ] #6 LLM 评分按 comment_id 匹配 + 校验
- [ ] #7 单实例锁 + 关闭重复调度器
- [ ] #8 挂载 WebUI StaticFiles
- [ ] #9 登录态加密 + 限权限

## P2（清理）

- [ ] #10 死常量 / 重复 import 清理
- [ ] #11 关键词扩展改 NLP
- [ ] #12 测试用例修复

## 验证方法

每项修复后:
1. git commit + push
2. 服务 restart (如需)
3. 浏览器验证 / API 测试
4. 单元测试