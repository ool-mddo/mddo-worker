#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import re       # 正規表現モジュールを追加
import time
import socket

from ansible.module_utils.basic import AnsibleModule

try:
    import pexpect
    import pexpect.fdpexpect
    HAS_PEXPECT = True
except ImportError:
    HAS_PEXPECT = False


DOCUMENTATION = r'''
---
module: proxmox_serial_exec
short_description: Execute commands on Proxmox VM via serial socket with VNF-specific profiles
options:
  container:
    description: Target Proxmox container name.
    required: false
    type: str
  vmid:
    description: Proxmox VMID.
    required: false
    default: 100
    type: int
  socket_path:
    description: Full path override for the UNIX domain socket.
    required: false
    type: str
  commands:
    description: Command string or list of commands to execute.
    required: true
    type: raw
  user:
    description: Login user for console prompt.
    required: false
    default: "root"
    type: str
  password:
    description: Login password for console prompt.
    required: false
    type: str
  timeout:
    description: Timeout for socket connection and prompt wait in seconds.
    required: false
    default: 180
    type: int
  expect_reboot:
    description: Whether to expect a reboot after reboot command.
    required: false
    default: false
    type: bool
  vnf_type:
    description: VNF type to select vendor/OS-specific prompt regex and timing ('vsrx', 'linux', etc.).
    required: false
    default: "vsrx"
    type: str
'''

# VNFタイプ別のプロファイル定義
# prompt_regex は [ログインプロンプト, パスワードプロンプト, シェルプロンプト] を返す
VNF_PROFILES = {
    "vsrx": {
        "prompt_regex": lambda user: [
            r"(?i)login:\s*",
            r"(?i)password:\s*",
            r"(?i)" + re.escape(user) + r"(?:@[^%#>\s]*)?\s*[%#>]"
        ],
        # vSRX特有のDPDKやインターフェース認識によるカーネルログが落ち着くまで15秒待機
        "post_login_delay": 15,
        "reboot_patterns": [r"(?i)going down", r"(?i)reboot"],
    },
    "linux": {
        "prompt_regex": lambda user: [
            r"(?i)login:\s*",
            r"(?i)password:\s*",
            r"(?i)(?:[#$>]|" + re.escape(user) + r"(?:@[^%#$>\s]*)?\s*[%#$>])"
        ],
        "post_login_delay": 1,
        "reboot_patterns": [r"(?i)reboot", r"(?i)shutdown", r"(?i)power down"],
    },
    "generic": {
        "prompt_regex": lambda user: [
            r"(?i)login:\s*",
            r"(?i)password:\s*",
            r"(?i)(?:[#$%>]|" + re.escape(user) + r"@.*[#$%>])\s*"
        ],
        "post_login_delay": 1,
        "reboot_patterns": [r"(?i)reboot", r"(?i)shutdown"],
    },
}


def get_vnf_config(vnf_type, user):
    """VNFタイプに応じた設定を展開して返すヘルパー関数"""
    key = (vnf_type or "vsrx").lower()
    if key not in VNF_PROFILES:
        supported = ", ".join(sorted(VNF_PROFILES.keys()))
        raise ValueError(f"Unsupported vnf_type '{vnf_type}'. Supported types: {supported}")

    profile = VNF_PROFILES[key]
    prompt_builder = profile.get("prompt_regex")
    if callable(prompt_builder):
        prompts = prompt_builder(user)
    else:
        prompts = list(prompt_builder)

    return {
        "prompt_regex": prompts,
        "post_login_delay": profile.get("post_login_delay", 0),
        "reboot_patterns": list(profile.get("reboot_patterns", [r"(?i)reboot"])),
    }


