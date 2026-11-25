#!/usr/bin/env python3
# 获取斗鱼的登录 Cookie，存成 douyu_cookie.txt 供 douyu.py / 代理服务器使用。
#
#   python3 douyu_cookie.py --qrcode             # 推荐：终端出二维码，用斗鱼 App 扫码登录
#   python3 douyu_cookie.py --wechat             # 微信扫码登录（不用装斗鱼 App）
#   python3 douyu_cookie.py --browser safari     # 从浏览器读（需要完全磁盘访问权限）
#   python3 douyu_cookie.py --browser chrome     # Chrome/Edge/Brave/Vivaldi/Arc
#   python3 douyu_cookie.py --browser firefox
#   python3 douyu_cookie.py --paste              # 手动粘贴 Cookie 头
#   python3 douyu_cookie.py --qrcode --check 36252   # 登录完顺便验证画质有没有提升
#
# 关于权限：
#   macOS 会保护浏览器数据（Chrome 与 Safari 都算），非授权进程读取会报
#   Operation not permitted。要用 --browser，需要给运行它的终端/App 授予
#   「系统设置 → 隐私与安全性 → 完全磁盘访问权限」，然后重开终端。
#   Chrome 还需要访问钥匙串解密（第一次会弹窗）。
#   --qrcode 走网络接口，完全不需要这些权限。
#
# 安全：脚本默认只打印 Cookie 的名字，完整值只写进文件（600 权限）。
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DEFAULT_COOKIE_FILE = os.path.join(HERE, 'douyu_cookie.txt')

LOGIN_KEYS = ['acf_uid', 'acf_stk', 'acf_ltk', 'acf_auth', 'dy_auth', 'dy_uid']
ALSO_KEEP = ['dy_did']

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

CHROMIUM = {
    'chrome': ('~/Library/Application Support/Google/Chrome', 'Chrome Safe Storage', 'Chrome'),
    'chrome-beta': ('~/Library/Application Support/Google/Chrome Beta', 'Chrome Safe Storage', 'Chrome'),
    'edge': ('~/Library/Application Support/Microsoft Edge', 'Microsoft Edge Safe Storage', 'Microsoft Edge'),
    'brave': ('~/Library/Application Support/BraveSoftware/Brave-Browser', 'Brave Safe Storage', 'Brave'),
    'vivaldi': ('~/Library/Application Support/Vivaldi', 'Vivaldi Safe Storage', 'Vivaldi'),
    'arc': ('~/Library/Application Support/Arc/User Data', 'Arc Safe Storage', 'Arc'),
    'chromium': ('~/Library/Application Support/Chromium', 'Chromium Safe Storage', 'Chromium'),
}
FIREFOX_PROFILES = '~/Library/Application Support/Firefox/Profiles'
SAFARI_COOKIE_FILES = [
    '~/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies',
    '~/Library/Cookies/Cookies.binarycookies',
]


# --------------------------------------------------------------------------- #
# Chromium 系（AES 加密，密钥在钥匙串）
# --------------------------------------------------------------------------- #
def _keychain_secret(service, account):
    out = subprocess.run(
        ['security', 'find-generic-password', '-w', '-s', service, '-a', account],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError('读钥匙串失败（{}）: {}'.format(service, out.stderr.strip()[:120]))
    return out.stdout.rstrip('\n').encode()


def _decrypt(encrypted, key, host_key):
    if not encrypted:
        return ''
    if not encrypted.startswith(b'v1'):
        return encrypted.decode('utf-8', 'ignore')
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b' ' * 16)).decryptor()
    plain = decryptor.update(encrypted[3:]) + decryptor.finalize()
    # 新版 Chrome 在明文前加 32 字节 sha256(host_key)
    if len(plain) > 32 and plain[:32] == hashlib.sha256(host_key.encode()).digest():
        plain = plain[32:]
    plain = plain.rstrip(b'\x00')
    if plain and plain[-1] <= 16 and plain[-plain[-1]:] == bytes([plain[-1]]) * plain[-1]:
        plain = plain[:-plain[-1]]
    return plain.decode('utf-8', 'ignore')


