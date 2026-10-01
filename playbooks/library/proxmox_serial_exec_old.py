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

DOCUMENTATION = r'''
---
module: proxmox_serial_exec
short_description: Execute commands on Proxmox VM via container-specific shared serial socket
options:
  container:
    description: Target Proxmox container name (directory name under /var/run/qemu-server/).
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
  timeout:
    description: Timeout for socket connection and prompt wait in seconds.
    required: false
    default: 180
    type: int
'''


def run_serial_commands(socket_path, commands, user, timeout):
    # ソケット生成を待機（VM起動時のラグ対応）
    start_wait = time.time()
    while time.time() - start_wait < 30:
        if os.path.exists(socket_path):
            break
        time.sleep(1)

    if not os.path.exists(socket_path):
        raise Exception(f"Socket file not found at {socket_path}")

    # コマンドのリスト正規化
    if isinstance(commands, list):
        cmd_list = [str(c).strip() for c in commands if str(c).strip()]
    else:
        cmd_list = [line.strip() for line in str(commands).strip().splitlines() if line.strip()]

    # UNIX ドメインソケットへ接続
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

    # login プロンプト検知ループ
    start_time = time.time()
    logged_in = False
    while time.time() - start_time < timeout:
        try:
            s.sendall(b"\n")
            idx = proc.expect([r"login:", r"root@.*[#%]"], timeout=5)
            if idx == 0:
                s.sendall(f"{user}\n".encode("utf-8"))
                proc.expect(r"root@.*[#%]", timeout=30)
                logged_in = True
                break
            elif idx == 1:
                logged_in = True
                break
        except pexpect.TIMEOUT:
            continue

    if not logged_in:
        s.close()
        raise Exception("Timed out waiting for login prompt on serial console.")

    time.sleep(1)

    # コマンドを順次実行
    output_lines = []
    for cmd in cmd_list:
        s.sendall(cmd.encode("utf-8") + b"\n")
        proc.expect(r"root@.*[#%]", timeout=timeout)
        output_lines.append(proc.before.decode("utf-8", errors="ignore"))

    s.close()
    return "\n".join(output_lines)


def main():
    module_args = dict(
        container=dict(type='str', required=False, default=None),
        vmid=dict(type='int', required=False, default=100),
        socket_path=dict(type='str', required=False, default=None),
        commands=dict(type='raw', required=True),
        user=dict(type='str', required=False, default="root"),
        timeout=dict(type='int', required=False, default=180),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=False
    )

    if not HAS_PEXPECT:
        module.fail_json(msg="The 'pexpect' Python module is required on the Ansible worker.")

    container = module.params['container']
    vmid = module.params['vmid']
    socket_path = module.params['socket_path']
    commands = module.params['commands']
    user = module.params['user']
    timeout = module.params['timeout']

    # パスの自動解決
    if not socket_path:
        if container:
            #socket_path = f"/tmp/proxmox-shared/qemu/{container}/{vmid}.serial0"
            socket_path = f"/tmp/proxmox-shared/qemu/{vmid}.serial0"
        else:
            socket_path = f"/tmp/proxmox-shared/qemu/{vmid}.serial0"

    try:
        output = run_serial_commands(socket_path, commands, user, timeout)
        module.exit_json(changed=True, stdout=output, socket_used=socket_path)
    except Exception as e:
        module.fail_json(msg=str(e), socket_used=socket_path)


if __name__ == '__main__':
    main()
