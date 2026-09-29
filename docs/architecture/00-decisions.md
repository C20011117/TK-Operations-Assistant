# 00 · 架构决策与统一契约

本文件是各设计分册共用的约束。所有技术版本在实际建仓时锁定精确版本并完成兼容性验证；这里的技术组合是设计选择，不是已安装环境报告。

## ADR-01：采用模块化单体，按进程职责部署

一个后端工程包含业务模块和公共基础设施。API、Outbox 分发器、调度器、资料处理 Worker、匹配 Worker、外部动作 Worker 使用同一领域代码，分别部署。生产系统可以独立扩容各类 Worker，不要求先拆服务。

理由：当前闭环涉及寄样、拍摄轮次、视频版本、费用和权限的一致性，统一数据库和事务更容易确保正确性。拆分依据应是独立扩容、故障隔离或研发组织边界的实际证据。架构基线不加入 Kafka、独立向量数据库、Elasticsearch 或 Kubernetes。

## ADR-02：技术栈

- Web：React + TypeScript + Vite；React Router 管理路由，TanStack Query 管理服务端状态，表单使用 React Hook Form + Zod。内部工作台不依赖 SSR。
- API：Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic。每个请求 / 并行任务拥有独立 Session，禁止全局共享 AsyncSession。
- 持久化：PostgreSQL（部署基线为受维护的 18.x）+ 兼容版本 pgvector。关系数据、向量、业务事件和费用账本集中存储。
- 异步执行：Celery + Redis；PostgreSQL `jobs` / Outbox 是恢复依据，Redis 丢失不能使业务任务永久消失。
- 文件：S3 兼容私有对象存储；数据库保留对象引用、校验和、权限与生命周期。
- AI：LangChain 组织模型、检索与结构化输出；LangGraph 在 Worker 内实现可暂停、可恢复的匹配流程。模型通过受控适配器接入，具体模型经能力、成本、数据使用范围与质量评测后固定。
- 观测：结构化日志、OpenTelemetry 追踪、Prometheus 兼容指标；供应商可以替换，统一 trace_id / job_id / run_id。

部署前检查 PostgreSQL 与 pgvector 支持矩阵。开发机为 Windows 时，Web / API 可原生运行，Celery / Redis / PostgreSQL 使用 Linux 容器或 WSL2；不把 Windows 原生 Celery 当作支持环境。

## ADR-03：以数据库记录为业务真相

状态改变及待分发事件在同一数据库事务提交。分发采用至少一次投递；消费者用唯一键、租约、fencing token 和幂等记录处理重复。跨数据库和外部接口无法承诺统一的 exactly-once。

LangGraph checkpoint 保存执行位置和对象引用，不复制整套业务状态，也不自行驱动另一套作业调度。图恢复时必须读取当前权限、预算、连接和运行状态；图节点重入不等于可以重做付费调用或外部动作。

## ADR-04：关系数据、检索文本和新鲜指标分工

硬条件、金额、日期、状态、身份与去重走 SQL / 规则。产品资料、已审核规则、历史案例及获授权内容证据进入 RAG。GMV、粉丝、近期表现保留类型化观测和时间窗口，按授权刷新；向量相似度不能代替最新数值。

默认先限制 tenant / owner / ACL / 版本 / 生命周期，再做词法及精确向量召回。量级需要 ANN 时，验证分区与过滤策略的召回质量后启用；embedding 模型和维度由独立 profile 固定，升级通过重建索引切换。

## ADR-05：FastMoss 是能力受限的数据源

客户带入的是 API / MCP 使用权。系统保存连接能力快照，为某个操作选择 REST 或 MCP 一种路径；同一逻辑请求不会同时尝试两条路径。支持的字段、站点、内容深度与数据保留范围按真实账户验证。

连接器注册固定操作、参数 schema、允许的目标地址、分页、费用与重试策略。模型不得自造 URL、接口、筛选字段或传输方式。授权不足和未知能力保留为明确状态。

FastMoss 官方目录中有 `video_script_info`；可作为字幕 / 口播信息的条件数据源，但须验证实际权限、视频覆盖和处理许可。没有拿到相应文本 / 媒体时，不声称分析过完整视频。

## ADR-06：企业隔离与身份边界

以 `tenant_id` 为企业边界，不默认支持一个租户跨多个客户混存数据的代理机构模式。用户可以加入多家企业，但每次请求、任务、缓存和文件操作只处于一个明确企业上下文。

