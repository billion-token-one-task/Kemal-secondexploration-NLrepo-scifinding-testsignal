# NewtonBench (科学发现) 实验结果报告

> **实验日期**: 2026-03-11
> **Benchmark**: NewtonBench
> **批次**: batch_20260311_040300
> **任务类型**: 通过实验发现物理定律（科学发现）

---

## 1. 实验设计

### 1.1 研究问题

在**科学发现**任务中，不同类型的验证反馈如何影响 AI Agent 发现物理规律的能力？

### 1.2 四臂对比（4 Arms）

| Arm | 名称 | 信号策略 | Agent 可见信息 |
|-----|------|----------|---------------|
| **A** | Batched Dataset | 一次性花掉全部实验预算，然后仅分析 | 全部实验的原始输出，无 R² 反馈 |
| **B** | Interactive Observation | 标准行为：实验→观察→修正 | 每次实验的原始输出，无 R² 反馈 |
| **C** | Quantitative Feedback | 每次实验后给出定量反馈 | 原始输出 + hidden validation points 上的 R², RMSLE |
| **D** | Directional Feedback | 每次实验后给出方向性反馈 | 原始输出 + 误差最大的变量/区间/方向 |

### 1.3 数据泄漏防护

- **实验数据**（Agent 指定参数 → 模块返回观测值）: Agent 可见
- **评估数据**（模块内部随机生成的 test_data，如 5000 个随机点）: Agent **不可见**
- 评估点无固定随机种子，每次调用重新生成
- C/D arm 给出的 R² 是在 hidden validation points 上计算的汇总指标
- Agent 从未看到评估点的具体坐标

### 1.4 评估指标

- `best_r2`: 最佳假设在 hidden validation points 上的 R² 分数（0.0-1.0）
- `rounds`: 使用的回合数
- `num_experiments`: 执行的实验总数

---

## 2. 任务总览

共 **20 个 pilot 任务**，涵盖 12 个物理模块 × 2 种系统类型：

### 2.1 Vanilla Equation（12 个，直接方程拟合）

| 模块 | 物理定律 |
|------|---------|
| m0_gravity | 万有引力 |
| m1_coulomb_force | 库仑力 |
| m2_magnetic_force | 磁力 |
| m3_fourier_law | 傅里叶热传导定律 |
| m4_snell_law | 斯涅尔折射定律 |
| m5_radioactive_decay | 放射性衰变 |
| m6_underdamped_harmonic | 欠阻尼谐振 |
| m7_malus_law | 马吕斯定律 |
| m8_sound_speed | 声速 |
| m9_hooke_law | 胡克定律 |
| m10_be_distribution | 玻色-爱因斯坦分布 |
| m11_heat_transfer | 热传递 |

### 2.2 Simple System（8 个，含系统动力学）

与上述相同的物理模块，但增加了系统层面的复杂性。

---

## 3. 逐任务详细结果

### 3.1 Vanilla Equation 任务

| 模块 | A (Batched) | B (Interactive) | C (Quantitative) | D (Directional) |
|------|------------|-----------------|-------------------|-----------------|
| **m0_gravity** | **1.000** | **1.000** | **1.000** | **1.000** |
| **m1_coulomb** | **1.000** | **1.000** | **1.000** | **1.000** |
| **m2_magnetic** | **1.000** | **1.000** | **1.000** | **1.000** |
| **m3_fourier** | **1.000** | **1.000** | **1.000** | **1.000** |
| **m9_hooke** | **1.000** | **1.000** | **1.000** | **1.000** |
| m6_harmonic | **1.000** | **1.000** | **1.000** | **1.000** |
| m5_decay | 0.954 | 0.964 | **0.997** | 0.974 |
| m7_malus | 0.948 | 0.410 | **1.000** | 0.967 |
| m10_be_dist | 0.000 | 0.000 | **0.725** | 0.012 |
| m4_snell | 0.000 | 0.000 | **1.000** | 0.000 |
| m11_heat | 0.570 | **1.000** | **1.000** | 0.000 |
| m8_sound | **0.487** | 0.000 | 0.000 | 0.000 |

