# 战舰世界yuyuko战绩查询机器人

# 确保hikari-core依赖版本大于等于1.2.2

# 升级hikari-core依赖（第一次安装不用看）

astrbot管理面里面找到平台日志，右上角有个安装安装pip依赖

点击输入 `hikari-core==1.2.2`

其中1.2.2为hikari-core版本 建议填入最新版本

暂时仅支持QQ个人，频道版本后续支持

# onebot v11 配置
```aiignore
ws://127.0.0.1:8080/onebot/v11/ws
```

### 机器人插件冲突问题

如果你同时安装了其他插件然后发现docker重启后无法启动，一般是下面两个依赖导致的。
注意pydantic最新版本hikari-core不兼容，sqlmodel最新版本也不兼容pydantic。

```
sqlmodel==0.0.24
pydantic==2.10.3
```

### 如果你已经安装了插件，请执行以下命令
pip install sqlmodel==0.0.24 pydantic==2.10.3 -i https://pypi.tuna.tsinghua.edu.cn/simple
