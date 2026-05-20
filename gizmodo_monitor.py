#!/usr/bin/env python3
import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

# ==========================================
# 設定値
# ==========================================
# 2026年リニューアル後の正しいRSSフィードURLを指定
RSS_URL = "https://www.gizmodo.jp/feed/index.xml"
STATE_FILE = "state.json"
MAX_EMBEDS = 10  # Discord Webhookの1回あたり最大Embed制限
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def log(message: str, level: str = "INFO"):
    """タイムスタンプ付きの標準ログ出力"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def fetch_rss_feed(url: str) -> list:
    """
    RSSフィードを取得して解析する。
    相対パスリダイレクト（例: '/feed/index.xml'）やHTTP 307/308を安全に自動追従します。
    """
    log(f"RSSフィードの取得を開始: {url}")
    current_url = url
    max_redirects = 3
    xml_data = None

    # リダイレクト自動追従ループ
    for attempt in range(max_redirects + 1):
        req = urllib.request.Request(current_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                xml_data = response.read()
                if current_url != url:
                    log(f"リダイレクト追従に成功。最終解決URL: {response.geturl()}")
                break
        except urllib.error.HTTPError as e:
            # 301, 302, 303, 307, 308 のリダイレクトステータスを補足
            if e.code in (301, 302, 303, 307, 308) and attempt < max_redirects:
                new_url = e.headers.get("Location")
                if new_url:
                    # 相対パス（例: /feed/index.xml）を絶対URLに結合・変換する
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

    # XMLパース
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        log(f"XMLのパース（解析）に失敗しました。フィードの形式が崩れている可能性があります: {e}", "ERROR")
        return []

    articles = []
    # 標準的なRSS 2.0形式の要素を抽出
    for item in root.findall(".//item"):
        title = item.find("title")
        link = item.find("link")
        pub_date = item.find("pubDate")
        creator = item.find("{http://purl.org/dc/elements/1.1/}creator")
        description = item.find("description")

        title_text = title.text if title is not None else "無題"
        link_text = link.text.strip() if link is not None else ""
        pub_date_text = pub_date.text if pub_date is not None else ""
        author_text = creator.text if creator is not None else "GIZMODO JAPAN"
        
        # 本文抜粋（HTMLタグの簡易除去）
        desc_text = ""
        if description is not None and description.text:
            desc_text = description.text.split("<")[0][:100] + "..."

        if link_text:
            articles.append({
                "title": title_text,
                "link": link_text,
                "pub_date": pub_date_text,
                "author": author_text,
                "description": desc_text
            })

    log(f"RSSフィードより {len(articles)} 件の記事を解析完了。")
    return articles


def load_state() -> list:
    """ローカル（state.json）から既読のURLリストをロードする"""
    if not os.path.exists(STATE_FILE):
        log("過去の履歴ファイル(state.json)が存在しません。初回実行として処理します。")
        return []
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("notified_urls", [])
    except Exception as e:
        log(f"履歴ファイルの読み込みエラー: {e}", "WARNING")
        return []


def save_state(notified_urls: list):
    """既読のURLリストを保存する（履歴の上限は500件とし、肥大化を防止）"""
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
    429レートリミット対策、リトライ制御、各種エラー捕捉を完備。
    """
    payload = {
        "username": "Gizmodo 新着BOT",
        "avatar_url": "https://www.gizmodo.jp/favicon.ico",
        "embeds": embeds
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT
        },
        method="POST"
    )

    max_retries = 5
    delay = 1.0

    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status in (200, 204):
                    log(f"Discord通知成功 ({len(embeds)}件のEmbedを送出)")
                    return True
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            
            # HTTP 429: Discordのレートリミット
            if e.code == 429:
                retry_after = 5.0
                try:
                    resp_json = json.loads(body)
                    retry_after = float(resp_json.get("retry_after", 5.0))
                except Exception:
                    retry_after = float(e.headers.get("Retry-After", 5.0))
                
                log(f"Discordがレートリミット(429)に到達しました。 {retry_after}秒間待機します。(試行: {attempt}/{max_retries})", "WARNING")
                time.sleep(retry_after)
                continue
            
            # HTTP 403: トークン無効またはアクセス拒否
            elif e.code == 403:
                log(f"Discordから 403 Forbidden が返されました。Webhook URLが無効か、ブロックされています。レスポンス: {body}", "ERROR")
                return False
            
            # HTTP 400: リクエスト不正
            elif e.code == 400:
                log(f"Discordから 400 Bad Request が返されました。データ構造に問題があります。レスポンス: {body}", "ERROR")
                return False
            
            # HTTP 5xx: サーバー一時障害（リトライ対象）
            elif e.code >= 500:
                log(f"Discord側の一時的なサーバーエラー (HTTP {e.code})。{delay}秒後にリトライします。レスポンス: {body}", "WARNING")
                time.sleep(delay)
                delay *= 2
                continue
            else:
                log(f"Discordへの送信時にエラーが発生しました (HTTP {e.code}): {e.reason}. レスポンス: {body}", "ERROR")
                return False
        except Exception as e:
            log(f"Discordとの接続中に例外が発生しました: {e}。{delay}秒後にリトライします。", "WARNING")
            time.sleep(delay)
            delay *= 2

    log("リトライ制限を超過したため、Discordへの送信タスクは失敗しました。", "ERROR")
    return False


