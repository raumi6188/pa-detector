# J-Quants V2 対応版 — 手順

`403 The requested endpoint does not exist` の原因は、私が書いたパスが
**すべてV1のもの**だったことです。V1は2026年6月1日に廃止されており、
V2はエンドポイントもカラム名も別物になっていました。

---

## 何が変わったか

| | V1（廃止） | V2（現行） |
|---|---|---|
| 上場銘柄一覧 | `/v1/listed/info` | `/v2/equities/master` |
| 株価四本値 | `/v1/prices/daily_quotes` | `/v2/equities/bars/daily` |
| 財務情報 | `/v1/fins/statements` | `/v2/fins/summary` |

カラム名も短縮形になっています。

```
Open→O   High→H   Low→L   Close→C   Volume→Vo   TurnoverValue→Va
AdjustmentFactor→AdjFactor
調整済み四本値: AdjO / AdjH / AdjL / AdjC / AdjVo
```

レスポンスも `{"data": [...], "pagination_key": "..."}` 形式に変わりました。

---

## 調べてみて分かった良い知らせが2つ

### ① V2は調整済みOHLCVを全部くれる

既存DBは `adj_close` しか持っておらず、比率を逆算してO/H/Lを復元していました。
V2は `AdjO / AdjH / AdjL / AdjC / AdjVo` をそのまま返します。

そこで `quotes` テーブルに `adj_open` / `adj_high` / `adj_low` / `adj_volume` を
**自動で追加**するようにしました（`ALTER TABLE`。既存データは壊しません）。
今後は逆算不要で、精度も上がります。

`store.py` は調整済み列があればそれを使い、無ければ従来どおり比率で復元します。
両方の経路を検証済みです。

### ② 配当は `/fins/summary` に含まれていた

Premiumプラン専用の `/fins/dividend` は不要でした。
`FDivAnn`（予想年間配当）と `DivAnn`（実績年間配当）が財務情報に入っています。
**配当利回りが表示できるようになります。**

さらに `FEPS`（会社予想の通期EPS）も取れるので、
**予想PER**が出せます。前回作ったTTM計算は、予想が無い銘柄のフォールバックに回しました。

---

## 実行手順

### ステップ1　まず疎通確認（これを最初に）

```
cd /d C:\quant
set JQUANTS_API_KEY=（APIキー）
set PA_DB_PATH=C:\quant\stock_data.db

python -m pa_detector.update_db --probe
```

3つのエンドポイントを叩いて、**実際に返ってくるカラム名を表示**します。

```
■ 上場銘柄一覧  GET https://api.jquants.com/v2/equities/master
  ✓ 1件
  カラム(9): ['Code', 'Name', 'Sec17Code', ...]

■ 株価四本値  GET https://api.jquants.com/v2/equities/bars/daily
  ✓ 1223件
  カラム(16): ['Date', 'Code', 'O', 'H', 'L', 'C', 'UL', 'LL', 'Vo', 'Va',
              'AdjFactor', 'AdjO', 'AdjH', 'AdjL', 'AdjC', 'AdjVo']
```

**この出力をそのまま貼ってください。** カラム名が想定と違えば、そこだけ直します。
コード側は候補名を順に試す作りにしてあるので、多少の差異は吸収できますが、
念のため確認したいところです。

`403` が出たら、そのエンドポイントが契約プランの対象外という意味です。

### ステップ2　銘柄一覧

```
python -m pa_detector.update_db --refresh-listed
```

現在491銘柄が、プライム全銘柄（約1,600）に増えます。

**491銘柄のまま進めたい場合はこれを飛ばしてください。** 株価取得は
`stocks` の銘柄数に関係なく日付単位で全銘柄ぶん取りますが、
検知の対象は `stocks` に載っている銘柄だけになります。

### ステップ3　株価

```
python -m pa_detector.update_db --quotes
```

DBの最終日（2026-05-29）から今日まで、約60営業日ぶんを取ります。
1営業日あたり全銘柄が1リクエストで返るので、数分で終わるはずです。

### ステップ4　財務・配当

```
python -m pa_detector.update_db --statements
```

配当も同時に入ります。`--dividends` は不要になりましたが、
互換のため受け付けます。

### ステップ5　確認

```
python -m pa_detector.store --health
```

```
  最終営業日 : 2026-08-22（0日前）
  収録銘柄数 : 1600（stocks のプライム 1598）
  配当テーブル: あり 4,800行
  ✓ 問題なし
```

### ステップ6　試運転

```
set PA_OUT_DIR=C:\quant\pa_out
python -m pa_detector.run_intraday --eod --dry-run
```

---

## 以降の運用

```
python -m pa_detector.update_db --all
```

これ一発で銘柄一覧・株価・財務・配当が更新されます。
`run_pa.bat` の先頭に入れておけば、13:50の実行前に自動更新されます。

**分割の再同期を自動で行います。** 分割が起きると過去すべての調整値が
変わるため、直近120日に分割があった銘柄を検出し、その銘柄の全履歴を
取り直すようにしてあります。

---

## 検証済みの内容

モックAPIでV2の応答を再現し、以下を確認しました。

- 3エンドポイントすべての取得とDB書き込み
- `adj_open` 等の列が自動追加されること（既存データを壊さない）
- 分割銘柄で、調整済み列が優先して使われること
- 調整済み列がNULLの旧データでは比率復元にフォールバックすること
- `forecast_eps`（102.5）がEPSとして採用されること
- 予想年間配当（48.0）が `dividends` に入ること

既存の `stock_data.db` に対する回帰テストも実施し、
143件のヒットが変わらず出ることを確認しています。

---

## エラーが出たら

| メッセージ | 意味 |
|---|---|
| `403 ... endpoint does not exist` | パスが誤り。`--probe` の出力を送ってください |
| `403` で `--probe` の一部だけ失敗 | そのデータが契約プランの対象外 |
| `JQUANTS_API_KEY が未設定です` | 環境変数が別窓のもの。同じ窓で `set` し直す |
| `⚠ 0件。--probe でカラム名を確認` | カラム名が想定と違う |
