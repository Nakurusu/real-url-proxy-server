#!/usr/bin/env python3
# 校验直播间每一档清晰度「实际」拿到的是什么流（支持斗鱼 / B站）。
#
#   python3 check_quality.py 36252                       # 斗鱼
#   python3 check_quality.py --site bilibili 6           # B站
#   python3 check_quality.py 36252 --no-cookie           # 强制匿名，用来对比
#   python3 check_quality.py --site bilibili 6 --cookie 'SESSDATA=...'
#
# 判读方法：斗鱼和B站都会把真实的分辨率/帧率/码率写进流的元数据（FLV onMetaData）。
# 如果某几档的数值完全相同，说明服务器把这几档降级成了同一条流。
import argparse
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import douyu  # noqa: E402
from douyu import DouYu  # noqa: E402


def fetch_meta(url, seconds=8):
    """取流头部一小段，解析 FLV onMetaData（用 curl 避开 Python 对这些 CDN 的 TLS 兼容问题）。"""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        path = f.name
    try:
        subprocess.run(
            ['curl', '-sL', '--max-time', str(seconds), '-r', '0-300000',
             '-H', 'Referer: https://www.douyu.com/', '-o', path, url],
            check=False,
        )
        with open(path, 'rb') as f:
            data = f.read()
    finally:
        os.unlink(path)

    out = {}
    for key in (b'width', b'height', b'framerate', b'videodatarate', b'videocodecid'):
        i = data.find(key)
        if i >= 0 and i + len(key) + 9 <= len(data):
            out[key.decode()] = round(struct.unpack('>d', data[i + len(key) + 1:i + len(key) + 9])[0])
    out['_bytes'] = len(data)
    return out


def report(rid, cookie=None, sample=True):
    """打印该房间每档清晰度的真实参数。cookie 为 None 时用本地 cookie 文件/环境变量。"""
    d = DouYu(rid, cookie=cookie)
    print('rid = {} | cookie = {}'.format(d.rid, '已加载（登录态）' if d.cookie else '无（匿名）'))
    ts, enc = d._get_encryption()
    top = d._play(0, ts, enc)
    if not top:
        print('接口无数据（未开播？）')
        return

    print('\n接口宣称的清晰度：')
    for m in top.get('multirates', []):
        print('  {:<16} rate={:<3} 宣称码率={}kbps  diamondFan={}'.format(
            m['name'], m['rate'], m.get('bit'), m.get('diamondFan')))

    print('\n实际拉到的流：')
    for m in top.get('multirates', []):
        rate = m['rate']
        data = top if rate == 0 else d._play(rate, ts, enc)
        if not data:
            print('  {:<16} -> 接口拒绝'.format(m['name']))
            continue
        if sample:
            meta = fetch_meta(d._url(data))
            detail = '{}x{} @{}fps 视频码率{}kbps codec={}'.format(
                meta.get('width', '?'), meta.get('height', '?'), meta.get('framerate', '?'),
                meta.get('videodatarate', '?'), meta.get('videocodecid', '?'))
        else:
            detail = ''
        print('  {:<16} -> {}  ({})'.format(
            m['name'], detail, data['rtmp_live'].split('?')[0].rsplit('_', 1)[-1]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('rid', help='房间号')
    ap.add_argument('--site', choices=['douyu', 'bilibili'], default='douyu', help='平台，默认 douyu')
    ap.add_argument('--cookie', help='直接给 Cookie 字符串')
    ap.add_argument('--cookie-file', help='Cookie 文件路径')
    ap.add_argument('--no-cookie', action='store_true', help='强制匿名（对比用）')
    ap.add_argument('--no-sample', action='store_true', help='不下载流样本，只列 URL')
    args = ap.parse_args()

    if args.site == 'bilibili':
        import bilibili
        if args.cookie_file:
            bilibili.BILIBILI_COOKIE_FILE = args.cookie_file
        cookie = '' if args.no_cookie else (args.cookie or bilibili.resolve_cookie(args.cookie_file))
        bilibili.probe(args.rid, cookie=cookie)
        return

    if args.cookie_file:
        douyu.DOUYU_COOKIE_FILE = args.cookie_file
    cookie = '' if args.no_cookie else (args.cookie or douyu.resolve_cookie())
    if args.cookie_file and not args.cookie:
        cookie = '' if args.no_cookie else douyu.load_cookie(args.cookie_file)
    report(args.rid, cookie=cookie, sample=not args.no_sample)


if __name__ == '__main__':
    main()
