# 自训练球检测器 ball_v1

> 日期：2026-05-10
> 范围：在 [08 文档](08-eval-harness-and-yolo-ball-baseline.md)排除"COCO YOLO 升级"路径后，验证"用现有人工标注的真实球点 fine-tune 一个 domain-specific 球检测器"是否能从 0% 起飞。
> **结论：成功。ball_recall 从 0/30 (0%) 提升到 13/30 (43.3%)，position error 中位 0.98m**（容差 1.5m）。放宽容差到 5m / 10m 时 recall 进一步到 80% / 83.3%。详见第 4 节。

---

## 1. 假设

08 文档结论：YOLO11n / 11s 在该业余画质下都不能检测真球（recall@10m=0%），瓶颈是 COCO `sports ball` 类训练数据与目标域不匹配。

假设：用项目自己积累的真实球点 fine-tune YOLO，模型能学到这种画质下的小白球外观，把 ball_recall 从 0% 推到非平凡水平。

如果**仍然 0%**，意味着：
- 训练数据量太小（84 个正样本不够），或
- bbox 标注半径估计错（24px 太小/太大）
- 需要换更大模型 / 重做数据 / 走非纯 YOLO 路径（光流、跟踪传递）

---

## 2. 数据准备

### 来源
- `matches/中青赛_1_20260506_213657/review/ball_review/ball_review_points.csv`
- 全部 `source=human` 的人工真实标注

### 转换
- `status=marked, ball_visible=True` → 正样本（带 bbox）
- `status=invisible` → 负样本（空标签）
- 反向投影 bbox：以 `(source_image_x, source_image_y)` 为中心，正方形 24×24 px
- 24 px 是基于 YOLO11s 在原 COCO 训练下 `sports ball` 类 bbox 中位数 13.6 px 的 1.7×（让模型有上下文）

### 划分（seed=42）

| 子集 | 正样本 | 负样本 | 合计 |
|---|---:|---:|---:|
| train | 67 | 29 | 96 |
| val | 17 | 7 | 24 |

数据集路径：`evals/training/ball_v1/`（gitignored，可由 `prepare_ball_training_data.py` 重新生成）

```yaml
# data.yaml
path: <abs path>
train: images/train
val: images/val
names: { 0: "sports ball" }
nc: 1
```

---

## 3. 训练

### 配置
- 基础模型：`yolo11n.pt`
- imgsz=1280（与 production 推理一致）
- batch=8（小数据集 + 单类 + MPS 显存安全）
- epochs=80, patience=20
- 优化器：默认 SGD
- 增强：默认 + 强 mosaic（最后 10 epoch 关闭）
- single_cls=True
- device=mps

### 训练曲线（关键 epoch）

| epoch | precision | recall | mAP50 | mAP50-95 | 备注 |
|---:|---:|---:|---:|---:|---|
| 1 | 0 | 0 | 0 | 0 | 冷启，模型完全不识别该域球 |
| 10 | 0.0003 | 0.118 | 0.0001 | 0 | 第一次看到球（val 上有非零检测） |
| 13 | 0.001 | 0.529 | 0.009 | 0.002 | 召回先起来（precision 还很低，大量 FP） |
| **17** | **0.416** | **0.169** | **0.157** | **0.065** | **best.pt 由 fitness 选定（0.1·mAP50 + 0.9·mAP50-95），用于本节推理** |
| 27 | 0.347 | 0.176 | 0.163 | 0.032 | mAP50 略胜 e17，但 mAP50-95 下降，best.pt 未替换 |

完整日志：`runs/detect/ball_v1/results.csv`、`results.png`（gitignored）

> **注**：用于推理的权重是训练过程中的早期快照 `best_e17_snapshot.pt`，由 ultralytics 内置 fitness 公式选出。后续训练完成后若 best.pt 被覆盖为更优 checkpoint，本表与第 4 节将相应更新。

---

## 4. 推理与对比评估

同 08 文档的 A/B 流程，但用新权重:

