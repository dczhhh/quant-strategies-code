# quant-strategies-code

代码公开运行、结果直接私有归档的准备仓库。当前仍为私有，尚未完成原项目全量代码导出、公开发布或真实迁移。

原 `quant-strategies` 保留私有历史、Issue、PR、旧归档和验收身份；`quant-strategies-data` 只存私有资料。已有 PR 不合并，旧引用不删除或改写。

本次仅准备已有归档的按字节迁移，没有供应商 API、行情扩采、策略信号或真实回测。冻结清单在私有资料仓库中，迁移程序只接受已登记的 22 个归档引用、91 个文件路径及 26 个预约标记。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用自行构造的字节与 Git 对象，不下载真实行情。公开 CI 的当前范围仅为归档隔离，不代表原项目的全部 CI 或回测验收。

## 下一步配置

在本仓库创建 `private-archive` Environment，并把部署分支限定为 `main`。凭据只放该环境 Secrets，不能写入代码、聊天、普通变量或日志。

| Secret | 仓库权限 | 使用目的 |
|---|---|---|
| `ARCHIVE_WRITE_TOKEN` | 仅 `quant-strategies-data`，Contents: read and write，Metadata: read | 固定目的仓库写入与读回 |
| `LEGACY_SOURCE_READ_TOKEN` | 仅原 `quant-strategies`，Contents: read，Metadata: read | 一次性读取旧归档，迁移后撤销 |

建议使用短有效期的 fine-grained PAT 或等价的限定仓库 GitHub App 凭据，不授予 Administration、Workflows 或全仓库权限。不要将 token 发给助手。

运行开关 `ARCHIVE_MIGRATION_ENABLED` 默认关闭；只有经过验收的代码进入 `main`、完整的干净代码导出完成并确认公开范围之后，才设置精确 `ARCHIVE_MIGRATION_REVIEWED_SHA` 并启用迁移。当前未设置这些变量，也未配置任何新凭据。

`archive-migration.yml` 只允许仓库所有者手动运行固定 `main` SHA，并要求执行时代码仓库公开、两个资料仓库私有。原始字节只存在于 GitHub runner 内存中并直接写入私有仓库，不使用公开 artifact、缓存、提交或 payload 日志。

迁移以创建新分支发布，绝不 PATCH、DELETE、强推或覆盖旧引用。新仓库提交与旧证据身份分开记录；原始 receipt、manifest、报告字节不修改。再次运行只读回已存在的迁移，不重写资料，不重置供应商预约预算。失败只输出固定状态，不打印底层异常。
