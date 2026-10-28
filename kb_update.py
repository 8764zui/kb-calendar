# -*- coding: utf-8 -*-
"""一键更新课表 -> calendar.ics

流程：确保 VPN 容器 -> 等隧道 -> 补内网路由 -> 登录教务系统 -> 抓 20 周
      -> 生成 ICS（内容无变化则不写文件）-> 可选 git 提交推送

用法：
    python kb_update.py            # 只更新本地 calendar.ics
    python kb_update.py --push     # 有变化时顺带 git commit & push

凭据放在脚本同目录的 secrets.json（已在 .gitignore 中）：
    {"vpn_user":"...","vpn_pass":"...","jw_user":"...","jw_pass":"..."}
也可用环境变量 JW_PASS / JW_USER 覆盖。
"""
import io, os, sys, json, time, re, hashlib, datetime, subprocess
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
ICS = os.path.join(HERE, "calendar.ics")
SECRETS = os.path.join(HERE, "secrets.json")
BASE = "https://192.168.66.199"
SOCKS = "127.0.0.1:1080"
CONTAINER = "sangfor-vpn"
IMAGE = "hagb/docker-easyconnect:cli"
VPN_HOST = "sslvpn.zhcpt.edu.cn:44331"
CAMPUS_NET = "192.168.66.0/24"
CLS = "25光伏工程①"
KB_PATH = ("/studentportal.php/Jxxx/xskbxx/optype/2/xn/2026-2027/xq/1/dqz/%s"
           "/sybmdmstr/4655,4846,5121,5667,43030125301/bjmc/" + CLS)
WEEK1_MONDAY = datetime.date(2026, 9, 7)
TZID = "Asia/Shanghai"


