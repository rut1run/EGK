# チーム継続確認Bot（DM送信版）

## EGK投票順位パネル

既存の継続確認・DM機能と同じBotで動きます。追加トークンは不要です。

1. 更新した `bot.py` と追加の `vote_panel.py` を同じフォルダに置きます。
2. `python -m pip install -r requirements.txt` を実行し、Botを再起動します。
3. Discordでパネルを置くチャンネルから `/vote_panel` を実行します。
   別チャンネルは `/vote_panel channel:表示先` で指定できます。
4. 停止・削除は `/vote_panel_stop` です。

- コマンドの実行には「サーバー管理」権限が必要です。
- Botには表示先の「チャンネルを見る」「メッセージを送信」「埋め込みリンク」「メッセージ履歴を読む」を許可します。
- EGKの順位・票数を公式の公開データから30秒ごとに取得し、同じ投稿を編集します。元サイトの集計・通信による遅れが加わる場合があります。
- 各サーバー1パネル。設置先とメッセージIDは既存の `DB_PATH`（通常 `keizoku_dm.db`）に保存され、再起動後も継続します。DBは消さずに更新してください。
- 同じチャンネルで再実行すると既存パネルを更新します。別チャンネルを指定すると移設します。
- 取得失敗時は順位・票数を消して取得失敗と表示し、自動再試行します。
- 11月14日22:00〜11月15日12:00（2026年・日本時間）はランキング非公開と表示します。公式から非公開が返された場合も順位を表示しません。
- 公開再開後は発表後の集計データを確認してから結果を表示します。
- パネルを手動削除すると自動更新を停止します。再設置は `/vote_panel` で行えます。
- `GUILD_ID` を設定すると、そのサーバーにコマンドを同期します。未設定の場合はグローバル登録の反映に時間がかかることがあります。
- 出典・投票先：https://stats.lynx-est.com/last-chance-split/

### EC2での更新例

Gitで配置している場合は、Botのフォルダで更新します。

```bash
cd ~/egk-bot
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
```

その後、既存のBotサービスを再起動し、Discordで `/vote_panel` を実行してください。
サービス名が不明な場合は `systemctl list-units --type=service --all | rg -i 'egk|keizoku'` で確認できます。

### 検証

```bash
python -m unittest -v test_vote_panel.py
```

## 仕組み
1. 管理画面の「メンバー」で、運営がユーザーIDと名前を入力 → DM送信先に登録
2. 管理者がPCでBotを起動 → ブラウザで管理画面（http://localhost:8080）が開く
3. 管理画面で シーズン名・本文・絵文字ボタンを入力 → 登録メンバー全員にDM送信
4. メンバーはDMのボタンで回答（押し直すと変更可）
5. 管理画面の「一覧」でシーズン別に集計・内訳確認、CSV出力

## セットアップ
1. https://discord.com/developers/applications でBotを作成しトークン取得（特権インテントは不要）
2. OAuth2 → URL Generator で `bot` と `applications.commands` を選び、サーバーに招待
3. `.env.example` を `.env` にコピーしてトークンを記入
4. 実行
   ```
   pip install -r requirements.txt
   python bot.py
   ```

## 注意
- DMを送るには、メンバーがBotと同じサーバーにいて、「サーバーメンバーからのDMを許可」がONである必要があります（送れなかった人は送信結果に表示されます）。
- ユーザーIDは、Discordの開発者モードをONにして、メンバーを右クリック →「ユーザーIDをコピー」で調べられます。
- 管理画面は自分のPC(127.0.0.1)からのみ開けます。Botを起動している間だけ使えます。
- データは `keizoku_dm.db` に保存されます。前のサーバー投稿版のDBとは別ファイルです。
