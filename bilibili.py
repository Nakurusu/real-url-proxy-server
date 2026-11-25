# 获取哔哩哔哩直播的真实流媒体地址。
#
# 画质 qn：30000 杜比 / 20000 4K / 10000 原画 / 400 蓝光 / 250 超清 / 150 高清 / 80 流畅
# 重要：**未登录时接口会把 current_qn 压到 250（超清 720p）**，哪怕你请求 qn=10000。
#       想拿原画必须带登录 Cookie（SESSDATA）；4K/杜比还要大会员。
#
# Cookie 自动加载顺序：
#   1. BiliBili(rid, cookie='...')
#   2. 环境变量 BILIBILI_COOKIE
#   3. cookie 文件（环境变量 BILIBILI_COOKIE_FILE，或同目录 bilibili_cookie.txt）
# 用 bilibili_cookie.py 可以扫码获取。
import os
import struct
import subprocess
import tempfile
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BILIBILI_COOKIE_FILE = os.environ.get('BILIBILI_COOKIE_FILE') or os.path.join(HERE, 'bilibili_cookie.txt')

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

ROOM_INIT = 'https://api.live.bilibili.com/room/v1/Room/room_init'
PLAY_INFO = 'https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo'

# qn -> 名字
QUALITY_NAMES = {30000: '杜比', 20000: '4K', 10000: '原画', 400: '蓝光', 250: '超清', 150: '高清', 80: '流畅'}
# 依次尝试的顺序（拿不到就往下退）
QUALITY_ORDER = [20000, 10000, 400, 250, 150]


def load_cookie(path=None):
    path = path or BILIBILI_COOKIE_FILE
    if path and os.path.isfile(path):
        with open(path) as f:
            return f.read().strip()
    return ''


def resolve_cookie(path=None):
    """优先级：BILIBILI_COOKIE 环境变量 > cookie 文件。"""
    return os.environ.get('BILIBILI_COOKIE', '').strip() or load_cookie(path)


class BiliBili:
    def __init__(self, rid, cookie=None, hevc=False):
        self.s = requests.Session()
        self.s.headers.update({'User-Agent': UA, 'Referer': 'https://live.bilibili.com/'})
        self.cookie = resolve_cookie() if cookie is None else cookie
        if self.cookie:
            self.s.headers['Cookie'] = self.cookie
        self.hevc = hevc
        self.rid, self.room_id, self.live_status = self._room_init(rid)

    # ---------- 房间信息 ----------
    def _room_init(self, rid):
        j = self.s.get(ROOM_INIT, params={'id': rid}, timeout=30).json()
        if j.get('code') != 0:
            raise Exception('房间不存在: {}'.format(j.get('message')))
        d = j['data']
        return str(rid), str(d['room_id']), d['live_status']

    # ---------- 取流 ----------
    def _play_info(self, qn):
        params = {
            'room_id': self.room_id,
            'no_playurl': 0,
            'mask': 0,
            'qn': qn,
            'platform': 'web',
            'protocol': '0,1',   # 0: http_stream(flv) 1: http_hls
            'format': '0,1,2',   # 0: flv 1: ts 2: fmp4
            'codec': '1,0' if self.hevc else '0,1',
        }
        j = self.s.get(PLAY_INFO, params=params, timeout=30).json()
        pi = ((j.get('data') or {}).get('playurl_info') or {}).get('playurl')
        if not pi:
            return None
        return pi

    @staticmethod
    def _collect(pi, protocol_name, format_name=None, codec_name=None):
        """按协议/格式/编码挑出一个地址，返回 (url, 实际 qn)。"""
        for stream in pi.get('stream', []):
            if stream['protocol_name'] != protocol_name:
                continue
            for fmt in stream['format']:
                if format_name and fmt['format_name'] != format_name:
                    continue
                for codec in fmt['codec']:
                    if codec_name and codec['codec_name'] != codec_name:
                        continue
                    info = codec['url_info'][0]
                    return info['host'] + codec['base_url'] + info['extra'], codec.get('current_qn')
        return None, None

    def get_real_url(self, qn=None):
        """返回 {清晰度: 播放地址}，'flv' / 'm3u8' 是默认档（最高可得）。
        未登录时所有档都会指向 250（超清）。"""
        if qn is not None:
            order = [qn]
        else:
            order = list(QUALITY_ORDER)

        ret = {}
        best = None
        for want in order:
            pi = self._play_info(want)
            if not pi:
                continue
            url, actual_qn = self._collect(pi, 'http_stream', 'flv', 'avc')
            if not url:
                continue
            name = QUALITY_NAMES.get(actual_qn, str(actual_qn))
            ret.setdefault(str(actual_qn), url)
            ret.setdefault(name, url)
            if best is None:
                best = url
                ret['flv'] = url
                hls, _ = self._collect(pi, 'http_hls', None, 'avc')
                if hls:
                    ret['m3u8'] = hls
        return ret

    # ---------- 画质体检 ----------
    def probe(self):
        """逐个 qn 请求，打印『请求的档 / 实际拿到的档 / 流的真实参数』。"""
        accepted = None
        for want in [20000, 10000, 400, 250, 150]:
            pi = self._play_info(want)
            if not pi:
                continue
            url, actual_qn = self._collect(pi, 'http_stream', 'flv', 'avc')
            if accepted is None:
                for stream in pi.get('stream', []):
                    for fmt in stream['format']:
                        for codec in fmt['codec']:
                            if codec.get('accept_qn'):
                                accepted = codec['accept_qn']
                                break
            if not url:
                print('  请求 qn={:<6} 无 http_stream/flv 地址'.format(want))
                continue
            st = stream_stats(url)
            flag = '' if actual_qn == want else '  ← 被降级'
            print('  请求 qn={:<6}({:<3}) 实际 qn={:<6}({:<3}) {}x{} @{}fps 实测 {}kbps{}'.format(
                want, QUALITY_NAMES.get(want, '?'), actual_qn, QUALITY_NAMES.get(actual_qn, '?'),
                st.get('width', '?'), st.get('height', '?'), st.get('framerate', '?'),
                st.get('kbps', '?'), flag))
        if accepted:
            print('  房间可提供: {}'.format('、'.join(
                '{}({})'.format(QUALITY_NAMES.get(q, q), q) for q in accepted)))


