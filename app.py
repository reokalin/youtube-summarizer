import os
import re
import json
import urllib.request
from datetime import datetime, timedelta, timezone
import streamlit as st
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from google import genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# ページ設定
st.set_page_config(page_title="YouTube Summarizer", page_icon="🎬", layout="centered")

# スマホ向けUI最適化（CSS）
st.markdown("""
<style>
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 2rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }
    .stButton>button {
        height: 3.5rem;
        font-size: 1.1rem;
        font-weight: bold;
        border-radius: 8px;
    }
    input {
        font-size: 1.05rem !important;
    }
</style>
""", unsafe_allow_html=True)

st.title("🎬 YouTube AI Summarizer")
st.markdown("<p style='font-size: 0.9rem; color: gray;'>動画URLから内容を要約し、企業名と共にドライブへ自動保存します。</p>", unsafe_allow_html=True)

# Secrets情報の読み込み
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    GOOGLE_DRIVE_FOLDER_ID = st.secrets["GOOGLE_DRIVE_FOLDER_ID"]
    CLIENT_ID = st.secrets["GOOGLE_CLIENT_ID"]
    CLIENT_SECRET = st.secrets["GOOGLE_CLIENT_SECRET"]
    REFRESH_TOKEN = st.secrets["GOOGLE_REFRESH_TOKEN"]
except Exception:
    st.error("設定情報（Secrets）が見つかりません。")
    st.stop()

# ----------------------------
# 各種関数定義
# ----------------------------
def get_drive_service():
    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build('drive', 'v3', credentials=creds)

def extract_video_id(url):
    match = re.search(r'(?:v=|\/|shorts\/)([0-9A-Za-z_-]{11})', url)
    return match.group(1) if match else None

def get_video_metadata(video_url):
    oembed_url = f"https://www.youtube.com/oembed?url={video_url}&format=json"
    try:
        with urllib.request.urlopen(oembed_url) as response:
            data = json.loads(response.read().decode('utf-8'))
            title = re.sub(r'[\\/*?:"<>|]', '', data.get("title", "不明な動画"))
            author = re.sub(r'[\\/*?:"<>|]', '', data.get("author_name", "不明なチャンネル"))
            return title, author
    except Exception:
        return "動画タイトル不明", "チャンネル不明"

def get_recent_files(service, max_results=15):
    """ドライブから最新のテキストファイルを取得"""
    query = f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed=false and mimeType='text/plain'"
    results = service.files().list(
        q=query,
        orderBy='createdTime desc',
        spaces='drive',
        fields='files(id, name, createdTime)',
        pageSize=max_results
    ).execute()
    return results.get('files', [])

def read_file_content(service, file_id):
    """ファイルの内容を読み込む"""
    request = service.files().get_media(fileId=file_id)
    return request.execute().decode('utf-8')

# ----------------------------
# 画面レイアウト（タブ切り替え）
# ----------------------------
tab1, tab2 = st.tabs(["🚀 手動で要約", "📂 自動保存の履歴（確認）"])

# ==========================================
# タブ1：手動実行画面
# ==========================================
with tab1:
    st.markdown("### 今すぐ気になる動画を要約")
    video_url_input = st.text_input("YouTube動画URLをペースト", placeholder="https://www.youtube.com/watch?v=...")

    if st.button("🚀 要約してGoogleドライブへ保存", type="primary", use_container_width=True):
        if not video_url_input:
            st.warning("URLを入力してください。")
        else:
            video_id = extract_video_id(video_url_input)
            if not video_id:
                st.error("有効なYouTube URLではありません。")
            else:
                clean_url = f"https://www.youtube.com/watch?v={video_id}"
                transcript_text = None
                status_text = st.empty()
                
                status_text.info("🔍 動画の情報を取得中...")
                title, author = get_video_metadata(clean_url)
                
                jst = timezone(timedelta(hours=+9), 'JST')
                today_str = datetime.now(jst).strftime("%Y%m%d")
                file_name = f"{today_str}_{author}_{title}_[{video_id}].txt"

                status_text.info("📝 動画のテキストデータを取得中...")
                try:
                    yt_api = YouTubeTranscriptApi()
                    fetched = yt_api.fetch(video_id, languages=['ja', 'en'])
                    formatter = TextFormatter()
                    transcript_text = formatter.format_transcript(fetched)
                except Exception:
                    pass 

                status_text.info("🧠 Gemini 2.5 Flash が内容を解析・要約中...")
                try:
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
                    if transcript_text:
                        prompt += f"\n\n【文字起こしテキスト】\n{transcript_text[:30000]}"
                        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
                    else:
                        response = client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=[{"file_data": {"file_uri": clean_url, "mime_type": "video/mp4"}}, prompt]
                        )
                    summary_result = response.text
                except Exception as e:
                    status_text.empty()
                    st.error(f"Gemini API 解析エラー: {e}")
                    st.stop()

                status_text.info("☁️ Googleドライブへファイルを保存中...")
                try:
                    service = get_drive_service()
                    file_content_full = f"ファイル名: {file_name}\nURL: {clean_url}\n\n====================\n【AI要約結果】\n====================\n\n{summary_result}"
                    file_metadata = {'name': file_name, 'parents': [GOOGLE_DRIVE_FOLDER_ID]}
                    media = MediaInMemoryUpload(file_content_full.encode('utf-8'), mimetype='text/plain')
                    
                    service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                    status_text.empty()
                    st.success(f"🎉 保存完了！\nファイル名: `{file_name}`")
                    
                    with st.expander("📝 要約結果を見る", expanded=True):
                        st.markdown(summary_result)
                except Exception as e:
                    status_text.empty()
                    st.error(f"Googleドライブ保存エラー: {e}")

# ==========================================
# タブ2：自動・手動保存の履歴確認画面
# ==========================================
with tab2:
    st.markdown("### ドライブに保存された最新の要約")
    st.caption("GitHub Actions（自動）や手動で保存した最近の要約（最新15件）を確認できます。")
    
    if st.button("🔄 履歴を更新", use_container_width=True):
        st.rerun()
        
    try:
        service = get_drive_service()
        files = get_recent_files(service, max_results=15)
        
        if not files:
            st.info("保存された要約がまだありません。")
        else:
            # 選択肢用の辞書作成（ファイル名 -> ファイルID）
            file_options = {f['name']: f['id'] for f in files}
            
            # ドロップダウンリストでファイルを選択
            selected_file_name = st.selectbox("確認したいファイルを選択してください", list(file_options.keys()))
            
            if selected_file_name:
                file_id = file_options[selected_file_name]
                with st.spinner("要約内容を読み込み中..."):
                    content = read_file_content(service, file_id)
                
                # スクロール可能なテキストエリアで内容を表示
                st.text_area("テキスト内容", content, height=500)
                
    except Exception as e:
        st.error(f"履歴の取得に失敗しました: {e}")
