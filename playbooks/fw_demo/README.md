# mddo-fw DNS / Web デモ用 Ansible (`mddo-worker` 版)

`mddo-fw` トポロジ上で、site-a に DNS / Web サーバを構築し、site-b / site-c のクライアントから
「site-a DNS で名前解決 → Web サーバから HTML を GET」するデモを行う Playbook 群です。
`mddo-worker-eda-worker-1` コンテナ内からの直接実行（`ansible-playbook`）および Event-Driven Ansible Webhook（`:48090`）の両方に対応しています。

| ホスト | 役割 | アドレス | データ側 IF (既定) |
| --- | --- | --- | --- |
| site-a-host-1 | DNS サーバ (dnsmasq) `dns.demo.mddo.local` | 192.168.100.10 | eth1 |
| site-a-host-2 | Web サーバ (nginx) `www.demo.mddo.local` | 192.168.200.10 | eth1 |
| site-b-host-1 / 2 | クライアント | 192.168.101.10 / 192.168.201.10 | eth1 |
| site-c-host-1 / 2 | クライアント | 192.168.102.10 / 192.168.202.10 | eth1 |

- サーバ / クライアントとも `ool-iperf` イメージ（python・systemd・sshd 無し）を前提とし、`mddo-worker-eda-worker-1` から `local` 接続 + `docker exec` (`sh`) で操作するため、追加の Ansible コレクション（`community.docker` 等）のインストールは不要です。
- コンテナ名プレフィックスは `-e snapshot_name=...` → `/clab/clab-topo.yaml` の `name` → `emulated_asis` の優先順で自動解決されます（`clab-<snapshot_name>-<ホスト名>`）。

## 構成

```
playbooks/fw_demo/
  ansible.cfg
  site.yml                        # 10 → 20 → 30 を一括実行
  00_load_inventory.yml           # -i 省略時 / EDA Webhook 実行時のインベントリ動的ロード
  01_setup_network.yml            # （任意）ホストの IP / 経路設定
  10_deploy_servers.yml           # DNS / Web サーバ構築
  20_test_servers.yml             # サーバのテストシナリオ
  30_demo_clients.yml             # クライアントのデモシナリオ
  90_cleanup.yml                  # 後片付け
  inventory/
    hosts.yml                     # ホスト・グループ定義
    group_vars/all.yml            # デモ共通パラメータ（ドメイン、期待状態、タイムアウト等）
    group_vars/lab_hosts.yml      # 接続設定（local + docker exec、コンテナ名自動解決）
  tasks/                          # 共通タスク（ファイル配置、チェック実行・判定）
  templates/                      # dnsmasq.conf / nginx 設定 / index.html
```

## 実行方法 1: `mddo-worker-eda-worker-1` で `ansible-playbook` を直接実行

```sh
# 一括（サーバ構築 → サーバテスト → クライアントデモ）
docker exec -w /playbooks/fw_demo mddo-worker-eda-worker-1 ansible-playbook site.yml

# as-is（FW で DNS / HTTP が遮断され、ping のみ通ることを期待）
docker exec -w /playbooks/fw_demo mddo-worker-eda-worker-1 ansible-playbook site.yml -e demo_expected=deny

# 個別実行
docker exec -w /playbooks/fw_demo mddo-worker-eda-worker-1 ansible-playbook 10_deploy_servers.yml
docker exec -w /playbooks/fw_demo mddo-worker-eda-worker-1 ansible-playbook 20_test_servers.yml
docker exec -w /playbooks/fw_demo mddo-worker-eda-worker-1 ansible-playbook 30_demo_clients.yml -e demo_expected=deny

# ライブデモ（ステップごとに Enter 待ち / -it を付与）
docker exec -it -w /playbooks/fw_demo mddo-worker-eda-worker-1 \
  ansible-playbook 30_demo_clients.yml -e demo_step_pause=true -l site_b_clients

# 後片付け
docker exec -w /playbooks/fw_demo mddo-worker-eda-worker-1 ansible-playbook 90_cleanup.yml
docker exec -w /playbooks/fw_demo mddo-worker-eda-worker-1 ansible-playbook 90_cleanup.yml -e cleanup_servers=true
```

その他の主なオプション:

| 変数 / オプション | 内容 |
| --- | --- |
| `-e snapshot_name=emulated_asis` | 対象ラボ（スナップショット）名を明示指定 |
| `-e demo_expected=allow\|deny` | FW 通過期待状態（既定: `allow`） |
| `-e demo_step_pause=true` | STEP ごとに Enter 待ち（ライブデモ用、`docker exec -it` で実行） |
| `-e client_resolver_mode=none` | `resolv.conf` を変更せず、`dig` の結果を `curl --resolve` に渡す |
| `-e demo_fail_on_mismatch=false` | 期待と異なっても失敗扱いにしない |
| `--tags ping,dns,http,resolve,info` | 特定の STEP だけ実行 |
| `-l site_b_clients` / `-l site_c_clients` | 対象サイトを絞る |

## 実行方法 2: Event-Driven Ansible Webhook (`:48090`) 経由で実行

`ansible-eda/rulebook.yaml` に `message: "fw_demo"` のルールが定義されています（`rulebook.yaml` 更新後は `docker restart mddo-worker-eda-worker-1` で反映されます）。

```sh
# 一括実行（action 省略または "all"）
curl -X POST http://localhost:48090/ -H 'Content-Type: application/json' \
  -d '{"message": "fw_demo", "demo_expected": "deny"}'

# 個別アクション（setup_network / deploy / test / demo / cleanup）
curl -X POST http://localhost:48090/ -H 'Content-Type: application/json' \
  -d '{"message": "fw_demo", "action": "deploy"}'

curl -X POST http://localhost:48090/ -H 'Content-Type: application/json' \
  -d '{"message": "fw_demo", "action": "test"}'

curl -X POST http://localhost:48090/ -H 'Content-Type: application/json' \
  -d '{"message": "fw_demo", "action": "demo", "demo_expected": "deny", "target_client_group": "site_b_clients"}'

curl -X POST http://localhost:48090/ -H 'Content-Type: application/json' \
  -d '{"message": "fw_demo", "action": "cleanup", "cleanup_servers": true}'
```