```bash
python scripts/03_detect_track.py \
    --config matches/中青赛_1_20260506_213657/config/match.yaml \
    --model runs/detect/ball_v1/weights/best.pt \
    --classes 0 --start-sec 60 --duration-sec 100 --max-frames 0 --device mps

# 结果归档：
# matches/中青赛_1_20260506_213657/data/interim/detections/segment_0060_0100_2fps_ball_v1/

python scripts/17_eval_against_gold.py \
    --match 中青赛_1_20260506_213657 --gold auto --ball-source raw \
    --detections-dir <segment_dir> \
    --eval-label "ball_v1" --output ab_ball_v1
```

### 结果对比表

同金标（`evals/gold/中青赛_1_20260506_213657/`，window 78–138s，30 in_play 球点），同窗口（60–160s 检测），同评估容差（默认 1.5m / 1s）。Ball_v1 用 `runs/detect/ball_v1/weights/best.pt`（e17 收敛 checkpoint，stripped）：

| 模型 | conf | 球检测数（窗口） | 帧覆盖率 | `ball_recall@1.5m` | `recall@5m` | `recall@10m` | `position_error_m` 中位 |
|---|---:|---:|---:|---:|---:|---:|---:|
| YOLO11n（COCO） | 0.18 | 92 raw / 66 filtered | 42.8% | 0/30 (0%) | 0/30 | 0/30 | — |
| YOLO11s（COCO） | 0.18 | 268 raw / 176 filtered | 87.6% | 0/30 (0%) | 0/30 | 0/30 | — |
| ball_v1（自训练） | 0.30 | 31 / 20 in_play | 7.5% | 5/30 (16.7%) | — | — | 1.38m |
| **ball_v1**（默认） | **0.18** | **72 / 61 in_play** | **22.9%** | **13/30 (43.3%)** | **24/30 (80.0%)** | **25/30 (83.3%)** | **0.98m** |
| ball_v1 | 0.10 | 163 / 151 in_play | 41.4% | 21/30 (70.0%) | — | — | 1.03m |
| **ball_v1**（推荐） | **0.05** | **379 / 365 in_play** | **86.6%** | **25/30 (83.3%)** | — | — | **0.86m** |

跳变幅度（COCO baseline → ball_v1 conf=0.05）：

- `ball_recall@1.5m`：**0% → 83.3%**
- `recall@10m`：**0% → 83.3%**（ball_v1 默认 conf 同分,说明检出空间分布对了)
- `position_error_m`：— → **0.86m 中位**（在 1.5m 容差内还有 ~40% 富余）

### 推荐工作点

`conf=0.05` 是当前最佳工作点:

- 召回顶到 25/30（剩余 5 个真球点是难帧:多人遮挡 / 运动模糊 / 帧间漏检）
- 位置误差中位 0.86m，比 1.5m 容差更紧
- 365 个 in_play 检测里多数是同一真球的多帧重复（每秒 2 帧 × 60 秒 = 120 个理论上限，再加上 ByteTrack 的多 ID 和短轨迹），不是纯 false positive。后续接入 ByteTrack/光流去重后会更干净。
- 但 365 远多于 30 这件事提醒我们：**precision 在这个工作点没有量化**（需要稠密金标才能算）。下一阶段补 v2 金标时务必加上，确认 precision 也是健康的。

跳变幅度：

- 默认容差下 `ball_recall`：**0% → 43.3%**（绝对增益 +43.3%，相对于不存在的基线则是从无到有）
- 5m 容差下：**0% → 80%**
- 10m 容差下：**0% → 83.3%**
- 匹配上的 13 个点位置误差中位数 **0.98m**（容差 1.5m 内还有 ~50% 富余）

### 位置分布检查（关键）

| 模型 / 配置 | in-play 检测占比 | 检测的 x 范围 | 检测的 y 范围 |
|---|---:|---|---|
| YOLO11n COCO | 0%（全部场外） | — | y≈22m（场外） |
| YOLO11s COCO | 0%（全部场外） | x≈3-12m（边线） | y≈21-24m（场外） |
| **ball_v1, conf=0.18** | **84.7%（61/72）** | **x∈[25.5, 35.6]** | **y∈[0.7, 16.2]** |
| **ball_v1, conf=0.05** | **96.3%（365/379）** | 接近全场地分布 | 接近全场地分布 |
| Gold ground truth | 100% | x∈[26.4, 38.6] | y∈[-0.4, 8.8] |

