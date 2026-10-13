# Private archive migration

`quant-strategies-code` 已公开。完整代码及原有 CI 通过独立的代码迁移 PR 导入，参见 [源码迁移说明](public-code-migration.md)。真实归档尚未完成迁移。

原 `quant-strategies` 保留私有历史、Issue、PR、旧归档和验收身份；`quant-strategies-data` 只存私有资料。已有 PR 不合并，旧引用不删除或改写。

本次仅准备已有归档的按字节迁移，没有供应商 API、行情扩采、策略信号或真实回测。冻结清单在私有资料仓库中，迁移程序只接受已登记的 22 个归档引用、91 个文件路径及 26 个预约标记。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用自行构造的字节与 Git 对象，不下载真实行情。公开 CI 的当前范围仅为归档隔离，不代表原项目的全部 CI 或回测验收。

## 下一步配置

本仓库的 `private-archive` Environment 已创建，部署范围限定为唯一的 `main` 分支，不允许 tag。迁移凭据已由所有者填入该环境的 Secrets；凭据值不能写入代码、聊天、普通变量或日志。

| Secret | 仓库权限 | 使用目的 |
|---|---|---|
| `ARCHIVE_WRITE_TOKEN` | 仅 `quant-strategies-data`，Contents: read and write，Metadata: read | 固定目的仓库写入与读回 |
| `LEGACY_SOURCE_READ_TOKEN` | 仅原 `quant-strategies`，Contents: read，Metadata: read | 一次性读取旧归档，迁移后撤销 |

建议使用短有效期的 fine-grained PAT 或等价的限定仓库 GitHub App 凭据，不授予 Administration、Workflows 或全仓库权限。不要将 token 发给助手。

运行开关 `ARCHIVE_MIGRATION_ENABLED` 和精确的 `ARCHIVE_MIGRATION_REVIEWED_SHA` 必须与运行提交一致。代码迁入新的 `main` 后需要重新固定该 SHA；旧值会令工作流跳过，不会自动授予新源码执行权限。启动真实归档迁移须有所有者对该归档步骤的授权。

`archive-migration.yml` 只允许仓库所有者手动运行固定 `main` SHA，并要求执行时代码仓库公开、两个资料仓库私有。原始字节只存在于 GitHub runner 内存中并直接写入私有仓库，不使用公开 artifact、缓存、提交或 payload 日志。

迁移以创建新分支发布，绝不 PATCH、DELETE、强推或覆盖旧引用。新仓库提交与旧证据身份分开记录；原始 receipt、manifest、报告字节不修改。再次运行只读回已存在的迁移，不重写资料，不重置供应商预约预算。失败只输出固定状态，不打印底层异常。
