#!/usr/bin/env python3
# 获取哔哩哔哩的登录 Cookie，存成 bilibili_cookie.txt 供 bilibili.py / 代理服务器使用。
#
#   python3 bilibili_cookie.py --wechat            # 推荐：微信扫码登录
#   python3 bilibili_cookie.py --qrcode            # B站 App 扫码登录
#   python3 bilibili_cookie.py --paste             # 手动粘贴 Cookie 头
#   python3 bilibili_cookie.py --wechat --check 6  # 登录完顺便验证画质
#
# 说明：B站直播的「原画(10000)」及以上需要登录，未登录会被压到 250（超清 720p）；
#       4K(20000)/杜比(30000) 还需要大会员。
# 出于安全考虑，脚本只打印 Cookie 的名字，完整值只写进文件（600 权限）。
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DEFAULT_COOKIE_FILE = os.path.join(HERE, 'bilibili_cookie.txt')

LOGIN_KEYS = ['SESSDATA', 'bili_jct', 'DedeUserID']
KEEP_KEYS = LOGIN_KEYS + ['DedeUserID__ckMd5', 'sid', 'buvid3']

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

URL_GENERATE = 'https://passport.bilibili.com/x/passport-login/web/qrcode/generate'
URL_POLL = 'https://passport.bilibili.com/x/passport-login/web/qrcode/poll'
URL_SNS_STATE = 'https://passport.bilibili.com/x/passport-login/web/sns/state/generate'
URL_SNS_AUTHORIZE = 'https://passport.bilibili.com/x/passport-login/web/sns/authorize/url'
URL_SNS_LOGIN = 'https://passport.bilibili.com/x/passport-login/web/sns/login'


# --------------------------------------------------------------------------- #
# 二维码页面（和 douyu_cookie.py 一样的做法：内嵌 CDN 的 qrcode.js，浏览器打开）
# --------------------------------------------------------------------------- #
def _open_qr_page(title, hint, text):
    html = ('<!doctype html><meta charset="utf-8"><title>{title}</title>'
            '<body style="font:16px -apple-system,sans-serif;text-align:center;padding:40px">'
            '<h2>{title}</h2><div id="qr" style="display:inline-block"></div>'
            '<p style="color:#666">{hint}</p>'
            '<p style="color:#bbb;font-size:12px;word-break:break-all">{text}</p>'
            '<script src="https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"></script>'
            '<script>new QRCode(document.getElementById("qr"),{{text:{text_json},width:300,height:300}});</script>'
            '</body>').format(title=title, hint=hint, text=text, text_json=json.dumps(text))
    path = os.path.join(HERE, 'bilibili_qr.html')
    with open(path, 'w') as f:
        f.write(html)
    subprocess.run(['open', path], check=False)
    return path


# --------------------------------------------------------------------------- #
# 扫码登录
# --------------------------------------------------------------------------- #
def qrcode_login(save=DEFAULT_COOKIE_FILE, timeout=240):
    import requests

    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Referer': 'https://www.bilibili.com/'})

    r = s.get(URL_GENERATE, timeout=20).json()
    data = r.get('data') or {}
    if r.get('code') != 0 or not data.get('qrcode_key'):
        raise RuntimeError('获取二维码失败: {}'.format(r.get('message') or r.get('code')))
    key, url = data['qrcode_key'], data['url']

    path = _open_qr_page('用「哔哩哔哩」App 扫码登录', '扫完请在手机上点确认', url)
    print('  二维码页面: {}（已用浏览器打开）'.format(path))
    print('  请用哔哩哔哩 App 扫描并确认')

    deadline, notified = time.time() + timeout, False
    while time.time() < deadline:
        j = s.get(URL_POLL, params={'qrcode_key': key}, timeout=20).json()
        d = j.get('data') or {}
        code = d.get('code')
        if code == 0:
            break
        if code == 86090 and not notified:
            print('  已扫码，请在手机上点确认...')
            notified = True
        elif code == 86038:
            raise RuntimeError('二维码已失效，请重新运行')
        time.sleep(2)
    else:
        raise RuntimeError('等待扫码超时，请重新运行')

    cookies = {}
    for c in s.cookies:
        if 'bilibili' in (c.domain or '') or c.name in KEEP_KEYS:
            cookies[c.name] = c.value
    if 'SESSDATA' not in cookies:
        raise RuntimeError('扫码流程结束但没拿到 SESSDATA（可能没在手机上点确认）')
    return _finish(cookies, '扫码登录')


