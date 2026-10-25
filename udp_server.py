"""Kênh UDP cho app Android: tìm máy tính, trạng thái nút bấm, ping (và sau này video).

Gói điện thoại -> máy tính (byte đầu là loại):
  b"PPD?"                         tìm máy (broadcast)
  b"S" seq:u32 lx ly rx ry:i16 flags:u8 + các hành động đang giữ, ngăn bằng "\n"
                                  flags bit0 = có cần trái, bit1 = có cần phải
  b"P" + 8 byte bất kỳ            ping
  b"X"                            ngắt kết nối (nhả hết)
Gói máy tính -> điện thoại:
  b"PPD!" + JSON                  trả lời tìm máy
  b"Q" + 8 byte ping + server_us:u32
  b"I" + JSON                     thông báo (vd. không tạo được tay cầm ảo)
                                  {"t":"player","n":2} = tay cầm ảo của điện thoại này là người chơi 2
                                  (số XInput Windows gán, game dùng số này), gửi lại mỗi 3 s

Gửi *trạng thái* thay vì *sự kiện*: mất gói không sao vì gói sau mang đủ trạng thái, không bao giờ kẹt phím.
"""
import asyncio
import json
import socket
import struct
import time

from session import PAD_LIB, Session

STATE_HDR = struct.Struct("<I4hB")
PEER_TIMEOUT = 1.0  # quá 1 s không nhận gói nào -> nhả hết phím của điện thoại đó
PLAYER_RESEND = 3.0


def now_us32():
    return (time.perf_counter_ns() // 1000) & 0xFFFFFFFF


class UdpPeer:
    def __init__(self, addr):
        self.addr = addr
        self.sess = Session("%s (app)" % addr[0])
        self.seq = -1
        self.last = time.monotonic()
        self.held = set()
        self.axes = {"L": (0.0, 0.0), "R": (0.0, 0.0)}
        self.reported_pad_error = False
        self.player = None        # 1-4: số người chơi của tay cầm ảo (tạo khi bấm nút Xbox đầu tiên)
        self.player_sent = 0.0
        self.pad_since = None
        self.reported_no_slot = False

    def apply(self, seq, lx, ly, rx, ry, flags, held):
        self.seq = seq
        for act in self.held - held:
            self.sess.release(act)
        for act in held - self.held:
            self.sess.press(act)
        self.held = held
        for side, bit, x, y in (("L", 1, lx, ly), ("R", 2, rx, ry)):
            v = (x / 32767.0, y / 32767.0) if flags & bit else (0.0, 0.0)
            if v != self.axes[side]:
                self.axes[side] = v
                self.sess.analog(side, *v)

    def close(self):
        self.sess.close()
        self.held.clear()


class UdpServer(asyncio.DatagramProtocol):
    def __init__(self, http_port, udp_port):
        self.http_port = http_port
        self.udp_port = udp_port
        self.peers = {}
        self.transport = None
        self.on_packet = None  # móc cho module video: on_packet(data, addr) -> True nếu đã xử lý

    def connection_made(self, transport):
        self.transport = transport
        sock = transport.get_extra_info("socket")
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
        except OSError:
            pass
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            # Windows: điện thoại tắt app -> ICMP "port unreachable" làm socket báo lỗi; tắt hành vi đó
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except OSError:
                pass
        asyncio.get_running_loop().create_task(self._reaper())

    def error_received(self, exc):
        pass

    def send(self, data, addr):
        if self.transport is not None:
            self.transport.sendto(data, addr)

    def datagram_received(self, data, addr):
        if not data:
            return
        t = data[0]
        if data[:4] == b"PPD?":  # phải kiểm tra trước ping vì cũng bắt đầu bằng 'P'
            info = {"name": socket.gethostname(), "http": self.http_port, "udp": self.udp_port, "pad": PAD_LIB}
            self.send(b"PPD!" + json.dumps(info).encode(), addr)
        elif t == 0x53 and len(data) >= 1 + STATE_HDR.size:  # 'S'
            peer = self.peers.get(addr)
            if peer is None:
                peer = self.peers[addr] = UdpPeer(addr)
                print("[+] App kết nối: %s  (UDP)" % addr[0])
            seq, lx, ly, rx, ry, flags = STATE_HDR.unpack_from(data, 1)
            now = time.monotonic()
            # bỏ gói đến muộn; nếu im lặng lâu (app mở lại) thì chấp nhận seq mới
            if seq > peer.seq or now - peer.last > 0.5:
                raw = data[1 + STATE_HDR.size:]
                held = set(raw.decode("utf-8", "ignore").split("\n")) if raw else set()
                held.discard("")
                peer.apply(seq, lx, ly, rx, ry, flags, held)
            peer.last = now
            if peer.sess.pad_error and not peer.reported_pad_error:
                peer.reported_pad_error = True
                print("[!] %s" % peer.sess.pad_error)
                self.send(b"I" + json.dumps({"t": "padfail", "msg": peer.sess.pad_error}).encode(), addr)
        elif t == 0x50:  # 'P' ping
            self.send(b"Q" + data[1:9] + struct.pack("<I", now_us32()), addr)
            peer = self.peers.get(addr)
            if peer is not None:
                peer.last = time.monotonic()
        elif t == 0x58:  # 'X'
            self._drop(addr)
        elif self.on_packet is not None:
            self.on_packet(data, addr)

    def _drop(self, addr):
        peer = self.peers.pop(addr, None)
        if peer is not None:
            peer.close()
            print("[-] App ngắt kết nối: %s  (UDP)" % addr[0])

    async def _reaper(self):
        while True:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            for addr, peer in list(self.peers.items()):
                if now - peer.last > PEER_TIMEOUT:
                    self._drop(addr)
                else:
                    self._check_player(peer, now)

    def _check_player(self, peer, now):
        """Báo cho điện thoại nó là người chơi số mấy (để 4 người biết ai là ai)."""
        if peer.sess.pad is None:
            return
        if peer.player is None:
            if peer.pad_since is None:
                peer.pad_since = now
            peer.player = peer.sess.player()
            if peer.player is not None:
                print("[pad] %s là tay cầm %d" % (peer.addr[0], peer.player))
            elif now - peer.pad_since > 2.0 and not peer.reported_no_slot:
                peer.reported_no_slot = True
                msg = ("Windows chỉ nhận 4 tay cầm Xbox (kể cả tay cầm thật đang cắm): tay cầm của điện "
                       "thoại này không có số người chơi, game không thấy nó.")
                print("[!] %s: %s" % (peer.addr[0], msg))
                self.send(b"I" + json.dumps({"t": "padfail", "msg": msg}).encode(), peer.addr)
                return
        if peer.player is not None and now - peer.player_sent > PLAYER_RESEND:
            peer.player_sent = now
            self.send(b"I" + json.dumps({"t": "player", "n": peer.player}).encode(), peer.addr)

    def close_all(self):
        for addr in list(self.peers):
            self._drop(addr)


async def start_udp(http_port, udp_port):
    loop = asyncio.get_running_loop()
    _, proto = await loop.create_datagram_endpoint(
        lambda: UdpServer(http_port, udp_port), local_addr=("0.0.0.0", udp_port))
    return proto
