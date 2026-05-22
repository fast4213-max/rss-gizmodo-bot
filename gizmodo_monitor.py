#!/usr/bin/env python3
import os
import sys
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ==========================================
# 設定値
# ==========================================
RSS_URL = "https://www.gizmodo.jp/feed/index.xml"
STATE_FILE = "state.json"
MAX_EMBEDS = 10  # Discord Webhookの1回あたり最大Embed制限
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def log(message: str, level: str = "INFO"):
    """タイムスタンプ付きのログ出力。ERRORとWARNINGは標準エラー出力へ"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {message}"
    # [FIX #7] ERROR/WARNINGはstderrへ出力することでCI/CD環境での判定を可能にする
    if level in ("ERROR", "WARNING"):
        print(line, file=sys.stderr)
    else:
        print(line)


def strip_html(text: str) -> str:
    """
    HTMLタグを除去し、HTMLエンティティをデコードする。
    [FIX #6] split("<")より安全なHTMLクリーニング処理
    """
    # タグ除去
    cleaned = re.sub(r"<[^>]+>", "", text)
    # HTMLエンティティのデコード（&amp; → & など）
    cleaned = html.unescape(cleaned)
    # 連続する空白・改行を1つのスペースに
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_to_jst(date_str: str) -> str:
    """
    RFC 2822またはISO 8601形式の日付文字列を日本時間(JST)に変換する。
    [FIX #5] ISO 8601形式（例: 2024-01-15T10:00:00+09:00）にも対応
    """
    if not date_str:
        return ""
    jst = timezone(timedelta(hours=9), "JST")

    # まずRFC 2822として試みる（例: Mon, 15 Jan 2024 10:00:00 +0900）
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(jst).strftime("%Y年%m月%d日 %H:%M")
    except Exception:
        pass

    # 次にISO 8601として試みる（例: 2024-01-15T10:00:00+09:00）
    try:
        # Python 3.7+の fromisoformat はタイムゾーン付きISO 8601を処理可能
        # ただし末尾の "Z" は Python 3.11未満では非対応のため置換する
        normalized = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.astimezone(jst).strftime("%Y年%m月%d日 %H:%M")
    except Exception:
        pass

    # どちらでも変換できない場合は元の文字列を返す
    return date_str


def fetch_rss_feed(url: str) -> list:
    """
    RSSフィードを取得して解析する。
    相対パスリダイレクトやHTTP 307/308を安全に自動追従します。
    """
    log(f"RSSフィードの取得を開始: {url}")
    current_url = url
    max_redirects = 3
    xml_data = None

    for attempt in range(max_redirects + 1):
        req = urllib.request.Request(current_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                xml_data = response.read()
                if current_url != url:
                    log(f"リダイレクト追従に成功。最終解決URL: {response.geturl()}")
                break
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and attempt < max_redirects:
                new_url = e.headers.get("Location")
                if new_url:
                    resolved_url = urllib.parse.urljoin(current_url, new_url)
                    log(f"HTTP {e.code} (Redirect) を検知しました。リダイレクト先: {resolved_url}")
                    current_url = resolved_url
                    continue
            log(f"RSSの取得に失敗しました (HTTP {e.code}): {e.reason}", "ERROR")
            return []
        except Exception as e:
            log(f"RSS取得中に予期せぬエラーが発生しました: {e}", "ERROR")
            return []

    if not xml_data:
        log("リダイレクト上限に達した、もしくはデータが取得できませんでした。", "ERROR")
        return []

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        log(f"XMLのパース（解析）に失敗しました: {e}", "ERROR")
        return []

    articles = []
    for item in root.findall(".//item"):
        title = item.find("title")
        link = item.find("link")
        pub_date = item.find("pubDate")

        # 著者（dc:creator など名前空間付き要素に対応）
        author_text = "GIZMODO JAPAN"
        for child in item:
            if child.tag.endswith("creator") and child.text:
                author_text = child.text
                break

        description = item.find("description")

        title_text = title.text.strip() if title is not None and title.text else "無題"

        # [FIX #4] Atom形式の <link href="..."/> にも対応
        link_text = ""
        if link is not None:
            link_text = (link.text or link.get("href", "")).strip()

        raw_pub_date = pub_date.text if pub_date is not None else ""
        pub_date_jst = parse_to_jst(raw_pub_date)

        # [FIX #6] HTMLタグ除去とエンティティデコードを安全に処理
        desc_text = ""
        if description is not None and description.text:
            cleaned = strip_html(description.text)
            desc_text = cleaned[:100] + "..." if len(cleaned) > 100 else cleaned

        # 画像URL抽出（enclosure → media:content → media:thumbnail の優先順）
        image_url = ""
        enclosure = item.find("enclosure")
        if enclosure is not None and enclosure.get("url"):
            image_url = enclosure.get("url")
        else:
            for child in item:
                if child.tag.endswith("content") and child.get("url"):
                    image_url = child.get("url")
                    break
                elif child.tag.endswith("thumbnail") and child.get("url"):
                    image_url = child.get("url")
                    break

        if link_text:
            articles.append({
                "title": title_text,
                "link": link_text,
                "pub_date": pub_date_jst,
                "author": author_text,
                "description": desc_text,
                "image_url": image_url
            })

    log(f"RSSフィードより {len(articles)} 件の記事を解析完了。")
    return articles


def load_state() -> tuple[list, bool]:
    """
    ローカル（state.json）から既読のURLリストをロードする。
    [FIX #2] 戻り値に「ファイルが実際に存在したか」を含め、初回判定を明確化。
    Returns:
        (notified_urls, file_existed): URLリストとファイル存在フラグのタプル
    """
    if not os.path.exists(STATE_FILE):
        log("過去の履歴ファイル(state.json)が存在しません。初回実行として処理します。")
        return [], False
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("notified_urls", []), True
    except Exception as e:
        log(f"履歴ファイルの読み込みエラー: {e}", "WARNING")
        return [], False


def save_state(notified_urls: list):
    """既読のURLリストを保存する（履歴の上限は500件）"""
    keep_urls = notified_urls[-500:]
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "notified_urls": keep_urls,
                "updated_at": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
        log("履歴ファイル(state.json)を保存・更新しました。")
    except Exception as e:
        log(f"履歴ファイルの保存に失敗しました: {e}", "ERROR")


def send_to_discord(webhook_url: str, embeds: list) -> bool:
    """
    Discord WebhookへEmbed形式でメッセージを送信する。
    [FIX #1] リトライのたびにRequestオブジェクトを再生成することで
             ボディの二重読み取り問題を回避する。
    """
    payload = json.dumps({
        "username": "Gizmodo 新着BOT",
        "avatar_url": "https://www.gizmodo.jp/favicon.ico",
        "embeds": embeds
    }).encode("utf-8")

    max_retries = 5
    delay = 1.0

    for attempt in range(1, max_retries + 1):
        # [FIX #1] リトライごとに新しいRequestオブジェクトを生成する
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status in (200, 204):
                    log(f"Discord通知成功 ({len(embeds)}件のEmbedを送出)")
                    return True
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""

            if e.code == 429:
                retry_after = 5.0
                try:
                    retry_after = float(json.loads(body).get("retry_after", 5.0))
                except Exception:
                    retry_after = float(e.headers.get("Retry-After", 5.0))
                log(f"レートリミット(429)に到達。{retry_after}秒待機します。(試行: {attempt}/{max_retries})", "WARNING")
                time.sleep(retry_after)
                continue
            elif e.code == 403:
                log(f"403 Forbidden: Webhook URLが無効かブロックされています。レスポンス: {body}", "ERROR")
                return False
            elif e.code == 400:
                log(f"400 Bad Request: データ構造に問題があります。レスポンス: {body}", "ERROR")
                return False
            elif e.code >= 500:
                log(f"Discord側のサーバーエラー (HTTP {e.code})。{delay}秒後にリトライします。", "WARNING")
                time.sleep(delay)
                delay *= 2
                continue
            else:
                log(f"送信エラー (HTTP {e.code}): {e.reason}. レスポンス: {body}", "ERROR")
                return False
        except Exception as e:
            log(f"接続中に例外が発生しました: {e}。{delay}秒後にリトライします。", "WARNING")
            time.sleep(delay)
            delay *= 2

    log("リトライ制限を超過したため、Discord送信は失敗しました。", "ERROR")
    return False


def build_embed(article: dict) -> dict:
    """記事情報オブジェクトからDiscordのEmbed構造に成形する"""
    embed = {
        "title": article["title"],
        "url": article["link"],
        "description": article["description"],
        "color": 1507330,  # ギズモードレッド (#e60012)
        "author": {"name": article["author"]},
        "footer": {"text": f"Gizmodo Japan • {article['pub_date']}"}
    }
    if article.get("image_url"):
        embed["thumbnail"] = {"url": article["image_url"]}
    return embed


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        log("環境変数 'DISCORD_WEBHOOK_URL' が未設定のため、処理を中断します。", "ERROR")
        return

    # 1. 既読リスト読み込み
    # [FIX #2] file_existedフラグで初回判定を明確化（URLリストの中身に依存しない）
    notified_urls, file_existed = load_state()
    is_initial_run = not file_existed

    # 2. RSSフィードの取得・解析
    articles = fetch_rss_feed(RSS_URL)
    if not articles:
        log("フィードが空、あるいは取得スキップされました。処理を完了します。")
        return

    # 3. 重複排除（未通知の記事を検出）
    notified_set = set(notified_urls)  # 検索をO(1)にする
    new_articles = [art for art in articles if art["link"] not in notified_set]

    if not new_articles:
        log("新しい未読記事はありません。")
        return

    log(f"新着記事を {len(new_articles)} 件検知しました。")

    # 4. 初回実行時は通知せず既読保存のみ
    if is_initial_run:
        log("初回実行を検知。現在のフィード記事をすべて既読として保存し、通知送信はスキップします。")
        save_state([art["link"] for art in articles])
        return

    # 時系列順（古い順）に並べ替え
    new_articles.reverse()

    batch = new_articles[:MAX_EMBEDS]
    embeds_to_send = [build_embed(art) for art in batch]

    # 5. Discordへ通知
    if embeds_to_send:
        success = send_to_discord(webhook_url, embeds_to_send)
        if success:
            # 送信成功した分だけ既読登録する。残りは次回実行時に通知される。
            notified_urls.extend([art["link"] for art in batch])
            save_state(notified_urls)
            remaining = len(new_articles) - len(batch)
            if remaining > 0:
                log(f"送信上限({MAX_EMBEDS}件)のため、残り{remaining}件は次回実行時に通知します。")
        else:
            log("Discord送信でエラーが発生したため、今回分の既読状態の更新を保留しました。", "WARNING")


if __name__ == "__main__":
    main()