服务端同时执行 membership、动作级 RBAC、资源 ACL 与数据库 RLS。路径或请求体中的 tenant_id 不是授权凭证。运行数据库角色必须非 owner、无 BYPASSRLS；业务表启用 FORCE RLS。租户外键用 `(tenant_id, id)` 防止跨企业关联。

角色固定为 admin、bd、viewer，不设日常分工角色。creator_relationships 及其沟通、报价、合作、视频反馈、分类和维护记录属于 owner_principal_id，其他 BD 和企业管理员不因角色自动获得正文读取权限；企业公共产品资料另按 ACL 开放。企业内稳定达人身份可在受限服务中解析，接口不得借身份复用泄漏另一 BD 的业务记录。

后台任务使用企业范围内、可审计的执行身份。用户触发的敏感动作在执行时重新检查用户资格；周期任务使用限定当前 BD 所有关系/任务范围的服务身份，不能作为读取其他 BD 私有记录的通道。运维支持访问必须有理由、范围、时限和审计，不设置默认任意查看企业内容的后台入口。

## ADR-07：历史可追溯，但不能绕过删除和授权期限

产品、任务条件、规则集合、推荐名单、拍摄包、视频、反馈、报价和本人确认都保留版本。旧推荐绑定当时快照，不被重新排序覆盖。

不可变是业务编辑约束，不代表永不删除。来源过期、撤销或删除时，按授权清除原文、向量、对象、导出、缓存和 checkpoint 中的相关内容；可保留获准的墓碑、哈希、时间与审计元数据。旧记录展示“证据已过期 / 已删除”，不能为了复现重新获取一份新内容冒充旧证据。

## ADR-08：费用控制和对外动作使用不同但一致的保护

付费操作先估算与预占预算；实际调用记录至 `provider_calls`，费用写入 `usage_ledger`，通过 `usage_reservations` 结算或核对。未知收费不得记作零或无条件释放。预算币种和供应方积分分开记账。

应用只能严格控制它自行发起且有可计算上界的使用量；客户在别处使用同一 Key、未知供应方收费或汇率变化会影响供应方总账。界面分别显示本应用用量、估计费用、核对后费用和可取得的供应方余额。

外发、报价承诺、样品申请等有外部影响的动作必须绑定已确认内容、payload_hash、版本、有效期与执行范围。修改内容需要重新确认。超时结果不明进入核对流程，不能盲目重发。

## ADR-09：多站点以独立运行和显式聚合实现

`campaigns` 是业务容器，`campaign_markets` 是单站点执行单元。产品可以共用版本，各站点条件、预算、名单、统计窗口分别记录。

销售站点、达人所在地、受众所在地、语言和带货资格是不同字段。跨站点汇总必须注明原币种、汇率日期、指标窗口和转换方法；未经校准不得把不同市场分项分数直接合成“全球达人排名”。

## ADR-10：单个 BD 贯穿内容生产和关系维护

候选名单由本人确认，直接进入合作与寄样准备；不实现认领、分配、转交、上级审名单或团队评论模块。对外消息、费用承诺与样品申请仍需本人确认准确内容，这是防误操作的执行授权，不是管理审批。

`creator_relationships` 代表 BD 与达人的长期关系，唯一 `(tenant_id,owner_principal_id,creator_id)`；`collaborations` 是其中某次具体合作；`production_rounds` 是其中一条新视频目标。每轮绑定 `content_brief_versions`，回收内容进入 `video_assets` / `video_versions`；返修只增加版本，不增加新视频产出数。该轮反馈和通过状态绑定准确的视频版本，不能让旧审核自动覆盖新上传内容。

`video_reviews` / `feedback_items` 保存带证据的具体意见；`creator_classification_events` 保存按时间窗口、产出、内容价值、商业表现与维护优先级分类的依据，AI 只提出建议，由本人决定。`maintenance_plans` / `maintenance_actions` 推动再次联系和下一轮约拍；关系不随一轮结束而消失。

分类不能把缺少销售数据当作零价值。寄样发出、签收、素材回收、内容可用、平台发布和广告使用许可各有独立事实；任何一个不能自动证明另一个。每轮新增要求、价格和期限需要真实确认，不根据历史合作无限推定承诺。

## 统一术语与状态

