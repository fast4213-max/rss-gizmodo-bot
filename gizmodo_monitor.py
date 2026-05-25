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
# フィード設定
# ==========================================
FEEDS = [
    {
        "id": "gizmodo",
        "url": "https://www.gizmodo.jp/index.xml",
        "webhook_env": "DISCORD_WEBHOOK_GIZMODO",
        "bot_name": "Gizmodo 新着BOT",
        "favicon": "https://www.gizmodo.jp/favicon.ico",
        "color": 0xE60012,  # ギズモードレッド
        "default_author": "GIZMODO JAPAN",
    },
    {
        "id": "itmedia",
        "url": "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml",
        "webhook_env": "DISCORD_WEBHOOK_ITMEDIA",
        "bot_name": "ITmedia NEWS 新着BOT",
        "favicon": "https://www.itmedia.co.jp/favicon.ico",
        "color": 0x005BAC,  # ITmediaブルー
        "default_author": "ITmedia NEWS",
    },
    {
        "id": "ascii_digital",
        "url": "https://ascii.jp/digital/rss.xml",
        "webhook_env": "DISCORD_WEBHOOK_ASCII",
        "bot_name": "ASCII デジタル 新着BOT",
        "favicon": "https://ascii.jp/favicon.ico",
        "color": 0xE95504,
        "default_author": "ASCII.jp",
    },
    {
        "id": "ascii_pc",
        "url": "https://ascii.jp/pc/rss.xml",
        "webhook_env": "DISCORD_WEBHOOK_ASCII",
        "bot_name": "ASCII PC 新着BOT",
        "favicon": "https://ascii.jp/favicon.ico",
        "color": 0xE95504,
        "default_author": "ASCII.jp",
    },
    {
        "id": "pcwatch",
        "url": "https://pc.watch.impress.co.jp/data/rss/1.0/pcw/feed.rdf",
        "webhook_env": "DISCORD_WEBHOOK_PCWATCH",
        "bot_name": "PC Watch 新着BOT",
        "favicon": "https://pc.watch.impress.co.jp/favicon.ico",
        "color": 0x0068B7,  # PC Watchブルー
        "default_author": "PC Watch",
    },
    {
        "id": "ktai",
        "url": "https://k-tai.watch.impress.co.jp/data/rss/1.0/ktw/feed.rdf",
        "webhook_env": "DISCORD_WEBHOOK_KTAI",
        "bot_name": "ケータイ Watch 新着BOT",
        "favicon": "https://k-tai.watch.impress.co.jp/favicon.ico",
        "color": 0x00A960,  # ケータイWatchグリーン
        "default_author": "ケータイ Watch",
    },
]

STATE_FILE = "state.json"
MAX_EMBEDS = 10
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# ==========================================
# ユーティリティ
# ==========================================