def read_chromium(browser):
    base, service, account = CHROMIUM[browser]
    base = os.path.expanduser(base)
    dbs = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name, 'Cookies')
            if os.path.isfile(p):
                dbs.append(p)
    if not dbs:
        raise RuntimeError('没找到 {} 的 Cookies 数据库'.format(browser))
    dbs.sort(key=os.path.getmtime, reverse=True)
    key = hashlib.pbkdf2_hmac('sha1', _keychain_secret(service, account), b'saltysalt', 1003, dklen=16)

    best = None
    for db in dbs:
        cookies = {}
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            tmp = f.name
        try:
            shutil.copy2(db, tmp)
            con = sqlite3.connect(tmp)
            for host_key, name, value, encrypted in con.execute(
                    "select host_key, name, value, encrypted_value from cookies "
                    "where host_key like '%douyu%'"):
                val = value or _decrypt(encrypted, key, host_key)
                if val:
                    cookies[name] = val
            con.close()
        except OSError as e:
            raise RuntimeError('读取失败: {}（需要给本程序「完全磁盘访问权限」）'.format(e))
        finally:
            os.unlink(tmp)
        logged_in = any(k in cookies for k in LOGIN_KEYS)
        if cookies and (best is None or (logged_in and not best[1])):
            best = (cookies, logged_in, db)
        if logged_in:
            break
    if not best:
        raise RuntimeError('{} 里没有斗鱼的 Cookie（没在上面登录过？）'.format(browser))
    return _finish(best[0], '{}（{}）'.format(browser, os.path.dirname(best[2])), best[1])


# --------------------------------------------------------------------------- #
# Firefox（明文 sqlite）
# --------------------------------------------------------------------------- #
def read_firefox():
    import glob
    best = None
    for db in glob.glob(os.path.expanduser(os.path.join(FIREFOX_PROFILES, '*', 'cookies.sqlite'))):
        cookies = {}
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            tmp = f.name
        try:
            shutil.copy2(db, tmp)
            con = sqlite3.connect(tmp)
            for name, value in con.execute("select name, value from moz_cookies where host like '%douyu%'"):
                if value:
                    cookies[name] = value
            con.close()
        finally:
            os.unlink(tmp)
        logged_in = any(k in cookies for k in LOGIN_KEYS)
        if cookies and (best is None or (logged_in and not best[1])):
            best = (cookies, logged_in, db)
    if not best:
        raise RuntimeError('Firefox 里没有斗鱼的 Cookie')
    return _finish(best[0], 'firefox（{}）'.format(os.path.dirname(best[2])), best[1])


# --------------------------------------------------------------------------- #
# Safari（未加密的 Cookies.binarycookies）
# --------------------------------------------------------------------------- #
def parse_binarycookies(data):
    """解析 Safari 的 .binarycookies 二进制格式，返回 cookie 字典列表。"""
    if data[:4] != b'cook':
        raise ValueError('不是 Cookies.binarycookies 格式')
    num_pages = struct.unpack('>i', data[4:8])[0]
    sizes = struct.unpack('>{}i'.format(num_pages), data[8:8 + 4 * num_pages])
    off = 8 + 4 * num_pages
    out = []
    for size in sizes:
        page = data[off:off + size]
        off += size
        num_cookies = struct.unpack('<i', page[4:8])[0]
        p = 8
        for _ in range(num_cookies):
            csize = struct.unpack('<i', page[p:p + 4])[0]
            rec = page[p:p + csize]
            if len(rec) < 48:
                break
            # size(4) version(4) flags(4) has_port(4) domain(4) name(4) path(4) value(4) end(4)
            (flags, has_port, d_off, n_off, path_off, v_off, end) = struct.unpack('<7i', rec[8:36])
            expiry = struct.unpack('<d', rec[36:44])[0]
            if end != csize:
                raise ValueError('cookie 记录长度不一致（{} != {}）'.format(end, csize))

            def s(offset):
                if not (0 < offset < len(rec)):
                    return ''
                stop = rec.find(b'\x00', offset)
                return rec[offset:stop if stop >= 0 else len(rec)].decode('utf-8', 'ignore')

            out.append({
                'domain': s(d_off), 'name': s(n_off), 'path': s(path_off), 'value': s(v_off),
                'secure': bool(flags & 1), 'expiry': expiry + 978307200 if expiry else 0,
            })
            p += csize
    return out


