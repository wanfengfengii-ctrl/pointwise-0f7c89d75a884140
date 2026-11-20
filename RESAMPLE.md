# 补测计划交班

夜班使用已经签发的 V1 报告取未检槽，使用该报告原双站交来的能力件安排补测。每站开班时已经装好一种夹具。每次换夹须同时占本站和两站共用的换夹台。检测仅占本站，两站可同时检测不同的槽。

## 调用入口

沿用 README 中的 V1 连接管理约定。下表是在现有工程上新增的 Python 接口。

| 调用 | 输入与返回 |
| --- | --- |
| `station.issue.issue_capability(catalog, outbox, frozen_id, capability)` | `outbox` 为本站出箱，`frozen_id` 指向其中已发行的 V1 原站件。固定能力伴随件并返回 UTF-8 JSON 字节。 |
| `admission.accept.accept_capability(archive, catalog, parent_admission_id, raw)` | `parent_admission_id` 是中心已接纳的 V1 原站件号，`raw` 是能力伴随件字节。核对父件语义内容、固定清册和能力，返回字典 `{'capability_id': 整数, 'raw': 首存字节}`。 |
| `review.publish.publish_resample(archive, catalog, register, report_id, first_capability_id, second_capability_id)` | `report_id` 是已签 V1 报告号。两个能力接纳号依次对应报告中的两份 V1 父件。只使用中心和清册库，返回 `(plan, receipt)`。 |
| `review.readback.read_resample(register, plan_id)` | 按补测计划号从中心读取固定的 `(plan, receipt)`。 |

## 能力与父件

`capability` 是含下列四个字段的字典。夹具名遵循 V1 的名称规则，作用域为本站。

- `fixtures` 为一至两个互异夹具名的列表。`initial_fixture` 是其中已经装好的夹具名。
- `changes` 为 `[原夹具, 目标夹具, 分钟]` 的行列表。两夹具须在本站声明且互异，同一有向对至多一行。正向许可只适用于所列方向。
- `checks` 为 `[清册槽标签, 夹具名, 分钟]` 的行列表。同一槽与夹具组合至多一行，夹具须在本站声明，槽须在原固定清册中。可登记已检槽的检测条件，调度范围取原报告的未检槽。缺少某组合表示该站在该夹具下不可检。
- 两种时长均为非布尔正整数分钟。`changes` 和 `checks` 可为空。已声明夹具可因缺少切换许可而不可达。

伴随件为 UTF-8 JSON 对象，字段为 `kind`、`parent`、`capability`。`kind` 固定为 `resample-capability`。`parent` 含 `station`、`frozen_id`、`roster_id`、`content_sha256`。摘要为父件按 V1 语义编码后的 SHA256 小写十六进制值，语义编码沿用 `station.envelope.encode_envelope(station.envelope.decode_envelope(raw_v1))`。因此中心首存的 V1 原件即使有等价分数或 JSON 空白差异，也能匹配同一父件。盘、轮及清册身份由 V1 父件和清册登记核对。字段须完整，拒绝额外字段和重复 JSON 键。

对同一父件，能力以初装名称、夹具集合、有向换夹表和检测时长表比较。列表顺序和 JSON 空白不影响等价。站端等价重发返回首次发行的字节，中心等价重投返回首次接纳号和原字节。能力冲突、V1 与伴随件混用、坏件、非法输入和父件失配报 `ValueError`。未知冻结号、未发行的 V1 件及未知清册、接纳、报告或计划号报 `KeyError`。各类数字编号按其调用参数所属的记录类型查找。

## 作业表与回执

`plan` 和 `receipt` 为可 JSON 序列化的字典。`plan` 有 `plan_id`、`report_id`、`roster_id`、`sources`、`status`、`finish`、`schedules`。`sources` 按原报告父件顺序列两个对象，每个含 V1 `admission_id`、`capability_id`、`station`、`frozen_id`。`receipt` 含整数 `receipt_id` 和对应的 `plan_id`。

`status` 为 `scheduled` 时，`finish` 为从班初零时刻到全部作业完成的最少整数分钟。`schedules` 为两个按父件顺序排列的站作业列表，每站按开始时刻升序。检测行格式为 `{'kind': 'inspect', 'slot': 'a', 'fixture': 'Y', 'start': 6, 'end': 7}`，换夹行格式为 `{'kind': 'change', 'from': 'X', 'to': 'Y', 'start': 2, 'end': 6}`。各区间左闭右开，持续时间为 `end - start`。闲置用作业间的空档表达。完成换夹后，目标夹具成为本站当前夹具，空档与检测保持该状态。同刻结束和开始合法。每个未检槽恰检一次，已检槽不进入作业表。并列最优可选任意一张完整作业表。

原报告无未检槽时，仍须使用匹配的双件，签出 `scheduled`、`finish=0` 和 `schedules=[[], []]`。能力有效但无法完成全部补测时，签发 `infeasible`，`finish` 与 `schedules` 均为 `None`。

出处、计划和回执在同一次中心提交中写入，提交失败无新增且可用原选件重试。沿用 `ReportRegister` 的 `before_commit` 钩子，在新计划及回执已写入但尚未提交时调用。更正和重开后按计划号读回原存值。V1 的读写、旧件及旧报告照旧使用。

## 样例和运行

`python3 -B resample_fixture.py` 建立四个独立 V1 场景，核对原报告的未检槽及重开存值，并打印补测能力输入及手算的预期状态和完工值。`prepare(stores, case)` 可在空样例库中取得 `((report, receipt), parents, spec)`，`parents` 是按报告次序排列的两个 V1 接纳记录，`spec['capabilities']` 与该次序一致。发行时可从接纳记录的 `envelope.frozen_id` 取得本站原冻结号。

- `wait` 未检 a、b、c。乙在 0–1 测 b、1–2 换夹、2–7 测 c。甲在 2–6 换夹、6–7 测 a。乙需至少 7 分钟，见证达到该下界。甲若先于乙在 0–4 换夹，全批需 10 分钟。
- `revisit` 未检 a、b、c、d。甲在 0–2 从 X 换到 Y、2–5 测 a、5–6 换回 X、6–7 测 c。乙在 0–2 测 b、2–3 换夹、3–7 测 d。乙需至少 7 分钟，见证达到该下界。甲若先在 X 测 c 以只换一次夹具，全批至少 8 分钟。
- `infeasible` 从 `wait` 去掉乙站的 X 到 Y 许可，c 在两站均无法安排，签不可行。
- `empty` 已完成原托盘的全槽检测，签 0 和两个空作业列表。

沿用 `.pointwise-runtime.json` 的 `version`、`kind` 和 argv 约定。完成后让 `verify` 执行完整原回归及补测业务核验，失败以非零状态退出。`demo` 展示关站后实际签发的两站作业区间、完工分钟和重开后的原号领取结果。