# E2 配套笔记本（可学习 + 可复跑）

本目录把 `../reports/e2-window-audit.md` 扩展成三本 Jupyter 笔记本：
**"不懂交叉熵/logprob/分桶/轨迹选择"的读者可以从 01 学起；想自己重算数字的用 02；想深挖口径与预算问题的用 03。**

| 文件 | 用途 | 数据需求 |
|---|---|---|
| `01-概念篇-logprob-采样窗-CE.ipynb` | 从零讲清：概率与 logprob、序列概率、采样与删失、采样窗 θ_B、分桶/校准、交叉熵（硬/软目标）、轨迹选择全流程（玩具模拟） | 无（完全自包含） |
| `02-实战篇-复现E2审计.ipynb` | 用真实证据包复现报告全部数字（总量、k 分布、窗内比例、校准表、失败对照、发现曲线），含图与 22 项自检 | < 5MB（自动准备） |
| `03-深挖篇-口径-选择-预算.ipynb` | 选择规则三命题核验、logprob 失准散点 + fate_m_009 逐 token 案例、预算 what-if（B90 线、期望解出曲线），含 12 项自检 | 同上 |

另附：

- `src/`：三本笔记本的 **jupytext percent 格式源码**（`.py`）。改完源码后重新生成 `.ipynb`：
  ```bash
  cd docs/diversity-mcts/notebooks
  jupytext --to notebook src/*.py && mv -f src/*.ipynb .
  ```
- 本目录的 `.ipynb` 均已**执行过**（输出已保存）：02 自检 22/22 通过，03 自检 12/12 通过（2026-10-07）。

## 如何运行

### 方式 A：my-new-linux 上的 JupyterLab（推荐）

```bash
# 远端启动（venv 已装好 jupyterlab/jupytext/nbconvert/ipykernel）
~/e2-venv/bin/jupyter lab --no-browser --port 8888 --ip 127.0.0.1
```

```bash
# 本地（你自己的机器）做端口转发后，浏览器打开 http://127.0.0.1:8888
ssh -N -L 8888:127.0.0.1:8888 a@my-new-linux
```

### 方式 B：VS Code Remote / 其他 Jupyter

打开本目录任一 `.ipynb`，内核选择 `~/e2-venv/bin/python`（该环境已注册 ipykernel）。

### 方式 C：无界面批量执行（回归验证）

```bash
cd docs/diversity-mcts/notebooks
~/e2-venv/bin/jupyter nbconvert --to notebook --execute --inplace *.ipynb
```

## 数据从哪来

- `02`/`03` 首次运行时自动把证据包解压到 `~/e2-work`（幂等，重复运行安全）。
  证据包位置：`hsy-的分析/extracted/modelscope_ce_online_delivery_20261006/evidence/`。
- 想换工作目录：设环境变量 `E2_WORK`，例如 `E2_WORK=/data/e2 jupyter lab`。

## 依赖

- Python 3 + `numpy` + `matplotlib`（系统已有）；
- `jupyterlab` / `jupytext` / `nbconvert` / `ipykernel`（装在 `~/e2-venv`，`--system-site-packages` 模式，可直接用系统 numpy/matplotlib）；
- 中文字体：Noto Sans CJK（图内中文正常显示；无该字体时自动回退，不影响运行）。

## 与报告/脚本的对应关系

| 笔记本章节 | 对应报告章节 | 对应脚本 |
|---|---|---|
| 02 §5–§7 | §1–§3 | `scripts/e2_audit_window.py` |
| 02 §8–§10 | §4–§6 | 同上 + `scripts/e2_audit_window_details.py` |
| 03 §1–§4 | §3–§4、§7 | 同上 |

> 数字口径与报告完全一致：`B=64`，`δ=0.1`，`θ_B = ln(1/δ)/B ≈ 3.6%`。