def read_safari():
    last_err = None
    for path in SAFARI_COOKIE_FILES:
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError as e:
            last_err = '{}: {}'.format(path, e)
            continue
        cookies = {}
        now = time.time()
        for c in parse_binarycookies(data):
            if 'douyu' not in c['domain'] or not c['value']:
                continue
            if c['expiry'] and c['expiry'] < now:
                continue
            cookies[c['name']] = c['value']
        if cookies:
            logged_in = any(k in cookies for k in LOGIN_KEYS)
            return _finish(cookies, 'safari（{}）'.format(path), logged_in)
        last_err = '{} 里没有斗鱼 cookie'.format(path)
    if last_err and 'Operation not permitted' in str(last_err):
        raise RuntimeError('读取失败: {}（需要给本程序「完全磁盘访问权限」）'.format(last_err))
    raise RuntimeError(last_err or '没找到 Safari 的 Cookies.binarycookies')


# --------------------------------------------------------------------------- #
# 扫码登录（不需要任何本地权限）
# --------------------------------------------------------------------------- #
def _open_qr_page(title, hint, text=None, image_bytes=None):
    """生成一个本地 HTML 页面并在浏览器里打开。有图片就直接内嵌（离线可用），
    否则用 CDN 的 qrcode.js 把 text 画成二维码。"""
    import base64
    if image_bytes is not None:
        body = ('<img src="data:image/jpeg;base64,{}" width="300" height="300" '
                'style="border:1px solid #eee;border-radius:8px" alt="QR">').format(
            base64.b64encode(image_bytes).decode())
    else:
        body = ('<div id="qr" style="display:inline-block"></div>'
                '<script src="https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"></script>'
                '<script>new QRCode(document.getElementById("qr"),'
                '{{text:{text},width:300,height:300}});</script>').format(text=json.dumps(text))

    html = ('<!doctype html><meta charset="utf-8"><title>{title}</title>'
            '<body style="font:16px -apple-system,sans-serif;text-align:center;padding:40px">'
            '<h2>{title}</h2>{body}'
            '<p style="color:#666">{hint}</p>'
            '<p style="color:#bbb;font-size:12px;word-break:break-all">{text}</p></body>').format(
        title=title, hint=hint, body=body, text=text or '')
    path = os.path.join(HERE, 'douyu_qr.html')
    with open(path, 'w') as f:
        f.write(html)
    subprocess.run(['open', path], check=False)
    return path


def _show_qr(text):
    """斗鱼 App 扫码：优先在终端打 ASCII，再打开浏览器页面。"""
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(text)
        qr.make()
        qr.print_ascii(invert=True)
    except Exception:
        pass
    path = _open_qr_page('用「斗鱼」App 扫码登录', '扫完请在手机上点确认', text=text)
    print('  二维码页面: {}（已用浏览器打开）'.format(path))
    return path


def qrcode_login(save=DEFAULT_COOKIE_FILE, timeout=300, check_room=None):
    import requests

    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Referer': 'https://passport.douyu.com/member/login',
                      'Origin': 'https://passport.douyu.com'})

    r = s.post('https://passport.douyu.com/scan/generateCode',
               data={'client_id': 1, 'isMultiAccount': 0}, timeout=20).json()
    if r.get('error') != 0 or not r.get('data'):
        raise RuntimeError('获取二维码失败: {}'.format(r.get('msg') or r.get('error')))
    code = r['data']['code']
    expire = r['data'].get('expire', 300)

    _show_qr(r['data']['url'])
    print('  二维码有效期 {} 秒，请用斗鱼 App 扫描并确认'.format(expire))

    login_url, deadline, scanned = None, time.time() + min(timeout, expire), False
    while time.time() < deadline:
        j = s.get('https://passport.douyu.com/japi/scan/auth',
                  params={'code': code, 'time': int(time.time() * 1000)}, timeout=20).json()
        err = j.get('error')
        if err == 0:
            login_url = (j.get('data') or {}).get('url') or (j.get('data') or {}).get('redirect_url')
            break
        if err == 1:
            if not scanned:
                print('  已扫码，等待手机端确认...')
                scanned = True
        elif err == -1:
            raise RuntimeError('二维码已失效或取消，请重新运行')
        time.sleep(1)
    if not login_url:
        raise RuntimeError('等待扫码超时，请重新运行')

    if login_url.startswith('//'):
        login_url = 'https:' + login_url

    # 这一步才是真正下发登录 cookie 的请求（网页端用 JSONP 调的）
    s.get(login_url, params={'callback': 'appClient_json_callback'}, timeout=20)

    cookies = {}
    for c in s.cookies:
        if 'douyu' in (c.domain or '') or c.name in LOGIN_KEYS + ALSO_KEEP:
            cookies[c.name] = c.value
    if not any(k in cookies for k in LOGIN_KEYS):
        raise RuntimeError('扫码流程结束但没拿到登录 cookie（可能没在手机上点确认）')
    return _finish(cookies, '扫码登录', True)


