#!/usr/bin/env python3
"""
마우스 커서 찾기 (Mouse Cursor Finder)
─────────────────────────────────────
사용법: 마우스 휠(중간) 버튼을 한 번 클릭하면 2초간 커서 위치를 강조합니다.
효과 1 - 점멸 링  : 커서 주변에 3겹 링이 커졌다 작아지기를 3회 반복
효과 2 - 커서 확대: 실제 커서 위에 3배 크기 화살표 오버레이가 실시간으로 따라다님
종료  : 화면 우측 상단 상태 표시줄의 X 클릭
"""
import tkinter as tk
import ctypes
import ctypes.wintypes
import threading
import time
import math
import sys

# ── Win32 API 진입점 ──────────────────────────────────────────────────────────
user32 = ctypes.windll.user32

# ── Win32 상수 ────────────────────────────────────────────────────────────────
WH_MOUSE_LL        = 14           # 전역 저수준 마우스 훅 종류
WM_MBUTTONDOWN     = 0x0207       # 휠(중간) 버튼 누름 이벤트
HC_ACTION          = 0            # 훅 콜백: 실제 이벤트 발생 코드
GWL_EXSTYLE        = -20          # GetWindowLong 인덱스: 확장 윈도우 스타일
WS_EX_LAYERED      = 0x00080000   # 레이어드 윈도우 (투명색 지원)
WS_EX_TRANSPARENT  = 0x00000020   # 마우스 클릭을 아래 창으로 통과시킴
LWA_COLORKEY       = 0x00000001   # SetLayeredWindowAttributes 플래그: 색상 키 투명
SM_XVIRTUALSCREEN  = 76           # 가상 화면 시작 X (다중 모니터에서 음수 가능)
SM_YVIRTUALSCREEN  = 77           # 가상 화면 시작 Y
SM_CXVIRTUALSCREEN = 78           # 가상 화면 전체 너비 (모든 모니터 합산)
SM_CYVIRTUALSCREEN = 79           # 가상 화면 전체 높이

# ── 투명 처리용 배경 색상 ─────────────────────────────────────────────────────
# 캔버스 배경을 이 색으로 채우면 해당 픽셀이 완전 투명해짐
# #020202(거의 검정)를 쓰는 이유: #000000(순수 검정)은 일부 윈도우 테마와 충돌 가능
TKEY          = '#020202'
TKEY_COLORREF = 0x00020202        # Win32 COLORREF 형식 (0x00BBGGRR 순서)


# ── 저수준 마우스 훅 데이터 구조체 ───────────────────────────────────────────
class MSLLHOOKSTRUCT(ctypes.Structure):
    """WH_MOUSE_LL 훅 콜백에서 lParam으로 전달되는 마우스 이벤트 정보"""

    class _POINT(ctypes.Structure):
        """화면 좌표 (x, y)"""
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    _fields_ = [
        ("pt",          _POINT),       # 이벤트 발생 시 마우스 화면 좌표
        ("mouseData",   ctypes.c_ulong),  # 휠 이동량 또는 X 버튼 식별자
        ("flags",       ctypes.c_ulong),  # 이벤트 플래그 (인젝션 여부 등)
        ("time",        ctypes.c_ulong),  # 이벤트 발생 시각 (ms)
        ("dwExtraInfo", ctypes.c_void_p), # 애플리케이션 정의 추가 정보
    ]


