# ALFWorld 全量数据集（Git LFS 追踪）

ALFWorld json_2.1.x 全量游戏数据集（来自 alfworld/alfworld GitHub releases），
经 **Git LFS** 追踪，避免污染常规 clone：

| zip | 内容 | 解压后 |
| --- | --- | --- |
| `json_2.1.1_json.zip` (72M) | traj_data.json（任务/演示） | ~ |
| `json_2.1.2_tw-pddl.zip` (36M) | game.tw-pddl（TextWorld 游戏） | ~ |
| `json_2.1.1_pddl.zip` (35M) | PDDL domain/problem | ~1.7GB（4027 game.tw-pddl / 7080 traj_data.json） |

用法：`mkdir -p $ALFWORLD_DATA && for z in *.zip; do unzip -q -o "$z" -d $ALFWORLD_DATA; done`
（或用 `../fetch_data.sh` 从上游 release 重新下载。191 个 split gamefile 全部 resolve。）
