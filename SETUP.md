# セットアップ手順

8月にお渡しした個別の手順書を1本にまとめたものです。これだけ見れば完了します。

リポジトリ `raumi6188/pa-detector` は既にあり、`pa_detector/` と
`results/pa_2026-08-24.csv` までは入っている状態を前提にしています。

---

## 全体像

```
① リポジトリをPublicに切り替え     ← Pagesを無料で使うため
② ファイルを入れ替え               ← ZIPの中身を丸ごと
③ Pagesを有効化                    ← Settings で1箇所
④ Variables を2つ登録              ← ページURL、Zenn公開フラグ
⑤ 手動実行して確認
```

Secretsは既に4つ登録済みのはずなので、触りません。

---

## ① リポジトリをPublicにする

**PrivateリポジトリのGitHub Pagesは有料プラン（Pro、月約4ドル）が必要**です。
無料で公開するにはPublicにする必要があります。

**Settings** → 一番下の **Danger Zone** →
**Change repository visibility** → **Change to public**

リポジトリ名の入力を求められるので `raumi6188/pa-detector` と打ちます。

公開されるのはコードと結果CSVだけです。**Secrets（APIキー、Gmailパスワード）は
公開リポジトリでも安全**で、GitHubが暗号化して保持し、ログにも自動でマスクされます。
DBは `.gitignore` で除外しているのでリポジトリに入りません。

コードを非公開にしたい場合は、公開用の別リポジトリを立てて `docs/` だけを
送る方法もあります。その場合はトークン設定がひと手間増えるので、必要なら言ってください。

---

## ② ファイルを入れ替える

ZIPを展開すると、こういう構成になっています。

```
pa-detector/
├─ .github/workflows/pa_detector.yml   ← 差し替え（Pages・記事生成を追加済み）
├─ pa_detector/                        ← 差し替え（site.py, article.py を追加）
├─ verification/                       ← 新規（検証結果。ページに表示される）
├─ results/.gitkeep                    ← 既存のCSVはそのまま残してください
├─ docs/.gitkeep, .nojekyll            ← 新規（公開ページの出力先）
├─ requirements.txt
├─ .gitignore
├─ README.md
└─ SETUP.md
```

### ブラウザだけで入れる場合

**`.github` のようにドットで始まるフォルダは、エクスプローラーからのドラッグでは
扱いにくい**ので、ワークフローだけGitHubの画面上で作ります。

**通常のファイル** … リポジトリで **Add file → Upload files**、
`pa_detector` フォルダ・`verification` フォルダ・`requirements.txt`・
`README.md`・`SETUP.md` をドラッグ&ドロップ → **Commit changes**

**ワークフロー** … **Add file → Create new file**、ファイル名欄に

```
.github/workflows/pa_detector.yml
```

と打ち（`/` ごとにフォルダが作られます）、`pa_detector.yml` の中身を全文コピペ。

同じ要領で `.gitignore`、`docs/.nojekyll`、`docs/.gitkeep` も作ります。
`docs/` の2つは中身を空のままでコミットして構いません。

既に `.gitignore` がある場合は上書きしてください。

---

## ③ GitHub Pages を有効にする

**Settings** → 左メニュー **Pages**

| 項目 | 設定 |
|---|---|
| Source | `Deploy from a branch` |
| Branch | `main` / **`/docs`** |

**Save** を押します。

数分後に `https://raumi6188.github.io/pa-detector/` が有効になります。
最初は `docs/` が空なので404が出ますが、④のあと手動実行すれば表示されます。

---

## ④ Variables を2つ登録する

**Secrets ではなく Variables** です。タブが分かれています。

**Settings** → **Secrets and variables** → **Actions** → **Variables** タブ →
**New repository variable**

| 名前 | 値 | 効果 |
|---|---|---|
| `PA_PAGE_URL` | `https://raumi6188.github.io/pa-detector/` | 記事末尾に公開ページへのリンクが入る |
| `PA_ZENN_PUBLISHED` | `false` | Zenn記事を下書きのままにする |

`PA_ZENN_PUBLISHED` は、Zennを使わないなら登録自体が不要です（既定で下書き）。

---

## ⑤ 手動実行して確認する

**Actions** → 左の **プライスアクション検知（日次）** → **Run workflow**

初回は **「メールを送らずページだけ生成する」にチェックを入れて**実行することを勧めます。

DBキャッシュが8月のまま残っていれば差分更新で数分、
消えていればフルビルドで20分程度かかります。

### 見るべきところ

**DBの状態** のステップ：

```
最終営業日 : 2026-10-05（0日前）
収録銘柄数 : 4496（stocks のプライム 1557）
配当テーブル: あり 18,551行
✓ 問題なし
```

**結果・ページ・記事をコミット** のステップで
`docs/index.html` が作られ、コミットされていれば成功です。

数分後に公開ページが表示されます。

問題なければ、チェックを外してもう一度実行するとメールが届きます。
以降は平日17:15頃に自動で動きます。

---

## ローカルのタスクについて

Windowsのタスクスケジューラに `PA検知EOD` を登録していた場合、
**メールが2通届きます**。削除してください。

```
schtasks /Delete /TN "PA検知EOD" /F
```

登録していなければ不要です。

---

## note / Zenn への投稿

### note

**noteには公式の投稿APIがありません**（公式ヘルプに「今後の公開予定も未定」と明記）。
非公式エンドポイントを使う方法はありますが、規約リスク・アカウントリスク・
仕様変更で壊れるリスクがあるので勧めません。

代わりに、毎日 `note/note_YYYY-MM-DD.md` が自動生成され、
**メールにも添付されます**。開いて全選択して貼るだけ、30秒程度です。

### Zenn

ZennはGitHubリポジトリ連携が**公式機能**で、`articles/` を push すれば自動公開されます。

1. [Zenn](https://zenn.dev/) にGitHubアカウントでログイン
2. ダッシュボード → **GitHubからのデプロイ** → **リポジトリを連携**
3. `pa-detector` を選択して認可

既定では `published: false`（下書き）なので、pushしても公開されません。
内容を確認してから、Variables の `PA_ZENN_PUBLISHED` を `true` に変えてください。

ただし**Zennは技術記事のプラットフォーム**です。日々の銘柄一覧は場違いに映る
可能性があります。「18型を定義して10万セルで検証したら優位性がなかった」という
検証の方法論のほうが、Zennの読者層には合うと思います。

---

## つまずきやすいところ

| 症状 | 対処 |
|---|---|
| Actionsにワークフローが出ない | `.github/workflows/` の中にあるか確認 |
| Pagesが404 | `docs/index.html` がコミットされているか、Branch設定が `/docs` か確認 |
| `git push` が失敗 | ymlの `permissions: contents: write` が消えていないか |
| 毎回20分かかる | キャッシュが効いていない。「DBキャッシュを復元」のログを確認 |
| `JQUANTS_API_KEY が未設定` | Secretの名前を確認（前後の空白にも注意） |
| SMTP認証エラー | Gmailのアプリパスワード16桁をスペース詰めで登録しているか |

エラーログはステップを開いてコピーできます。そのまま貼ってください。

---

## 公開にあたって

技術の話ではありませんが2点。

**銘柄名を出す以上、免責があっても動く人はいます。** そのため、ページも記事も
検証結果（優位性なし）を先に置き、本文にも明記する構成にしてあります。

**J-Quantsの利用規約**は一度確認されることを勧めます。株価そのものではなく
検知結果と集計値を出す形にしていますが、再配布の扱いはプランによって
条件が違う可能性があります。