def wechat_login(save=DEFAULT_COOKIE_FILE, timeout=300, check_room=None):
    """走斗鱼的微信第三方登录：二维码由微信官方接口直接给图片，不需要自己画码。"""
    import re
    import requests
    from urllib.parse import urlparse, parse_qs

    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Referer': 'https://passport.douyu.com/member/login'})

    # 1. 让斗鱼给出微信开放平台的 qrconnect 地址（这一步会种下 acf_devid，后面换 code 要用）
    r = s.get('https://www.douyu.com/member/oauth/signin/weixin',
              params={'biz_type': 1, 'ref_url': ''}, allow_redirects=False, timeout=20)
    loc = r.headers.get('Location') or ''
    if 'open.weixin.qq.com' not in loc:
        raise RuntimeError('斗鱼没有返回微信登录地址: {}'.format(loc[:120]))
    q = parse_qs(urlparse(loc).query)
    state, redirect_uri = q['state'][0], q['redirect_uri'][0]

    # 2. 拿 uuid 和二维码图片（微信直接返回 jpg）
    #    注意：只能用 connect/qrcode/<uuid> 这个正则，页面里的
    #    `var uuid = "..."` 在压缩后的 JS 里会被误匹配成 `+G+(e?`。
    page = s.get(loc, timeout=20).text
    m = re.search(r'connect/qrcode/([A-Za-z0-9_=-]+)', page)
    if not m:
        raise RuntimeError('没能从微信页面里取到 uuid')
    uuid = m.group(1)

    img = s.get('https://open.weixin.qq.com/connect/qrcode/' + uuid, timeout=20)
    ctype = img.headers.get('Content-Type', '')
    if 'image' not in ctype:
        raise RuntimeError('微信返回的不是二维码图片（{}）：{}'.format(
            ctype, img.text[:120].replace('\n', ' ')))
    with open(os.path.join(HERE, 'douyu_wechat_qr.jpg'), 'wb') as f:
        f.write(img.content)
    path = _open_qr_page('用「微信」扫码登录斗鱼', '扫完请在手机上点确认', image_bytes=img.content)
    print('  二维码页面: {}（已用浏览器打开）'.format(path))
    print('  请用微信「扫一扫」扫描并确认')

    # 3. 长轮询扫码状态：408 等待扫码 / 404 已扫待确认 / 405 已确认 / 403 取消或过期
    deadline, notified, wx_code = time.time() + timeout, False, None
    while time.time() < deadline:
        txt = s.get('https://long.open.weixin.qq.com/connect/l/qrconnect',
                    params={'uuid': uuid, '_': int(time.time() * 1000)}, timeout=40).text
        m = re.search(r'wx_errcode=(-?\d+)', txt)
        errcode = m.group(1) if m else '?'
        if errcode == '404' and not notified:
            print('  已扫码，请在手机上点确认...')
            notified = True
        elif errcode == '405':
            c = re.search(r"wx_code='([^']*)'", txt)
            wx_code = c.group(1) if c else ''
            if wx_code:
                break
        elif errcode == '403':
            raise RuntimeError('二维码已失效或被取消，请重新运行')
    if not wx_code:
        raise RuntimeError('等待扫码超时，请重新运行')

    # 4. 拿微信给的 code 回到斗鱼换登录 cookie
    s.get(redirect_uri, params={'code': wx_code, 'state': state}, allow_redirects=True, timeout=20)

    cookies = {}
    for c in s.cookies:
        if 'douyu' in (c.domain or '') or c.name in LOGIN_KEYS + ALSO_KEEP:
            cookies[c.name] = c.value
    if not any(k in cookies for k in LOGIN_KEYS):
        raise RuntimeError('微信授权成功但没换到斗鱼登录 cookie（可能需要在手机上确认，或该微信未绑定斗鱼账号）')
    return _finish(cookies, '微信扫码登录', True)