def wechat_login(save=DEFAULT_COOKIE_FILE, timeout=240):
    """B站微信扫码登录：微信开放平台的二维码由官方接口直接给图片，不需要自己画码。"""
    import re
    import requests
    from urllib.parse import urlparse, parse_qs

    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Referer': 'https://passport.bilibili.com/login'})
    form = {'Content-Type': 'application/x-www-form-urlencoded'}

    # 1) 拿 csrf_state（B站要求，和 sns/login 时必须一致）
    r = s.get(URL_SNS_STATE, timeout=20).json()
    csrf_state = (r.get('data') or {}).get('csrf_state')
    if r.get('code') != 0 or not csrf_state:
        raise RuntimeError('获取 csrf_state 失败: {}'.format(r.get('message') or r.get('code')))

    # 2) 换出微信授权地址（redirect_uri 指向 B站自己的回调）
    j = s.post(URL_SNS_AUTHORIZE,
               data={'csrf_state': csrf_state, 'sns_platform': 'wechat',
                     'source': 'main-fe', 'go_url': 'https://www.bilibili.com'},
               headers=form, timeout=20).json()
    qrconnect = (j.get('data') or {}).get('url')
    if j.get('code') != 0 or not qrconnect:
        raise RuntimeError('获取微信授权地址失败: {}'.format(j.get('message') or j.get('code')))

    # 3) 二维码：uuid -> 微信官方图片（注意只能用 connect/qrcode/ 这个正则）
    page = s.get(qrconnect, timeout=20).text
    m = re.search(r'connect/qrcode/([A-Za-z0-9_=-]+)', page)
    if not m:
        raise RuntimeError('没能从微信页面里取到 uuid')
    uuid = m.group(1)
    img = s.get('https://open.weixin.qq.com/connect/qrcode/' + uuid, timeout=20)
    if 'image' not in img.headers.get('Content-Type', ''):
        raise RuntimeError('微信返回的不是二维码图片（{}）'.format(img.headers.get('Content-Type')))

    import base64
    html = ('<!doctype html><meta charset="utf-8"><title>用「微信」扫码登录哔哩哔哩</title>'
            '<body style="font:16px -apple-system,sans-serif;text-align:center;padding:40px">'
            '<h2>用「微信」扫码登录哔哩哔哩</h2>'
            '<img src="data:image/jpeg;base64,{}" width="300" height="300" '
            'style="border:1px solid #eee;border-radius:8px">'
            '<p style="color:#666">扫完请在手机上点确认</p></body>').format(
        base64.b64encode(img.content).decode())
    path = os.path.join(HERE, 'bilibili_qr.html')
    with open(path, 'w') as f:
        f.write(html)
    subprocess.run(['open', path], check=False)
    print('  二维码页面: {}（已用浏览器打开）'.format(path))
    print('  请用微信「扫一扫」扫描并确认')

    # 4) 长轮询扫码状态：408 等扫码 / 404 已扫待确认 / 405 已确认 / 403 取消
    deadline, notified, wx_code = time.time() + timeout, False, None
    while time.time() < deadline:
        txt = s.get('https://long.open.weixin.qq.com/connect/l/qrconnect',
                    params={'uuid': uuid, '_': int(time.time() * 1000)}, timeout=40).text
        mm = re.search(r'wx_errcode=(-?\d+)', txt)
        errcode = mm.group(1) if mm else '?'
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

    # 5) 把微信的 code 回给 B站回调，B站会换成自己的 sns code / cookie
    redirect_uri = parse_qs(urlparse(qrconnect).query)['redirect_uri'][0]
    resp = s.get(redirect_uri, params={'code': wx_code}, allow_redirects=True, timeout=20)

    if 'SESSDATA' not in {c.name for c in s.cookies}:
        # 回调页通常把 sns code 放在 URL 或页面数据里，再调一次 sns/login 才算登录完
        code = (parse_qs(urlparse(resp.url).query).get('code') or [None])[0]
        if not code:
            m2 = re.search(r'"code"\s*:\s*"([^"]+)"', resp.text) or re.search(r'code=([0-9a-f]{16,})', resp.text)
            code = m2.group(1) if m2 else None
        if code:
            j2 = s.post(URL_SNS_LOGIN,
                        data={'csrf_state': csrf_state, 'sns_platform': 'wechat',
                              'source': 'main-fe', 'go_url': 'https://www.bilibili.com',
                              'code': code},
                        headers=form, timeout=20).json()
            if j2.get('code') != 0:
                raise RuntimeError('sns/login 失败: {}'.format(j2.get('message')))
            if not (j2.get('data') or {}).get('has_bind'):
                raise RuntimeError('这个微信还没绑定 B站账号，请先在 B站 App/网页里绑定手机号再试')

    cookies = {}
    for c in s.cookies:
        if 'bilibili' in (c.domain or '') or c.name in KEEP_KEYS:
            cookies[c.name] = c.value
    if 'SESSDATA' not in cookies:
        raise RuntimeError('微信授权成功但没换到 SESSDATA，可能需要在手机上确认绑定')
    return _finish(cookies, '微信扫码登录')


def _finish(cookies, source):
    print('  来源: {}'.format(source))
    found = [k for k in LOGIN_KEYS if k in cookies]
    print('  取到 {} 条 cookie，登录凭证: {}'.format(len(cookies), ', '.join(found) or '无'))
    if 'SESSDATA' not in cookies:
        print('  ！没有 SESSDATA，等于未登录，画质会被压到 250（超清 720p）')
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
    src.add_argument('--qrcode', action='store_true', help='B站 App 扫码登录')
    src.add_argument('--wechat', action='store_true', help='微信扫码登录（推荐）')
    src.add_argument('--paste', action='store_true', help='手动粘贴 Cookie 字符串')
    ap.add_argument('--save', default=DEFAULT_COOKIE_FILE, help='保存路径，默认 bilibili_cookie.txt')
    ap.add_argument('--check', metavar='房间号', help='取到后顺便检查该房间的实际画质')
    ap.add_argument('--timeout', type=int, default=240, help='扫码等待秒数')
    args = ap.parse_args()

    try:
        if args.paste:
            print('把浏览器开发者工具里请求头的 Cookie 整条粘进来，回车结束：')
            cookies = _finish(parse_cookie_string(sys.stdin.read()), '手动粘贴')
        elif args.qrcode:
            cookies = qrcode_login(args.save, args.timeout)
        else:
            cookies = wechat_login(args.save, args.timeout)
    except Exception as e:
        print('失败: {}'.format(e))
        return 1

    path = save_cookie(cookies, args.save)
    print('  已写入 {} (权限 600)'.format(path))

    if args.check:
        import bilibili
        bilibili.BILIBILI_COOKIE_FILE = args.save
        print('\n用这条 cookie 检查房间 {} 的实际画质：'.format(args.check))
        bilibili.probe(args.check, cookie_header(cookies))
    else:
        print('\n下一步：python3 check_quality.py --site bilibili {}'.format(args.check or '房间号'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
