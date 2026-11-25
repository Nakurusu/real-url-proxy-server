# real-url-proxy-server

用**房间号**直接取直播真实流地址的本地代理服务器，支持 **斗鱼 / 虎牙 / Bilibili / YouTube**。

播放器访问 `http://<本机IP>:<端口>/<平台>/<房间号>`，服务器返回真实流地址（301 跳转或 m3u8），
播放器后续直接连 CDN，流量不过本机，所以服务器负载和占用都很低。

> **斗鱼和 B站 都需要登录才能拿到最高画质**（两个平台都会把未登录用户降级）：
>
> | 平台 | 未登录 | 登录后 |
> | --- | --- | --- |
> | 斗鱼 | 1080p30 / 4000kbps | **原画 1080P60 / 16384kbps** |
> | B站 | 1280x720@30fps（被压到 qn=250） | **原画 1920x1080@60fps** |
>
> 两边都提供了扫码登录工具，登录一次即可。

---

## 目录

- [快速开始](#快速开始)
- [斗鱼：怎么拿到最高画质](#斗鱼怎么拿到最高画质)
- [B站：怎么拿到原画 1080P60](#b站怎么拿到原画-1080p60)
- [运行参数](#运行参数)
- [访问方式](#访问方式)
- [刷新机制](#刷新机制)
- [常见问题](#常见问题)
- [文件说明](#文件说明)
- [感谢](#感谢)

---

## 快速开始

```bash
cd real-url-proxy-server

# 1) 取登录 Cookie（都是扫码，不需要任何系统权限）
python3 douyu_cookie.py --wechat       # 斗鱼（微信扫码）
python3 bilibili_cookie.py --wechat    # B站（微信扫码）

# 2) 起服务（-c / -b 可省略，默认读同目录的 douyu_cookie.txt / bilibili_cookie.txt）
python3 real-url-proxy-server.py -p 8080 -c douyu_cookie.txt -b bilibili_cookie.txt

# 3) 播放
iina http://127.0.0.1:8080/douyu/6657          # 原画（最高可得）
iina http://127.0.0.1:8080/douyu/6657/4000     # 蓝光4M
iina http://127.0.0.1:8080/bilibili/6          # B站原画
```

启动日志会告诉你两个平台的登录态：

```
INFO: douyu cookie 已加载（acf_uid, acf_stk, acf_auth, dy_auth）: douyu_cookie.txt
INFO: bilibili cookie 已加载（SESSDATA）: bilibili_cookie.txt
```

启动日志会告诉你登录态是否正常：

```
INFO: douyu cookie 已加载（acf_uid, acf_stk, acf_auth, dy_auth）: douyu_cookie.txt
INFO: Serving HTTP on 0.0.0.0 port 8080...
```

看到 `douyu cookie 未找到` 就说明没登录，画质会被斗鱼降到 4000kbps。

---

## 斗鱼：怎么拿到最高画质

### 1. 关键结论：斗鱼按登录状态给画质

同一个房间、同一个接口（`rate=0`，也就是网页上的「原画」），实测：

| 状态 | 实际拿到的流 |
| --- | --- |
| 未登录 | 1920x1080 **@30fps** / **4000kbps** |
| **已登录** | 1920x1080 **@60fps** / **16384kbps**（以 6657 为例） |

数据是读流里的 FLV `onMetaData` 拿到的（`python3 check_quality.py 房间号` 可以自己复核）。
也就是说：**不登录，斗鱼会把「原画 / 蓝光8M」静默降级到 4000kbps 档**。
所以想提高清晰度 = PC 网页接口 + 登录 Cookie，两件事都要有。

### 2. 取登录 Cookie

```bash
python3 douyu_cookie.py --wechat      # 微信扫码（推荐，浏览器里出二维码）
python3 douyu_cookie.py --qrcode      # 斗鱼 App 扫码
python3 douyu_cookie.py --browser safari   # 读浏览器里的 Cookie（需要完全磁盘访问权限）
python3 douyu_cookie.py --paste       # 手动粘贴开发者工具里的 Cookie 头
```

成功后会写入 `douyu_cookie.txt`（权限 600，已加入 `.gitignore`）。
日志里出现 `acf_uid, acf_stk, acf_auth, dy_auth` 之类就是登录态。

> 为什么推荐扫码：macOS 会保护浏览器数据，非授权进程读 Chrome/Safari 的 Cookie 会报
> `Operation not permitted`，需要给终端授予「完全磁盘访问权限」；扫码走网络接口，零权限依赖。

### 3. 各清晰度对应的地址

| 地址 | 档位 | 6657 实测 |
| --- | --- | --- |
| `/douyu/房间号` | 原画（最高可得） | 1080p60 / 16384kbps |
| `/douyu/房间号/8000` | 蓝光8M | 1080p60 / 8000kbps |
| `/douyu/房间号/4000` | 蓝光4M | 1080p30 / 4000kbps |
| `/douyu/房间号/2000` | 超清 | 720p30 / 2000kbps |
| `/douyu/房间号/900` | 高清 | 540p25 / 900kbps |
| `/douyu/房间号/refresh` | 手动刷新真实地址 | — |

具体有哪些档、每档多少码率，各房间不一样，以接口返回的 `multirates` 为准
（`check_quality.py` 会打印出来）。

### 4. 验证画质

```bash
python3 check_quality.py 6657                # 用本地 Cookie
python3 check_quality.py 6657 --no-cookie    # 强制匿名，用来对比降级效果
```

会打印每一档**真实**的分辨率 / 帧率 / 码率，以及斗鱼宣称的值和 `diamondFan`（是否要粉丝牌）。

---

## B站：怎么拿到原画 1080P60

### 1. 关键结论：和斗鱼一样，未登录会被降档

B站的取流接口有个 `qn`（画质）参数：`150` 高清 / `250` 超清 / `400` 蓝光 / `10000` 原画 /
`20000` 4K / `30000` 杜比。**未登录时，不管你请求哪一档，接口返回的 `current_qn` 都会被压到 250。**

实测房间 6（英雄联盟赛事，真实房间 7734200）：

| 请求的档 | 登录后实际拿到 | 未登录实际拿到 |
| --- | --- | --- |
| 原画 10000 | **1920x1080 @60fps** | 1280x720 @30fps（被压到 250） |
| 蓝光 400 | 1920x1080 @30fps | 同上 |
| 超清 250 | 1280x720 @30fps | 1280x720 @30fps |
| 高清 150 | 854x480 @30fps | 同上（被压） |

> 4K(20000) / 杜比(30000) 还需要大会员；普通登录账号最高到原画。

### 2. 取登录 Cookie

```bash
python3 bilibili_cookie.py --wechat    # 微信扫码（推荐）
python3 bilibili_cookie.py --qrcode    # B站 App 扫码
python3 bilibili_cookie.py --paste     # 手动粘贴开发者工具里的 Cookie
```

成功后写入 `bilibili_cookie.txt`（权限 600），关键字段是 `SESSDATA`。

### 3. 各清晰度对应的地址

| 地址 | 档位 |
| --- | --- |
| `/bilibili/房间号` | 原画（最高可得） |
| `/bilibili/房间号/400` | 蓝光 |
| `/bilibili/房间号/250` | 超清 |
| `/bilibili/房间号/150` | 高清 |
| `/bilibili/房间号/m3u8` | 同档位的 HLS 版（给只吃 m3u8 的播放器） |

### 4. 验证画质

```bash
python3 check_quality.py --site bilibili 6
python3 check_quality.py --site bilibili 6 --no-cookie    # 强制匿名，做对比
```

会打印每一档**实际的 qn**（被降级会标 `← 被降级`）以及真实的分辨率、帧率和实测码率。

## 运行参数

```
python3 real-url-proxy-server.py [-h] -p PORT [-r REFRESH] [-c DOUYU_COOKIE_FILE]
                                 [-k YOUTUBE_COOKIE_FILE] [-l LOG_FILE]
```

| 参数 | 说明 |
| --- | --- |
| `-p PORT` | 端口，监听 `0.0.0.0:PORT`（局域网设备也能访问） |
| `-r REFRESH` | 自动刷新间隔（秒），0 表示不自动刷新。**只影响虎牙/B站/YouTube**；斗鱼地址约 300 秒过期，程序内部固定 240 秒重取，不受这个值影响 |
| `-c FILE` | 斗鱼登录 Cookie 文件；不指定时默认读 `douyu.py` 同目录的 `douyu_cookie.txt` |
| `-b FILE` | B站登录 Cookie 文件；不指定时默认读 `bilibili.py` 同目录的 `bilibili_cookie.txt` |
| `-k FILE` | YouTube 的 Cookie 文件 |
| `-l FILE` | 日志文件，不指定则只输出到控制台 |

---

## 访问方式

**斗鱼**（详见[上面](#3-各清晰度对应的地址)）

```
http://xxx.xxx.xxx.xxx:xxxx/douyu/房间号          # 原画
http://xxx.xxx.xxx.xxx:xxxx/douyu/房间号/4000     # 蓝光4M
http://xxx.xxx.xxx.xxx:xxxx/douyu/房间号/2000     # 超清
```

**虎牙**

```
http://xxx.xxx.xxx.xxx:xxxx/huya/房间号           # 4M
http://xxx.xxx.xxx.xxx:xxxx/huya/房间号/2000p     # 2M
```

**Bilibili**（详见[B站那节](#b站怎么拿到原画-1080p60)）

```
http://xxx.xxx.xxx.xxx:xxxx/bilibili/房间号          # 原画
http://xxx.xxx.xxx.xxx:xxxx/bilibili/房间号/400      # 蓝光
http://xxx.xxx.xxx.xxx:xxxx/bilibili/房间号/250      # 超清
http://xxx.xxx.xxx.xxx:xxxx/bilibili/房间号/150      # 高清
http://xxx.xxx.xxx.xxx:xxxx/bilibili/房间号/m3u8     # HLS（给只吃 m3u8 的播放器）
```

**YouTube**

```
http://xxx.xxx.xxx.xxx:xxxx/youtube/视频ID
```

---

## 刷新机制

程序首次取到真实地址后会缓存，后续访问用缓存；到期自动刷新，也可以手动访问
`http://xxx.xxx.xxx.xxx:xxxx/平台/房间号/refresh` 强制刷新。

斗鱼换成了带 token 的新接口，地址有效期约 300 秒（登录后实测 `expire=0`，但 CDN 地址仍会换），
所以斗鱼的地址固定 240 秒重取一次；B站地址带 `expires`，按 300 秒重取。**两者都与 `-r` 无关。**

---

## 常见问题

**Q：播放器一直转圈？**
`/douyu/房间号` 返回的是 **301 跳转**，播放器需要会跟随跳转、并且支持 HTTP-FLV。
推荐 **IINA**（mpv 内核，`brew install --cask iina`）：

```bash
iina http://127.0.0.1:8080/douyu/6657
```

不跟随跳转的播放器（例如 OmniPlayer）会一直转圈。

**Q：画质掉回 4000kbps 了？**
Cookie 失效了，重新扫码即可，**服务不用重启**（每次取地址都会重新读 Cookie 文件）：

```bash
python3 douyu_cookie.py --wechat
python3 check_quality.py 6657     # 确认恢复到 1080p60
```

**Q：B站只有 720p（超清），没有原画？**
Cookie 失效了，重新扫码即可，服务不用重启：

```bash
python3 bilibili_cookie.py --wechat
python3 check_quality.py --site bilibili 6     # 确认恢复到 1920x1080@60fps
```

**Q：地址是 `.flv` 不是 m3u8？**
斗鱼新接口只给 HTTP-FLV，播放端要支持 FLV（IINA、mpv、PotPlayer、Kodi +
inputstream.ffmpegdirect 都行）。把斗鱼的扩展名改成 `.m3u8` 没用，CDN 还是回 `video/x-flv`。
B站两种都有，要 HLS 就用 `/bilibili/房间号/m3u8`。

**Q：某些档看不了 / 提示要粉丝牌？**
`check_quality.py` 输出的 `diamondFan` 大于 0 就表示该档需要粉丝牌或贵族。

**Q：局域网设备访问不了？**
确认手机/盒子与电脑同一网段，地址用电脑的局域网 IP（`ipconfig getifaddr en0`）。
macOS 防火墙第一次会弹「是否允许 python 接受传入网络连接」，点允许。

**Q：2K/4K 原画没有画面？**
部分房间的高分辨率原画只有 H.265 版本，可以试 `export DOUYU_HEVC=1` 后再启动。

---

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `real-url-proxy-server.py` | 代理服务器主体 |
| `douyu.py` | 斗鱼取流：PC 端 `getH5PlayV1` + `websec/getEncryption` 签名，自动加载登录 Cookie |
| `douyu_cookie.py` | 取斗鱼登录 Cookie：微信扫码 / 斗鱼 App 扫码 / 读浏览器 / 手动粘贴 |
| `bilibili.py` | B站取流：按 qn 取各档地址，自动加载登录 Cookie，支持选 `flv`/`m3u8` |
| `bilibili_cookie.py` | 取 B站登录 Cookie：微信扫码 / B站 App 扫码 / 手动粘贴 |
| `check_quality.py` | 校验各档清晰度**真实**的分辨率、帧率、码率（`--site douyu\|bilibili`） |
| `douyu_cookie.txt` / `bilibili_cookie.txt` | 登录凭证，权限 600，已在 `.gitignore` 中 |
| `huya.py` / `bilibili.py` / `youtube.py` | 其它平台取流 |
| `stream_tester.py` | 流可用性测试脚本 |

---

## 感谢

- 获取直播源地址使用的 douyu.py、huya.py 及 bilibili.py 代码来自于
  [Real-Url](https://github.com/wbt5/real-url) 项目，在此表示由衷的感谢！
- 斗鱼 PC 接口的签名与取流思路参考了 [streamlink](https://github.com/streamlink/streamlink) 的 douyu 插件。
- B站的 `qn`/画质接口与登录方式参考了 B站 Web 端（`passport.bilibili.com`）的公开接口。
