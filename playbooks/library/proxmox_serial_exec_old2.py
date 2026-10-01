#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import time
import socket

from ansible.module_utils.basic import AnsibleModule

try:
    import pexpect
    import pexpect.fdpexpect
    HAS_PEXPECT = True
except ImportError:
    HAS_PEXPECT = False


def run_serial_commands(socket_path, commands, user, password, timeout, expect_reboot=False):
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

    # login, password, または Junosのプロンプト末尾 (% >, # ) を検知
    prompt_regex = [r"(?i)login:\s*$", r"(?i)password:\s*$", r"[%#>]\s*$"]

    start_time = time.time()
    logged_in = False

    while time.time() - start_time < timeout:
        try:
            s.sendall(b"\r\n")
            # ログイン関連のプロンプトか、すでにログイン済みかを判定
            idx = proc.expect(prompt_regex, timeout=5)
            if idx == 0:
                s.sendall(f"{user}\r\n".encode("utf-8"))
            elif idx == 1:
                if password:
                    s.sendall(f"{password}\r\n".encode("utf-8"))
                else:
                    raise Exception("Password required but not provided.")
            elif idx == 2:
                logged_in = True
                break
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            break

    if not logged_in:
        s.close()
        buffer_content = proc.before.decode('utf-8', errors='ignore') if proc.before else "No output buffer"
        raise Exception(f"Timed out waiting for login prompt.\nConsole buffer:\n{buffer_content}")

    time.sleep(1)
    output_lines = []

    # コマンド送信ループ
    for cmd in cmd_list:
        s.sendall(cmd.encode("utf-8") + b"\r\n")
        
        if expect_reboot and "reboot" in cmd:
            try:
                proc.expect([r"(?i)going down", r"(?i)reboot", pexpect.EOF], timeout=10)
            except (pexpect.TIMEOUT, pexpect.EOF):
                pass
            output_lines.append(f"Reboot command '{cmd}' sent.")
            break

        try:
            # 実行完了のプロンプト（% , > , # ）が戻ってくるまで待機
            proc.expect(prompt_regex[2:], timeout=timeout)
            output_lines.append(proc.before.decode("utf-8", errors="ignore"))
            # コマンド間の処理追いつきを防ぐため微小なディレイ
            time.sleep(0.5)
        except pexpect.EOF:
            if expect_reboot:
                break
            else:
                raise Exception("Socket closed unexpectedly.")

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

    if not socket_path:
        socket_path = f"/var/run/qemu-server/{vmid}.serial0" 

    try:
        output = run_serial_commands(socket_path, commands, user, password, timeout, expect_reboot)
        module.exit_json(changed=True, stdout=output, socket_used=socket_path)
    except Exception as e:
        module.fail_json(msg=str(e), socket_used=socket_path)


if __name__ == '__main__':
    main()