def run_serial_commands(socket_path, commands, user, password, timeout, expect_reboot=False, vnf_type="vsrx"):
    vnf_config = get_vnf_config(vnf_type, user)
    prompt_regex = vnf_config["prompt_regex"]
    post_login_delay = vnf_config["post_login_delay"]
    reboot_patterns = vnf_config["reboot_patterns"]

    start_wait = time.time()
    while time.time() - start_wait < 30:
        if os.path.exists(socket_path):
            break
        time.sleep(1)

    if not os.path.exists(socket_path):
        raise Exception(f"Socket file not found at {socket_path}")

    if isinstance(commands, list):
        cmd_list = [str(c).strip() for c in commands if str(c).strip()]
    else:
        cmd_list = [line.strip() for line in str(commands).strip().splitlines() if line.strip()]

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connected = False
    for _ in range(30):
        try:
            s.connect(socket_path)
            connected = True
            break
        except Exception:
            time.sleep(1)

    if not connected:
        raise Exception(f"Failed to connect to UNIX socket: {socket_path}")

    proc = pexpect.fdpexpect.fdspawn(s.fileno(), timeout=timeout)

    # 1. 接続直後のゴミデータ（前回のセッションの残骸など）を完全にクリア
    while True:
        try:
            proc.read_nonblocking(size=4096, timeout=0.1)
        except (pexpect.TIMEOUT, pexpect.EOF):
            break

    start_time = time.time()
    logged_in = False
    last_send = 0

    # 2. ログイン待機ループ（スマート・ポーリング）
    while time.time() - start_time < timeout:
        # 画面が4秒以上止まっている場合のみ、Enterを押して画面を更新させる
        if time.time() - last_send > 4:
            try:
                s.sendall(b"\r\n")
            except Exception:
                pass
            last_send = time.time()
        
        try:
            idx = proc.expect(prompt_regex, timeout=5)
            
            if idx == 0:
                time.sleep(0.5)
                s.sendall(f"{user}\r\n".encode("utf-8"))
                last_send = time.time()
            elif idx == 1:
                time.sleep(0.5)
                if password:
                    s.sendall(f"{password}\r\n".encode("utf-8"))
                else:
                    raise Exception("Password required but not provided.")
                last_send = time.time()
            elif idx == 2:
                logged_in = True
                break
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            time.sleep(2)
            continue

    if not logged_in:
        s.close()
        buffer_content = proc.before.decode('utf-8', errors='ignore') if proc.before else "No output buffer"
        raise Exception(f"Timed out after {timeout}s waiting for login.\nConsole buffer:\n{buffer_content}")

    # 3. ログイン後のノイズ回避
    # VNF固有の待機時間（vSRX特有のDPDKやインターフェース認識によるカーネルログが落ち着くまで待機等）
    if post_login_delay > 0:
        time.sleep(post_login_delay)

    # 待機中にコンソールに吐き出されたノイズログを完全に読み捨ててクリアする
    while True:
        try:
            proc.read_nonblocking(size=4096, timeout=0.1)
        except (pexpect.TIMEOUT, pexpect.EOF):
            break

    # クリア後、綺麗なプロンプトを再表示させて同期を確実に取る
    try:
        s.sendall(b"\r\n")
        proc.expect(prompt_regex[2], timeout=5)
    except Exception:
        pass

    time.sleep(1)
    output_lines = []

    # 4. コマンドの順次実行
    for cmd in cmd_list:
        s.sendall(cmd.encode("utf-8") + b"\r\n")
        
        if expect_reboot and "reboot" in cmd:
            try:
                proc.expect(reboot_patterns + [pexpect.EOF], timeout=10)
            except (pexpect.TIMEOUT, pexpect.EOF):
                pass
            output_lines.append(f"Reboot command '{cmd}' sent.")
            break

        try:
            proc.expect(prompt_regex[2], timeout=timeout)
            output_lines.append(proc.before.decode("utf-8", errors="ignore"))
            # 次のコマンドとログが混ざらないように微小なディレイを入れる
            time.sleep(0.5)
        except pexpect.TIMEOUT:
            buffer_content = proc.before.decode('utf-8', errors='ignore') if proc.before else "No output buffer"
            raise Exception(f"Command '{cmd}' timed out.\nConsole buffer:\n{buffer_content}")
        except pexpect.EOF:
            buffer_content = proc.before.decode('utf-8', errors='ignore') if proc.before else "No output buffer"
            if expect_reboot:
                break
            else:
                raise Exception(f"Socket closed unexpectedly.\nConsole buffer:\n{buffer_content}")

    s.close()
    return "\n".join(output_lines)


def main():
    module_args = dict(
        container=dict(type='str', required=False, default=None),
        vmid=dict(type='int', required=False, default=100),
        socket_path=dict(type='str', required=False, default=None),
        commands=dict(type='raw', required=True),
        user=dict(type='str', required=False, default="root"),
        password=dict(type='str', required=False, default=None, no_log=True),
        timeout=dict(type='int', required=False, default=180),
        expect_reboot=dict(type='bool', required=False, default=False),
        vnf_type=dict(type='str', required=False, default="vsrx"),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=False)

    if not HAS_PEXPECT:
        module.fail_json(msg="The 'pexpect' Python module is required.")

    container = module.params['container']
    vmid = module.params['vmid']
    socket_path = module.params['socket_path']
    commands = module.params['commands']
    user = module.params['user']
    password = module.params['password']
    timeout = module.params['timeout']
    expect_reboot = module.params['expect_reboot']
    vnf_type = module.params['vnf_type']

    if not socket_path:
        socket_path = f"/var/run/qemu-server/{vmid}.serial0" 

    try:
        output = run_serial_commands(socket_path, commands, user, password, timeout, expect_reboot, vnf_type)
        module.exit_json(changed=True, stdout=output, socket_used=socket_path)
    except Exception as e:
        module.fail_json(msg=str(e), socket_used=socket_path)


if __name__ == '__main__':
    main()