def build_embed(article: dict) -> dict:
    """記事情報オブジェクトからDiscordのEmbed構造に成形する"""
    color_hex = 1507330  # ギズモードレッド (#e60012)
    return {
        "title": article["title"],
        "url": article["link"],
        "description": article["description"],
        "color": color_hex,
        "author": {"name": article["author"]},
        "footer": {"text": f"Gizmodo Japan • {article['pub_date']}"}
    }


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        log("環境変数 'DISCORD_WEBHOOK_URL' が未設定のため、処理を中断します。", "ERROR")
        return

    # 1. 既読リスト読み込み
    notified_urls = load_state()
    is_initial_run = (len(notified_urls) == 0)

    # 2. RSSフィードの取得・解析
    articles = fetch_rss_feed(RSS_URL)
    if not articles:
        log("フィードが空、あるいは取得スキップされました。処理を完了します。")
        return

    # 3. 重複排除（未通知の記事を検出）
    new_articles = []
    for art in articles:
        if art["link"] not in notified_urls:
            new_articles.append(art)

    if not new_articles:
        log("新しい未読記事はありません。")
        return

    log(f"新着記事を {len(new_articles)} 件検知しました。")

    # 初回実行時は通知爆発を避けるため、現在の全記事を履歴に突っ込んで終了する
    if is_initial_run:
        log("初回実行を検知。現在のフィード記事をすべて既読として保存し、通知送信はスキップします。", "INFO")
        all_links = [art["link"] for art in articles]
        save_state(all_links)
        return

    # 4. 時系列順（古い記事が上、新しい記事が下）にして送信する
    # RSSは通常、最新が先頭[0]にあるため、reverse()をかけることで古い順にする
    new_articles.reverse()

    embeds_to_send = []
    processed_links = []

    # 最大10件までに制限（Discordの上限を厳守）
    for art in new_articles[:MAX_EMBEDS]:
        embeds_to_send.append(build_embed(art))
        processed_links.append(art["link"])

    # 5. Discordへ通知
    if embeds_to_send:
        success = send_to_discord(webhook_url, embeds_to_send)
        if success:
            # 送信できたものだけ既読に追加して保存
            notified_urls.extend(processed_links)
            save_state(notified_urls)
        else:
            log("Discord送信プロセスでエラーが発生したため、今回分の既読状態の更新を保留しました。", "WARNING")


if __name__ == "__main__":
    main()