# --------------------------------------------------------------------------- #
# 公共
# --------------------------------------------------------------------------- #
def _finish(cookies, source, logged_in):
    print('  来源: {}'.format(source))
    print('  取到 {} 条 douyu cookie，登录凭证: {}'.format(
        len(cookies), ', '.join(k for k in LOGIN_KEYS if k in cookies) or '无（这是游客态）'))
    if not logged_in:
        print('  ！没有 acf_* 登录凭证，画质不会有提升，请确认已在浏览器/手机上登录斗鱼')
    return cookies


def parse_cookie_string(text):
    cookies = {}
    for part in text.strip().split(';'):
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            cookies[k.strip()] = v.strip()
    return cookies


def cookie_header(cookies):
    return '; '.join('{}={}'.format(k, v) for k, v in cookies.items())


def save_cookie(cookies, path=DEFAULT_COOKIE_FILE):
    with open(path, 'w') as f:
        f.write(cookie_header(cookies))
    os.chmod(path, 0o600)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--qrcode', action='store_true', help='斗鱼 App 扫码登录（推荐）')
    src.add_argument('--wechat', action='store_true', help='微信扫码登录')
    src.add_argument('--browser', choices=list(CHROMIUM) + ['safari', 'firefox', 'auto'], help='从浏览器读')
    src.add_argument('--paste', action='store_true', help='手动粘贴 Cookie 字符串')
    ap.add_argument('--save', default=DEFAULT_COOKIE_FILE, help='保存路径，默认 douyu_cookie.txt')
    ap.add_argument('--check', metavar='房间号', help='取到后顺便检查该房间的实际画质')
    ap.add_argument('--timeout', type=int, default=300, help='扫码等待秒数')
    args = ap.parse_args()

    try:
        if args.qrcode:
            cookies = qrcode_login(args.save, args.timeout)
        elif args.wechat:
            cookies = wechat_login(args.save, args.timeout)
        elif args.paste:
            print('把浏览器开发者工具里请求头的 Cookie 整条粘进来，回车结束：')
            pasted = parse_cookie_string(sys.stdin.read())
            cookies = _finish(pasted, '手动粘贴', any(k in pasted for k in LOGIN_KEYS))
        else:
            order = [args.browser] if args.browser and args.browser != 'auto' else \
                ['chrome', 'edge', 'brave', 'vivaldi', 'arc', 'chromium', 'firefox', 'safari']
            cookies, errors = None, []
            for name in order:
                print('尝试 {} ...'.format(name))
                try:
                    cookies = read_firefox() if name == 'firefox' else (
                        read_safari() if name == 'safari' else read_chromium(name))
                    break
                except Exception as e:
                    errors.append('{}: {}'.format(name, e))
            if cookies is None:
                print('\n都没取到：')
                for e in errors:
                    print('  -', e)
                print('\n改用扫码登录: python3 douyu_cookie.py --qrcode')
                return 1
    except Exception as e:
        print('失败: {}'.format(e))
        return 1

    path = save_cookie(cookies, args.save)
    print('  已写入 {} (权限 600)'.format(path))

    if args.check:
        import douyu
        douyu.DOUYU_COOKIE_FILE = args.save
        print('\n用这条 cookie 检查房间 {} 的实际画质：'.format(args.check))
        from check_quality import report
        report(args.check, cookie_header(cookies))
    else:
        print('\n下一步：python3 check_quality.py <房间号>   # 看看画质有没有真的提升')
    return 0


if __name__ == '__main__':
    sys.exit(main())
