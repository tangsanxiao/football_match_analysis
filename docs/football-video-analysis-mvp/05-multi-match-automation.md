# 多比赛项目隔离与自动化流程

## 目标

当前项目从单场 MVP 脚本升级为多比赛项目结构。每场比赛拥有独立目录，配置文件、中间态、人工复核文件、报告输出都放在同一个比赛项目下。

默认项目目录:

```text
matches/<match_id>/
  config/
    match.yaml
    calibration_points.yaml
  data/
    interim/
  review/
  reports/
  videos/
    raw/
```

`videos/raw/` 默认使用符号链接指向原始视频，避免重复复制大文件；如需复制，可在创建项目时使用 `--video-mode copy`。

## 创建新比赛项目

推荐使用主服务:

```bash
.venv/bin/python scripts/12_serve_analysis_app.py --port 8765
```

浏览器打开:

```text
http://127.0.0.1:8765/
```

主服务包含两个页签:

- `新增比赛分析`: 填写比赛与球队基础信息、勾选分析指标、录入人员与特征、完成场地标定，并在提交标定后启动视频分析。
- `场地标定与运行`: 在 Home 内完成场地标定，提交后后台启动分析，并在 Home 顶部显示任务日志和状态。
- `查看历史分析`: 查看已有比赛项目，打开报告，调整设置，重新标定或重新分析。

人工校验闸口设计见:

```text
docs/football-video-analysis-mvp/06-human-review-gate-design.md
```

交互式创建:

```bash
.venv/bin/python scripts/08_new_match_project.py \
  --video videos/raw/<new_match_video>.mp4
```

非交互式创建:

```bash
.venv/bin/python scripts/08_new_match_project.py \
  --video videos/raw/<new_match_video>.mp4 \
  --match-id match_20260506_a \
  --name "2026-05-06 五人制 A 场" \
  --analyze-team red \
  --team-name 红队 \
  --team-color pink_red \
  --field-colors pink_red \
  --goalkeeper-colors yellow \
  --non-interactive
```

交互式向导会询问:

- 比赛项目 ID 和比赛名称
- 球场长宽
- 重点分析球队 key、名称、球衣颜色
- 球员代号、号码、位置、重点特征
- 换人时间、上/下场球员
- 重点分析指标

## 准备人工复核包

第一步，抽帧并生成复核工具:

```bash
.venv/bin/python scripts/09_prepare_match_review.py \
  --config matches/<match_id>/config/match.yaml \
  --step calibration
```

脚本会自动:

- 探测视频信息
- 抽取关键帧
- 选择一张中间帧作为标定图
- 写入 `matches/<match_id>/config/calibration_points.yaml`
- 生成浏览器点击取点页面

启动自动保存的复核服务:

```bash
.venv/bin/python scripts/serve_point_picker.py \
  --config matches/<match_id>/config/match.yaml \
  --points matches/<match_id>/config/calibration_points.yaml \
  --port 8765
```

浏览器打开:

```text
http://127.0.0.1:8765/match
http://127.0.0.1:8765/
```

`/match` 用来确认比赛、球队、人员、号码、位置、重点特征、换人信息和重点指标。点击“确认并写入 match.yaml”后，系统会在 `review.match_info` 写入确认状态。

点击后像素坐标会直接写回该比赛项目自己的 `calibration_points.yaml`。完成点位后，点击页面里的“提交打标”按钮，系统会在该 YAML 的 `calibration` 段写入:

```yaml
labeling_status: submitted
labeling_submitted: true
labeling_submitted_at: ...
labeling_submitted_point_count: ...
```

如果提交后又修改点位，状态会自动回到 `draft_after_edit`，需要再次提交。

第二步，场地标定完成后跑初步识别:

```bash
.venv/bin/python scripts/09_prepare_match_review.py \
  --config matches/<match_id>/config/match.yaml \
  --step recognition \
  --device mps
```

脚本会自动:

- 计算场地 homography
- 跑一段初步检测/追踪
- 汇总 tracklet
- 按球队颜色筛候选
- 自动做一版身份绑定
- 生成复核文件 `review/identity_review.yaml`
- 生成复核说明 `review/README.md`

需要人工重点检查:

- `tracklet_contact_sheet.jpg`
- `analyze_candidate_contact_sheet.jpg`
- `review/identity_review.yaml`

## 生成完整 MVP 报告

确认比赛信息、场地标定和初步识别结果后，运行:

```bash
.venv/bin/python scripts/10_run_match_mvp.py \
  --config matches/<match_id>/config/match.yaml \
  --device mps
```

脚本会自动:

- 全片检测追踪
- 汇总轨迹
- 颜色候选识别
- 默认按全片 tracklet 自动做身份绑定
- 生成 Markdown、HTML、CSV 报告

输出默认在:

```text
matches/<match_id>/reports/mvp_initial/
```

注意: `review/identity_review.yaml` 来自初步识别片段，它的 track_id 通常不能直接复用到全片检测结果。只有当人工复核文件确实对应同一批全片 detections 时，才运行:

```bash
.venv/bin/python scripts/10_run_match_mvp.py \
  --config matches/<match_id>/config/match.yaml \
  --device mps \
  --use-review-bindings
```

## 验证比赛项目完整性

可以用校验脚本检查项目隔离、比赛信息、人员信息、标定包、初步识别包和报告是否齐全:

```bash
.venv/bin/python scripts/11_verify_match_project.py \
  --config matches/<match_id>/config/match.yaml
```

校验结果为 `COMPLETE` 表示当前项目从配置到报告都已齐全；`PARTIAL` 通常表示还没完成标定、初步识别或报告生成。

## 现阶段能力边界

已支持:

- 多比赛目录隔离
- 新比赛配置向导
- 球队颜色与门将颜色配置
- 人员、号码、位置、重点特征录入
- 换人信息录入
- 场地坐标标定工具自动写入项目 YAML
- 初步检测识别和身份复核包
- 完整 MVP 报告流水线

仍需后续增强:

- 不同球衣颜色的阈值需要更多样本校准
- 场上球员身份仍建议用号码/球鞋 crop 复核
- 足球小目标检测仍是传球、射门、抢断、1v1 成功率的主要瓶颈
- 当前高级事件以候选和代理指标为主，正式技术统计需要球权链路