### 3.2 Simple System 任务

| 模块 | A (Batched) | B (Interactive) | C (Quantitative) | D (Directional) |
|------|------------|-----------------|-------------------|-----------------|
| **m1_coulomb** | **1.000** | 0.748 | 0.000 | **1.000** |
| m5_decay | 0.715 | 0.000 | 0.000 | **0.893** |
| m2_magnetic | 0.000 | 0.827 | **0.835** | 0.830 |
| m11_heat | 0.000 | 0.000 | **0.485** | 0.000 |
| m3_fourier | **0.381** | 0.015 | 0.000 | 0.007 |
| m0_gravity | **0.067** | 0.000 | 0.000 | 0.000 |
| m4_snell | 0.000 | 0.000 | 0.000 | 0.000 |
| m10_be_dist | *(failed)* | — | — | — |

---

## 4. 汇总统计（19 个有效任务）

### 4.1 各 Arm 汇总

| 指标 | A (Batched) | B (Interactive) | C (Quantitative) | D (Directional) |
|------|------------|-----------------|-------------------|-----------------|
| **Wins** | 5.0 | 3.0 | **8.0** | 3.0 |
| **Strict Wins** | 3 | 1 | **6** | 1 |
| **avg best R²** | 0.585 | 0.524 | **0.634** | 0.562 |
| **Perfect (R²=1.0)** | 6 | 6 | **8** | 6 |
| **avg rounds** | 1.89 | 2.42 | 2.21 | 2.05 |
| **avg experiments** | 5.37 | 5.47 | 5.68 | 5.11 |

### 4.2 按系统类型分解

| 系统 | 任务数 | 最佳 Arm | avg best R² |
|------|--------|----------|-------------|
| **vanilla_equation** | 12 | **C_quantitative** (wins=5.75) | **0.893** |
| **simple_system** | 7 | D_directional (wins=1.75) | 0.390 |

### 4.3 逐任务 Winner

| 任务 | 系统 | best R² | Winner(s) |
|------|------|---------|-----------|
| m0_gravity | vanilla | 1.000 | A, B, C, D (全部) |
| m1_coulomb | vanilla | 1.000 | A, B, C, D (全部) |
| m2_magnetic | vanilla | 1.000 | A, B, C, D (全部) |
| m3_fourier | vanilla | 1.000 | A, B, C, D (全部) |
| m9_hooke | vanilla | 1.000 | A, B, C, D (全部) |
| m4_snell | vanilla | 1.000 | **C** |
| m7_malus | vanilla | 1.000 | **C** |
| m11_heat | vanilla | 1.000 | B, **C** |
| m5_decay | vanilla | 0.997 | **C** |
| m10_be_dist | vanilla | 0.725 | **C** |
| m6_harmonic | vanilla | ~1.000 | B (微弱优势) |
| m8_sound | vanilla | 0.487 | A |
| m1_coulomb | simple | 1.000 | A, D |
| m5_decay | simple | 0.893 | D |
| m2_magnetic | simple | 0.835 | **C** |
| m11_heat | simple | 0.485 | **C** |
| m3_fourier | simple | 0.381 | A |
| m0_gravity | simple | 0.067 | A |
| m4_snell | simple | 0.000 | (全部平手) |

---

## 5. 关键发现

1. **C_quantitative_feedback 是最大赢家**: 8 wins / 6 strict wins / avg R² 0.634 / 8 个 perfect
2. **Vanilla equation 比 simple system 容易得多**: avg R² 0.893 vs 0.19-0.39
3. **6 个简单定律所有 arm 都能完美发现**: gravity, coulomb, magnetic, fourier, hooke, harmonic
4. **定量反馈在困难任务上优势巨大**: snell (0→1.0), malus (0.41→1.0), be_dist (0→0.725)
5. **1 个任务失败**: m10_be_distribution (simple_system), returncode 1
