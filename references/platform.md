# 平台接口与交付

以工作区配置的 HTTPS API 根路径（含 /api/open/v1）和授权凭据访问平台。平台 API 使用 Authorization Bearer，请求写入带 X-Operator；下载平台返回的媒体 URL 时单独请求，不转发凭据。

| 请求（相对 API 根路径） | 用途 |
| --- | --- |
| GET /projects | 遍历组织可见项目，列表字段 repaintStatus 为 1 表示待转绘。 |
| GET /projects/{projectId} | 核对项目、创作目标和 repaintTask 的 runId、status、交付信息。runId 由平台提供。 |
| PATCH /projects/{projectId} | 开工时提交 repaintStatus 为 3 和本轮 runId；核验响应中的 status 和 runId。 |
| GET /projects/{projectId}/assets?types=character,scene,prop | 每页 limit=200，传回 nextCursor 直至为空。按 assetId 去重，读取 sourceImage、primaryImage。重复记录内容有变化时重新读取。 |
| GET /projects/{projectId}/assets/{assetId} | 取角色详情、候选帧或原片视频等核对信息，上传前核对原片版本、当前主图和关联成员。 |
| POST /projects/{projectId}/assets/{assetId}/candidates?setAsPrimary=true | 以 image/png 原始二进制上传，单张最多 10 MiB。按 assetId 寻址，保存新 variantId 并回读主图。 |

## 从资产到平台定妆

1. 本轮任务成员走队列脚本 deliver。上传前同时检查任务成员、实际联动范围、允许类型、排除名单及本轮快照。任何受影响成员未定妆、被排除、归属另一张图或无法核实，暂停这次写入并记录原因。用户未要求调整的平台关联保持现状。
2. 在授权范围内，清单外的独立资产、以及用户明确要求重跑的已完成项目，使用平台支持的普通单资产候选接口。已完成项目保留原平台状态，使用独立的本地执行编号。开工时的主图若被清空，跳过写入；主图或原片版本变更，先核对现状。对返回冲突、限流、响应丢失或 candidateSaved 的请求，先查当前主图和候选、核对回执，再决定是否重试。清单外写入被拒绝时保留本地成品并报告。
3. 每次回读的资产主图 variantId 应与本次上传回执一致；检查所有受联动影响的成员。续跑时用 assetId、当前轮次、原片版本、成图摘要、有效回执和实际主图判断是否已经完成。同组不同资产仍需各自的生效记录。
4. 结束时重新读取全量台账，核对每个允许类型的入选资产均有输出或明确的未交付原因。逐项核对原片文件、设定图相对路径、有效回执及生效主图；单独列出跳过或排除的资产。平台状态 2 仅表示平台轮次结束，全部入选资产完成核对后才报告整项目完成。

交付 JSON 顶层记录 projectId、platformRunId、独立执行编号、assets、skipped。每条入选资产记录 assetId、type、episodeNumber、name、groupId、groupStatus、sourceImageUrl、inputVariantId（如有）、imagePath；人物额外记录 personId、castRole、roleSource 和 roleEvidence。已上传项补充 variantId、primaryImageUrl，并在本地回执记录图片 SHA-256。暂定项保留 groupEvidence，跳过或未交付项记录原因。同组资产共享同一个 imagePath。