def stream_stats(url, referer='https://live.bilibili.com/', limit=12000000, timeout=6, retry=1):
    """下一小段流，同时给出两样东西：
      * 分辨率 / 帧率：读 FLV onMetaData（width / height / framerate）
      * 实测码率：按实际下载字节 / 耗时算（B站元数据里的 videodatarate 不准，不能直接用）
    注意：B站的 CDN 不吃 Range 请求（会返回 0 字节），所以用 --max-filesize + --max-time 截断；
    而且不少地址会先 302 一次，必须加 -L 跟随。
    """
    out = {}
    for attempt in range(retry + 1):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        t0 = time.time()
        try:
            subprocess.run(['curl', '-sL', '--max-time', str(timeout),
                            '--max-filesize', str(limit),
                            '-H', 'Referer: ' + referer, '-H', 'User-Agent: ' + UA,
                            '-o', path, url], check=False)
            elapsed = max(time.time() - t0, 0.001)
            with open(path, 'rb') as f:
                data = f.read()
        finally:
            os.unlink(path)

        out = {'bytes': len(data), 'seconds': round(elapsed, 1),
               'kbps': round(len(data) * 8 / elapsed / 1000)}
        for key in (b'width', b'height', b'framerate'):
            i = data.find(key)
            if i >= 0 and i + len(key) + 9 <= len(data):
                out[key.decode()] = round(struct.unpack('>d', data[i + len(key) + 1:i + len(key) + 9])[0])
        if 'width' in out or attempt == retry:
            return out
    return out


def probe(rid, cookie=None):
    b = BiliBili(rid, cookie=cookie)
    print('rid = {} (真实房间 {}) | cookie = {}'.format(
        rid, b.room_id, '已加载（登录态）' if b.cookie else '无（匿名）'))
    if b.live_status != 1:
        print('未开播 (live_status={})'.format(b.live_status))
        return
    print('实际拿到的流：')
    b.probe()


if __name__ == '__main__':
    import sys
    r = input('请输入bilibili直播房间号：\n') if len(sys.argv) < 2 else sys.argv[1]
    probe(r)
