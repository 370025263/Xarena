# 1. 创建一个目录来存放数据库文件
mkdir -p /home/leaderboard-db

# 2. （可选）设置权限，确保容器内进程可写
#    777 是为了方便测试，生产中应使用更精细的权限
chmod 777 /home/leaderboard-db

