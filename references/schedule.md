# 定时扫描与续跑

定时任务负责唤起 Codex；每次唤起执行一次下列流程。首次启用按用户要求立即执行一次，之后每 30 分钟一次。已有定时任务时复用其工作区、周期、状态及项目范围限制。手动调用 skill 只执行本次请求。迁移到新工作区时，由获授权的调度器创建或绑定定时任务，并指向新工作区的 skill。

## 准备运行环境

解包后将整个 skill 目录保存在工作区的 skills/ 下。队列脚本用自身所在 scripts 目录的上一级定位 .env 和 runs，正脸脚本同样从上一级定位 models。当前转绘工作区已具备工作区级 scripts；在新的专用工作区，将包内 scripts/ 下的三个 Python 文件放入工作区根目录的 scripts/ 后，从根目录运行下列命令。各次扫描与手动工作使用同一工作区，才能共享锁、输入快照和上传回执。

队列使用 macOS 或 Linux 的 Python 3、requests 和 fcntl。在工作区根目录配置 .env 的 REPAINT_API_BASE_URL（HTTPS，含 /api/open/v1）和 REPAINT_API_KEY。凭据由部署者填写。生成阶段需要当前执行环境具有图像生成工具。正脸总览使用 OpenCV（含 FaceDetectorYN）与 NumPy，在工作区 models/yunet.onnx 放置 YuNet 模型。可使用项目已有虚拟环境，或取得[OpenCV Zoo 的 YuNet 模型](https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx)。

## 每次唤起

1. 运行 `python3 scripts/repaint_queue.py poll`，分页扫描项目列表。pending 是待开工项目，resume 是平台处于处理中且本机留有同轮 started 标记的项目。两者都为空就结束本次扫描。
2. 对待处理项目运行 `python3 scripts/repaint_queue.py task PROJECT_ID` 取得平台 runId。在开工前读取该轮已存在的 `runs/PROJECT_ID/auto/RUN_ID/scope.json`，核对 runId，应用 hold、allowedTypes、excludedAssetIds 及该部署的用户限制。hold 为 true 则跳过并继续扫描其他项目。
3. 在交互终端运行 `python3 scripts/repaint_queue.py start PROJECT_ID RUN_ID`。收到 working 为 true 后保持此会话，另开命令执行后续步骤。开工脚本先持有项目轮次文件锁和共享并发名额，核对当前轮次并 PATCH 到状态 3。所有扫描在共享工作区最多并发两个项目。已在运行、由其他客户端开工、轮次已变或名额已满时按返回原因跳过。本机锁的作用范围是共享该锁目录的进程；跨机器互斥取决于平台开工接口的原子认领能力。
4. 运行 `python3 scripts/repaint_queue.py assets PROJECT_ID --repaint-inputs`；有限定类型时追加 `--types character,scene` 等实际值。保存全部 items 和 skipped，再应用资产排除项。按 SKILL.md 取材、归集、出图和核验。恢复时验证原片来源和回执。手动重跑已完成项目使用独立执行目录，同样持有项目轮次 worker.lock 和 project_slot() 名额，在退出时释放。
5. 正脸总览命令示例为 `.venv/bin/python scripts/cast_portraits.py 正脸总览.jpg C01.png C02.png C03.png`，参数传入该项目全部人形角色组的真实路径。按[平台接口与交付](platform.md)核对当前主图和联动范围后，对同轮任务成员运行 `python3 scripts/repaint_queue.py deliver PROJECT_ID RUN_ID ASSET_ID FILE.png`，记录回执。将已核验与待处理状态及时保存，最后向持锁会话输入换行以释放名额。
6. 仅在完成、遇到影响交付的错误或需要用户决定时通知。普通空队列或运行中保持安静。一个项目完成后，下一次扫描仍检查其他待转绘项目。

### 调度器可用的任务内容

> 在指定转绘工作区读取 skills/drama-character-repaint/SKILL.md 和 references/schedule.md（相对该 skill），执行一次项目扫描。以该轮 scope.json 和用户已指定的限制为准。领取可处理项目，按 skill 完成原片取材、归集、生成、平台交付与回读；保存映射及回执并释放锁。

安装包提供这段任务内容和执行脚本。实际周期、工作区和启停状态保存在调度器中；凭据、项目例外和过往回执由各部署保管。
