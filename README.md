# 环线巡检区域卷

测点班组提交现场巡检清册。白班发布冻结修订并录制门户查询。夜班在门户关停前接收有限正文和精确命中声明，离线办理区域卷。复查员凭卷号领取签发时的原序读值、现场及下载出处、刻度合计和首执。

## 运行

使用 Python 3.11 和标准库。在项目根目录运行完整回归 `python3 -B check_project.py`，该入口由运行平台物化。直接运行同一测试包可用 `python3 -B -m unittest discover -s tests -t .`。演示命令为 `python3 -B demo.py`。演示从 `samples/` 读取固定样例，在临时目录建库，每次独立运行。

## 一次办理

1. 白班在源库登记区域，校验清册后发布首版。清册以冻结行号定序，读值单位为巡检仪的整刻度。
2. 如有勘误，白班提交同批父版及修订后的完整清册。父版及其区域快照继续可读。
3. 白班按指定版号下载。首次查甲区命中三条，下载正文只交付 A 和 B。
4. 夜班收下首件，取得耐久实收号。以首件申请甲区卷得到待补，无卷或首执。
5. 夜班收下甲乙联合查询返回的 D、C。在所选同版实收中，C 补齐甲区，D 用于核对相应声明。
6. 核定按实际查询范围统计冻结行。A、B、C 三个身份都已到齐。值同为 11 的 B 和 C 分别计入。
7. 签出原序 A、B、C，整数合计 29。卷和首执一次提交，重复投递和重复办理可复领原号。
8. 复查员关源重开归档库，凭卷号核回原卷、实际采用的每个出处和首执。

## Python 接口

- 从 `survey.revision_store` 构造 `RevisionStore(path)` 并调用 `initialize()`。`register_areas([Area(code, name), ...])` 登记区域，同一记录重投保留原目录，异名重用区域代码拒绝。`survey.freeze.freeze(store, batch, rows, parent=None)` 接收 `SurveyRow(number, area, value, origin)` 清册，返回 `FrozenRevision`，其 `revision_id` 可供查询。勘误传入同批父版号。坏清册或父版拒绝发布。
- `portal.recording.RecordingStore(path).initialize()` 建立录制存储，可与冻结库共用文件。`portal.export.export(revisions, recordings, revision_id, scope=None, returned_ids=None)` 返回 `CapturedExport`，其 `record` 是已固化录制，`raw` 是可独立传递的字节。`None` 范围使用该版全部区域。默认正文按冻结行号取前两条，确定性本地录制可用 `returned_ids` 明确指定实际返回的行及顺序。`replay_export(recordings, recording_id)` 重放原件。需要分开录制阶段时可用 `portal.query.bind_query` 保留查询绑定，再用 `RecordingStore.save_recording` 固化录制。本地数据录制可给定独立的 `hits` 声明。
- `intake.store.IntakeStore(path).initialize()` 初始化独立收件库。`intake.receipts.receive(store, raw)` 返回 `Receipt`，可用 `load_receipts(store, ids)` 按原号取得固定凭据。原件完全相同且录制号相同的重投复用原号，录制号异文、格式无效及同版同号的区域、读值或现场出处冲突会拒收整件。版号和录制号属于同一巡检中心的源库命名空间。
- `archive.store.ArchiveStore(path).initialize()` 初始化归档库。`archive.issue.close(archive, intake, revision_id, target, receipt_ids)` 返回 `CloseResult`。`issued` 状态返回卷与首执。`pending` 表示所选证据尚未证明目标查全，`conflict` 表示已证实矛盾，`bad_receipt` 表示选择含无效实收，`invalid_request` 表示版号或目标格式无效，`write_failed` 表示归档事务失败。只有 `issued` 含成功成果。实收库不可读时数据库错误直接返回给调用方，由调用方处理重试。
- `archive.readback.read(archive, bundle_id)` 返回固定的 `IssuedBundle`，未知号返回 `None`。领取只需归档库。相同版号、目标及实收集合的成功请求复用原卷执，实收号的排列和重复不影响请求身份。其他版的有效实收不会进入目标证明或卷内出处。

## 凭据与核定口径

独立来件是版本 1 的 UTF-8 JSON 字节。顶层字段为 `version`、`recording`、`revision`、`scope`、`hits`、`rows`。每行含 `number`、`area`、`value`、`origin`、`recording`、`position`。冻结行号、版号、录制号为正的 SQLite 整数标识。正文位置从 1 起，按实际返回次序。现场出处为非空文本，下载出处为录制号与正文位置。范围必须非空，命中数为非负整数，正文可为空且最多两行。结构合法的计数矛盾交由核定处理。冻结和接收均拒绝布尔读值，合计采用精确整数算术。

核定对各声明扣减所选同版实收中其范围内的唯一冻结行。目标直接声明或含目标的范围查全时，可以证明完整性。V1 对范围超计、同范围异计及范围包含关系所证实的矛盾返回冲突，无完整性证明时返回待补。来自别版或未选用的实收不会补足当前范围。空正文仍按声明核对，已证明的空目标可签卷并记合计 0。每条入卷行固定本次所选同版实收中的全部实际出处，按录制号、正文位置排列。

多个库的初始化都只建缺失结构。再次初始化会保留区域、勘误、录制、实收、卷和首执。样例导入是演示中的显式业务操作。