def log(*a):
    print("[%s]" % datetime.datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def load_secrets():
    s = {}
    if os.path.exists(SECRETS):
        s = json.load(open(SECRETS, encoding="utf-8-sig"))
    s.setdefault("vpn_user", "24430301050")
    s.setdefault("vpn_pass", os.environ.get("EC_PASS", ""))
    s.setdefault("jw_user", os.environ.get("JW_USER", "24430301050"))
    s.setdefault("jw_pass", os.environ.get("JW_PASS", ""))
    return s


# ---------------- 1. VPN 容器 ----------------
def ensure_container(sec):
    r = sh('docker ps -a --filter name=%s --format "{{.Status}}"' % CONTAINER)
    status = (r.stdout or "").strip()
    if status.startswith("Up"):
        log("容器已在运行")
    elif status:
        log("启动已有容器")
        sh("docker start %s" % CONTAINER)
    else:
        log("首次创建容器")
        sh(("docker run -d --name %s --device /dev/net/tun --cap-add NET_ADMIN "
            "-p 127.0.0.1:1080:1080 -p 127.0.0.1:8888:8888 "
            "-e EC_VER=7.6.8 -e CLI_OPTS=\"-d %s -t pwd -u %s -p %s\" "
            "--restart unless-stopped %s")
           % (CONTAINER, VPN_HOST, sec["vpn_user"], sec["vpn_pass"], IMAGE))

    # 等 tun0
    for i in range(40):
        r = sh("docker exec %s sh -c \"ip -4 a show tun0 2>/dev/null | head -2\"" % CONTAINER)
        if "inet " in (r.stdout or ""):
            log("隧道就绪: " + (r.stdout or "").strip().splitlines()[-1].strip())
            break
        if i == 39:
            log("!! 隧道 90 秒内未就绪，尝试重建")
            sh("docker rm -f %s" % CONTAINER)
            return False
        time.sleep(3)

    # 补内网路由（幂等）
    sh("docker exec %s sh -c \"ip route add %s dev tun0 2>/dev/null; true\"" % (CONTAINER, CAMPUS_NET))
    return True


# ---------------- 2. 抓课表 ----------------
def ocr_solve(requests_mod, engines, sess, url):
    r = sess.get(url, timeout=30)
    im = __import__("PIL.Image", fromlist=["Image"]).open(io.BytesIO(r.content)).convert("RGB")
    import numpy as np
    from PIL import Image, ImageFilter, ImageEnhance
    arr = np.array(im).astype(int)
    b = Image.fromarray(np.where(arr.mean(axis=2) < 120, 0, 255).astype("uint8"))
    cands = []
    for v in (im, b):
        for sc in (2, 3):
            big = v.resize((v.width * sc, v.height * sc), Image.LANCZOS)
            big = ImageEnhance.Contrast(big).enhance(1.5)
            buf = io.BytesIO(); big.save(buf, format="PNG")
            for eng in engines:
                try:
                    t = "".join(c for c in str(eng.classification(buf.getvalue())) if 32 <= ord(c) < 127)
                except Exception:
                    t = ""
                if len(t) == 4 and t not in cands:
                    cands.append(t)
    return cands


def fetch_all(sec):
    import requests, ddddocr
    from lxml import html as LH
    ocr = ddddocr.DdddOcr(show_ad=False)
    try:
        ocr_beta = ddddocr.DdddOcr(show_ad=False, beta=True)
    except Exception:
        ocr_beta = None
    engines = [e for e in (ocr, ocr_beta) if e]
    s = requests.Session()
    s.proxies = {"http": "socks5h://" + SOCKS, "https": "socks5h://" + SOCKS}
    s.verify = False

    md5 = hashlib.md5(sec["jw_pass"].encode()).hexdigest()
    logged = False
    for i in range(1, 21):
        try:
            s.get(BASE + "/studentportal.php", timeout=30)
            ts = int(time.time() * 1000)
            cands = ocr_solve(requests, engines, s, BASE + "/studentportal.php/Public/verify/%d" % ts)
            log("第 %d 次 候选=%s" % (i, cands))
            if not cands:
                continue
            rd = s.post(BASE + "/studentportal.php/Index/checkLogin",
                        data={"logintype": "xsxh", "xsxh": sec["jw_user"],
                              "dlmm": md5, "yzm": cands[0]}, timeout=40)
            txt = rd.text
            log("   -> %s" % txt[:100])
            if '"status":1' in txt:
                log("登录成功（验证码 %s）" % cands[0])
                logged = True
                break
            if '"code":3' not in txt:
                log("登录失败：%s" % txt[:120])
                return None
        except Exception as e:
            log("网络异常: %s" % str(e)[:80])
            time.sleep(2)
    if not logged:
        log("验证码重试上限，登录失败")
        return None

    def clean(t):
        return re.sub(r"\s+", " ", (t or "").replace("\xa0", " ")).strip()

    def parse(html_text):
        doc = LH.fromstring(html_text)
        tabs = doc.xpath("//table")
        if not tabs:
            return None
        grid = {}
        for ri, tr in enumerate(tabs[0].xpath(".//tr")):
            ci = 0
            for td in tr.xpath("./td|./th"):
                while (ri, ci) in grid:
                    ci += 1
                rs = int(td.get("rowspan") or 1)
                cs = int(td.get("colspan") or 1)
                if ri == 0 or ci == 0:
                    lab = " ".join(x.strip() for x in td.text_content().replace("\xa0", " ").split("\n") if x.strip())
                    val = ("L", lab)
                else:
                    out = []
                    for d in td.xpath(".//div[@title]"):
                        parts = [clean(x) for x in (d.get("title") or "").split("\n") if clean(x)]
                        if parts:
                            out.append(parts)
                    val = ("C", out)
                for dr in range(rs):
                    for dc in range(cs):
                        grid[(ri + dr, ci + dc)] = val
                ci += cs
        mr = max(k[0] for k in grid) + 1
        mc = max(k[1] for k in grid) + 1
        return [[grid.get((r, c), ("C", [])) for c in range(mc)] for r in range(mr)]

    weeks = {}
    for w in range(1, 21):
        url = BASE + quote(KB_PATH % w, safe="/,()")
        r = s.get(url, timeout=30)
        r.encoding = "utf-8"
        weeks[w] = parse(r.text)
    log("已抓取 20 周课表")
    return weeks


# ---------------- 3. ICS ----------------
def parse_period(label):
    m = re.search(r"(\d{2}:\d{2})-(\d{2}:\d{2})", label or "")
    t1, t2 = (m.group(1), m.group(2)) if m else (None, None)
    n = re.search(r"第(\d+)节", label or "")
    idx = int(n.group(1)) if n else None
    return idx, t1, t2


def build_events(weeks):
    ev = []
    for w in range(1, 21):
        g = weeks.get(w) or weeks.get(str(w))
        if not g:
            continue
        for c in range(1, len(g[0])):
            r = 1
            while r < len(g):
                cell = g[r][c][1]
                if not cell:
                    r += 1
                    continue
                parts = cell[0]
                r0 = r
                while r + 1 < len(g) and g[r + 1][c][1] and g[r + 1][c][1][0] == parts:
                    r += 1
                r1 = r
                r += 1
                _, t1, _ = parse_period(g[r0][0][1])
                _, _, t2 = parse_period(g[r1][0][1])
                if not t1 or not t2:
                    continue
                day = WEEK1_MONDAY + datetime.timedelta(days=(w - 1) * 7 + (c - 1))
                ev.append({
                    "uid": hashlib.md5(("%s|%s|%s" % (day, t1, parts[0])).encode()).hexdigest(),
                    "date": day.isoformat(), "start": t1, "end": t2,
                    "course": parts[0] if parts else "",
                    "teacher": parts[1] if len(parts) > 1 else "",
                    "room": parts[2] if len(parts) > 2 else "",
                })
    ev.sort(key=lambda e: (e["date"], e["start"], e["course"]))
    return ev


def esc(t):
    return (t or "").replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def fold(line):
    b = line.encode("utf-8")
    if len(b) <= 75:
        return line
    out, cur = [], b""
    for ch in line:
        cb = ch.encode("utf-8")
        if len(cur) + len(cb) > 74:
            out.append(cur.decode("utf-8")); cur = b""
        cur += cb
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def render(events, seq):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//zhcpt-kb//course calendar//CN",
         "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:我的课表",
         "X-WR-TIMEZONE:" + TZID, "BEGIN:VTIMEZONE", "TZID:" + TZID, "BEGIN:STANDARD",
         "DTSTART:19700101T000000", "TZOFFSETFROM:+0800", "TZOFFSETTO:+0800",
         "TZNAME:CST", "END:STANDARD", "END:VTIMEZONE"]
    for e in events:
        d = e["date"].replace("-", "")
        L += ["BEGIN:VEVENT", "UID:%s@zhcpt-kb" % e["uid"], "DTSTAMP:" + now,
              "DTSTART;TZID=%s:%sT%s00" % (TZID, d, e["start"].replace(":", "")),
              "DTEND;TZID=%s:%sT%s00" % (TZID, d, e["end"].replace(":", "")),
              "SUMMARY:" + esc(e["course"]), "LOCATION:" + esc(e["room"]),
              "DESCRIPTION:" + esc("教师：" + e["teacher"]),
              "SEQUENCE:%d" % seq, "STATUS:CONFIRMED", "TRANSP:OPAQUE", "END:VEVENT"]
    L.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in L) + "\r\n"


