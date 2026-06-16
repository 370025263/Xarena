
export http_proxy="http://50.67.89.137:10086"
export https_proxy="http://50.67.89.137:10086"
export no_proxy="localhost,127.0.0.1,*.local"

# 1. 构建镜像
# -t 表示 "tag"，我们给它取个名字叫 leaderboard-api
# docker build -t leaderboard-api:latest .
docker build \
  --build-arg http_proxy=$http_proxy \
  --build-arg https_proxy=$https_proxy \
  --build-arg no_proxy=$no_proxy \
  -t leaderboard-api:latest .

# 2. 标记 (Tag) 镜像，使其指向你的本地 Registry
# 格式: [REGISTRY_HOST]/[PROJECT]/[IMAGE]:[TAG]
docker tag leaderboard-api:latest localhost:5000/leaderboard-api:v1

# 3. 推送镜像到你的本地 Registry
docker push localhost:5000/leaderboard-api:v1

