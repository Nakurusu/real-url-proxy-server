# 获取斗鱼直播间的真实流媒体地址（PC 网页版 getH5PlayV1 接口）
#
# 与原仓库 real-url-proxy-server 里的 douyu.py 接口兼容：
#   DouYu('房间号').get_real_url()  ->  dict
#   dict['flv']  = 当前可得的最高画质
#   dict['4000'] = 蓝光4M，dict['2000'] = 超清，dict['900'] = 高清
#   兼容旧键：dict['2000p'] / dict['900p']
#
# Cookie（决定能不能拿到「原画/蓝光8M」）按以下优先级自动加载：
#   1. DouYu(rid, cookie='...') 显式传入
#   2. 环境变量 DOUYU_COOKIE
#   3. cookie 文件（环境变量 DOUYU_COOKIE_FILE，或同目录 douyu_cookie.txt）
#   用 douyu_cookie.py 可以从本机浏览器里把 cookie 取出来。
#
# 旧的 m.douyu.com/ratestream、hlsH5Preview 接口最高只到 2000kbps（720p 超清），已弃用。
import hashlib
import os
import re
import time
from email.utils import parsedate_to_datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DOUYU_COOKIE_FILE = os.environ.get('DOUYU_COOKIE_FILE') or os.path.join(HERE, 'douyu_cookie.txt')

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')


def load_cookie(path=None):
    path = path or DOUYU_COOKIE_FILE
    if path and os.path.isfile(path):
        with open(path) as f:
            return f.read().strip()
    return ''


def resolve_cookie(path=None):
    """优先级：DOUYU_COOKIE 环境变量 > cookie 文件。"""
    return os.environ.get('DOUYU_COOKIE', '').strip() or load_cookie(path)


class DouYu:
    URL_ENCRYPTION = 'https://www.douyu.com/wgapi/livenc/liveweb/websec/getEncryption'
    URL_PLAY = 'https://www.douyu.com/lapi/live/getH5PlayV1/{}'
    URL_BETARD = 'https://www.douyu.com/betard/{}'
    DID = '10000000000000000000000000001501'

    def __init__(self, rid, cookie=None, hevc=None):
        self.s = requests.Session()
        self.s.headers.update({
            'User-Agent': UA,
            'Referer': 'https://www.douyu.com/',
            'Accept': 'application/json, text/plain, */*',
        })
        self.cookie = resolve_cookie() if cookie is None else cookie
        if self.cookie:
            self.s.headers['Cookie'] = self.cookie
        # hevc=1 请求 H.265（部分房间的 2K/4K 原画只有 H.265 版本）
        self.hevc = os.environ.get('DOUYU_HEVC', '0') if hevc is None else str(hevc)
        self.rid = self._resolve_rid(rid)

    # ---------- 房间号 -> 真实 rid ----------
    def _resolve_rid(self, rid):
        try:
            data = self.s.get(self.URL_BETARD.format(rid), timeout=30).json()
            return str(data['room']['room_id'])
        except Exception:
            res = self.s.get('https://m.douyu.com/{}'.format(rid), timeout=30).text
            m = re.search(r'rid":(\d{1,8}),"vipId', res)
            if m:
                return m.group(1)
            raise Exception('房间号错误')

    # ---------- 签名 ----------
    @staticmethod
    def _md5(s):
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    def _get_encryption(self):
        r = self.s.get(self.URL_ENCRYPTION, params={'did': self.DID}, timeout=30)
        try:
            # 用服务器 Date 头当时间戳，避免本机时钟偏差导致 auth 校验失败
            ts = int(parsedate_to_datetime(r.headers.get('date', '')).timestamp())
        except (TypeError, ValueError, OverflowError):
            ts = int(time.time())
        j = r.json()
        if j.get('error') != 0 or not j.get('data'):
            raise Exception('获取加密参数失败: {}'.format(j.get('error')))
        return ts, j['data']

    def _compute_auth(self, ts, enc):
        suffix = '' if enc['is_special'] == 1 else '{}{}'.format(self.rid, ts)
        f = enc['rand_str']
        for _ in range(enc['enc_time']):
            f = self._md5(f + enc['key'])
        return self._md5(f + enc['key'] + suffix)

    def _play(self, rate, ts, enc):
        """rate: 0=原画(最高) 8=蓝光8M 4=蓝光4M 3=超清 2=高清 1=流畅"""
        resp = self.s.post(
            self.URL_PLAY.format(self.rid),
            data={
                'enc_data': enc['enc_data'],
                'tt': str(ts),
                'did': self.DID,
                'auth': self._compute_auth(ts, enc),
                'cdn': '',
                'rate': str(rate),
                'hevc': self.hevc,
                'fa': '0',
                'ive': '0',
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=30,
        ).json()
        if resp.get('error') != 0 or not resp.get('data'):
            return None
        return resp['data']

    @staticmethod
    def _url(data):
        return data['rtmp_url'] + '/' + data['rtmp_live']

    # ---------- 对外接口 ----------
    def get_real_url(self):
        """返回 {清晰度名称/码率: 播放地址}，'flv' 为当前可得的最高画质。"""
        ts, enc = self._get_encryption()
        top = self._play(0, ts, enc)  # rate=0 即网页上的「原画」
        if not top:
            raise Exception('房间未开播或接口无数据')

        ret = {}
        top_url = self._url(top)
        ret['flv'] = top_url
        ret['source'] = top_url

        for item in top.get('multirates', []):
            rate = item.get('rate', 0)
            bit = item.get('bit') or 0
            name = item.get('name', '')
            data = top if rate == 0 else self._play(rate, ts, enc)
            if not data:
                continue
            url = self._url(data)
            for key in (name, str(bit) if bit else '', '{}p'.format(bit) if bit else ''):
                if key:
                    ret[key] = url
            # 兼容原仓库的旧键名
            if name == '超清':
                ret.setdefault('2000p', url)
            if name == '高清':
                ret.setdefault('900p', url)
            if name.startswith('原画'):
                ret['flv'] = url
        return ret


if __name__ == '__main__':
    import sys
    r = input('输入斗鱼直播间号：\n') if len(sys.argv) < 2 else sys.argv[1]
    urls = DouYu(r).get_real_url()
    for k, v in urls.items():
        print('{:16} {}'.format(k, v))