def log(message: str, level: str = "INFO", feed_id: str = ""):
    """タイムスタンプ付きのログ出力。ERRORとWARNINGは標準エラー出力へ"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"[{feed_id.upper()}] " if feed_id else ""
    line = f"[{timestamp}] [{level}] {prefix}{message}"
    if level in ("ERROR", "WARNING"):
        print(line, file=sys.stderr)
    else:
        print(line)


def strip_html(text: str) -> str:
    """HTMLタグを除去し、HTMLエンティティをデコードする"""
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_to_jst(date_str: str) -> str:
    """RFC 2822またはISO 8601形式の日付文字列を日本時間(JST)に変換する"""
    if not date_str:
        return ""
    jst = timezone(timedelta(hours=9), "JST")

    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(jst).strftime("%Y年%m月%d日 %H:%M")
    except Exception:
        pass

    try:
        normalized = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.astimezone(jst).strftime("%Y年%m月%d日 %H:%M")
    except Exception:
        pass

    return date_str


# ==========================================
# RSS取得・解析
# ==========================================

def fetch_rss_feed(url: str, default_author: str, feed_id: str = "") -> list:
    """RSSフィードを取得して解析する"""
    log(f"RSSフィードの取得を開始: {url}", feed_id=feed_id)
    current_url = url
    max_redirects = 3
    xml_data = None

    for attempt in range(max_redirects + 1):
        req = urllib.request.Request(current_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                xml_data = response.read()
                if current_url != url:
                    log(f"リダイレクト追従に成功。最終解決URL: {response.geturl()}", feed_id=feed_id)
                break
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and attempt < max_redirects:
                new_url = e.headers.get("Location")
                if new_url:
                    resolved_url = urllib.parse.urljoin(current_url, new_url)
                    log(f"HTTP {e.code} Redirect → {resolved_url}", feed_id=feed_id)
                    current_url = resolved_url
                    continue
            log(f"RSSの取得に失敗しました (HTTP {e.code}): {e.reason}", "ERROR", feed_id=feed_id)
            return []
        except Exception as e:
            log(f"RSS取得中に予期せぬエラーが発生しました: {e}", "ERROR", feed_id=feed_id)
            return []

    if not xml_data:
        log("リダイレクト上限に達した、もしくはデータが取得できませんでした。", "ERROR", feed_id=feed_id)
        return []

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        log(f"XMLのパースに失敗しました: {e}", "ERROR", feed_id=feed_id)
        return []

    def find_local(parent, local_name):
        """名前空間の有無に関わらずローカル名でタグを検索する（RSS 1.0/RDF対応）"""
        for child in parent:
            local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if local == local_name:
                return child
        return None

    # RSS 2.0は <item>、RSS 1.0（RDF）は名前空間付き <rss:item> 等になるため両方に対応
    all_items = [el for el in root.iter()
                 if (el.tag.split("}")[-1] if "}" in el.tag else el.tag) == "item"]

    articles = []
    for item in all_items:
        title = find_local(item, "title")
        link = find_local(item, "link")
        pub_date = find_local(item, "pubDate") or find_local(item, "date")  # RSS 1.0はdc:dateを使う場合あり

        author_text = default_author
        for child in item:
            if child.tag.endswith("creator") and child.text:
                author_text = child.text
                break

        description = find_local(item, "description")

        title_text = title.text.strip() if title is not None and title.text else "無題"

        link_text = ""
        if link is not None:
            link_text = (link.text or link.get("href", "")).strip()

        raw_pub_date = pub_date.text if pub_date is not None else ""
        pub_date_jst = parse_to_jst(raw_pub_date)

        desc_text = ""
        if description is not None and description.text:
            cleaned = strip_html(description.text)
            desc_text = cleaned[:100] + "..." if len(cleaned) > 100 else cleaned

        image_url = ""
        enclosure = find_local(item, "enclosure")
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
                "image_url": image_url,
            })

    log(f"{len(articles)} 件の記事を解析完了。", feed_id=feed_id)
    return articles


# ==========================================
# state.json 管理
# ==========================================

def load_state() -> tuple[dict, bool]:
    """
    state.jsonから全フィードの既読URLを読み込む。
    旧フォーマット（notified_urlsがトップレベル）も自動マイグレーションする。
    Returns:
        (feeds_state, file_existed)
        feeds_state: { "gizmodo": {"notified_urls": [...]}, ... }
    """
    if not os.path.exists(STATE_FILE):
        log("履歴ファイル(state.json)が存在しません。初回実行として処理します。")
        return {}, False

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 旧フォーマット検出: トップレベルに notified_urls がある場合
        if "notified_urls" in data and "feeds" not in data:
            log("旧フォーマットのstate.jsonを検出。自動マイグレーションします。", "WARNING")
            migrated = {
                "gizmodo": {"notified_urls": data["notified_urls"]}
            }
            return migrated, True

        return data.get("feeds", {}), True

    except Exception as e:
        log(f"履歴ファイルの読み込みエラー: {e}", "WARNING")
        return {}, False


def save_state(feeds_state: dict):
    """全フィードの既読URLをstate.jsonに保存する（各フィード上限500件）"""
    trimmed = {}
    for feed_id, feed_data in feeds_state.items():
        urls = feed_data.get("notified_urls", [])
        trimmed[feed_id] = {"notified_urls": urls[-500:]}

    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "feeds": trimmed,
                "updated_at": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
        log("履歴ファイル(state.json)を保存・更新しました。")
    except Exception as e:
        log(f"履歴ファイルの保存に失敗しました: {e}", "ERROR")


# ==========================================
# Discord送信
# ==========================================

def send_to_discord(webhook_url: str, embeds: list, feed: dict) -> bool:
    """Discord WebhookへEmbed形式でメッセージを送信する"""
    payload = json.dumps({
        "username": feed["bot_name"],
        "avatar_url": feed["favicon"],
        "embeds": embeds
    }).encode("utf-8")

    max_retries = 5
    delay = 1.0
    feed_id = feed["id"]

    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status in (200, 204):
                    log(f"Discord通知成功 ({len(embeds)}件のEmbedを送出)", feed_id=feed_id)
                    return True
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""

            if e.code == 429:
                retry_after = 5.0
                try:
                    retry_after = float(json.loads(body).get("retry_after", 5.0))
                except Exception:
                    retry_after = float(e.headers.get("Retry-After", 5.0))
                log(f"レートリミット(429)。{retry_after}秒待機します。(試行: {attempt}/{max_retries})", "WARNING", feed_id=feed_id)
                time.sleep(retry_after)
                continue
            elif e.code == 403:
                log(f"403 Forbidden: Webhook URLが無効かブロックされています。レスポンス: {body}", "ERROR", feed_id=feed_id)
                return False
            elif e.code == 400:
                log(f"400 Bad Request: データ構造に問題があります。レスポンス: {body}", "ERROR", feed_id=feed_id)
                return False
            elif e.code >= 500:
                log(f"Discord側のサーバーエラー (HTTP {e.code})。{delay}秒後にリトライします。", "WARNING", feed_id=feed_id)
                time.sleep(delay)
                delay *= 2
                continue
            else:
                log(f"送信エラー (HTTP {e.code}): {e.reason}", "ERROR", feed_id=feed_id)
                return False
        except Exception as e:
            log(f"接続中に例外が発生しました: {e}。{delay}秒後にリトライします。", "WARNING", feed_id=feed_id)
            time.sleep(delay)
            delay *= 2

    log("リトライ制限を超過したため、Discord送信は失敗しました。", "ERROR", feed_id=feed_id)
    return False


def build_embed(article: dict, feed: dict) -> dict:
    """記事情報オブジェクトからDiscordのEmbed構造に成形する"""
    embed = {
        "title": article["title"],
        "url": article["link"],
        "description": article["description"],
        "color": feed["color"],
        "author": {"name": article["author"]},
        "footer": {"text": f"{feed['bot_name'].replace(' 新着BOT', '')} • {article['pub_date']}"}
    }
    if article.get("image_url"):
        embed["thumbnail"] = {"url": article["image_url"]}
    return embed


# ==========================================
# メイン処理
# ==========================================

def process_feed(feed: dict, feeds_state: dict, is_initial_run: bool) -> dict:
    """
    1つのフィードを処理してfeeds_stateを更新して返す。
    初回実行時は通知せず既読保存のみ行う。
    """
    feed_id = feed["id"]

    webhook_url = os.environ.get(feed["webhook_env"])
    if not webhook_url:
        log(f"環境変数 '{feed['webhook_env']}' が未設定のためスキップします。", "WARNING", feed_id=feed_id)
        return feeds_state

    articles = fetch_rss_feed(feed["url"], feed["default_author"], feed_id=feed_id)
    if not articles:
        log("フィードが空、あるいは取得スキップされました。", feed_id=feed_id)
        return feeds_state

    feed_state = feeds_state.get(feed_id, {"notified_urls": []})
    notified_urls = feed_state["notified_urls"]
    notified_set = set(notified_urls)
    new_articles = [art for art in articles if art["link"] not in notified_set]

    if not new_articles:
        log("新しい未読記事はありません。", feed_id=feed_id)
        return feeds_state

    log(f"新着記事を {len(new_articles)} 件検知しました。", feed_id=feed_id)

    if is_initial_run:
        log("初回実行のため通知せず既読として保存します。", feed_id=feed_id)
        feeds_state[feed_id] = {"notified_urls": [art["link"] for art in articles]}
        return feeds_state

    new_articles.reverse()  # 古い順に並べ替え
    batch = new_articles[:MAX_EMBEDS]
    embeds_to_send = [build_embed(art, feed) for art in batch]

    success = send_to_discord(webhook_url, embeds_to_send, feed)
    if success:
        notified_urls.extend([art["link"] for art in batch])
        feeds_state[feed_id] = {"notified_urls": notified_urls}
        remaining = len(new_articles) - len(batch)
        if remaining > 0:
            log(f"送信上限({MAX_EMBEDS}件)のため、残り{remaining}件は次回実行時に通知します。", feed_id=feed_id)
    else:
        log("Discord送信でエラーが発生したため、今回分の既読状態の更新を保留しました。", "WARNING", feed_id=feed_id)

    return feeds_state


def main():
    feeds_state, file_existed = load_state()
    is_initial_run = not file_existed

    for feed in FEEDS:
        feeds_state = process_feed(feed, feeds_state, is_initial_run)

    save_state(feeds_state)


if __name__ == "__main__":
    main()
