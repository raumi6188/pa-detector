# プライスアクション検知システム

解説書『弱いプライスアクションと、それを否定するプライスアクション』の
18の型を、毎営業日14:00にメールで通知する。

---

## 1. 何をしているか

```
過去足（J-Quants → 既存SQLite）      ← 確定値。基準線はここから作る
        ＋
当日の未完成足（kabuステーションAPI）  ← 14:00時点のスナップショット
        ↓
    18型の判定 → 確度ランク付け → PER/PBR/利回り付与 → メール
```

**基準線（前日安値・スイング点・移動平均・窓）はすべて確定足から計算する。**
未完成足を混ぜると基準線自体が場中に動き、判定が不安定になるため。

## 2. 確度ランクの意味

14:00以降に動くのは現在値 C だけである。高値 H・安値 L・出来高 V は
単調にしか動かないので、それらを使う条件は一度満たせば剥落しない。
残る不確実性は「引けまでに C が基準線 B を割り込むか」に集約される。

    余裕 = (現在値 − 基準線) ÷ ATR

| ランク | 条件 | 意味 |
|---|---|---|
| A | 余裕 ≥ 1.5 ATR | 後場で剥落する可能性は小さい |
| B | 余裕 ≥ 0.5 ATR | 通常の候補 |
| C | それ未満 | 際どい。メールには載せない |

ATRで正規化するのは、値がさ株と低位株を同じ尺度で比べるため。

**型4（引け際の売り叩きの否定）は既定で除外している。**
CLV は引け際の値動きそのものなので、14:00時点での判定は定義矛盾になる。

## 3. セットアップ

### 3.1 依存

```
python -m pip install pandas numpy requests
```

### 3.2 DBスキーマの確認（最初に必ず実行）

`store.py` の `SCHEMA` は一般的な J-Quants の列名で書いてある。
実際のDBと違えばここを書き換える。

```
python -m pa_detector.store --inspect
```

テーブル名・列名を確認し、`SCHEMA` の値を合わせる。

### 3.3 環境変数

`run_pa.bat` を作り、以下を設定する。

```bat
@echo off
set PA_DB_PATH=C:\quant\jquants.db
set PA_OUT_DIR=C:\quant\pa_out

set KABUS_PASSWORD=（kabuステーションのAPIパスワード）
set KABUS_BASE=http://localhost:18080/kabusapi
set KABUS_RATE_LIMIT=8

set PA_SMTP_USER=（Gmailアドレス）
set PA_SMTP_PASSWORD=（アプリパスワード16桁。通常のログインパスワードではない）
set PA_MAIL_FROM=（Gmailアドレス）
set PA_MAIL_TO=（送信先）

cd /d C:\quant
python -m pa_detector.run_intraday
```

Gmailを使う場合、Googleアカウントで2段階認証を有効にしたうえで
「アプリパスワード」を発行する。通常のパスワードでは送信できない。

### 3.4 動作確認

```
python -m pa_detector.run_intraday --eod --dry-run --limit 100
```

`--eod` で確定足を使い（kabuステーション不要）、`--dry-run` で送信せずHTML保存。
`PA_OUT_DIR` にHTMLが出るのでブラウザで開いて確認する。

## 4. スケジュール登録（Windows タスクスケジューラ）

kabuステーションAPIは 1,574銘柄 × 8req/秒 で約3分半かかるため、
**13:50 起動**にする。

```
schtasks /Create /TN "PA検知" /TR "C:\quant\run_pa.bat" ^
  /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 13:50 /F
```

前提：
- kabuステーションが起動しログイン済みであること
- PCがスリープしていないこと（電源オプションで無効化）
- 祝日は市場が閉まっているためスナップショットが取れず、
  ヒット0件のメールが届く。気になるなら祝日判定を足す

## 5. 出来高比率の較正

14:00時点の出来高は当日フル出来高の7割前後だが、実測すべき値である。
`run_intraday` が毎回スナップショットを保存するので、
2〜3週間ためてから：

```
python -m pa_detector.calibrate_volume --fit
```

出力された中央値を `PA_VOL_FRACTION` に設定する。

## 6. 調整の勘所

| 現象 | 対処 |
|---|---|
| ヒットが多すぎる | `PA_CONF_A/B` を上げる、`PA_MIN_TURNOVER` を上げる |
| ヒットが少なすぎる | `PA_CONF_B` を 0.3 程度に下げる |
| 薄商い銘柄ばかり出る | `PA_MIN_TURNOVER` を 1億円に上げる |
| 特定の型ばかり出る | `PA_EXCLUDE=4,13,14` のように除外する |
| スイング点が細かすぎる | `PA_SWING_N` を 5 に上げる |

**`PA_SWING_N` は結果を最も大きく動かすパラメータである。**
3と5の両方で走らせ、両方で出る銘柄だけを見る、という使い方もできる。
片方でしか出ないシグナルは、構造ではなくノイズを拾っている可能性が高い。

## 7. 検証（次の段階）

現時点でこのシステムが出しているのは「候補」であって、
統計的な優位性はまだ何も確認されていない。解説書 第9章の手順に従い、

1. 検知日 t の t+5 / t+20 リターンを記録する
2. 同日・同流動性層の全銘柄のリターン分布と比較する
3. 尤度比 LR = P(検知|上昇) ÷ P(検知|上昇せず) を型ごとに推定する

LR が1に近い型は、除外候補になる。
`PA_OUT_DIR` に日次CSVが残るので、これがそのまま検証データになる。

## 8. ファイル構成

```
pa_detector/
  config.py            設定（環境変数で上書き）
  store.py             SQLite読み込み。★SCHEMAを実環境に合わせる
  kabus.py             kabuステーションAPIクライアント
  indicators.py        ATR・移動平均・スイング点抽出
  patterns.py          18型の検知器
  confidence.py        確度A/B/C判定
  valuation.py         PER/PBR/配当利回り
  mailer.py            HTMLメール組み立て・送信
  run_intraday.py      エントリポイント
  calibrate_volume.py  出来高比率の較正
```