关键对象名：`tenants`、`products`、`product_versions`、`campaigns`、`campaign_markets`、`criteria_versions`、`rule_set_versions`、`matching_runs`、`creators`、`provider_snapshots`、`recommendation_snapshots`、`creator_relationships`、`collaborations`、`production_rounds`、`video_versions`、`jobs`、`outbox_events`。达人数据快照是 `provider_snapshots` 的一种 resource_kind，不另设语义重复的 creator_snapshots 表。

MatchingRun 至少固定：`tenant_id`、`campaign_market_id`、`product_version_id`、`criteria_version_id`、`rule_set_version_id`、能力快照，以及 graph / prompt / model / embedding 版本。单条规则版本与规则集合快照不是同一对象。

运行 / 作业状态统一为：

```text
queued | running | waiting_input | cancel_requested | succeeded | partial | failed | cancelled
```

- `waiting_input`：等待确认条件、资料冲突处理、预算或其他明确输入，不占用执行进程。
- `partial`：部分结果已持久化且可解释，存在未完成来源或步骤；不是“全部搜索完成”。
- `cancel_requested`：不再发起新的外部调用；已在途的请求可能完成并产生费用。
- `cancelled`：执行停止，保留已完成结果与费用。需要继续则创建关联的新运行；不原地复活终态运行。
- `failed`：有可理解错误；满足重试条件时创建关联的新运行 / 作业，并依据账本复用可复用步骤。

图内阶段另用 `stage` 表示，例如 `discovering`、`enriching`、`assessing`，不扩大运行状态枚举。业务合作状态、外部动作状态、授权状态、费用状态都有独立枚举，不能复用 run 状态。

`provider_calls` 表示逻辑调用，`call_attempts` 表示每次实际网络尝试；费用和回执可定位到具体 attempt。队列负载只包含 `job_id` 和非敏感追踪元数据，执行企业与权限从受控数据库记录解析。04 中 API `revision` 映射到 02 的 `lock_version`。

产品版本的发布动作对应数据库状态 `confirmed`。`RecommendationSnapshot` 是推荐输出的不可变 DTO，持久化为 `recommendation_snapshots` / `recommendation_items`；`CandidateAssessment` 对应 `candidate_evaluations`；`EvidenceRecord` 对应 `evidence_items`；`ClaimRecord` 对应 `evaluation_claims`，模型 DTO 字段需由显式映射转换成持久化 schema，不能各自形成重复事实表。

硬条件：`pass | fail | unknown`。金额使用十进制字符串传输、数据库 NUMERIC；数量单位、原币种、积分、估算性质分开存。时间戳使用 RFC3339 UTC，日历规则同时保存 IANA 时区；日期承诺明确是本地日期还是时刻。

API：`/api/v1/tenants/{tenant_id}`。内部 ID 为 UUID，供应方 ID 为字符串；用户昵称不能作为稳定身份。变更请求使用实体 revision / If-Match 防止覆盖；创建付费任务和外部动作使用企业、操作者、操作范围内的 Idempotency-Key 与 request hash。

本人确认统一使用 `action_confirmations` 和 `run_confirmations`。`external_actions` 状态为 `draft | awaiting_confirmation | confirmed | dispatching | succeeded | failed | unknown | needs_manual_review | cancelled`；`message_drafts` 为 `draft | confirmed | discarded | superseded`；个人 `shortlist_versions` 为 `draft | confirmed | superseded`。不再使用旧 action_approvals、run_reviews、候选分配或上级审核状态。发现路径 `relationship_library` 仅查询本人的达人关系库。

## 待验证事项与记录方式

尚未确定的是具体生产供应商、真实规模、供应方授权与字段、模型评测结果及需接通的发送渠道。研发前记录实际选择与证据，更新本文件的 ADR；不能把“以后决定”藏在默认配置中。每次变更要写动机、影响、迁移和验证要求。

官方参考：[FastAPI 后台任务](https://fastapi.tiangolo.com/tutorial/background-tasks/)、[SQLAlchemy 并发会话](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)、[FastMoss 官方目录](https://github.com/FastMoss/cli)、[LangGraph 持久执行](https://docs.langchain.com/oss/python/langgraph/durable-execution)、[PostgreSQL 行安全](https://www.postgresql.org/docs/18/ddl-rowsecurity.html)、[pgvector](https://github.com/pgvector/pgvector)、[Celery Windows 支持说明](https://docs.celeryq.dev/en/stable/faq.html#does-celery-support-windows)。引用描述能力和限制，项目选型属于本设计决策。