# ── 메인 클래스 ───────────────────────────────────────────────────────────────
class MouseFinder:
    # 동작 설정값
    WSIZE       = 300    # 오버레이 창 크기 (가로 = 세로, px)
    TIP_OFFSET  = 110    # 오버레이 창 내부에서 커서 팁까지의 여백 (px)
                         # 이 값이 클수록 링이 화면 끝에서 잘리지 않음
    DURATION    = 2.0    # 효과 지속 시간 (초)
    INTERVAL_MS = 25     # 프레임 갱신 간격 ms (~40 fps)
    CURSOR_SIZE = 66     # 오버레이 커서 높이 (기본 Windows 커서 22px × 3배)

    def __init__(self):
        # tkinter 루트 창: UI 이벤트 루프 담당, 화면에는 표시하지 않음
        self.root = tk.Tk()
        self.root.withdraw()           # 루트 창 숨김 (상태 표시줄은 Toplevel로 별도 생성)
        self._busy = False             # 효과 실행 중 중복 실행 방지 플래그
        self._setup_status()           # 상태 표시줄 생성
        self._start_hook()             # 전역 마우스 훅 시작

    # ── 상태 표시줄 ───────────────────────────────────────────────────────────
    def _setup_status(self):
        """화면 우측 상단에 '실행 중' 표시줄과 종료 버튼을 만든다."""
        sw  = user32.GetSystemMetrics(0)   # 주 모니터 너비
        bar = tk.Toplevel(self.root)
        bar.overrideredirect(True)          # 윈도우 제목 표시줄·테두리 제거
        bar.wm_attributes('-topmost', True) # 항상 최상단
        bar.wm_attributes('-alpha', 0.88)   # 약간 반투명
        bar.geometry(f"232x26+{sw - 242}+4")  # 우측 상단 고정

        f = tk.Frame(bar, bg='#1e1e2e', padx=5, pady=3)
        f.pack(fill='both', expand=True)

        tk.Label(f, text="● 커서 찾기 실행 중",
                 fg='#50fa7b', bg='#1e1e2e',
                 font=('맑은 고딕', 8)).pack(side='left')

        # X 버튼: 클릭하면 tkinter 루프와 프로세스를 완전히 종료
        btn = tk.Label(f, text="  X  ", fg='#ff6b6b', bg='#1e1e2e',
                       cursor='hand2', font=('맑은 고딕', 9, 'bold'))
        btn.pack(side='right')
        btn.bind('<Button-1>', lambda _: (self.root.destroy(), sys.exit()))

    # ── 커서 화살표 그리기 ────────────────────────────────────────────────────
    def _draw_cursor(self, canvas, ox, oy):
        """
        (ox, oy)를 팁으로 하는 Windows 표준 화살표 모양의 폴리곤을 그린다.
        크기는 CURSOR_SIZE(기본 66px = 표준 22px의 3배).
        """
        s = self.CURSOR_SIZE

        # 폴리곤 꼭짓점 좌표 (팁=원점 기준)
        pts = [
            (ox,          oy          ),  # ① 팁 (클릭 지점)
            (ox,          oy + s      ),  # ② 왼쪽 하단
            (ox + s//3,   oy + s*2//3 ),  # ③ 왼쪽 노치 (손잡이 시작)
            (ox + s//2,   oy + s*4//3 ),  # ④ 손잡이 좌하단
            (ox + s*2//3, oy + s*4//3 ),  # ⑤ 손잡이 우하단
            (ox + s//2,   oy + s*2//3 ),  # ⑥ 오른쪽 노치
            (ox + s,      oy + s*2//3 ),  # ⑦ 오른쪽 상단
        ]

        # 그림자: 동일 모양을 오른쪽 아래 3px 오프셋으로 먼저 그려 입체감 부여
        shadow = [(x + 3, y + 3) for x, y in pts]
        canvas.create_polygon(shadow, fill='#333333', outline='', smooth=False)

        # 커서 본체: 흰색 채우기 + 검은 테두리
        canvas.create_polygon(pts, fill='white', outline='#111111',
                               width=2, smooth=False)

        # 팁 강조 점: 빨간 원으로 클릭 지점을 명확하게 표시
        canvas.create_oval(ox - 6, oy - 6, ox + 6, oy + 6,
                           fill='#FF3300', outline='white', width=2)

    # ── 커서 강조 효과 실행 ───────────────────────────────────────────────────
    def show_effect(self):
        """
        오버레이 창을 생성하고 점멸 링 + 확대 커서 애니메이션을 2초간 재생한다.
        이미 효과가 실행 중이면 중복 실행하지 않는다.
        """
        if self._busy:
            return
        self._busy = True

        S   = self.WSIZE
        TIP = self.TIP_OFFSET

        # 다중 모니터 전체 가상 화면 좌표 범위 확보
        # GetSystemMetrics(0/1)은 주 모니터만 반환하므로 SM_VIRTUAL* 사용
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)   # 왼쪽 끝 X (음수 가능)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)   # 위쪽 끝 Y
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)  # 전체 너비
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)  # 전체 높이

        # 오버레이 창 생성
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)           # 제목 표시줄·테두리 제거
        win.wm_attributes('-topmost', True)  # 항상 최상단
        win.wm_attributes('-transparentcolor', TKEY)  # TKEY 색상 픽셀을 투명 처리

        # 초기 위치를 화면 밖(-600, -600)으로 설정
        # → update_idletasks() 호출 시 창이 렌더링되는 순간 검은 사각형이
        #   화면에 잠깐 보이는 플래시 현상을 방지
        win.geometry(f"{S}x{S}+-{S * 2}+-{S * 2}")

        # 투명 배경 캔버스: TKEY 색상 부분이 투명, 그린 도형만 보임
        canvas = tk.Canvas(win, width=S, height=S,
                           bg=TKEY, highlightthickness=0)
        canvas.pack(fill='both', expand=True)

        # update_idletasks로 창 핸들(hwnd) 확보 (화면 밖이므로 사용자에게 안 보임)
        win.update_idletasks()
        hwnd = win.winfo_id()

        # 클릭 통과(WS_EX_TRANSPARENT) 설정
        # 주의: SetWindowLongW 호출 후 반드시 SetLayeredWindowAttributes를 재호출해야 함
        #       그렇지 않으면 tkinter가 설정한 transparentcolor(colorkey)가 초기화되어
        #       배경이 투명해지지 않고 검은 사각형으로 보임
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                              style | WS_EX_TRANSPARENT | WS_EX_LAYERED)
        user32.SetLayeredWindowAttributes(
            hwnd,
            ctypes.c_ulong(TKEY_COLORREF),  # 투명으로 처리할 색상
            ctypes.c_byte(0),               # 알파값 (LWA_COLORKEY 모드에선 무시됨)
            ctypes.c_ulong(LWA_COLORKEY),   # 색상 키 방식 투명 사용
        )

        t0 = time.time()  # 효과 시작 시각

        def update():
            """매 프레임 호출: 창 위치 갱신 + 점멸 링·커서 오버레이 재그림"""
            dt = time.time() - t0  # 경과 시간 (초)

            if dt >= self.DURATION:
                # 2초 경과 → 오버레이 창 제거 및 플래그 해제
                win.destroy()
                self._busy = False
                return

            # 커서 현재 위치를 tkinter 좌표계로 읽음
            # ctypes의 GetCursorPos 대신 winfo_pointerx/y를 사용하는 이유:
            #   고DPI(예: 150%) 환경에서 ctypes는 물리 픽셀을 반환하지만
            #   tkinter 창 위치는 논리 픽셀(DPI 스케일 적용)을 사용하므로
            #   두 좌표계가 달라 커서 오버레이 위치가 어긋나는 문제가 발생함
            mx = win.winfo_pointerx()
            my = win.winfo_pointery()

            # 오버레이 창 위치: 커서 팁이 창 내부 (TIP, TIP)에 오도록 배치
            # 화면 경계를 벗어나지 않도록 가상 화면 범위로 클램프
            wx = max(vx, min(mx - TIP, vx + vw - S))
            wy = max(vy, min(my - TIP, vy + vh - S))

            # tkinter geometry()로 창 이동: winfo_pointer와 동일한 좌표계 사용
            # (SetWindowPos를 직접 쓰면 DPI 좌표계 불일치 재발 가능)
            win.geometry(f"+{wx}+{wy}")

            # 창 내부 기준 커서 팁 좌표 (lx, ly)
            # = 화면 커서 좌표 - 창 왼쪽 상단 좌표
            lx = mx - wx
            ly = my - wy

            canvas.delete('all')  # 이전 프레임 지우기

            # ── 효과 1: 점멸 링 ──────────────────────────────────────────────
            # sin 함수로 반지름이 주기적으로 커졌다 작아짐 (2초 동안 3회)
            pulse = abs(math.sin(dt * math.pi * 3))  # 0.0 ~ 1.0 왕복
            r = 20 + pulse * 40                       # 반지름 20 → 60 px (3배 변화)

            # 바깥쪽부터 빨강·주황·노랑 3겹 링 (각 12px 간격)
            for i, (col, lw) in enumerate(
                    [('#FF3300', 4), ('#FF8800', 3), ('#FFCC00', 2)]):
                ri = r + i * 12
                canvas.create_oval(lx - ri, ly - ri,
                                   lx + ri, ly + ri,
                                   outline=col, width=lw)

            # ── 효과 2: 3배 크기 커서 오버레이 ──────────────────────────────
            # 실제 커서 팁(lx, ly) 위에 큰 화살표를 덮어 그려 시각적으로 커 보이게 함
            self._draw_cursor(canvas, lx, ly)

            # 다음 프레임 예약
            win.after(self.INTERVAL_MS, update)

        update()  # 첫 프레임 즉시 실행 (창을 화면 밖에서 올바른 위치로 이동)

    # ── 전역 마우스 훅 설정 ───────────────────────────────────────────────────
    # ctypes 콜백 타입: 클래스 속성으로 선언해 인스턴스 생명주기 동안 GC 방지
    PROC = ctypes.CFUNCTYPE(
        ctypes.c_long,                    # 반환값: 다음 훅으로 전달할 결과
        ctypes.c_int,                     # nCode: 훅 처리 방법 (HC_ACTION 등)
        ctypes.c_ulong,                   # wParam: 메시지 종류 (WM_MBUTTONDOWN 등)
        ctypes.POINTER(MSLLHOOKSTRUCT),   # lParam: 마우스 이벤트 상세 정보
    )

    def _hook_proc(self, nCode, wParam, lParam):
        """
        전역 마우스 훅 콜백: 모든 마우스 이벤트에서 호출됨.
        휠(중간) 버튼 클릭 감지 시 메인 스레드에 show_effect 예약.
        """
        if nCode == HC_ACTION and wParam == WM_MBUTTONDOWN:
            # root.after(0, ...) : 훅 스레드에서 tkinter 메인 스레드로 안전하게 전달
            self.root.after(0, self.show_effect)

        # 반드시 CallNextHookEx를 호출해야 다음 훅·애플리케이션이 이벤트를 받음
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _start_hook(self):
        """별도 스레드에서 훅 메시지 루프를 실행한다."""
        # self._cb에 저장: 함수 객체가 GC되면 콜백 호출 시 크래시 발생하므로 참조 유지
        self._cb = self.PROC(self._hook_proc)

        def run():
            # WH_MOUSE_LL: 시스템 전체 마우스 이벤트를 가로챔
            hk = user32.SetWindowsHookExW(WH_MOUSE_LL, self._cb, None, 0)
            msg = ctypes.wintypes.MSG()
            # GetMessage 루프: 훅이 동작하려면 메시지 루프가 필요
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            user32.UnhookWindowsHookEx(hk)  # 루프 종료 시 훅 해제

        # daemon=True: 메인 스레드 종료 시 훅 스레드도 자동 종료
        threading.Thread(target=run, daemon=True).start()

    def run(self):
        """tkinter 이벤트 루프 시작 (프로그램 메인 루프)"""
        self.root.mainloop()


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    MouseFinder().run()
