import os
import re
import json
import urllib.request
from datetime import datetime, timedelta, timezone
import streamlit as st
import feedparser
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
        font-size: 1rem;
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

# RSS経由でチャンネル名を高速取得（キャッシュ化）
@st.cache_data(ttl=86400)
def get_ordered_channels():
    channel_names = []
    if os.path.exists("channels.txt"):
        with open("channels.txt", "r") as f:
            cids = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        for cid in cids:
            try:
                rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
                feed = feedparser.parse(rss_url)
                if feed.entries:
                    name = feed.feed.get('title', 'Unknown Channel')
                    safe_name = re.sub(r'[\\/*?:"<>|]', '', name)
                    if safe_name not in channel_names:
                        channel_names.append(safe_name)
            except Exception:
                pass
    return channel_names

def get_recent_files(service, max_results=300):
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
    request = service.files().get_media(fileId=file_id)
    return request.execute().decode('utf-8')

# ----------------------------
# 画面レイアウト（タブ切り替え）
# ----------------------------
tab1, tab2 = st.tabs(["🚀 手動で要約", "📂 保存済みの要約履歴"])

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
    st.markdown("### 📺 チャンネルを選択")
    
    try:
        service = get_drive_service()
        # まずドライブからファイルを全取得
        files = get_recent_files(service, max_results=300)
        
        # チャンネルリストの取得
        base_channels = get_ordered_channels()
        display_channels = base_channels + ["その他（手動要約など）"] if base_channels else ["その他（手動要約など）"]
        
        # --- NEWバッジ判定（過去24時間以内の更新があるか） ---
        now_utc = datetime.now(timezone.utc)
        new_threshold = now_utc - timedelta(hours=24)
        
        channel_has_new = {ch: False for ch in display_channels}
        
        for f in files:
            if 'createdTime' in f:
                # DriveAPIの日付(UTC)を変換
                created_time = datetime.fromisoformat(f['createdTime'].replace("Z", "+00:00"))
                if created_time >= new_threshold:
                    # このファイルは新しい！ どのチャンネルのものか判定
                    is_other = True
                    for bc in base_channels:
                        if f"_{bc}_" in f['name']:
                            channel_has_new[bc] = True
                            is_other = False
                            break
                    if is_other:
                        channel_has_new["その他（手動要約など）"] = True
        
        # セッション（選択状態）の初期化
        if "selected_channel" not in st.session_state:
            st.session_state.selected_channel = display_channels[0]
            
        # ボタンを2列で並べる
        cols = st.columns(2)
        for i, ch in enumerate(display_channels):
            with cols[i % 2]:
                # NEWバッジの追加
                btn_label = f"🆕 {ch}" if channel_has_new[ch] else ch
                
                is_selected = (ch == st.session_state.selected_channel)
                b_type = "primary" if is_selected else "secondary"
                
                if st.button(btn_label, key=f"btn_{i}", use_container_width=True, type=b_type):
                    st.session_state.selected_channel = ch
                    st.rerun()

        current_ch = st.session_state.selected_channel
        st.divider()
        
        if not files:
            st.info("ドライブに保存された要約がまだありません。")
        else:
            # 選択されたボタンに応じて、表示するファイルを絞り込む
            if current_ch == "その他（手動要約など）":
                target_files = []
                for f in files:
                    is_other = True
                    for bc in base_channels:
                        if f"_{bc}_" in f['name']:
                            is_other = False
                            break
                    if is_other:
                        target_files.append(f)
            else:
                target_files = [f for f in files if f"_{current_ch}_" in f['name']]
            
            if not target_files:
                st.info(f"「{current_ch}」の要約はまだありません。")
            else:
                st.markdown(f"#### 📄 「{current_ch}」の要約一覧")
                # セレクトボックス用の辞書
                file_options = {f['name'].split('_', 2)[-1].replace('.txt', ''): f for f in target_files}
                
                selected_label = st.selectbox("確認したい動画を選択してください", list(file_options.keys()))
                
                if selected_label:
                    selected_file = file_options[selected_label]
                    file_id = selected_file['id']
                    file_name = selected_file['name']
                    
                    # --- 動画のURLを抽出してボタンを配置 ---
                    st.write("") # 少し余白
                    match = re.search(r'\[([a-zA-Z0-9_-]{11})\]\.txt$', file_name)
                    if match:
                        vid = match.group(1)
                        video_url = f"https://www.youtube.com/watch?v={vid}"
                        # ここにリンクボタンを表示
                        st.link_button("▶️ YouTubeアプリでこの動画を開く", video_url, use_container_width=True)
                    else:
                        # 抽出できなかった場合の予備（ほぼ起きない）
                        st.warning("動画のURLリンクが生成できませんでした。")
                    st.write("") # 少し余白
                    
                    with st.spinner("要約内容を読み込み中..."):
                        content = read_file_content(service, file_id)
                    
                    st.text_area("要約テキスト", content, height=500)
                
    except Exception as e:
        st.error(f"履歴の取得に失敗しました: {e}")
