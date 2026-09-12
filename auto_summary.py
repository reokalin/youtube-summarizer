import os, re, time
import feedparser
from datetime import datetime, timedelta, timezone
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from google import genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# GitHub Actions から Secrets を読み込む
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")

def get_drive_service():
    creds = Credentials(token=None, refresh_token=REFRESH_TOKEN, token_uri="https://oauth2.googleapis.com/token", client_id=CLIENT_ID, client_secret=CLIENT_SECRET, scopes=["https://www.googleapis.com/auth/drive.file"])
    return build('drive', 'v3', credentials=creds)

def is_processed(service, video_id):
    query = f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents and name contains '[{video_id}]' and trashed=false"
    results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    return len(results.get('files', [])) > 0

def summarize_video(video_id, title, author, service):
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"処理開始: {title} ({clean_url})")
    
    jst = timezone(timedelta(hours=+9), 'JST')
    today_str = datetime.now(jst).strftime("%Y%m%d")
    
    safe_title = re.sub(r'[\\/*?:"<>|]', '', title)
    safe_author = re.sub(r'[\\/*?:"<>|]', '', author)
    file_name = f"{today_str}_{safe_author}_{safe_title}_[{video_id}].txt"

    transcript_text = None
    try:
        yt_api = YouTubeTranscriptApi()
        fetched = yt_api.fetch(video_id, languages=['ja', 'en'])
        formatter = TextFormatter()
        transcript_text = formatter.format_transcript(fetched)
    except Exception:
        print("  -> 字幕なし。Geminiの直接解析にフォールバックします。")

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
以下のYouTube動画のコンテンツを正確に読み取り、わかりやすく要約してください。
【対象動画URL】{clean_url}
【動画タイトル】{title}
【チャンネル名】{author}
【出力フォーマット】
■ 動画概要（3行で要約）
・
・
・
■ 主なキーポイント・要点
・
・
■ 動画内で紹介されている企業
・
・
■ 詳細まとめ・結論
"""
    try:
        if transcript_text:
            prompt += f"\n\n【文字起こしテキスト】\n{transcript_text[:30000]}"
            response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
        else:
            response = client.models.generate_content(
                model='gemini-1.5-flash',
                contents=[{"file_data": {"file_uri": clean_url, "mime_type": "video/mp4"}}, prompt]
            )
        summary_result = response.text
    except Exception as e:
        print(f"  -> Gemini API 解析エラー: {e}")
        return False

    try:
        file_content_full = f"ファイル名: {file_name}\nURL: {clean_url}\n\n====================\n【AI要約結果】\n====================\n\n{summary_result}"
        file_metadata = {'name': file_name, 'parents': [GOOGLE_DRIVE_FOLDER_ID]}
        media = MediaInMemoryUpload(file_content_full.encode('utf-8'), mimetype='text/plain')
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        print(f"  -> ✅ 保存完了: {file_name}")
        return True
    except Exception as e:
        print(f"  -> Googleドライブ保存エラー: {e}")
        return False

def main():
    service = get_drive_service()
    if not os.path.exists("channels.txt"):
        print("channels.txt が見つかりません。")
        return
        
    with open("channels.txt", "r") as f:
        channel_ids = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
    print(f"計 {len(channel_ids)} チャンネルのチェックを開始します。")
    
    for cid in channel_ids:
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
        feed = feedparser.parse(rss_url)
        
        if not feed.entries:
            continue
            
        channel_name = feed.feed.get('title', 'Unknown Channel')
        print(f"\n■ {channel_name} の最新動画をチェック中...")
        
        for entry in feed.entries[:2]:
            video_id = entry.yt_videoid
            title = entry.title
            
            if is_processed(service, video_id):
                print(f"  - スキップ（処理済）: {title}")
            else:
                print(f"  - 🌟 新着動画発見！")
                summarize_video(video_id, title, channel_name, service)
                time.sleep(10)

if __name__ == "__main__":
    main()
