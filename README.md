# 我的课表 · 日历订阅

把珠海城职院「智慧校园信息平台」的课表，自动同步成一份 `calendar.ics`，供手机 / 电脑日历订阅。
**课表有变化才提交**，所以 git 历史本身就是一份调课记录。

---

## 它是怎么工作的

```
VPN 容器 (EasyConnect)  →  内网 192.168.66.199  →  登录教务系统（含验证码识别）
   →  抓 20 周「我的周课表」  →  生成 calendar.ics  →  内容有变化才 git push
```

- VPN：`hagb/docker-easyconnect:cli` 镜像，账号密码在 `secrets.json`
- 登录：密码做 md5，验证码用 ddddocr 多策略 OCR 自动识别（失败自动换一张重试）
- ICS：每节课一个 VEVENT，UID 稳定，调课时日历里是"更新"而不是"重复"

---

## 一、一次性部署

### 1. 建仓库

在 GitHub 新建一个**公开**仓库，例如 `kb-calendar`。

> 公开是必须的：日历订阅是匿名 GET，私有仓库的 raw 链接需要鉴权，日历拿不到。
> 所以 `calendar.ics` 里**不含学号和姓名**，只有课程名 / 教师 / 地点。

### 2. 本地初始化并推送

把本目录内容（**不含 `secrets.json`**，它已被 `.gitignore` 忽略）推到仓库：

```powershell
cd <本目录>
git init
git add .
git commit -m "init: 课表日历订阅"
git branch -M main
git remote add origin https://github.com/<你的用户名>/kb-calendar.git
git push -u origin main
```

### 3. 配置密码

```powershell
Copy-Item secrets.example.json secrets.json
notepad secrets.json      # 填 vpn_pass 和 jw_pass
```

`secrets.json` 只留在本地，不会进仓库。

### 4. 跑一次

```powershell
.\run.bat
```

首次会自动创建 VPN 容器（之后复用），约 1~3 分钟。
成功后会生成 / 更新 `calendar.ics` 并推送。

### 5. 订阅

日历 App 里添加"订阅日历 / 通过 URL 添加"，填：

```
https://raw.githubusercontent.com/<用户名>/kb-calendar/main/calendar.ics
```

- **iOS / macOS 日历**：直接能用
- **Google 日历**：如果提示"无法获取日历"，改用 GitHub Pages
  （仓库 Settings → Pages → 分支选 `main` → 根目录，地址变成
  `https://<用户名>.github.io/kb-calendar/calendar.ics`，Pages 会正确返回 `text/calendar`）

订阅的刷新频率由客户端决定（iOS 可在"设置 → 日历 → 账户 → 抓取新数据"里调成每小时/每天）。

---

## 二、让豆包每天自动跑

豆包（2.1）的**定时任务 + 本地电脑**可以直接执行本机程序：

1. 豆包里新建定时任务，时间建议 **每天 07:30**（或你起床前的时间）
2. 任务指令写成：

   > 在本机执行 `C:\Users\Weeee\Documents\AI_temp\kb-calendar\run.bat`，
   > 把输出最后几行发给我。如果输出里有"已写入 calendar.ics"，说明课表有变动，告诉我变了多少事件。

3. 首次会弹权限确认，允许它访问本地电脑

注意：**执行时必须开机且豆包客户端在线**，否则当天跳过（不影响，第二天会补上最新课表）。

> 为什么不用 GitHub Actions：云端跑要重新走一遍 VPN 登录，且学校可能限制境外 IP；
> 用自己电脑跑反而最稳，也完全不占用你的时间。

---

## 三、日常会遇到的几件事

| 情况 | 处理 |
|---|---|
| 课表调课 | 第二天自动提交，`git log` 里能看到「chore: 更新课表 2026-xx-xx」，自己 diff 就知道改了哪节 |
| VPN 隧道掉线 | 脚本每次都会检查并重启容器，一般自愈；实在不行 `docker restart sangfor-vpn` |
| 教务系统改密码 | 改 `secrets.json` 里的 `jw_pass` |
| VPN 改密码 | 改 `secrets.json` 里的 `vpn_pass`，并 `docker rm -f sangfor-vpn` 让脚本重建容器 |
| 新学期 | 改 `kb_update.py` 里的 `WEEK1_MONDAY`（第 1 周周一）和 `KB_PATH` 里的学年学期参数 |
| 想加课前提醒 | 在 `render()` 的 VEVENT 里加 `BEGIN:VALARM` 段（`TRIGGER:-PT15M`） |

---

## 四、安全提醒

- `secrets.json` 里有明文密码，别把它提交、别截图外发
- 教务系统现在是弱口令（`123456`），强烈建议改成强密码后同步更新 `secrets.json`
- 仓库是公开的：**任何拿到链接的人都能看到你的课表**（课程、教师、教室）。不介意就没问题；介意的话把仓库转私有，再自己加一层 Cloudflare Worker 做出口

---

## 五、文件说明

| 文件 | 作用 |
|---|---|
| `kb_update.py` | 主流程：容器 → 登录 → 抓课表 → 生成 ICS → 有变化才提交 |
| `run.bat` | 双击 / 给豆包调用的入口 |
| `calendar.ics` | 生成的日历文件（订阅源） |
| `calendar.ics.state.json` | 课表指纹与 SEQUENCE，用于判断"有没有变"（需提交） |
| `secrets.json` | 本地凭据（**不提交**） |
