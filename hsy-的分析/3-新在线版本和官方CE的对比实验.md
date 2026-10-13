C:\xwechat_files\wxid_vskfl3t3qji122_cd47\temp\RWTemp\2026-10\f1e06724629e2042dd7b6ebb9dfc57d0\modelscope_ce_online_delivery_20261006(1).zip



交付：ModelScope / REAL-Prover 7B 的 CE 与 Online-v2 对照实验

材料来源：
FATE-M 固定提交4eb33c8的20个代数母题，派生为20×200课程；
模型固定REAL-Prover 7B revision fe76f68d，使用相同初始LoRA、
64桶value head和真实Lean4.28验证。来源快照、许可、生成代码、
版本与哈希均在ZIP中。

实验内容：
实际训练20个family的v001，共20题、一个共同搜索波。
比较成功搜索路径CE与问题分组clipped PPO+KL（Online-v2）。
评测同族v009/v010共40题，使用相同参数和配对seed，
每题最多4次，首次严格成功后停止，heldout不参与调参。

结果：
策略                 解出题数       前1/2/4次内成功率
初始模型             24/40（60%）   25% / 50% / 60%
CE                   25/40（62.5%） 27.5% / 52.5% / 62.5%
Online回滚部署       24/40（60%）   复用初始模型证据，非独立评测

CE净增1题（+2.5个百分点）、没有退步题；两个独立评测均无截断
和timeout。CE学习20条transition、137个action token、更新1次。
Online使用233个候选尝试2个epoch，但KL=0.087034超过冻结上限
0.05，更新被拒绝并完整回滚，已提交更新0；身份审计通过。

结论：
40题均提供curriculum_target假设，新增成功仅使用
exact curriculum_target。因此只能说明本次脚手架利用表现，
不能证明数学证明发现能力提升或CE算法优于Online。
两臂暴露和成本不等；原多波协议及双方接受更新门禁仍未完成。

PR已提出并更新：
https://github.com/wufuju2023-cell/10-4-alpha-proof/pull/1
重要代码修复、冻结配置/数据、结果与审计、材料来源/许可、
交付说明和打包脚本均已提交，尚未合并。
实验代码检查325项通过、2项跳过；原始证据逐成员校验通过。

ZIP结构：
DELIVERY.txt：交付说明
code/：对应PR提交的完整源码、数据、测试和报告
evidence/：三个原样原始证据包
MANIFEST.json：文件哈希及对应PR提交
模型/checkpoint大文件不在ZIP内，保留其哈希和远端路径。****
