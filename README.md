## 事前準備
- リポジトリクローン
```
git clone https://github.com/ool-mddo/mddo-worker.git
```
- .envのパス指定
```
cd mddo-worker
pwd
#pwdで現在のパスを取得
vi .env
```
```
WORKERDIR={{ 上記手順で確認したパスを指定 }}
```
- cRPDライセンスファイルを作成
```
vi clab/license.key
```
→別途入手したライセンス情報を上書き

## 起動
```
docker compose up -d
```
