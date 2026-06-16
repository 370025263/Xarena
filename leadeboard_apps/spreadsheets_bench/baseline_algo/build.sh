# NETWORK
export http_proxy="http://50.67.89.228:10086"
export https_proxy="http://50.67.89.228:10086"
export no_proxy="localhost,127.0.0.1,*.local,leaderboard-api-svc"

# 1. 构建镜像
# -t 表示 "tag"，我们给它取个名字叫 algo-app
docker build \
  --build-arg http_proxy=$http_proxy \
  --build-arg https_proxy=$https_proxy \
  --build-arg no_proxy=$no_proxy \
  -t naive-algo-sjs-app:latest .


# 标记为打榜人 p_user1 的镜像
docker tag naive-algo-sjs-app localhost:5000/p_user1/naive-sjs-app:v1
docker push localhost:5000/p_user1/naive-sjs-app:v1