def write_ics(events):
    state = ICS + ".state.json"
    sig = hashlib.sha256(json.dumps(
        [(e["uid"], e["date"], e["start"], e["end"], e["course"], e["teacher"], e["room"]) for e in events],
        ensure_ascii=False).encode()).hexdigest()
    old = None
    if os.path.exists(state):
        try:
            old = json.load(open(state, encoding="utf-8"))
        except Exception:
            old = None
    if old and old.get("sig") == sig and os.path.exists(ICS):
        log("课表无变化（%d 个事件），不写文件" % len(events))
        return False
    seq = (old.get("seq", 0) + 1) if old else 0
    open(ICS, "w", encoding="utf-8", newline="").write(render(events, seq))
    json.dump({"sig": sig, "seq": seq, "count": len(events),
               "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
              open(state, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("已写入 calendar.ics：%d 个事件，SEQUENCE=%d" % (len(events), seq))
    return True


def git_push():
    r = sh("git rev-parse --is-inside-work-tree", cwd=HERE)
    if "true" not in (r.stdout or ""):
        log("不在 git 仓库，跳过推送")
        return
    sh("git add calendar.ics calendar.ics.state.json", cwd=HERE)
    r = sh('git commit -m "chore: 更新课表 %s"' % datetime.datetime.now().strftime("%Y-%m-%d"),
           cwd=HERE)
    log("commit:", (r.stdout or r.stderr or "").strip()[:120])
    r = sh("git push", cwd=HERE)
    log("push:", (r.stdout or r.stderr or "").strip()[:200])


def main():
    sec = load_secrets()
    if not sec["jw_pass"]:
        log("缺少 jw_pass，请在 secrets.json 中配置"); return 2
    if not ensure_container(sec):
        return 3
    weeks = fetch_all(sec)
    if not weeks:
        return 4
    events = build_events(weeks)
    changed = write_ics(events)
    if changed and "--push" in sys.argv:
        git_push()
    return 0


if __name__ == "__main__":
    sys.exit(main())
