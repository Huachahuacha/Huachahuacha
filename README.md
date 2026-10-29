# 券商专家网络采购提醒

每日检查中国券商公开采购信息，允许一天延迟，优先扩大覆盖、保留待核验项目。现有150条证监会机构记录逐家安排检索；7家配置22个官方采购入口，其他入口仍需核实。不能将“已纳入检索”理解为“全部采购渠道已接入”。

详细来源、使用方式、覆盖报告和限制见 [券商采购覆盖说明](docs/bid-alert-coverage.md)。

## 安装与只读核验

需要 Python 3.11 或以上版本：

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m src.bid_alerts --coverage-plan --report-file work/bid-alerts/coverage-plan.json
python -m src.bid_alerts --dry-run --verbose
```

`--dry-run`联网读取公开页面并生成覆盖报告，不发送邮件或更改已发送状态。正式扫描用 `python -m src.bid_alerts --verbose`，沿用现有邮件环境变量；勿把实际凭据提交到仓库。

GitHub Actions 每日北京时间09:17执行，17:47备份。手动运行可选择 scan、dry-run、test-email、prime；首次核验应选 dry-run。工作流Artifact保存覆盖计划和运行报告。

程序会读取正文及公开PDF/DOCX附件；不可读内容保留到重试或人工核验清单。同网址内容变更可再次提醒，未发送项目持久保存。公开搜索索引不完整、动态或登录平台及非公开邀请仍可能形成缺口；任务运行成功不能证明全国没有新项目。
