# 战舰世界yuyuko战绩查询机器人

# 确保hikari-core依赖版本大于等于1.2.2

# 升级hikari-core依赖（第一次安装不用看）

astrbot管理面里面找到平台日志，右上角有个安装安装pip依赖

点击输入 `hikari-core==1.2.2`

其中1.2.2为hikari-core版本 建议填入最新版本

暂时仅支持QQ个人，频道版本后续支持

# onebot v11 配置
```
ws://127.0.0.1:8080/onebot/v11/ws
```
或者
```
ws://127.0.0.1:8080/ws
```

### 机器人插件冲突问题

如果你同时安装了其他插件然后发现docker重启后无法启动，一般是下面两个依赖导致的。
注意pydantic最新版本hikari-core不兼容，sqlmodel最新版本也不兼容pydantic。


```
sqlmodel==0.0.24
pydantic==2.10.3
```

# 安装字体（yuyuko模板使用的是微软雅黑）

从Windows把雅黑相关字体复制到下面的目录

### 创建字体目录（如果不存在）
sudo mkdir -p /usr/share/fonts/truetype/microsoft/

### 复制字体文件到目录（假设文件已放在当前用户的Downloads目录）
sudo cp ~/Downloads/msyh*.ttc /usr/share/fonts/truetype/microsoft/

### 更新字体缓存
sudo fc-cache -fv