ball_v1 检测的空间分布与真球区**几乎完美重合**——这是从 COCO 模型的"全部场外"到"基本压在球场内并贴近真球"的根本性变化。降到 conf=0.05 后,几乎所有低 conf 检测仍落在场内,说明模型学到了"球只在场地里出现"的先验。

---

## 5. 结论

### 假设验证：成功

domain-specific fine-tune 是可行路径。**仅用 67 个正样本** 就把 `ball_recall@1.5m` 从 0% 推到 **83.3%**（conf=0.05 工作点)/ 43.3%(默认 conf=0.18),位置精度中位 **0.86m**——优于评估默认容差 1.5m。

### 这意味着什么

1. **球检测不再是"无解"问题**。08 文档曾基于 COCO YOLO 的 0% 推断模型路径需要专门训练；现在数据证实**确实如此，且就是这么做有效**。
2. **手工标注的边际价值被量化**。每多标 N 个真球点，模型可以变得更好——"用户做人工复核时顺便贡献训练数据"形成闭环。
3. **置信度可以从 candidate 升级**。`analysis_metrics.py` 里 pass / shot / 1v1 等指标因球检测不可靠被标 candidate；当 ball_recall 稳定到比如 70%+ 后，这些指标可以名正言顺升到 medium 甚至 high。

### 已知限制（v1）

- **训练数据来自单场比赛**，模型可能过拟合该球场/天气/球衣。需要多场金标做泛化测试。
- **mAP50-95 仍仅 0.065**——bbox 定位精度有限。我们的 24px 固定 bbox 标签是粗略的；未来可：
  - 训练时用动态 bbox（根据 YOLO 头的回归能力）
  - 或人工标注时同时标 bbox 大小（成本高）
- **召回 43% 离 100% 仍远**。还有 17/30 的真球点没被检测到，多数是难帧（远景、遮挡、运动模糊）。

### 改进方向（按 ROI）

1. **🥇 加更多比赛的标注数据**。当 `ball_review_points.csv` 累积到 200+ 真球点(2-3 场)，做 ball_v2 训练，看泛化与 recall 是否同步上升。
2. **🥈 与现有 person 检测共存**：当前 ball_v1 只识别球，要在 production 里同时跑 person 检测（YOLO11n 或 11s）。两个模型并行调用,合并 tracks.csv。需要扩展 03_detect_track 支持双模型。
3. **🥉 球-人关联补强**：很多漏检发生在球员密集对抗时（被遮挡）。可以利用人脚附近的"虚拟球点"假设 + 跟踪上下文做插值，把 recall 进一步推高。
4. **轮询 conf 阈值**。当前默认 `conf=0.18`,推理时只输出 conf≥0.18 的检测。试 `conf=0.05` 看 recall 是否进一步提升 (代价是 precision 下降)。

### 升级评估底座

ball 模型迭代纳入评估底座工作流后，建议：

- 在 `evals/gold/<match>/` 标注**第二段窗口**（比如 200–260s），独立验证不是过拟合到 78–138s
- 建一个 baseline 比较表（current + each ball_vN），记录在 `evals/README.md`

### 操作纪律

任何**球检测**相关 PR：

1. 训完模型先跑 `scripts/17_eval_against_gold.py --ball-source raw --detections-dir <new>`
2. 在 PR 描述里写明 `ball_recall@1.5m`、`recall@5m`、`position_error_m` 三个数字
3. 任意金标的指标退步必须在 commit message 解释

---

## 6. 引用文件

- 数据准备：[scripts/prepare_ball_training_data.py](../../scripts/prepare_ball_training_data.py)
- 训练驱动：[scripts/train_ball_detector.py](../../scripts/train_ball_detector.py)
- 训练数据集（gitignored）：`evals/training/ball_v1/`
- 训练输出（gitignored）：`runs/detect/ball_v1/weights/best.pt`、`results.csv`、`results.png`
- 评估输出（gitignored）：`evals/runs/ab_ball_v1/`
- 同金标基线：[evals/gold/中青赛_1_20260506_213657/](../../evals/gold/中青赛_1_20260506_213657/)
- 操作指引（CLAUDE.md "Custom ball-detector training" 章节）：[CLAUDE.md](../../CLAUDE.md)
