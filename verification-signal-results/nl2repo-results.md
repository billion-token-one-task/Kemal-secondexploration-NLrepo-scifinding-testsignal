# NL2Repo (软件工程) 实验结果报告

> **实验日期**: 2026-03-11
> **Benchmark**: NL2RepoBench
> **批次**: batch_20260311_033300
> **任务类型**: 从自然语言描述生成完整代码仓库

---

## 1. 实验设计

### 1.1 研究问题

在**代码生成（软件工程）**任务中，不同密度的验证信号（test feedback）如何影响 AI Agent 的代码质量？

### 1.2 四臂对比（4 Arms）

| Arm | 名称 | 信号策略 | Agent 可见测试 |
|-----|------|----------|---------------|
| **A** | Sparse Final Only | 仅最终提交时验证一次 | 无（直到最终提交） |
| **B** | Staged Modules | 阶段性验证（按模块组） | import + interface 测试 |
| **C** | Dense Any Test | 任意时刻可验证所有非 heldout 测试 | import + interface + functional + integration |
| **D** | Progressive Release | 按进度逐步解锁更多测试 | 随进度递增：0%→import, 30%→+interface, 60%→+functional, 80%→+integration |

### 1.3 数据泄漏防护

- 测试用例按类别分为 `import`, `interface`, `functional`, `integration`, `heldout` 五个桶
- **heldout 占 20%**，从 functional/integration 中抽取
- `determine_visible_files()` 从不包含 heldout
- `apply_visibility()` 仅拷贝 visible 测试到工作区
- **heldout 仅在 Agent 结束后评估**，不存在 data leakage

### 1.4 评估指标

- `visible_passed`: Agent 可见测试通过数
- `full_passed`: 全部测试（visible + heldout）通过数
- `heldout_passed`: 仅 heldout 测试通过数（核心指标）

---

## 2. 任务总览

共 **20 个 pilot 任务**（来自真实开源项目），涵盖不同规模与复杂度：

| 任务 | 测试用例数 | 项目类型 |
|------|-----------|---------|
| ydata-profiling | 2,182 | 数据分析 |
| dictdatabase | 594 | 数据库 |
| autopep8 | 564 | 代码格式化 |
| pandarallel | 217 | 并行计算 |
| aiofiles | 202 | 异步IO |
| python-fsutil | 152 | 文件工具 |
| box | 147 | 数据结构 |
| gitingest | 133 | Git工具 |
| verifiers | 123 | 验证器 |
| synthetic | 98 | 合成任务 |
| python-slugify | 82 | 文本处理 |
| ipytest | 81 | 测试框架 |
| markupsafe | 78 | HTML转义 |
| pyquery | 73 | HTML查询 |
| sklearn | 70 | 机器学习 |
| pss | 46 | 搜索工具 |
| flasky | 34 | Web框架 |
| retrying | 23 | 重试库 |
| coverage_shield | 15 | 覆盖率工具 |
| trimming | 10 | 文本处理 |

---

## 3. 逐任务详细结果

> 格式: visible_passed / full_passed / heldout_passed

| 任务 | 总测试数 | A (Sparse) | B (Staged) | C (Dense) | D (Progressive) |
|------|---------|------------|------------|-----------|-----------------|
| **pandarallel** | 217 | 73/73/73 | 0/0/0 | **217/217/217** | **217/217/217** |
| **python-slugify** | 82 | 65/65/65 | 64/64/64 | **75/75/75** | 64/64/64 |
| **retrying** | 23 | **22/22/22** | 19/19/19 | **22/22/22** | **22/22/22** |
| **markupsafe** | 78 | 28/72/44 | 14/36/22 | **28/72/44** | 14/35/21 |
| **aiofiles** | 202 | 1/1/0 | 0/0/0 | **151/194/43** | 0/0/0 |
| **pyquery** | 73 | 0/0/0 | **0/0/73** | 0/0/0 | 0/0/13 |
| **box** | 147 | 0/0/0 | 0/0/5 | 0/0/0 | 0/0/2 |
| coverage_shield | 15 | 0/0/0 | 0/0/5 | 0/0/5 | 0/0/0 |
| sklearn | 70 | 0/0/0 | 0/0/0 | 0/0/2 | 0/0/2 |
| python-fsutil | 152 | 1/1/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| autopep8 | 564 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| flasky | 34 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| dictdatabase | 594 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| gitingest | 133 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| pss | 46 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| synthetic | 98 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| trimming | 10 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| verifiers | 123 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| ydata-profiling | 2,182 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| ipytest | 81 | *(killed)* | — | — | — |

---

## 4. 汇总统计（已完成的 13 个任务）

### 4.1 各 Arm 汇总

| 指标 | A (Sparse) | B (Staged) | C (Dense) | D (Progressive) |
|------|-----------|------------|-----------|-----------------|
| **Wins** | 1.83 | 4.33 | **4.83** | 2.00 |
| **Strict Wins** | 0 | 2 | **2** | 0 |
| **avg visible ratio** | 16.2% | 12.7% | **29.0%** | 20.4% |
| **avg full ratio** | 15.9% | 12.8% | **28.7%** | 20.3% |
| **avg heldout ratio** | 15.5% | 29.2% | **36.1%** | 22.0% |
| **avg visible passed** | 12.9 | 6.0 | **36.2** | 22.7 |
| **avg full passed** | 16.3 | 7.7 | **42.9** | 24.3 |
| **avg heldout passed** | 14.0 | 13.0 | **29.5** | 24.4 |
| **ok heldout count** | 0 | 1 | **2** | 1 |

### 4.2 逐任务 Winner（按 heldout ratio）

| 任务 | best heldout ratio | Winner(s) |
|------|-------------------|-----------|
| pandarallel | 100% | C, D |
| pyquery | 98.6% | B |
| python-slugify | 91.5% | C |
| aiofiles | 89.6% | C |
| markupsafe | 88.0% | A, B, C |
| coverage_shield | 100% | B, C |
| box | 15.2% | B |
| 其余 6 个任务 | 0% | (全部平手) |

---

## 5. 关键发现

1. **C_dense_any_test 整体表现最优**: 在 heldout ratio 上平均达 36.1%，wins 最高 (4.83)
2. **11/20 任务全 arm 均为 0**: 说明从零开始构建完整仓库对 Agent 仍是极大挑战
3. **6/20 任务有显著通过**: pandarallel, python-slugify, retrying, markupsafe, aiofiles, pyquery
4. **密集反馈的关键优势体现在中等难度任务上**: aiofiles 在 C arm 下从 0 跃升到 151 visible passed
5. **1 个任务 (ipytest) 因超时被 kill**: returncode -15
