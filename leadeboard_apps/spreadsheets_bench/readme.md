这里是榜单文件夹
本榜单为spreadsheet榜单，分为基线算法镜像和榜单镜像；

这个榜单整体采用skillopt的管线，拆分到两个镜像内。
skillopt分为train/val/test；
其中test的部分是可以不同算法之间共同使用一个评测容器的；


榜单镜像：
  test split, 此榜单镜像将会包含三种模式：single（with harnness）、multi（direct chat）、react（reactloop）；
  榜单接受的是单个skill文件或一堆skill文件夹。
  榜单输出的是题目通过率，以及题目详情（input.xsls,output.xsls,task.md; chatmessage.log聊天过程日志；是否通过Yes/No)
  榜单single模式：采用TARGET_BACKEND=claude_code_exec方式来评测，需要显式指定；
  榜单multi模式：
  榜单react模式：
  每一个模式单独建立一个榜单镜像，但是共享同一个目录，每一个榜单模式对应一个build_single.sh之类的脚本即可。

   
打榜镜像：
  train/val split, 可用于训练，交付产物为skill文件或者多个skill文件。
  此打榜镜像会包含多个版本，xskill、trace2skill、skillopt；
  xskill交付的是多个skill的文件夹（标准的authoropic格式）类似skills/，对应地只能跑在with harness的single下（使用claude——）
  trace2skill和skillopt交付的是单个skill.md文件，适配所有三种模式；
  打榜拉起后，会进行训练，训练阶段完毕就会发送开始信号给榜单镜像，榜单镜像就会请求skill包；
  skill包有两种约定：单skill.md和多skill folder；
  t2s+skillopt可用单skill.md约定输出，但是xskill采用多skill约定输出；
  榜单的single multi react全部接受单skill.md的约定+多skill约定；榜单单skill约定在skillopt评测管线中原生支持，我们不做修改；榜单多skill约定我们只在之前的基础上做最小改动；





